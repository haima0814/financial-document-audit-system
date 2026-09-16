"""
backend/tests/app/test_approval_engine_workflow_db.py
P0 级：审批引擎 start_workflow 真实数据库状态与生命周期闭环测试
覆盖：
1. COMPLETE + low + <=500 自动放行、assignee_id 为 NULL 不违背外键约束
2. DEGRADED + low + <=500 严禁放行，转入人工审批并产生 PENDING 任务
3. INCOMPLETE 严禁放行，转入人工审批并产生 PENDING 任务
4. 不可覆盖 HIGH 自动 REJECT，终止流程且严禁产生 PENDING 任务
5. R14 缺票进入 NEED_SUPPLEMENT，挂起流程且严禁产生 PENDING 任务
6. 二次审核多实例生命周期：同一单据驳回/补正后重新提交，多 ApprovalInstance 并存不覆盖
7. 实时事件载荷 Pydantic 契约校验与字段规范化
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, and_

from app.core.database import Base
from app.models.user import User
from app.models.document import FinancialDocument, DocumentLineItem
from app.models.workflow import ApprovalWorkflow, ApprovalWorkflowNode, ApprovalInstance, ApprovalTask, WorkflowStatusLog
from app.models.audit import ReviewReport
from app.services.approval_engine import ApprovalEngine
from app.services.document_service import DocumentService
from engines.contract.events import EventTypeEnum, NodeStatusPayload, TaskCompletedPayload, AgentRoleEnum
from engines.orchestrator.stream_producer import StreamProducer

@pytest.fixture
async def test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        # 预置基础用户 (ID 1-5，确保无 ID 0 用户)
        u1 = User(id=1, username="admin", hashed_password="pwd", real_name="管理员", department_name="IT")
        u2 = User(id=2, username="manager", hashed_password="pwd", real_name="张经理", department_name="市场部")
        u3 = User(id=3, username="finance", hashed_password="pwd", real_name="李财务", department_name="财务部")
        u4 = User(id=4, username="cfo", hashed_password="pwd", real_name="王总监", department_name="财务部")
        u5 = User(id=5, username="emp", hashed_password="pwd", real_name="小赵", department_name="市场部")
        session.add_all([u1, u2, u3, u4, u5])

        # 预置测试流程模板
        wf = ApprovalWorkflow(id=1, workflow_code="WF_STANDARD", workflow_name="标准审批流", document_type="TRAVEL_REIMBURSEMENT")
        session.add(wf)
        await session.flush()

        node1 = ApprovalWorkflowNode(id=1, workflow_id=1, node_order=1, node_name="直属主管初审", approver_type="USER", user_id=2)
        node2 = ApprovalWorkflowNode(id=2, workflow_id=1, node_order=2, node_name="财务复核", approver_type="USER", user_id=3, is_final=True)
        session.add_all([node1, node2])
        await session.commit()

        yield session

    await engine.dispose()

@pytest.mark.asyncio
async def test_auto_approve_db_state_and_foreign_key(test_db: AsyncSession):
    """1. COMPLETE + low + <=500: 真实落地 APPROVED，且 assignee_id=NULL 严禁违反 users 外键"""
    doc = FinancialDocument(
        id=201, document_no="DOC201", document_type="TRAVEL_REIMBURSEMENT",
        title="小额打车费300元", applicant_id=5, total_amount=Decimal("300.00"), status="SUBMITTED"
    )
    report = ReviewReport(
        id=201, task_id="task_auto_201", document_id=201, overall_risk_level="low", final_score=100,
        full_report_payload={"audit_completeness": "COMPLETE", "findings": []}
    )
    test_db.add_all([doc, report])
    await test_db.commit()

    instance = await ApprovalEngine.start_workflow(test_db, 201, 1, report_id=201)
    await test_db.commit()

    # 验证单据和实例状态
    assert doc.status == "APPROVED"
    assert instance.status == "COMPLETED"
    assert instance.report_id == 201
    assert instance.audit_version == 1

    # 验证 ApprovalTask：assignee_id 必须为 None (不能为 0)
    task_stmt = select(ApprovalTask).where(ApprovalTask.instance_id == instance.id)
    tasks = list((await test_db.execute(task_stmt)).scalars().all())
    assert len(tasks) == 1
    assert tasks[0].status == "AUTO_PASSED"
    assert tasks[0].assignee_id is None

    # 验证 WorkflowStatusLog
    log_stmt = select(WorkflowStatusLog).where(WorkflowStatusLog.instance_id == instance.id)
    logs = list((await test_db.execute(log_stmt)).scalars().all())
    assert len(logs) == 1
    assert logs[0].action == "AUTO_PASS"
    assert "小额免审" in logs[0].comment or "自动放行" in logs[0].comment

@pytest.mark.asyncio
async def test_degraded_low_small_amount_blocks_auto_approve(test_db: AsyncSession):
    """2. DEGRADED + low + <=500: 严禁自动放行，必须转为 PENDING_APPROVAL 并创建 PENDING 审批任务"""
    doc = FinancialDocument(
        id=202, document_no="DOC202", document_type="TRAVEL_REIMBURSEMENT",
        title="小额差旅300元(但核验降级)", applicant_id=5, total_amount=Decimal("300.00"), status="SUBMITTED"
    )
    report = ReviewReport(
        id=202, task_id="task_deg_202", document_id=202, overall_risk_level="low", final_score=95,
        full_report_payload={"audit_completeness": "DEGRADED", "findings": []}
    )
    test_db.add_all([doc, report])
    await test_db.commit()

    instance = await ApprovalEngine.start_workflow(test_db, 202, 1, report_id=202)
    await test_db.commit()

    # 验证严禁放行，单据进入人工审批
    assert doc.status == "PENDING_APPROVAL"
    assert instance.status == "RUNNING"

    # 必须生成首节点待办任务给主管
    task_stmt = select(ApprovalTask).where(ApprovalTask.instance_id == instance.id)
    tasks = list((await test_db.execute(task_stmt)).scalars().all())
    assert len(tasks) == 1
    assert tasks[0].status == "PENDING"
    assert tasks[0].assignee_id == 2 # 张经理

    # 验证流转日志记录降级原因
    log_stmt = select(WorkflowStatusLog).where(WorkflowStatusLog.instance_id == instance.id)
    logs = list((await test_db.execute(log_stmt)).scalars().all())
    assert len(logs) == 1
    assert logs[0].action == "SUBMIT_FOR_REVIEW"
    assert "DEGRADED" in logs[0].comment

@pytest.mark.asyncio
async def test_incomplete_blocks_auto_approve(test_db: AsyncSession):
    """3. INCOMPLETE: 严禁放行，必须转入人工复核"""
    doc = FinancialDocument(
        id=203, document_no="DOC203", document_type="TRAVEL_REIMBURSEMENT",
        title="小额差旅200元(存在盲区)", applicant_id=5, total_amount=Decimal("200.00"), status="SUBMITTED"
    )
    report = ReviewReport(
        id=203, task_id="task_inc_203", document_id=203, overall_risk_level="low", final_score=90,
        full_report_payload={"audit_completeness": "INCOMPLETE", "findings": []}
    )
    test_db.add_all([doc, report])
    await test_db.commit()

    instance = await ApprovalEngine.start_workflow(test_db, 203, 1, report_id=203)
    await test_db.commit()

    assert doc.status == "PENDING_APPROVAL"
    assert instance.status == "RUNNING"

    task_stmt = select(ApprovalTask).where(ApprovalTask.instance_id == instance.id)
    tasks = list((await test_db.execute(task_stmt)).scalars().all())
    assert len(tasks) == 1
    assert tasks[0].status == "PENDING"

@pytest.mark.asyncio
async def test_non_overridable_high_auto_reject_db_state(test_db: AsyncSession):
    """4. 不可覆盖 HIGH: 直接 REJECT，流程 TERMINATED，且严禁产生任何 PENDING 审批任务"""
    doc = FinancialDocument(
        id=204, document_no="DOC204", document_type="TRAVEL_REIMBURSEMENT",
        title="跨单重复发票报销", applicant_id=5, total_amount=Decimal("1200.00"), status="SUBMITTED"
    )
    report = ReviewReport(
        id=204, task_id="task_rej_204", document_id=204, overall_risk_level="high", final_score=20,
        full_report_payload={
            "audit_completeness": "COMPLETE",
            "findings": [
                {"rule_code": "R08_CROSS_DOC_DUPLICATE_INVOICE", "rule_name": "跨单重复报销发票", "risk_level": "high", "is_overridable": False}
            ]
        }
    )
    test_db.add_all([doc, report])
    await test_db.commit()

    instance = await ApprovalEngine.start_workflow(test_db, 204, 1, report_id=204)
    await test_db.commit()

    # 验证单据直接被驳回
    assert doc.status == "REJECTED"
    assert instance.status == "TERMINATED"
    assert instance.end_time is not None

    # 关键断言：严禁产生任何 ApprovalTask
    task_stmt = select(ApprovalTask).where(ApprovalTask.instance_id == instance.id)
    tasks = list((await test_db.execute(task_stmt)).scalars().all())
    assert len(tasks) == 0

    # 验证审计日志
    log_stmt = select(WorkflowStatusLog).where(WorkflowStatusLog.instance_id == instance.id)
    logs = list((await test_db.execute(log_stmt)).scalars().all())
    assert len(logs) == 1
    assert logs[0].action == "AUTO_REJECT"
    assert "一票否决" in logs[0].comment or "不可覆盖" in logs[0].comment

@pytest.mark.asyncio
async def test_missing_invoice_r14_need_supplement_db_state(test_db: AsyncSession):
    """5. R14 缺票: 单据进入 NEED_SUPPLEMENT，流程 SUSPENDED，严禁产生普通审批任务"""
    doc = FinancialDocument(
        id=205, document_no="DOC205", document_type="TRAVEL_REIMBURSEMENT",
        title="缺票报销单据", applicant_id=5, total_amount=Decimal("800.00"), status="SUBMITTED"
    )
    report = ReviewReport(
        id=205, task_id="task_sup_205", document_id=205, overall_risk_level="high", final_score=40,
        full_report_payload={
            "audit_completeness": "COMPLETE",
            "findings": [
                {"rule_code": "R14_MISSING_INVOICE", "rule_name": "关键发票原件缺失", "risk_level": "high", "is_overridable": True}
            ]
        }
    )
    test_db.add_all([doc, report])
    await test_db.commit()

    instance = await ApprovalEngine.start_workflow(test_db, 205, 1, report_id=205)
    await test_db.commit()

    # 验证单据进入 NEED_SUPPLEMENT 状态
    assert doc.status == "NEED_SUPPLEMENT"
    assert instance.status == "SUSPENDED"

    # 严禁产生任何普通 PENDING 审批任务
    task_stmt = select(ApprovalTask).where(ApprovalTask.instance_id == instance.id)
    tasks = list((await test_db.execute(task_stmt)).scalars().all())
    assert len(tasks) == 0

    # 验证审计日志
    log_stmt = select(WorkflowStatusLog).where(WorkflowStatusLog.instance_id == instance.id)
    logs = list((await test_db.execute(log_stmt)).scalars().all())
    assert len(logs) == 1
    assert logs[0].action == "NEED_SUPPLEMENT"

@pytest.mark.asyncio
async def test_resubmit_multiversion_lifecycle_and_two_instances_preserved(test_db: AsyncSession):
    """6. 多实例生命周期：单据被驳回/补正后，再次提交审查，两次 ApprovalInstance 均完整保留且互不覆盖"""
    doc = FinancialDocument(
        id=206, document_no="DOC206", document_type="TRAVEL_REIMBURSEMENT",
        title="出差技术交流", applicant_id=5, total_amount=Decimal("1500.00"), status="SUBMITTED",
        current_version=1
    )
    test_db.add(doc)
    await test_db.flush()

    line = DocumentLineItem(document_id=206, line_no=1, expense_type="住宿费", item_desc="酒店1晚", amount=Decimal("1500.00"))
    test_db.add(line)

    # 第一次审查报告：一票否决
    report1 = ReviewReport(
        id=2061, task_id="task_v1_206", document_id=206, overall_risk_level="high", final_score=10,
        full_report_payload={
            "audit_completeness": "COMPLETE",
            "findings": [{"rule_code": "R08_CROSS_DOC_DUPLICATE_INVOICE", "risk_level": "high", "is_overridable": False}]
        }
    )
    test_db.add(report1)
    await test_db.commit()

    # 第一次启动流程 -> 被 REJECT
    instance1 = await ApprovalEngine.start_workflow(test_db, 206, 1, report_id=2061)
    await test_db.commit()

    assert doc.status == "REJECTED"
    assert instance1.status == "TERMINATED"
    assert instance1.audit_version == 1
    assert instance1.report_id == 2061

    # 经办人修改后重新提交 (RESUBMIT) -> 升级为 V2
    doc_service = DocumentService(test_db)
    await doc_service.submit_document(document_id=206, user_id=5)
    await test_db.commit()
    await test_db.refresh(doc)

    assert doc.status == "SUBMITTED"
    assert doc.current_version == 2

    # 第二次审查报告：已整改合规
    report2 = ReviewReport(
        id=2062, task_id="task_v2_206", document_id=206, overall_risk_level="low", final_score=98,
        full_report_payload={"audit_completeness": "COMPLETE", "findings": []}
    )
    test_db.add(report2)
    await test_db.commit()

    # 第二次启动流程 -> 产生 instance2 (由于金额 1500 > 500，进入人工审批)
    instance2 = await ApprovalEngine.start_workflow(test_db, 206, 1, report_id=2062)
    await test_db.commit()

    assert doc.status == "PENDING_APPROVAL"
    assert instance2.id != instance1.id
    assert instance2.status == "RUNNING"
    assert instance2.audit_version == 2
    assert instance2.report_id == 2062

    # 核心断言：两次审批实例均存在于数据库，互不影响与覆盖！
    all_instances_stmt = select(ApprovalInstance).where(ApprovalInstance.document_id == 206).order_by(ApprovalInstance.id)
    all_instances = list((await test_db.execute(all_instances_stmt)).scalars().all())
    assert len(all_instances) == 2
    assert all_instances[0].status == "TERMINATED"
    assert all_instances[0].audit_version == 1
    assert all_instances[1].status == "RUNNING"
    assert all_instances[1].audit_version == 2

@pytest.mark.asyncio
async def test_realtime_event_payload_pydantic_contract():
    """7. 契约校验：StreamProducer 发布的事件载荷必须严格符合 Pydantic DTO 规范与字段映射"""
    # 测试 NodeStatusPayload 与字段命名漂移兼容 (elapsed_ms -> duration_ms)
    env_node = await StreamProducer.publish_event(
        task_id="task_stream_01",
        document_id=1,
        event_type=EventTypeEnum.NODE_STATUS,
        payload={
            "role": AgentRoleEnum.AMOUNT,
            "status": "SUCCESS",
            "message": "金额核算完成",
            "elapsed_ms": 125,
            "findings_count": 0
        }
    )
    assert env_node.event == EventTypeEnum.NODE_STATUS
    assert env_node.data["role"] == "amount_agent"
    assert env_node.data["duration_ms"] == 125
    assert env_node.data["status"] == "SUCCESS"

    # 测试 TaskCompletedPayload 与 high_count -> high_risks_count 自动映射
    env_comp = await StreamProducer.publish_event(
        task_id="task_stream_02",
        document_id=1,
        event_type=EventTypeEnum.TASK_COMPLETED,
        payload={
            "report_id": 999,
            "overall_risk_level": "low",
            "risk_score": 95,
            "high_count": 0,
            "medium_count": 1,
            "low_count": 2,
            "summary": "风控完成"
        }
    )
    assert env_comp.event == EventTypeEnum.TASK_COMPLETED
    assert env_comp.data["high_risks_count"] == 0
    assert env_comp.data["medium_risks_count"] == 1
    assert env_comp.data["low_risks_count"] == 2
    assert env_comp.data["audit_completeness"] == "COMPLETE"
