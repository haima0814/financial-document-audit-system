"""
backend/tests/engines/test_supplier_agent.py
supplier_agent 工商失信穿透与 USCC 校验测试
"""
import pytest
from engines.supplier_agent import SupplierAgent, verify_uscc_checksum
from engines.contract.finding import RiskLevelEnum

def test_uscc_checksum():
    """测试统一社会信用代码 18 位校验位算法"""
    # 真实企业统一代码：百度在线网络技术(北京)有限公司: 91110108551385082Q
    assert verify_uscc_checksum("91110108551385082Q") is True
    # 华为技术有限公司: 914403001922038216
    assert verify_uscc_checksum("914403001922038216") is True
    # 篡改最后一位
    assert verify_uscc_checksum("91440300192203821X") is False
    # 非法字符
    assert verify_uscc_checksum("INVALID_TAX_ID_18") is False

@pytest.mark.asyncio
async def test_supplier_agent_clean():
    """测试合规供应商正常放行"""
    findings = await SupplierAgent.run(
        supplier_name="合规科技有限公司",
        uscc="914403001922038216",
        is_dishonest=False,
        operating_status="存续"
    )
    assert len(findings) == 0

@pytest.mark.asyncio
async def test_supplier_agent_dishonest_and_invalid_tax():
    """测试失信老赖与假税号一票否决"""
    findings = await SupplierAgent.run(
        supplier_name="失信老赖皮包公司",
        uscc="123456789012345679", # 假税号 (校验码不匹配)
        is_dishonest=True,
        operating_status="注销"
    )
    assert len(findings) == 3
    rule_codes = {f.rule_code for f in findings}
    assert "R12_SUPPLIER_UNREGISTERED" in rule_codes
    assert "R13_SUPPLIER_DISHONEST" in rule_codes
    assert "R15_SHELL_COMPANY" in rule_codes

from decimal import Decimal
from unittest.mock import AsyncMock
from engines.contract.context import DocumentContext
from engines.contract.result import AgentExecutionStatus
from engines.contract.agent_role import AgentRoleEnum
from engines.orchestrator.master_graph import MasterOrchestrator
from engines.orchestrator.planner import AuditPlanner
from app.core.llm_client import LLMClient

@pytest.mark.asyncio
async def test_supplier_profile_dishonest_triggers_r13():
    """测试真实供应商画像中的 is_dishonest=True 能穿透到 SupplierAgent 并触发 R13"""
    # 1. 直接智能体执行
    findings = await SupplierAgent.run(
        supplier_name="失信供应商",
        uscc="91110108551385082Q",
        is_dishonest=True
    )
    assert any(f.rule_code == "R13_SUPPLIER_DISHONEST" for f in findings)

    # 2. 完整编排流水线端到端穿透
    ctx = DocumentContext(
        document_id=311,
        document_no="CORP-2026-002",
        document_type="CORP_PAYMENT",
        total_amount=Decimal("10000.00"),
        title="采购服务器配件",
        applicant_id=1,
        tenant_id=1,
        line_items=[{"amount": 10000.00, "expense_type": "采购费"}],
        invoices=[{
            "invoice_code": "01",
            "invoice_number": "99990001",
            "total_amount": 10000.00,
            "untaxed_amount": 8849.56,
            "tax_amount": 1150.44,
            "tax_rate": 0.13,
            "seller_tax_id": "91110108551385082Q",
            "seller_name": "北京神州数码技术有限公司",
            "issue_date": "2026-09-01"
        }],
        extra_context={
            "supplier_profiles": {
                "91110108551385082Q": {
                    "uscc": "91110108551385082Q",
                    "supplier_name": "北京神州数码技术有限公司",
                    "is_dishonest": True,
                    "operating_status": "存续"
                }
            }
        }
    )
    result = await MasterOrchestrator.run(task_id="task-dishonest-e2e", context=ctx)
    rule_codes = {f.rule_code for f in result.verified_findings}
    assert "R13_SUPPLIER_DISHONEST" in rule_codes

@pytest.mark.asyncio
async def test_supplier_operating_status_cancelled_triggers_r15():
    """测试 operating_status 异常状态兼容 (注销、吊销、异常、经营异常、撤销) 与空壳公司触发 R15"""
    for st in ["注销", "吊销", "异常", "经营异常", "撤销"]:
        findings = await SupplierAgent.run(
            supplier_name="测试异常状态企业",
            uscc="91110108551385082Q",
            operating_status=st
        )
        assert any(f.rule_code == "R15_SHELL_COMPANY" for f in findings)

    # 空壳公司标记
    findings_shell = await SupplierAgent.run(
        supplier_name="疑似空壳企业",
        uscc="91110108551385082Q",
        operating_status="存续",
        is_shell_company=True
    )
    assert any(f.rule_code == "R15_SHELL_COMPANY" for f in findings_shell)

