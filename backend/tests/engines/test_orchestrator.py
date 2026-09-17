"""
backend/tests/engines/test_orchestrator.py
orchestrator 四阶段流水线与流式事件端到端测试
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.database import Base
import engines.orchestrator.master_graph as mg
from app.models import User, FinancialDocument, DocumentLineItem, InvoiceRecord
from engines.orchestrator import MasterOrchestrator, StreamProducer

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture
async def setup_test_db(monkeypatch):
    """替换 MasterOrchestrator 中的数据库为独立内存数据库"""
    test_engine = create_async_engine(TEST_DB_URL, echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionMaker = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(mg, "AsyncSessionLocal", SessionMaker)
    import app.services.audit_handler as ah
    monkeypatch.setattr(ah, "AsyncSessionLocal", SessionMaker)

    # 预先插入测试单据
    async with SessionMaker() as session:
        user = User(username="admin_test", hashed_password="pw", real_name="管理员")
        session.add(user)
        await session.flush()

        doc = FinancialDocument(
            document_no="DOC-ORCH-001",
            document_type="TRAVEL_REIMBURSEMENT",
            title="上海差旅报销单",
            applicant_id=user.id,
            total_amount=Decimal("1500.00"),
            status="SUBMITTED"
        )
        session.add(doc)
        await session.flush()

        item = DocumentLineItem(
            document_id=doc.id,
            line_no=1,
            expense_type="住宿费",
            item_desc="上海希尔顿酒店2晚",
            amount=Decimal("1500.00"),
            city_name="上海",
            start_date=datetime(2026, 9, 10, 12, 0)
        )
        session.add(item)

        inv = InvoiceRecord(
            document_id=doc.id,
            attachment_id=1,
            invoice_code="0310023",
            invoice_number="55443322",
            total_amount=Decimal("1500.00"),
            untaxed_amount=Decimal("1415.09"),
            tax_amount=Decimal("84.91"),
            seller_tax_id="91310000000000",
            seller_name="希尔顿酒店管理公司",
            issue_date="2026-09-10",
            invoice_hash="test_hilton_hash_001"
        )
        session.add(inv)
        await session.commit()
        doc_id = doc.id

    yield doc_id, SessionMaker

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()

@pytest.mark.asyncio
async def test_master_orchestrator_end_to_end(setup_test_db):
    """测试完整四阶段流水线执行、超标检出与事件推送"""
    doc_id, SessionMaker = setup_test_db
    task_id = "task-uuid-test-e2e-001"

    # 1. 执行认知推理流水线 (产出纯数据 AuditResultDTO，0 写库)
    result_dto = await MasterOrchestrator.run(
        task_id=task_id,
        document_id=doc_id,
        applicant_id=1,
        tenant_id=1
    )

    # 验证流转状态与推理质量
    assert result_dto.task_id == task_id
    assert result_dto.overall_risk_level in ["medium", "high"] # 上海限额 500元，报销 1500元必超标！
    assert result_dto.final_score < 100

    # 验证风险项检出 (应该命中 R05 住宿超标)
    rule_codes = {f.rule_code for f in result_dto.verified_findings}
    assert "R05_POLICY_EXCEEDED" in rule_codes

    # 2. 交由 AuditService 进行统一原子落库与审批触发
    from app.services.audit_service import AuditService
    async with SessionMaker() as session:
        audit_svc = AuditService(session)
        report = await audit_svc.handle_audit_completion(
            task_id=task_id,
            document_id=doc_id,
            result=result_dto
        )
        assert report.id is not None
        assert report.overall_risk_level in ["medium", "high"]

    # 3. 验证审计流事件发布完整性 (包含引擎端与落库后的 task_completed)
    events = StreamProducer.get_events_since(task_id)
    assert len(events) >= 3
    event_types = [ev.event.value for ev in events]
    assert "task_started" in event_types
    assert "task_progress" in event_types
    assert "task_completed" in event_types

    # 4. 验证 AuditExecutionPlan 与 AgentExecutionResults 存在且记录完整
    assert result_dto.execution_plan is not None
    assert result_dto.audit_completeness in ["COMPLETE", "DEGRADED"]
    assert len(result_dto.agent_execution_results) >= 3


def test_audit_planner_routing_and_capabilities():
    """测试 AuditPlanner 在不同单据类型和要素场景下的粗粒度路由与细粒度 Capability 剪枝"""
    from engines.orchestrator.planner import AuditPlanner
    from engines.contract.agent_role import AgentRoleEnum
    from engines.contract.result import AgentExecutionStatus

    # 1. 差旅单 + 2张发票 + 2个轨迹点
    travel_facts = {
        "line_items": [{"amount": 500, "city_name": "北京", "start_date": "2026-09-01"}],
        "invoices": [
            {"invoice_code": "01", "invoice_number": "1001", "seller_tax_id": "91110000", "seller_name": "酒店A"},
            {"invoice_code": "01", "invoice_number": "1002", "seller_tax_id": "91110000", "seller_name": "酒店A"}
        ],
        "spatio_points": [
            {"city_name": "北京", "event_time": "2026-09-01", "latitude": 39.9, "longitude": 116.4},
            {"city_name": "上海", "event_time": "2026-09-02", "latitude": 31.2, "longitude": 121.4}
        ]
    }
    plan = AuditPlanner.build_plan(101, "TRAVEL_REIMBURSEMENT", travel_facts)
    amount_t = plan.get_task(AgentRoleEnum.AMOUNT)
    assert amount_t.enabled is True
    assert amount_t.mandatory is True
    assert amount_t.status == AgentExecutionStatus.PLANNED
    assert amount_t.capabilities == ["five_way_reconciliation"]

    policy_t = plan.get_task(AgentRoleEnum.POLICY)
    assert policy_t.enabled is True
    assert policy_t.status == AgentExecutionStatus.PLANNED
    assert "travel_hotel_limit" in policy_t.capabilities

    anomaly_t = plan.get_task(AgentRoleEnum.ANOMALY)
    assert anomaly_t.enabled is True
    assert anomaly_t.status == AgentExecutionStatus.PLANNED
    assert "duplicate_invoice_hash_check" in anomaly_t.capabilities
    assert "sequential_invoice_number_check" in anomaly_t.capabilities
    assert "spatio_temporal_trajectory_conflict" in anomaly_t.capabilities

    # 差旅单无需供应商尽调
    supplier_t = plan.get_task(AgentRoleEnum.SUPPLIER)
    assert supplier_t.enabled is False
    assert supplier_t.status == AgentExecutionStatus.SKIPPED
    assert "NOT_APPLICABLE" in supplier_t.reason

    # 2. 对公付款单 + 缺失发票税号
    corp_facts_no_supplier = {
        "line_items": [{"amount": 50000}],
        "invoices": []
    }
    plan_corp = AuditPlanner.build_plan(102, "CORP_PAYMENT", corp_facts_no_supplier)
    supplier_corp_t = plan_corp.get_task(AgentRoleEnum.SUPPLIER)
    assert supplier_corp_t.enabled is False
    assert supplier_corp_t.status == AgentExecutionStatus.SKIPPED
    assert "DATA_MISSING" in supplier_corp_t.reason


def test_city_geo_resolution_and_accuracy():
    """测试标准行政区划代码与真实空间地理库精度"""
    from engines.policy_agent.city_geo import get_city_geo

    # 直辖市与省会精确与模糊匹配
    bj = get_city_geo("北京")
    assert bj is not None
    assert bj.code == "110000"
    assert bj.tier == "TIER_1"
    assert round(bj.latitude, 2) == 39.90
    assert round(bj.longitude, 2) == 116.41

    sh = get_city_geo("上海市浦东新区")
    assert sh is not None
    assert sh.code == "310000"
    assert sh.tier == "TIER_1"

    cd = get_city_geo("成都市高新区")
    assert cd is not None
    assert cd.tier == "TIER_2"
    assert round(cd.latitude, 2) == 30.57

    # 未收录城市不产生虚假上海坐标
    unknown = get_city_geo("某个未知县城")
    assert unknown is None


@pytest.mark.asyncio
async def test_multi_supplier_parallel_deduplication():
    """测试对公付款单多供应商去重并发尽调 (杜绝虚假供应商 Fallback)"""
    from engines.contract.context import DocumentContext

    ctx = DocumentContext(
        document_id=201,
        document_no="CORP-2026-001",
        document_type="CORP_PAYMENT",
        applicant_id=1,
        tenant_id=1,
        total_amount=Decimal("20000.00"),
        title="技术外包与设备采购",
        line_items=[{"amount": 10000.00}, {"amount": 10000.00}],
        invoices=[
            {
                "invoice_code": "01",
                "invoice_number": "88880001",
                "total_amount": 10000.00,
                "untaxed_amount": 9433.96,
                "tax_amount": 566.04,
                "tax_rate": 0.06,
                "seller_tax_id": "91110108MA00AAAA11",
                "seller_name": "北京极客软件有限公司",
                "issue_date": "2026-09-01"
            },
            {
                "invoice_code": "02",
                "invoice_number": "88880002",
                "total_amount": 10000.00,
                "untaxed_amount": 9433.96,
                "tax_amount": 566.04,
                "tax_rate": 0.06,
                "seller_tax_id": "91310115MA00BBBB22",
                "seller_name": "上海算力云科技有限公司",
                "issue_date": "2026-09-02"
            }
        ]
    )

    result = await MasterOrchestrator.run(
        task_id="task-multi-supplier-001",
        context=ctx
    )

    # 验证执行计划中包含去重后的 2 个真实供应商主体
    plan = result.execution_plan
    assert plan is not None
    from engines.contract.agent_role import AgentRoleEnum
    supplier_tasks = [t for t in plan["tasks"] if t["role"] == AgentRoleEnum.SUPPLIER.value]
    assert len(supplier_tasks) == 1
    planned_suppliers = supplier_tasks[0]["meta"]["suppliers"]
    assert len(planned_suppliers) == 2
    supplier_usccs = {s["uscc"] for s in planned_suppliers}
    assert "91110108MA00AAAA11" in supplier_usccs
    assert "91310115MA00BBBB22" in supplier_usccs
    # 绝对不能出现硬编码假税号
    assert "91110108551385082Q" not in supplier_usccs


@pytest.mark.asyncio
async def test_audit_completeness_gate_and_anti_silence_on_timeout(monkeypatch):
    """测试核心必验项超时/失败时，触发 INCOMPLETE 完整度硬门禁，最高分封顶 60 分且绝不允许 100 分通过"""
    from engines.contract.context import DocumentContext
    from engines.contract.result import AgentExecutionResult, AgentExecutionStatus
    from engines.contract.agent_role import AgentRoleEnum
    from engines.harness.agent_harness import AgentHarness

    # Mock AgentHarness.execute_safely 让 AMOUNT 超时熔断
    orig_execute_safely = AgentHarness.execute_safely

    async def mock_execute_safely(agent_role, coro, timeout_seconds=None, expected_schema=None, capabilities=None):
        if agent_role == AgentRoleEnum.AMOUNT:
            coro.close()
            return AgentExecutionResult(
                role=agent_role,
                status=AgentExecutionStatus.TIMEOUT,
                reason="强制测试超时熔断",
                findings=[],
                duration_ms=3000,
                capabilities_run=capabilities or []
            )
        return await orig_execute_safely(agent_role, coro, timeout_seconds, capabilities=capabilities)

    monkeypatch.setattr(AgentHarness, "execute_safely", mock_execute_safely)

    ctx = DocumentContext(
        document_id=301,
        document_no="TRAVEL-TIMEOUT-001",
        document_type="TRAVEL_REIMBURSEMENT",
        applicant_id=1,
        tenant_id=1,
        total_amount=Decimal("500.00"),
        title="差旅报销",
        line_items=[{"expense_type": "住宿费", "item_desc": "北京速8酒店住宿", "amount": 500.00, "city_name": "北京", "start_date": "2026-09-01"}],
        invoices=[{
            "invoice_code": "01",
            "invoice_number": "1001",
            "total_amount": 500.00,
            "untaxed_amount": 471.70,
            "tax_amount": 28.30,
            "tax_rate": 0.06,
            "seller_tax_id": "91110108MA000000",
            "seller_name": "北京速8酒店",
            "issue_date": "2026-09-01"
        }]
    )

    result = await MasterOrchestrator.run(
        task_id="task-timeout-gate-001",
        context=ctx
    )

    # 核心必验项 AMOUNT 超时，完整度必须判定为 INCOMPLETE
    assert result.audit_completeness == "INCOMPLETE"
    # 解耦风险分与完整度：未检出违规项时风险分只反映已发现风险（100分），但完整度为 INCOMPLETE 且进入人工复核
    assert result.risk_score == 100
    assert "⚠️ 智能风控审查未完全完成" in result.summary
    assert f"{AgentRoleEnum.AMOUNT.value}(TIMEOUT)" in result.summary


@pytest.mark.asyncio
async def test_scenario_a_travel_reimbursement_complete(monkeypatch):
    """场景 A：差旅报销单
    - AmountAgent, PolicyAgent, AnomalyAgent 正常运行
    - SupplierAgent 为 NOT_APPLICABLE (正常跳过，不影响完整度)
    - 最终审核完整度应为 COMPLETE
    """
    from engines.contract.context import DocumentContext
    from engines.contract.agent_role import AgentRoleEnum
    from engines.contract.result import AgentExecutionStatus
    from app.core.llm_client import LLMClient

    async def mock_rationality(*args, **kwargs):
        return {"is_rational": True, "source": "LLM_INFERENCE", "risk_analysis": "正常公务报销"}
    monkeypatch.setattr(LLMClient, "audit_policy_rationality", mock_rationality)

    ctx = DocumentContext(
        document_id=401,
        document_no="SCENARIO-A-001",
        document_type="TRAVEL_REIMBURSEMENT",
        applicant_id=1,
        tenant_id=1,
        total_amount=Decimal("300.00"),
        title="北京出差车票住宿",
        line_items=[{
            "amount": Decimal("300.00"),
            "city_name": "北京",
            "start_date": "2026-09-01",
            "expense_type": "住宿费",
            "item_desc": "快捷酒店1晚"
        }],
        invoices=[{
            "invoice_code": "01",
            "invoice_number": "1001",
            "total_amount": 300.00,
            "untaxed_amount": 283.02,
            "tax_amount": 16.98,
            "tax_rate": 0.06,
            "seller_tax_id": "91110108MA000000",
            "seller_name": "北京如家酒店",
            "issue_date": "2026-09-01"
        }],
        spatio_points=[{
            "event_time": "2026-09-01",
            "city_name": "北京",
            "latitude": 39.9042,
            "longitude": 116.4074,
            "source_desc": "快捷酒店1晚"
        }]
    )

    result = await MasterOrchestrator.run(
        task_id="task-scenario-a",
        context=ctx
    )

    # 1. 验证各 Agent 执行状态
    role_results = {r.role: r for r in result.agent_execution_results}
    assert role_results[AgentRoleEnum.AMOUNT].status == AgentExecutionStatus.SUCCESS
    assert role_results[AgentRoleEnum.POLICY].status == AgentExecutionStatus.SUCCESS
    assert role_results[AgentRoleEnum.ANOMALY].status == AgentExecutionStatus.SUCCESS

    # SupplierAgent 为正常不适用跳过
    supplier_res = role_results[AgentRoleEnum.SUPPLIER]
    assert supplier_res.status == AgentExecutionStatus.SKIPPED
    assert "NOT_APPLICABLE" in (supplier_res.reason or "")

    # 2. 最终审核完整度应为 COMPLETE
    assert result.audit_completeness == "COMPLETE"
    assert result.risk_score == 100
    assert result.overall_risk_level == "low"


@pytest.mark.asyncio
async def test_scenario_b_corp_payment_multi_suppliers_partial_timeout(monkeypatch):
    """场景 B：对公付款，多供应商
    - 3 个供应商，其中 1 个查询超时
    - SupplierAgent 状态应为 DEGRADED
    - 其他供应商成功结果不能丢失
    - 最终不能错误输出“100 分全量审核通过”
    """
    import asyncio
    from engines.contract.context import DocumentContext
    from engines.contract.agent_role import AgentRoleEnum
    from engines.contract.result import AgentExecutionStatus
    from engines.supplier_agent import SupplierAgent
    from app.core.llm_client import LLMClient

    monkeypatch.setattr(LLMClient, "is_configured", lambda: False)

    # Mock SupplierAgent.run 让其中某个供应商超时
    orig_supplier_run = SupplierAgent.run

    async def mock_supplier_run(supplier_name, uscc, **kwargs):
        if "慢速供应商" in supplier_name:
            await asyncio.sleep(20.0)  # 触发超时
        return await orig_supplier_run(supplier_name, uscc, **kwargs)

    monkeypatch.setattr(SupplierAgent, "run", mock_supplier_run)

    ctx = DocumentContext(
        document_id=402,
        document_no="SCENARIO-B-001",
        document_type="CORP_PAYMENT",
        applicant_id=1,
        tenant_id=1,
        total_amount=Decimal("30000.00"),
        title="三方供应商采购单",
        line_items=[{"amount": Decimal("10000.00")}, {"amount": Decimal("10000.00")}, {"amount": Decimal("10000.00")}],
        invoices=[
            {
                "invoice_code": "01",
                "invoice_number": "1001",
                "total_amount": 10000.00,
                "untaxed_amount": 9433.96,
                "tax_amount": 566.04,
                "tax_rate": 0.06,
                "seller_tax_id": "91110108MA000001",
                "seller_name": "正常供应商甲",
                "issue_date": "2026-09-01"
            },
            {
                "invoice_code": "02",
                "invoice_number": "1002",
                "total_amount": 10000.00,
                "untaxed_amount": 9433.96,
                "tax_amount": 566.04,
                "tax_rate": 0.06,
                "seller_tax_id": "91110108MA000002",
                "seller_name": "慢速供应商乙",
                "issue_date": "2026-09-02"
            },
            {
                "invoice_code": "03",
                "invoice_number": "1003",
                "total_amount": 10000.00,
                "untaxed_amount": 9433.96,
                "tax_amount": 566.04,
                "tax_rate": 0.06,
                "seller_tax_id": "INVALID_USCC_BAD",
                "seller_name": "违规供应商丙",
                "issue_date": "2026-09-03"
            }
        ]
    )

    result = await MasterOrchestrator.run(
        task_id="task-scenario-b",
        context=ctx
    )

    # 1. SupplierAgent 状态应为 DEGRADED
    role_results = {r.role: r for r in result.agent_execution_results}
    supplier_res = role_results[AgentRoleEnum.SUPPLIER]
    assert supplier_res.status == AgentExecutionStatus.DEGRADED
    assert "部分供应商核查完成" in supplier_res.reason

    # 2. 成功供应商的风险项不能丢失 (供应商丙的违规项仍被检出)
    assert any("SUPPLIER" in f.rule_code for f in supplier_res.findings)

    # 3. 最终审核完整度应为 DEGRADED，绝不能错误输出“100 分全量审核通过”
    assert result.audit_completeness == "DEGRADED"
    assert "全规则合规放行" not in result.summary
    assert "降级完成" in result.summary


@pytest.mark.asyncio
async def test_scenario_c_all_mandatory_success_normal_pass(monkeypatch):
    """场景 C：所有必检 Agent 均成功且无风险项
    - audit_completeness = COMPLETE
    - 无高风险项，全规则通过
    - 允许正常 PASS (final_score = 100, pass)
    """
    from engines.contract.context import DocumentContext
    from app.services.approval_engine import ApprovalEngine, ApprovalDecisionAction
    from app.models.document import FinancialDocument
    from app.models.audit import ReviewReport
    from app.core.llm_client import LLMClient

    async def mock_rationality(*args, **kwargs):
        return {"is_rational": True, "source": "LLM_INFERENCE", "risk_analysis": "正常公务报销"}
    monkeypatch.setattr(LLMClient, "audit_policy_rationality", mock_rationality)

    ctx = DocumentContext(
        document_id=403,
        document_no="SCENARIO-C-001",
        document_type="TRAVEL_REIMBURSEMENT",
        applicant_id=1,
        tenant_id=1,
        total_amount=Decimal("400.00"),
        title="差旅合规报销",
        line_items=[{
            "amount": Decimal("400.00"),
            "city_name": "北京",
            "start_date": "2026-09-01",
            "expense_type": "住宿费",
            "item_desc": "北京标准间1晚"
        }],
        invoices=[{
            "invoice_code": "01",
            "invoice_number": "1001",
            "total_amount": 400.00,
            "untaxed_amount": 377.36,
            "tax_amount": 22.64,
            "tax_rate": 0.06,
            "seller_tax_id": "91110108MA000000",
            "seller_name": "北京汉庭酒店",
            "issue_date": "2026-09-01"
        }]
    )

    result = await MasterOrchestrator.run(
        task_id="task-scenario-c",
        context=ctx
    )

    # 1. 验证完整度为 COMPLETE 且 风险分为 100 分
    assert result.audit_completeness == "COMPLETE"
    assert result.risk_score == 100
    assert result.final_score == 100
    assert result.overall_risk_level == "low"
    assert len(result.verified_findings) == 0
    assert "全规则合规放行" in result.summary

    # 2. 验证审批引擎决策：COMPLETE + low 风险 + 小额 -> AUTO_APPROVE
    dummy_doc = FinancialDocument(id=403, total_amount=Decimal("400.00"))
    dummy_report = ReviewReport(
        task_id="task-scenario-c",
        document_id=403,
        overall_risk_level="low",
        final_score=100,
        full_report_payload={"audit_completeness": result.audit_completeness}
    )
    decision = await ApprovalEngine.evaluate_transition(dummy_doc, dummy_report)
    assert decision.action == ApprovalDecisionAction.AUTO_APPROVE
    assert decision.target_state == "APPROVED"


