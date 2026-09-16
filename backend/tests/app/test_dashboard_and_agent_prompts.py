"""
backend/tests/app/test_dashboard_and_agent_prompts.py
测试风控态势大盘聚合 API 与 Agent 大模型提示词审计逻辑
"""
import pytest
from httpx import AsyncClient, ASGITransport
from main import app
from app.core.database import init_db
from app.services.auth_service import AuthService
from engines.policy_agent import PolicyAgent
from engines.supplier_agent import SupplierAgent
from app.core.llm_client import LLMClient

@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()

@pytest.mark.asyncio
async def test_dashboard_metrics_api():
    """测试风控态势大盘 GET /dashboard/metrics 指标聚合接口"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = AuthService.create_access_token(user_id=1, username="cfo", roles=["CFO"])
        headers = {"Authorization": f"Bearer {token}"}

        resp = await ac.get("/api/v1/dashboard/metrics", headers=headers)
        assert resp.status_code == 200
        data = resp.json()

        # 1. 检验核心 KPI 概览指标
        assert "overview" in data
        ov = data["overview"]
        assert "total_documents" in ov
        assert "total_audited_amount" in ov
        assert "blocked_high_risk_amount" in ov
        assert "auto_pass_rate" in ov
        assert "avg_score" in ov

        # 2. 检验风险分布与违规规则 TOP 5
        assert "risk_distribution" in data
        assert "high" in data["risk_distribution"]
        assert "top_rules" in data
        assert len(data["top_rules"]) >= 3

        # 3. 检验部门合规率分布与最新审查流水
        assert "department_stats" in data
        assert "recent_activities" in data

@pytest.mark.asyncio
async def test_policy_agent_rationality_prompt_audit():
    """测试 PolicyAgent 大模型事由合理性与公款私用推演"""
    # 构造包含明显个人娱乐消费的单据明细
    line_items = [
        {"line_no": 1, "expense_type": "餐饮费", "item_desc": "商务就餐", "amount": 200.0},
        {"line_no": 2, "expense_type": "娱乐消费", "item_desc": "周末高尔夫休闲体验券", "amount": 1800.0}
    ]

    findings = await PolicyAgent.run(
        document_type="EXPENSE_REIMBURSEMENT",
        line_items=line_items,
        document_title="日常商务开拓与客户拜访",
        department_name="市场营销部"
    )

    # 应当检出 R16_BUSINESS_PURPOSE_MISMATCH
    rule_codes = [f.rule_code for f in findings]
    assert "R16_BUSINESS_PURPOSE_MISMATCH" in rule_codes
    rat_finding = next(f for f in findings if f.rule_code == "R16_BUSINESS_PURPOSE_MISMATCH")
    assert rat_finding.risk_level.value == "high"
    assert "高尔夫" in rat_finding.description or "敏感" in rat_finding.description

@pytest.mark.asyncio
async def test_supplier_agent_scope_and_reviewer_summary():
    """测试 SupplierAgent 工商范围与 Reviewer 高管体检摘要"""
    # 1. SupplierAgent 经营范围偏离
    findings = await SupplierAgent.run(
        supplier_name="测试农副产品专营合作社",
        uscc="91110108551385082Q", # 故意校验通过税号
        purchase_desc="高性能GPU算力集群租赁与模型推理服务",
        business_scope="新鲜蔬菜、水果、家禽养殖销售"
    )
    assert isinstance(findings, list)

    # 2. ReviewerAgent 审计体检摘要生成
    summary = await LLMClient.generate_executive_summary(
        document_no="TRV-2026-TEST",
        total_amount=15000.0,
        findings_summary=[
            {"rule_code": "R05", "title": "超标", "description": "住宿费超标500元"}
        ],
        fallback_summary="默认兜底审计摘要"
    )
    assert len(summary) > 0
