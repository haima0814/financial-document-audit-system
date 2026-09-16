"""
backend/tests/app/test_engineering_semantics.py
工程语义与可靠性收紧单元测试：
1. AuditContextBuilder 不可变快照装配 (Agent 0 数据库直接读写)
2. AuditCompletionHandler 消费者幂等性防线 (防 Celery/Redis 重投)
3. ApprovalEngine.evaluate_transition 业务决策模型
4. HTTP 409 Conflict 状态机防冲突校验
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.database import Base
from app.models.user import User
from app.models.document import FinancialDocument, DocumentLineItem, DocumentAttachment
from app.models.invoice import InvoiceRecord
from app.models.audit import ReviewReport
from app.services.audit_context_builder import AuditContextBuilder
from app.services.audit_handler import AuditCompletionHandler
from app.services.approval_engine import ApprovalEngine, ApprovalDecisionAction
from app.services.document_service import DocumentService, DocumentStateConflictError
from engines.contract.events import AuditCompletedEvent
from engines.contract.context import AuditExecutionContext
from engines.contract.result import AuditResultDTO
from main import app

@pytest.fixture
async def semantic_test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        u = User(id=1, username="test_emp", hashed_password="pwd", real_name="测试员", department_name="研发部")
        doc = FinancialDocument(
            id=201,
            document_no="DOC-SEM-201",
            document_type="TRAVEL_REIMBURSEMENT",
            title="北京出差研发差旅单",
            total_amount=Decimal("1200.00"),
            department_name="研发部",
            applicant_id=1,
            status="DRAFT"
        )
        att = DocumentAttachment(
            id=1,
            document_id=201,
            file_name="invoice_hotel.pdf",
            file_type="PDF",
            file_path="/uploads/invoice_hotel.pdf",
            file_hash="hash_sem_123",
            file_size_bytes=10240,
            is_invoice=True
        )
        session.add_all([u, doc, att])
        await session.flush()

        line1 = DocumentLineItem(
            document_id=201, line_no=1, expense_type="住宿费", item_desc="北京酒店住宿2晚",
            amount=Decimal("1000.00"), city_name="北京", start_date=datetime(2026, 9, 10), end_date=datetime(2026, 9, 12)
        )
        line2 = DocumentLineItem(
            document_id=201, line_no=2, expense_type="差旅津贴", item_desc="差旅包干补贴",
            amount=Decimal("200.00"), city_name="北京", start_date=datetime(2026, 9, 10), end_date=datetime(2026, 9, 12)
        )
        inv = InvoiceRecord(
            document_id=201, attachment_id=1, invoice_code="011002200111", invoice_number="88997766",
            total_amount=Decimal("1000.00"), tax_amount=Decimal("60.00"),
            issue_date="2026-09-10",
            invoice_hash="hash_inv_88997766",
            seller_tax_id="91110108MA00000000", seller_name="北京如家快捷酒店有限公司"
        )
        session.add_all([line1, line2, inv])
        await session.commit()

        yield session

    await engine.dispose()

@pytest.mark.asyncio
async def test_audit_context_builder_assembly(semantic_test_db: AsyncSession):
    """验证 AuditContextBuilder 完整装配不可变快照，供 Agent 纯内存消费"""
    ctx = await AuditContextBuilder.build(
        db=semantic_test_db,
        document_id=201,
        task_id="task_sem_001",
        audit_version=1
    )

    assert isinstance(ctx, AuditExecutionContext)
    assert ctx.document_no == "DOC-SEM-201"
    assert ctx.total_amount == Decimal("1200.00")
    assert len(ctx.line_items) == 2
    assert len(ctx.invoices) == 1
    assert ctx.applicant_profile["applicant_id"] == 1
    assert ctx.applicant_profile["real_name"] == "测试员"
    assert len(ctx.spatio_points) == 2
    assert len(ctx.rules) >= 3
    assert ctx.approval_context["auto_approve_limit"] == 500.00

@pytest.mark.asyncio
async def test_consumer_idempotency_defense():
    """验证 AuditCompletionHandler 对重复投递的事件实现幂等拦截"""
    event_id = "evt_unique_123456"
    task_id = "task_idempotent_01"

    event = AuditCompletedEvent(
        event_id=event_id,
        task_id=task_id,
        document_id=201,
        audit_version=1,
        result=AuditResultDTO(
            task_id=task_id,
            document_id=201,
            overall_risk_level="low",
            final_score=100,
            summary="测试幂等审计"
        )
    )

    # 首次检查：未处理
    assert not AuditCompletionHandler.is_processed(event_id, task_id, 1)

    # 模拟标记已处理
    AuditCompletionHandler.mark_processed(event_id, task_id, 1)

    # 二次检查：已处理触发拦截
    assert AuditCompletionHandler.is_processed(event_id, task_id, 1)

    # 再次调用 handle 应触发幂等拦截跳过 (不报错且日志记录)
    await AuditCompletionHandler.handle(event)

@pytest.mark.asyncio
async def test_approval_engine_evaluate_transition():
    """验证 ApprovalEngine.evaluate_transition 业务决策模型"""
    doc_small = FinancialDocument(
        id=301, document_no="DOC-SMALL", document_type="EXPENSE",
        title="办公文具", total_amount=Decimal("350.00"), status="SUBMITTED"
    )
    report_low = ReviewReport(id=1, document_id=301, overall_risk_level="low", final_score=100)

    decision_auto = await ApprovalEngine.evaluate_transition(doc_small, report_low)
    assert decision_auto.action == ApprovalDecisionAction.AUTO_APPROVE
    assert decision_auto.target_state == "APPROVED"

    doc_large = FinancialDocument(
        id=302, document_no="DOC-LARGE", document_type="CORP_PAYMENT",
        title="大额采购款", total_amount=Decimal("50000.00"), status="SUBMITTED"
    )
    report_high = ReviewReport(id=2, document_id=302, overall_risk_level="high", final_score=60)

    decision_manual = await ApprovalEngine.evaluate_transition(doc_large, report_high)
    assert decision_manual.action == ApprovalDecisionAction.MANUAL_REVIEW
    assert decision_manual.target_state == "PENDING_APPROVAL"
    assert "CFO" in decision_manual.required_nodes

@pytest.mark.asyncio
async def test_document_state_conflict_raises_409(semantic_test_db: AsyncSession):
    """验证单据处于非草稿/驳回状态时重复提交触发 409 Conflict 语义"""
    doc = await semantic_test_db.get(FinancialDocument, 201)
    doc.status = "APPROVED"
    await semantic_test_db.commit()

    service = DocumentService(semantic_test_db)
    with pytest.raises(DocumentStateConflictError):
        await service.submit_document(document_id=201, user_id=1)


@pytest.mark.asyncio
async def test_audit_service_start_audit(semantic_test_db: AsyncSession):
    """验证 AuditService.start_audit() 统一组装快照并派发审查任务 (生命周期高内聚)"""
    from app.services.audit_service import AuditService
    from app.models.audit import AnalysisTask
    from sqlalchemy import select

    audit_service = AuditService(semantic_test_db)
    task_id = await audit_service.start_audit(
        document_id=201,
        applicant_id=1,
        tenant_id=1,
        audit_version=1
    )
    await semantic_test_db.commit()

    assert task_id.startswith("task_")
    task_rec = (await semantic_test_db.execute(
        select(AnalysisTask).where(AnalysisTask.task_id == task_id)
    )).scalar_one_or_none()
    assert task_rec is not None
    assert task_rec.document_id == 201
    assert task_rec.status == "PENDING"


@pytest.mark.asyncio
async def test_dual_event_publisher_ports():
    """验证 DomainEventPublisher 与 RealtimeEventPublisher 独立端口与适配器"""
    from engines.contract.event_bus import (
        DomainEventPublisher, RealtimeEventPublisher,
        InMemoryDomainEventBus, InMemoryRealtimeEventBus,
        domain_event_bus, realtime_event_bus
    )
    from engines.contract.events import BaseEventEnvelope, EventTypeEnum

    assert isinstance(domain_event_bus, DomainEventPublisher)
    assert isinstance(realtime_event_bus, RealtimeEventPublisher)

    # 验证 RealtimeEventPublisher 独立订阅与发布
    rt_bus = InMemoryRealtimeEventBus()
    q = rt_bus.subscribe("test_task_dual_bus")
    await rt_bus.publish(BaseEventEnvelope(
        event_id="evt_rt_01",
        task_id="test_task_dual_bus",
        document_id=201,
        event=EventTypeEnum.TASK_PROGRESS,
        data={"progress": 50}
    ))
    received = await q.get()
    assert received.event == EventTypeEnum.TASK_PROGRESS
    assert received.data["progress"] == 50


@pytest.mark.asyncio
async def test_transactional_idempotency_processed_event(semantic_test_db: AsyncSession):
    """验证 Unit of Work 事务内 ProcessedEvent 数据库唯一约束拦截重复事件"""
    from app.services.audit_service import AuditService
    from app.models.audit import ProcessedEvent
    from sqlalchemy import select

    task_id = "task_tx_idemp_01"
    event_id = "evt_tx_unique_9999"
    result_dto = AuditResultDTO(
        task_id=task_id,
        document_id=201,
        overall_risk_level="low",
        final_score=95,
        summary="事务型幂等测试"
    )

    audit_service = AuditService(semantic_test_db)
    # 首次执行：成功落库
    rep1 = await audit_service.handle_audit_completed(
        task_id=task_id,
        document_id=201,
        result=result_dto,
        event_id=event_id,
        audit_version=1
    )
    assert rep1 is not None

    # 查验 ProcessedEvent 已记录
    p_evt = (await semantic_test_db.execute(
        select(ProcessedEvent).where(ProcessedEvent.event_id == event_id)
    )).scalar_one_or_none()
    assert p_evt is not None
    assert p_evt.task_id == task_id

    # 模拟并发 Worker 重复执行相同 event_id：由于唯一约束，静默回滚并返回 None
    rep2 = await audit_service.handle_audit_completed(
        task_id=task_id,
        document_id=201,
        result=result_dto,
        event_id=event_id,
        audit_version=1
    )
    assert rep2 is None


def test_agent_harness_hierarchical_timeouts():
    """验证 Agent Harness 分级超时与整体 Deadline 常量定义"""
    from engines.harness.agent_harness import (
        AgentHarness, LLM_CALL_TIMEOUT, RAG_RETRIEVAL_TIMEOUT,
        ENTERPRISE_API_TIMEOUT, DETERMINISTIC_TOOL_TIMEOUT,
        SINGLE_AGENT_TIMEOUT, TASK_OVERALL_DEADLINE
    )

    assert LLM_CALL_TIMEOUT == 20.0
    assert RAG_RETRIEVAL_TIMEOUT == 3.0
    assert ENTERPRISE_API_TIMEOUT == 5.0
    assert DETERMINISTIC_TOOL_TIMEOUT == 0.5
    assert SINGLE_AGENT_TIMEOUT == 30.0
    assert TASK_OVERALL_DEADLINE == 120.0

    assert AgentHarness.LLM_TIMEOUT == 20.0
    assert AgentHarness.AGENT_TIMEOUT == 30.0
    assert AgentHarness.DEADLINE == 120.0
