"""
backend/tests/engines/test_decision_chain_consistency.py
MasterOrchestrator + ReviewerReflector + Stage4 决策链轻量一致性修复专项测试
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from engines.contract.context import DocumentContext
from engines.contract.result import AgentExecutionResult, AgentExecutionStatus, AuditResultDTO
from engines.contract.agent_role import AgentRoleEnum
from engines.contract.finding import RiskFindingContract, RiskLevelEnum
from engines.orchestrator.planner import AuditPlanner, PlannedAgentTask
from engines.orchestrator.state import MasterAuditState
from engines.orchestrator.master_graph import MasterOrchestrator
from engines.orchestrator.reviewer_reflector import ReviewerReflector
from app.core.llm_client import LLMClient


# =====================================================================
# 1. 完整度门禁：计划 vs 实际执行结果校验测试
# =====================================================================

@pytest.mark.asyncio
async def test_completeness_gate_mandatory_missing_result():
    """测试 mandatory 必检项在 agent_results 中缺失时裁定为 INCOMPLETE"""
    state = MasterAuditState(
        task_id="test_missing_res",
        document_id=101,
        document_type="TRAVEL_REIMBURSEMENT"
    )
    # 计划中有 AMOUNT 与 POLICY 两个必检项
    state.execution_plan = AuditPlanner.build_plan(
        document_id=101,
        document_type="TRAVEL_REIMBURSEMENT",
        facts={"total_amount": 500, "line_items": [{"amount": 500}]}
    )
    # 实际结果中缺少 POLICY
    state.agent_results = [
        AgentExecutionResult(
            role=AgentRoleEnum.AMOUNT,
            status=AgentExecutionStatus.SUCCESS,
            findings=[],
            duration_ms=10
        )
    ]

    res = await MasterOrchestrator._stage4_report(state)
    assert res.audit_completeness == "INCOMPLETE"
    assert "policy_agent(NO_EXECUTION_RESULT)" in res.summary


@pytest.mark.asyncio
async def test_completeness_gate_mandatory_skipped_due_to_data_missing():
    """测试 mandatory 必检项因数据缺失被 SKIPPED 时裁定为 INCOMPLETE"""
    state = MasterAuditState(
        task_id="test_mandatory_skipped",
        document_id=102,
        document_type="CORP_PAYMENT"
    )
    state.execution_plan = AuditPlanner.build_plan(
        document_id=102,
        document_type="CORP_PAYMENT",
        facts={"total_amount": 5000, "invoices": []} # 对公付款但无发票税号
    )
    # SupplierAgent 是 mandatory 但因 DATA_MISSING 被 SKIPPED
    state.agent_results = [
        AgentExecutionResult(
            role=AgentRoleEnum.AMOUNT,
            status=AgentExecutionStatus.SUCCESS,
            findings=[],
            duration_ms=10
        ),
        AgentExecutionResult(
            role=AgentRoleEnum.POLICY,
            status=AgentExecutionStatus.SUCCESS,
            findings=[],
            duration_ms=10
        ),
        AgentExecutionResult(
            role=AgentRoleEnum.SUPPLIER,
            status=AgentExecutionStatus.SKIPPED,
            reason="DATA_MISSING: SUPPLIER_IDENTITY_MISSING",
            findings=[],
            duration_ms=0
        )
    ]

    res = await MasterOrchestrator._stage4_report(state)
    assert res.audit_completeness == "INCOMPLETE"
    assert "supplier_agent(DATA_MISSING: SUPPLIER_IDENTITY_MISSING)" in res.summary


@pytest.mark.asyncio
async def test_completeness_gate_non_mandatory_degraded_and_not_applicable():
    """测试非 mandatory 项：NOT_APPLICABLE 保持 COMPLETE；DATA_MISSING 变为 DEGRADED"""
    # 1. 正常不适用跳过保持 COMPLETE
    state_complete = MasterAuditState(
        task_id="test_not_applicable",
        document_id=103,
        document_type="TRAVEL_REIMBURSEMENT"
    )
    state_complete.execution_plan = AuditPlanner.build_plan(
        document_id=103,
        document_type="TRAVEL_REIMBURSEMENT",
        facts={"total_amount": 500, "line_items": [{"amount": 500}]}
    )
    state_complete.agent_results = [
        AgentExecutionResult(role=AgentRoleEnum.AMOUNT, status=AgentExecutionStatus.SUCCESS),
        AgentExecutionResult(role=AgentRoleEnum.POLICY, status=AgentExecutionStatus.SUCCESS),
        AgentExecutionResult(role=AgentRoleEnum.ANOMALY, status=AgentExecutionStatus.SKIPPED, reason="NOT_APPLICABLE: NO_INVOICE"),
        AgentExecutionResult(role=AgentRoleEnum.SUPPLIER, status=AgentExecutionStatus.SKIPPED, reason="NOT_APPLICABLE: DOCUMENT_TYPE_EXEMPT")
    ]
    res_c = await MasterOrchestrator._stage4_report(state_complete)
    assert res_c.audit_completeness == "COMPLETE"

    # 2. 非 mandatory 项出现数据缺失降级
    state_degraded = MasterAuditState(
        task_id="test_non_mandatory_missing",
        document_id=104,
        document_type="TRAVEL_REIMBURSEMENT"
    )
    state_degraded.execution_plan = AuditPlanner.build_plan(
        document_id=104,
        document_type="TRAVEL_REIMBURSEMENT",
        facts={"total_amount": 500, "line_items": [{"amount": 500}]}
    )
    state_degraded.agent_results = [
        AgentExecutionResult(role=AgentRoleEnum.AMOUNT, status=AgentExecutionStatus.SUCCESS),
        AgentExecutionResult(role=AgentRoleEnum.POLICY, status=AgentExecutionStatus.SUCCESS),
        AgentExecutionResult(role=AgentRoleEnum.ANOMALY, status=AgentExecutionStatus.DEGRADED, reason="历史发票库未连接")
    ]
    res_d = await MasterOrchestrator._stage4_report(state_degraded)
    assert res_d.audit_completeness == "DEGRADED"


# =====================================================================
# 2. 保护最终安全文案：INCOMPLETE / DEGRADED 禁止 LLM 覆盖
# =====================================================================

@pytest.mark.asyncio
async def test_safety_summary_protected_from_llm_overwrite(monkeypatch):
    """测试当 audit_completeness 为 INCOMPLETE 或 DEGRADED 时，禁止调用 LLM 润色覆盖安全摘要"""
    llm_called = False

    async def mock_generate_exec_summary(*args, **kwargs):
        nonlocal llm_called
        llm_called = True
        return "LLM 生成的虚假合规摘要，无视风险"

    monkeypatch.setattr(LLMClient, "generate_executive_summary", mock_generate_exec_summary)
    monkeypatch.setattr(LLMClient, "is_configured", lambda: True)

    state_incomplete = MasterAuditState(
        task_id="test_safety_llm_block",
        document_id=105,
        document_type="TRAVEL_REIMBURSEMENT"
    )
    state_incomplete.execution_plan = AuditPlanner.build_plan(
        document_id=105,
        document_type="TRAVEL_REIMBURSEMENT",
        facts={"total_amount": 500, "line_items": [{"amount": 500}]}
    )
    # 模拟 AMOUNT 必检项超时
    state_incomplete.agent_results = [
        AgentExecutionResult(role=AgentRoleEnum.AMOUNT, status=AgentExecutionStatus.TIMEOUT),
        AgentExecutionResult(role=AgentRoleEnum.POLICY, status=AgentExecutionStatus.SUCCESS)
    ]

    res = await MasterOrchestrator._stage4_report(state_incomplete)
    assert res.audit_completeness == "INCOMPLETE"
    assert llm_called is False  # 确保 LLM 绝未被调用
    assert "⚠️ 智能风控审查未完全完成" in res.summary
    assert "LLM 生成的虚假合规摘要" not in res.summary


# =====================================================================
# 3. ReviewerReflector Fail-Safe 与明确制度证据双门禁测试
# =====================================================================

def test_reviewer_reflector_failsafe_on_exception(monkeypatch):
    """测试 ReviewerReflector 发生未捕获异常时触发 Fail-safe，全量保留原始风险项"""
    findings = [
        RiskFindingContract(
            rule_code="R02_INVOICE_SUM_MISMATCH",
            rule_name="发票总额不符",
            risk_level=RiskLevelEnum.HIGH,
            agent_role=AgentRoleEnum.AMOUNT,
            title="价税不平",
            description="存在差额 100",
            discrepancy_amount=Decimal("100.00"),
            suggestion="请核对发票金额"
        )
    ]

    # Mock 模拟抛出异常
    def mock_has_evidence(*args, **kwargs):
        raise RuntimeError("模拟反思模块内部意外崩溃")

    monkeypatch.setattr(ReviewerReflector, "has_allowance_policy_evidence", mock_has_evidence)

    verified, logs = ReviewerReflector.reflect_and_disambiguate(
        findings=findings,
        document_facts={"line_items": [{"expense_type": "差旅津贴", "amount": 100}]}
    )

    # 异常时零丢失：原始 R02 完整返回
    assert len(verified) == 1
    assert verified[0].rule_code == "R02_INVOICE_SUM_MISMATCH"
    assert len(logs) == 0


def test_reviewer_reflector_no_points_deducted_on_full_resolve():
    """测试完全消歧后不再加入 LOW 风险项（不扣 3 分），只保留 reflection log"""
    findings = [
        RiskFindingContract(
            rule_code="R02_INVOICE_SUM_MISMATCH",
            rule_name="发票总额不符",
            risk_level=RiskLevelEnum.HIGH,
            agent_role=AgentRoleEnum.AMOUNT,
            title="价税不平",
            description="存在差额 200",
            discrepancy_amount=Decimal("200.00"),
            suggestion="请核对发票金额"
        )
    ]
    facts = {
        "allowance_policy_verified": True,
        "line_items": [{"expense_type": "差旅津贴", "amount": "200.00"}]
    }

    verified, logs = ReviewerReflector.reflect_and_disambiguate(findings, facts)
    assert len(verified) == 0  # 不应有 LOW 风险项
    assert len(logs) == 1
    assert logs[0]["action"] == "AUTO_RESOLVED_FULL"


# =====================================================================
# 4. MasterGraph Decimal 边界防护测试 (零崩溃)
# =====================================================================

@pytest.mark.asyncio
async def test_master_graph_core_amount_missing_safe_incomplete():
    """测试 total_amount 为 None 时不发生 Decimal('None') 崩溃，而是 Amount 标记 FAILED 并裁定 INCOMPLETE"""
    state = MasterAuditState(
        task_id="task_dec_none",
        document_id=106,
        document_type="TRAVEL_REIMBURSEMENT"
    )
    state.document_facts = {
        "document_no": "TEST-DEC-NONE-001",
        "total_amount": None, # 模拟异常丢失核心金额
        "document_type": "TRAVEL_REIMBURSEMENT",
        "line_items": [],
        "invoices": []
    }
    state.execution_plan = AuditPlanner.build_plan(106, "TRAVEL_REIMBURSEMENT", state.document_facts)

    # 阶段 2 执行：不应抛出异常
    await MasterOrchestrator._stage2_parallel(state)
    amount_res = next(r for r in state.agent_results if r.role == AgentRoleEnum.AMOUNT)
    assert amount_res.status == AgentExecutionStatus.FAILED
    assert "CORE_TOTAL_AMOUNT_MISSING" in amount_res.reason

    # 阶段 4 报告：直接裁定为 INCOMPLETE
    await MasterOrchestrator._stage3_reviewer(state)
    res = await MasterOrchestrator._stage4_report(state)
    assert res.audit_completeness == "INCOMPLETE"


@pytest.mark.asyncio
async def test_master_graph_non_core_amount_missing_safe_degraded():
    """测试明细行或发票金额异常时，优雅降级为 DEGRADED"""
    state = MasterAuditState(
        task_id="task_non_core_dec",
        document_id=107,
        document_type="TRAVEL_REIMBURSEMENT"
    )
    state.document_facts = {
        "document_no": "TEST-DEC-NONCORE-001",
        "total_amount": "500.00",
        "document_type": "TRAVEL_REIMBURSEMENT",
        "line_items": [
            {"amount": "500.00", "expense_type": "住宿费"},
            {"amount": None, "expense_type": "交通费"} # 部分明细金额缺失
        ],
        "invoices": [
            {"total_amount": "invalid_number"} # 非法发票金额
        ]
    }
    state.execution_plan = AuditPlanner.build_plan(107, "TRAVEL_REIMBURSEMENT", state.document_facts)

    # 阶段 2 并行执行
    await MasterOrchestrator._stage2_parallel(state)
    amount_res = next(r for r in state.agent_results if r.role == AgentRoleEnum.AMOUNT)
    assert amount_res.status == AgentExecutionStatus.DEGRADED
    assert "缺失或非法" in amount_res.reason

    # 阶段 4 报告裁定为 DEGRADED
    await MasterOrchestrator._stage3_reviewer(state)
    res = await MasterOrchestrator._stage4_report(state)
    assert res.audit_completeness == "DEGRADED"


# =====================================================================
# 5. Planner 任务状态与 execution_elapsed_ms 测试
# =====================================================================

def test_planner_status_and_real_capabilities():
    """测试 planner enabled 任务初始化状态为 PLANNED，且不含伪展示型 capabilities"""
    facts = {
        "total_amount": 1000,
        "line_items": [{"amount": 1000}],
        "invoices": [{"invoice_code": "01", "invoice_number": "1001", "total_amount": 1000}],
        "spatio_points": [{"city_name": "北京", "event_time": "2026-09-01", "latitude": 39.9, "longitude": 116.4}]
    }
    plan = AuditPlanner.build_plan(108, "TRAVEL_REIMBURSEMENT", facts)

    amount_t = plan.get_task(AgentRoleEnum.AMOUNT)
    assert amount_t.status == AgentExecutionStatus.PLANNED
    assert amount_t.capabilities == ["five_way_reconciliation"]
    # 严禁出现未实现的假标签
    assert "invoice_amount_coverage" not in amount_t.capabilities
    assert "tax_rate_verification" not in amount_t.capabilities

    policy_t = plan.get_task(AgentRoleEnum.POLICY)
    assert policy_t.status == AgentExecutionStatus.PLANNED
    assert "travel_hotel_tier_limit" not in policy_t.capabilities
    assert "travel_transport_standard" not in policy_t.capabilities
    assert "travel_hotel_limit" in policy_t.capabilities
    assert "business_purpose_check" in policy_t.capabilities


def test_audit_result_dto_execution_elapsed_ms():
    """测试 AuditResultDTO 中的 execution_elapsed_ms 正确计算"""
    state = MasterAuditState(
        task_id="test_elapsed",
        document_id=109,
        document_type="TRAVEL_REIMBURSEMENT"
    )
    res = state.to_audit_result()
    assert isinstance(res.execution_elapsed_ms, int)
    assert res.execution_elapsed_ms >= 0


# =====================================================================
# 6. ReviewerReflector 避免泛化 is_exempt，优先明确 allowance 字段测试
# =====================================================================

def test_reviewer_reflector_rejects_generic_is_exempt():
    """测试仅具备泛化 is_exempt 字段不能作为津贴免票依据，严格保留 R02"""
    findings = [
        RiskFindingContract(
            rule_code="R02_INVOICE_SUM_MISMATCH",
            rule_name="发票总额不符",
            risk_level=RiskLevelEnum.HIGH,
            agent_role=AgentRoleEnum.AMOUNT,
            title="价税不平",
            description="存在差额 200",
            discrepancy_amount=Decimal("200.00"),
            suggestion="核对发票"
        )
    ]
    # 仅有泛化的 is_exempt，无 allowance_verified / policy_allowance_exempt
    facts = {
        "line_items": [{"expense_type": "差旅津贴", "amount": "200.00", "is_exempt": True}]
    }
    verified, logs = ReviewerReflector.reflect_and_disambiguate(findings, facts)
    assert len(verified) == 1
    assert verified[0].rule_code == "R02_INVOICE_SUM_MISMATCH"
    assert len(logs) == 0


def test_reviewer_reflector_accepts_explicit_allowance_verified():
    """测试具备明确 allowance_verified / policy_allowance_exempt 字段时允许消歧"""
    findings = [
        RiskFindingContract(
            rule_code="R02_INVOICE_SUM_MISMATCH",
            rule_name="发票总额不符",
            risk_level=RiskLevelEnum.HIGH,
            agent_role=AgentRoleEnum.AMOUNT,
            title="价税不平",
            description="存在差额 200",
            discrepancy_amount=Decimal("200.00"),
            suggestion="核对发票"
        )
    ]
    facts = {
        "line_items": [{"expense_type": "差旅津贴", "amount": "200.00", "allowance_verified": True}]
    }
    verified, logs = ReviewerReflector.reflect_and_disambiguate(findings, facts)
    assert len(verified) == 0
    assert len(logs) == 1
    assert logs[0]["action"] == "AUTO_RESOLVED_FULL"

