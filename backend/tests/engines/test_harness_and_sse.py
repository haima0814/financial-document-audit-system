"""
backend/tests/engines/test_harness_and_sse.py
AgentHarness 沙箱门禁与 EventBus/SSE 实时流测试
"""
import pytest
import asyncio
from decimal import Decimal
from httpx import AsyncClient, ASGITransport

from main import app
from engines.contract.finding import RiskFindingContract, RiskLevelEnum
from engines.contract.agent_role import AgentRoleEnum
from engines.contract.result import AgentExecutionStatus
from engines.contract.events import BaseEventEnvelope, EventTypeEnum
from engines.contract.event_bus import InMemoryEventBus, event_bus
from engines.harness.agent_harness import AgentHarness

@pytest.mark.asyncio
async def test_agent_harness_normal_execution():
    """测试 AgentHarness 正常放行合规契约项与返回 SUCCESS 状态"""
    async def _mock_subagent():
        return [
            RiskFindingContract(
                rule_code="R01_AMOUNT_MISMATCH",
                rule_name="金额不一致",
                risk_level=RiskLevelEnum.HIGH,
                agent_role=AgentRoleEnum.AMOUNT,
                title="金额不匹配",
                description="明细之和与总金额不一致",
                suggestion="请重新平账",
                discrepancy_amount=Decimal("150.00")
            )
        ]

    result = await AgentHarness.execute_safely(
        agent_role=AgentRoleEnum.AMOUNT,
        coro=_mock_subagent(),
        timeout_seconds=5.0
    )
    assert result.status == AgentExecutionStatus.SUCCESS
    findings = result.findings
    assert len(findings) == 1
    assert findings[0].rule_code == "R01_AMOUNT_MISMATCH"
    assert findings[0].discrepancy_amount == Decimal("150.00")

@pytest.mark.asyncio
async def test_agent_harness_timeout_fallback():
    """测试 AgentHarness 超时阻断并返回 TIMEOUT 状态与告警项"""
    async def _slow_agent():
        await asyncio.sleep(0.5)
        return []

    result = await AgentHarness.execute_safely(
        agent_role=AgentRoleEnum.POLICY,
        coro=_slow_agent(),
        timeout_seconds=0.05
    )
    assert result.status == AgentExecutionStatus.TIMEOUT
    findings = result.findings
    assert len(findings) == 1
    assert findings[0].rule_code == "H99_AGENT_TIMEOUT"
    assert findings[0].risk_level == RiskLevelEnum.LOW

@pytest.mark.asyncio
async def test_agent_harness_schema_and_math_validation():
    """测试 AgentHarness 对非法契约的清洗与 Decimal 算术防线校验"""
    async def _malformed_agent():
        return [
            # 合法但负金额需要纠偏
            RiskFindingContract(
                rule_code="R05_POLICY_EXCEEDED",
                rule_name="标准超标",
                risk_level=RiskLevelEnum.MEDIUM,
                agent_role=AgentRoleEnum.POLICY,
                title="超标",
                description="住宿超标",
                suggestion="扣减报销",
                discrepancy_amount=Decimal("-80.00")
            ),
            # 非法结构 (缺少必填字段 title 等)
            {"rule_code": "INVALID_ITEM"}
        ]

    result = await AgentHarness.execute_safely(
        agent_role=AgentRoleEnum.POLICY,
        coro=_malformed_agent(),
        timeout_seconds=5.0
    )
    assert result.status == AgentExecutionStatus.SUCCESS
    findings = result.findings
    # 只有合法项通过，非法项被静默过滤
    assert len(findings) == 1
    assert findings[0].rule_code == "R05_POLICY_EXCEEDED"
    # 负数被清洗为绝对值正数
    assert findings[0].discrepancy_amount == Decimal("80.00")

@pytest.mark.asyncio
async def test_event_bus_and_sse_pipeline():
    """测试 EventBus 发布订阅与 SSE 端点握手"""
    task_id = "test-sse-task-888"

    # 1. 预先通过 event_bus 发布领域事件
    envelope = BaseEventEnvelope(
        event=EventTypeEnum.TASK_STARTED,
        task_id=task_id,
        document_id=1,
        data={"stage": "STAGE_1_PARSED"}
    )
    await event_bus.publish(envelope)

    # 2. 检查历史缓存
    history = event_bus.get_history(task_id)
    assert len(history) >= 1
    assert history[0].task_id == task_id

    # 3. 通过 HTTP 客户端建立 SSE 请求获取首批历史推送并快速断开
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 发布一个结束事件以让 SSE 生成器自终止
        finish_envelope = BaseEventEnvelope(
            event=EventTypeEnum.TASK_COMPLETED,
            task_id=task_id,
            document_id=1,
            data={"status": "done"}
        )
        await event_bus.publish(finish_envelope)

        response = await client.get(f"/api/v1/audits/events/{task_id}")
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        content = response.text
        assert "task_started" in content
        assert "task_completed" in content