@pytest.mark.asyncio
async def test_supplier_profile_missing_marks_degraded():
    """测试供应商画像不存在时不默认放行，而是标记为 DEGRADED (SUPPLIER_PROFILE_MISSING)"""
    # 1. 智能体单体：profile_found=False
    findings = await SupplierAgent.run(
        supplier_name="库外未知供应商",
        uscc="91110108551385082Q",
        profile_found=False
    )
    assert findings.is_degraded is True
    assert findings.degraded_reason == "SUPPLIER_PROFILE_MISSING"

    # 2. MasterOrchestrator 编排：supplier_profiles 中无该供应商
    ctx = DocumentContext(
        document_id=302,
        document_no="CORP-2026-003",
        document_type="CORP_PAYMENT",
        total_amount=Decimal("10000.00"),
        title="采购办公电脑",
        applicant_id=1,
        tenant_id=1,
        line_items=[{"amount": 10000.00}],
        invoices=[{
            "invoice_code": "01",
            "invoice_number": "99990002",
            "total_amount": 10000.00,
            "untaxed_amount": 8849.56,
            "tax_amount": 1150.44,
            "tax_rate": 0.13,
            "seller_tax_id": "91110108551385082Q",
            "seller_name": "北京神州数码技术有限公司"
        }],
        extra_context={"supplier_profiles": {}} # 空画像库
    )
    result = await MasterOrchestrator.run(task_id="task-missing-profile", context=ctx)
    supplier_res = next((r for r in result.agent_execution_results if r.role == AgentRoleEnum.SUPPLIER), None)
    assert supplier_res is not None
    assert supplier_res.status == AgentExecutionStatus.DEGRADED
    assert "SUPPLIER_PROFILE_MISSING" in supplier_res.reason

@pytest.mark.asyncio
async def test_business_scope_missing_marks_degraded_without_calling_llm(monkeypatch):
    """测试缺少 business_scope 时跳过大模型调用，不使用虚构范围，并标记 DEGRADED"""
    mock_llm = AsyncMock()
    monkeypatch.setattr(LLMClient, "audit_supplier_business_scope", mock_llm)

    findings = await SupplierAgent.run(
        supplier_name="测试企业",
        uscc="91110108551385082Q",
        purchase_desc="采购ThinkPad笔记本电脑",
        business_scope=None,
        profile_found=True
    )
    mock_llm.assert_not_called()
    assert findings.is_degraded is True
    assert findings.degraded_reason == "SUPPLIER_BUSINESS_SCOPE_MISSING"
    assert not any(f.rule_code == "R17_SUPPLIER_SCOPE_DEVIATION" for f in findings)

@pytest.mark.asyncio
async def test_multi_supplier_aggregation_marks_degraded(monkeypatch):
    """测试多供应商聚合时，单个供应商降级将整体 SupplierAgent 设为 DEGRADED，且成功 findings 仍保留"""
    async def mock_supplier_scope(*args, **kwargs):
        return {"is_match": True, "analysis": "匹配", "source": "LLM_INFERENCE"}
    monkeypatch.setattr(LLMClient, "audit_supplier_business_scope", mock_supplier_scope)

    ctx = DocumentContext(
        document_id=303,
        document_no="CORP-2026-004",
        document_type="CORP_PAYMENT",
        total_amount=Decimal("20000.00"),
        title="采购办公家具与耗材",
        applicant_id=1,
        tenant_id=1,
        line_items=[{"amount": 10000.00}, {"amount": 10000.00}],
        invoices=[
            {
                "invoice_code": "01",
                "invoice_number": "77770001",
                "total_amount": 10000.00,
                "untaxed_amount": 8849.56,
                "tax_amount": 1150.44,
                "tax_rate": 0.13,
                "seller_tax_id": "91110108551385082Q",
                "seller_name": "北京神州数码技术有限公司"
            },
            {
                "invoice_code": "02",
                "invoice_number": "77770002",
                "total_amount": 10000.00,
                "untaxed_amount": 8849.56,
                "tax_amount": 1150.44,
                "tax_rate": 0.13,
                "seller_tax_id": "914403001922038216",
                "seller_name": "华为技术有限公司"
            }
        ],
        extra_context={
            "supplier_profiles": {
                "91110108551385082Q": {
                    "uscc": "91110108551385082Q",
                    "supplier_name": "北京神州数码技术有限公司",
                    "is_dishonest": True, # 会产生 R13
                    "operating_status": "存续",
                    "business_scope": "计算机软硬件销售"
                },
                "914403001922038216": {
                    "uscc": "914403001922038216",
                    "supplier_name": "华为技术有限公司",
                    "is_dishonest": False,
                    "operating_status": "存续",
                    "business_scope": None  # 缺失经营范围，触发降级
                }
            }
        }
    )
    result = await MasterOrchestrator.run(task_id="task-multi-degraded", context=ctx)
    supplier_res = next((r for r in result.agent_execution_results if r.role == AgentRoleEnum.SUPPLIER), None)
    assert supplier_res is not None
    # 验证整体为 DEGRADED
    assert supplier_res.status == AgentExecutionStatus.DEGRADED
    assert "SUPPLIER_BUSINESS_SCOPE_MISSING" in supplier_res.reason
    # 成功检出的 R13 仍全部保留
    rule_codes = {f.rule_code for f in supplier_res.findings}
    assert "R13_SUPPLIER_DISHONEST" in rule_codes

def test_planner_deduplicates_by_normalized_uscc():
    """测试 Planner 供应商去重按规范化 USCC，不按 (USCC, name) 重复执行"""
    invoices = [
        {
            "seller_tax_id": " 91110108551385082Q ",
            "seller_name": "北京神州数码技术有限公司",
            "total_amount": 5000.00
        },
        {
            "seller_tax_id": "91110108551385082q",
            "seller_name": "北京神州数码总公司（分部）",
            "total_amount": 5000.00
        }
    ]
    plan = AuditPlanner.build_plan(
        document_id=304,
        document_type="CORP_PAYMENT",
        facts={"invoices": invoices, "title": "设备采购"}
    )
    task = plan.get_task(AgentRoleEnum.SUPPLIER)
    assert task is not None
    suppliers = task.meta["suppliers"]
    assert len(suppliers) == 1
    assert suppliers[0]["uscc"] == "91110108551385082Q"
    assert "uscc_checksum_validation" in task.capabilities

@pytest.mark.asyncio
async def test_capabilities_filter_rules_individually(monkeypatch):
    """测试 capabilities 可独立控制 R12/R13/R15/R17 规则执行"""
    async def mock_supplier_scope(*args, **kwargs):
        return {"is_match": False, "analysis": "主营范围偏离严重", "source": "LLM_INFERENCE"}
    monkeypatch.setattr(LLMClient, "audit_supplier_business_scope", mock_supplier_scope)

    # 1. 仅启用 uscc_checksum_validation
    res_r12 = await SupplierAgent.run(
        supplier_name="测试企业",
        uscc="123456789012345679", # 假税号
        is_dishonest=True,
        operating_status="注销",
        purchase_desc="软件研发",
        business_scope="农副产品",
        capabilities=["uscc_checksum_validation"]
    )
    r12_codes = {f.rule_code for f in res_r12}
    assert r12_codes == {"R12_SUPPLIER_UNREGISTERED"}

    # 2. 仅启用 dishonest_debtor_check
    res_r13 = await SupplierAgent.run(
        supplier_name="测试企业",
        uscc="123456789012345679",
        is_dishonest=True,
        operating_status="注销",
        purchase_desc="软件研发",
        business_scope="农副产品",
        capabilities=["dishonest_debtor_check"]
    )
    r13_codes = {f.rule_code for f in res_r13}
    assert r13_codes == {"R13_SUPPLIER_DISHONEST"}

    # 3. 仅启用 shell_company_investigation
    res_r15 = await SupplierAgent.run(
        supplier_name="测试企业",
        uscc="123456789012345679",
        is_dishonest=True,
        operating_status="注销",
        purchase_desc="软件研发",
        business_scope="农副产品",
        capabilities=["shell_company_investigation"]
    )
    r15_codes = {f.rule_code for f in res_r15}
    assert r15_codes == {"R15_SHELL_COMPANY"}

    # 4. 仅启用 business_scope_matching
    res_r17 = await SupplierAgent.run(
        supplier_name="测试企业",
        uscc="123456789012345679",
        is_dishonest=True,
        operating_status="注销",
        purchase_desc="软件研发",
        business_scope="农副产品",
        capabilities=["business_scope_matching"]
    )
    r17_codes = {f.rule_code for f in res_r17}
    assert r17_codes == {"R17_SUPPLIER_SCOPE_DEVIATION"}