@pytest.mark.asyncio
async def test_invoice_fingerprint_calculator_pure_deterministic():
    """测试发票指纹计算器纯工具 (0网络/0数据库依赖)"""
    from engines.anomaly_agent.hash_verifier import InvoiceFingerprintCalculator
    from engines.anomaly_agent.schemas import InvoiceFact

    # 1. 确定性指纹计算
    fp1 = InvoiceFingerprintCalculator.compute_fingerprint(
        code=" 0110023 ",
        number=" 88889999 ",
        amount=Decimal("1250.00"),
        issue_date="2026-09-01"
    )
    fp2 = InvoiceFingerprintCalculator.compute_fingerprint(
        code="0110023",
        number="88889999",
        amount=Decimal("1250"),
        issue_date="2026-09-01"
    )
    assert fp1 == fp2 # 大小写与空格自动归一化

    # 2. 纯内存单内查重
    inv = InvoiceFact(
        invoice_code="0110023",
        invoice_number="88889999",
        total_amount=Decimal("1250.00"),
        issue_date="2026-09-01",
        seller_tax_id="91110000MA0001"
    )
    dup_findings = InvoiceFingerprintCalculator.detect_duplicate_invoices([inv, inv])
    assert len(dup_findings) == 1
    assert dup_findings[0].rule_code == "R08_INVOICE_DUPLICATE"

    # 3. 跨单上下文注入比对 (无直接SQL查询)
    historical_mock = {
        fp1: {"document_no": "EXP-2026-OLD-01", "status": "APPROVED"}
    }
    cross_findings = InvoiceFingerprintCalculator.detect_duplicate_invoices(
        [inv],
        historical_fingerprints=historical_mock
    )
    assert len(cross_findings) == 1
    assert "EXP-2026-OLD-01" in cross_findings[0].title

@pytest.mark.asyncio
async def test_audit_completed_domain_event_dispatch():
    """测试 AuditCompletedEvent 领域事件发布与消费链路"""
    from engines.contract.events import AuditCompletedEvent
    from engines.contract.result import AuditResultDTO

    received_events = []

    async def _mock_handler(event: AuditCompletedEvent):
        received_events.append(event)

    test_bus = InMemoryEventBus()
    test_bus.register_domain_handler(AuditCompletedEvent, _mock_handler)

    sample_result = AuditResultDTO(
        task_id="task-domain-ev-001",
        document_id=99,
        overall_risk_level="low",
        final_score=98,
        summary="低危通过"
    )
    domain_ev = AuditCompletedEvent(
        task_id="task-domain-ev-001",
        document_id=99,
        result=sample_result
    )

    await test_bus.publish_domain_event(domain_ev)
    assert len(received_events) == 1
    assert received_events[0].task_id == "task-domain-ev-001"
    assert received_events[0].result.final_score == 98


@pytest.mark.asyncio
async def test_agent_harness_degraded_status_and_audit_completeness():
    """测试 AgentHarness 在 AgentFindingList.is_degraded=True 时统一置为 DEGRADED，
    并确保 audit_completeness 能识别 DEGRADED"""
    from engines.contract.finding import AgentFindingList
    from engines.orchestrator.state import MasterAuditState
    from engines.orchestrator.master_graph import MasterOrchestrator

    # 1. 模拟子智能体返回降级结果列表 AgentFindingList (is_degraded=True)
    async def _degraded_subagent():
        return AgentFindingList(
            [],
            is_degraded=True,
            degraded_reason="大模型服务不可用，退化为启发式规则降级审核",
            source="HEURISTIC_RULE"
        )

    exec_result = await AgentHarness.execute_safely(
        agent_role=AgentRoleEnum.POLICY,
        coro=_degraded_subagent(),
        timeout_seconds=5.0
    )

    # 2. 确认 AgentHarness 行为：统一将 status 设为 DEGRADED，并且 is_degraded 为 True
    assert exec_result.status == AgentExecutionStatus.DEGRADED
    assert exec_result.is_degraded is True
    assert exec_result.source == "HEURISTIC_RULE"
    assert "降级审核" in (exec_result.reason or "")

    # 3. 确认 audit_completeness 识别逻辑：当包含 DEGRADED 结果时，完整度裁定为 DEGRADED
    state = MasterAuditState(
        task_id="test-degraded-task",
        document_id=999,
        document_type="TRAVEL_REIMBURSEMENT",
        agent_results=[exec_result]
    )
    result_dto = await MasterOrchestrator._stage4_report(state)
    assert result_dto.audit_completeness == "DEGRADED"
    assert state.audit_completeness == "DEGRADED"

