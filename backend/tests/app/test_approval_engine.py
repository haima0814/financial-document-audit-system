"""
backend/tests/app/test_approval_engine.py
审批引擎双轨状态机全流程单元测试 (对齐 Spec 05 全部用例)
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, and_

from app.core.database import Base
from app.models.user import User, Role
from app.models.document import FinancialDocument
from app.models.workflow import ApprovalWorkflow, ApprovalWorkflowNode, ApprovalInstance, ApprovalTask
from app.models.audit import ReviewReport
from app.schemas.approval import ApprovalActionReq, ApprovalActionEnum, AddSignTypeEnum
from app.services.approval_engine import ApprovalEngine

@pytest.fixture
async def test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        # 预置基础角色与测试人员
        u1 = User(id=1, username="admin", hashed_password="pwd", real_name="管理员", department_name="IT")
        u2 = User(id=2, username="manager", hashed_password="pwd", real_name="张经理", department_name="市场部")
        u3 = User(id=3, username="finance", hashed_password="pwd", real_name="李财务", department_name="财务部")
        u4 = User(id=4, username="cfo", hashed_password="pwd", real_name="王总监", department_name="财务部")
        u5 = User(id=5, username="emp", hashed_password="pwd", real_name="小赵", department_name="市场部")
        session.add_all([u1, u2, u3, u4, u5])

        # 预置测试流程模板 (2 级标准流程)
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
async def test_tc_wf_01_auto_pass_low_risk(test_db: AsyncSession):
    """TC_WF_01: 低危小额免审直通"""
    doc = FinancialDocument(
        id=101, document_no="DOC101", document_type="TRAVEL_REIMBURSEMENT",
        title="差旅报销300元", applicant_id=5, total_amount=Decimal("300.00"), status="SUBMITTED"
    )
    report = ReviewReport(
        id=1, task_id="task_auto_1", document_id=101, overall_risk_level="low", final_score=98
    )
    test_db.add_all([doc, report])
    await test_db.commit()

    instance = await ApprovalEngine.start_workflow(test_db, document_id=101, workflow_id=1, report_id=1)
    await test_db.commit()

    assert instance.status == "COMPLETED"
    assert doc.status == "APPROVED"

@pytest.mark.asyncio
async def test_tc_wf_02_and_03_standard_two_stage_flow(test_db: AsyncSession):
    """TC_WF_02 & TC_WF_03: 正常两级审批流转与终审办结"""
    doc = FinancialDocument(
        id=102, document_no="DOC102", document_type="TRAVEL_REIMBURSEMENT",
        title="差旅报销1500元", applicant_id=5, total_amount=Decimal("1500.00"), status="SUBMITTED"
    )
    report = ReviewReport(
        id=2, task_id="task_std_2", document_id=102, overall_risk_level="medium", final_score=80
    )
    test_db.add_all([doc, report])
    await test_db.commit()

    # 启动工作流
    instance = await ApprovalEngine.start_workflow(test_db, document_id=102, workflow_id=1, report_id=2)
    await test_db.commit()

    assert instance.status == "RUNNING"
    assert doc.status == "PENDING_APPROVAL"

    # 查询当前首个待办任务
    tasks_res = await test_db.execute(select(ApprovalTask).where(ApprovalTask.instance_id == instance.id))
    tasks = tasks_res.scalars().all()
    task1 = tasks[0]

    # 节点1 主管审批同意
    res1 = await ApprovalEngine.execute_action(
        db=test_db,
        task_id=task1.id,
        operator_id=2,
        action_req=ApprovalActionReq(action=ApprovalActionEnum.APPROVE, comment="主管核实同意")
    )
    await test_db.commit()

    assert res1["status"] == "SUCCESS"
    assert doc.status == "PENDING_APPROVAL" # 宏观依然处于审批中

    # 查询节点2任务
    t2_res = await test_db.execute(
        select(ApprovalTask).where(
            and_(ApprovalTask.instance_id == instance.id, ApprovalTask.status == "PENDING")
        )
    )
    task2 = t2_res.scalars().first()
    assert task2 is not None
    assert task2.assignee_id == 3 # 财务审批人

    # 节点2 财务审批终审通过
    res2 = await ApprovalEngine.execute_action(
        db=test_db,
        task_id=task2.id,
        operator_id=3,
        action_req=ApprovalActionReq(action=ApprovalActionEnum.APPROVE, comment="财务核算无误，通过")
    )
    await test_db.commit()

    assert res2["document_status"] == "APPROVED"
    assert res2["instance_status"] == "COMPLETED"

@pytest.mark.asyncio
async def test_tc_wf_04_reject(test_db: AsyncSession):
    """TC_WF_04: 审批驳回打回经办人"""
    doc = FinancialDocument(
        id=104, document_no="DOC104", document_type="TRAVEL_REIMBURSEMENT",
        title="报销单据", applicant_id=5, total_amount=Decimal("2000.00"), status="SUBMITTED"
    )
    test_db.add(doc)
    await test_db.commit()

    instance = await ApprovalEngine.start_workflow(test_db, document_id=104, workflow_id=1)
    await test_db.commit()

    t_res = await test_db.execute(select(ApprovalTask).where(ApprovalTask.instance_id == instance.id))
    task1 = t_res.scalars().first()
    res = await ApprovalEngine.execute_action(
        db=test_db,
        task_id=task1.id,
        operator_id=2,
        action_req=ApprovalActionReq(action=ApprovalActionEnum.REJECT, comment="发票抬头开错，请修改后重提")
    )
    await test_db.commit()

    assert res["document_status"] == "REJECTED"
    assert res["instance_status"] == "TERMINATED"

@pytest.mark.asyncio
async def test_tc_wf_05_and_06_transfer_and_anti_loop(test_db: AsyncSession):
    """TC_WF_05 & TC_WF_06: 委派转交及防循环转交拦截"""
    doc = FinancialDocument(
        id=105, document_no="DOC105", document_type="TRAVEL_REIMBURSEMENT",
        title="转交测试单", applicant_id=5, total_amount=Decimal("800.00"), status="SUBMITTED"
    )
    test_db.add(doc)
    await test_db.commit()

    instance = await ApprovalEngine.start_workflow(test_db, document_id=105, workflow_id=1)
    await test_db.commit()

    t_res = await test_db.execute(select(ApprovalTask).where(ApprovalTask.instance_id == instance.id))
    task1 = t_res.scalars().first()

    # 审批人2 转交给 审批人4
    res = await ApprovalEngine.execute_action(
        db=test_db,
        task_id=task1.id,
        operator_id=2,
        action_req=ApprovalActionReq(action=ApprovalActionEnum.TRANSFER, comment="出差中转交王总", target_user_id=4)
    )
    await test_db.commit()

    assert res["status"] == "SUCCESS"
    await test_db.refresh(task1)
    assert task1.status == "TRANSFERRED"

    # 查出新任务 (被转交人 4)
    t4_res = await test_db.execute(
        select(ApprovalTask).where(
            and_(ApprovalTask.instance_id == instance.id, ApprovalTask.status == "PENDING", ApprovalTask.assignee_id == 4)
        )
    )
    task1_trans = t4_res.scalars().first()
    assert task1_trans is not None

    # 用户4 试图转回给 用户2 -> 应当触发防环拦截
    with pytest.raises(ValueError, match="禁止循环转交"):
        await ApprovalEngine.execute_action(
            db=test_db,
            task_id=task1_trans.id,
            operator_id=4,
            action_req=ApprovalActionReq(action=ApprovalActionEnum.TRANSFER, comment="转回原审批人", target_user_id=2)
        )

@pytest.mark.asyncio
async def test_tc_wf_07_add_sign_before_and_wake(test_db: AsyncSession):
    """TC_WF_07: 前置加签与唤醒原任务"""
    doc = FinancialDocument(
        id=107, document_no="DOC107", document_type="TRAVEL_REIMBURSEMENT",
        title="前置加签测试", applicant_id=5, total_amount=Decimal("1200.00"), status="SUBMITTED"
    )
    test_db.add(doc)
    await test_db.commit()

    instance = await ApprovalEngine.start_workflow(test_db, document_id=107, workflow_id=1)
    await test_db.commit()

    t_res = await test_db.execute(select(ApprovalTask).where(ApprovalTask.instance_id == instance.id))
    task1 = t_res.scalars().first()

    # 审批人2 发起前置加签给 用户4 (CFO)
    await ApprovalEngine.execute_action(
        db=test_db,
        task_id=task1.id,
        operator_id=2,
        action_req=ApprovalActionReq(
            action=ApprovalActionEnum.ADD_SIGN,
            add_sign_type=AddSignTypeEnum.BEFORE,
            target_user_id=4,
            comment="请王总先过目"
        )
    )
    await test_db.commit()

    await test_db.refresh(task1)
    assert task1.status == "ADD_SIGN"

    # 加签人 4 进行审批同意
    sign_res = await test_db.execute(
        select(ApprovalTask).where(
            and_(ApprovalTask.instance_id == instance.id, ApprovalTask.assignee_id == 4, ApprovalTask.status == "PENDING")
        )
    )
    sign_task = sign_res.scalars().first()
    assert sign_task is not None

    await ApprovalEngine.execute_action(
        db=test_db,
        task_id=sign_task.id,
        operator_id=4,
        action_req=ApprovalActionReq(action=ApprovalActionEnum.APPROVE, comment="王总已核准，请张经理继续")
    )
    await test_db.commit()

    # 原任务应该被唤醒恢复为 PENDING
    await test_db.refresh(task1)
    assert task1.status == "PENDING"

@pytest.mark.asyncio
async def test_tc_wf_09_revoke_by_applicant(test_db: AsyncSession):
    """TC_WF_09: 经办人在首节点被审批前主动撤回"""
    doc = FinancialDocument(
        id=109, document_no="DOC109", document_type="TRAVEL_REIMBURSEMENT",
        title="经办人撤回单", applicant_id=5, total_amount=Decimal("600.00"), status="SUBMITTED"
    )
    test_db.add(doc)
    await test_db.commit()

    instance = await ApprovalEngine.start_workflow(test_db, document_id=109, workflow_id=1)
    await test_db.commit()

    t_res = await test_db.execute(select(ApprovalTask).where(ApprovalTask.instance_id == instance.id))
    task1 = t_res.scalars().first()

    # 经办人 5 执行主动撤回
    res = await ApprovalEngine.execute_action(
        db=test_db,
        task_id=task1.id,
        operator_id=5,
        action_req=ApprovalActionReq(action=ApprovalActionEnum.REVOKE, comment="发票金额填错了，撤回修改")
    )
    await test_db.commit()

    assert res["document_status"] == "CANCELLED"
    assert res["instance_status"] == "CANCELLED"


# =====================================================================
# 决策门禁专有测试 (Decision Gate Specific Tests)
# =====================================================================

@pytest.mark.asyncio
async def test_decision_gate_complete_does_not_equal_auto_pass():
    """1. 验证 COMPLETE 仅代表审核完整，不等于自动放行 (金额超标或中高风险仍走人工)"""
    from app.services.approval_engine import ApprovalDecisionAction
    from app.models.audit import ReviewReport

    # 场景 1: COMPLETE + low 风险，但金额为 600 元 (> 500元门槛) -> 必须人工审核
    doc_over_limit = FinancialDocument(id=201, total_amount=Decimal("600.00"), status="SUBMITTED")
    report_complete_low = ReviewReport(
        id=201, overall_risk_level="low", final_score=95,
        full_report_payload={"audit_completeness": "COMPLETE"}
    )
    decision1 = await ApprovalEngine.evaluate_transition(doc_over_limit, report_complete_low)
    assert decision1.action == ApprovalDecisionAction.MANUAL_REVIEW
    assert decision1.target_state == "PENDING_APPROVAL"

    # 场景 2: COMPLETE + medium 风险，金额 200 元 -> 必须人工审核
    doc_small = FinancialDocument(id=202, total_amount=Decimal("200.00"), status="SUBMITTED")
    report_complete_medium = ReviewReport(
        id=202, overall_risk_level="medium", final_score=80,
        full_report_payload={"audit_completeness": "COMPLETE"}
    )
    decision2 = await ApprovalEngine.evaluate_transition(doc_small, report_complete_medium)
    assert decision2.action == ApprovalDecisionAction.MANUAL_REVIEW
    assert decision2.target_state == "PENDING_APPROVAL"


@pytest.mark.asyncio
async def test_decision_gate_non_complete_strictly_forbids_auto_pass():
    """2. 验证 audit_completeness != COMPLETE 严禁 AUTO_PASS (无论评级与金额)"""
    from app.services.approval_engine import ApprovalDecisionAction
    from app.models.audit import ReviewReport

    doc_small = FinancialDocument(id=203, total_amount=Decimal("200.00"), status="SUBMITTED")

    # 场景 1: DEGRADED 降级审核，小额低危 -> 严禁放行，转入人工
    report_degraded = ReviewReport(
        id=203, overall_risk_level="low", final_score=100,
        full_report_payload={"audit_completeness": "DEGRADED"}
    )
    decision_deg = await ApprovalEngine.evaluate_transition(doc_small, report_degraded)
    assert decision_deg.action == ApprovalDecisionAction.MANUAL_REVIEW
    assert decision_deg.target_state == "PENDING_APPROVAL"
    assert "DEGRADED" in decision_deg.reason

    # 场景 2: INCOMPLETE 不完整审核，小额低危 -> 严禁放行，转入人工
    report_incomplete = ReviewReport(
        id=204, overall_risk_level="low", final_score=100,
        full_report_payload={"audit_completeness": "INCOMPLETE"}
    )
    decision_inc = await ApprovalEngine.evaluate_transition(doc_small, report_incomplete)
    assert decision_inc.action == ApprovalDecisionAction.MANUAL_REVIEW
    assert decision_inc.target_state == "PENDING_APPROVAL"
    assert "INCOMPLETE" in decision_inc.reason


@pytest.mark.asyncio
async def test_decision_gate_non_overridable_high_risk_rejects():
    """3. 验证任意 is_overridable=False 的 HIGH finding 严禁放行，直接 REJECT 驳回"""
    from app.services.approval_engine import ApprovalDecisionAction
    from app.models.audit import ReviewReport, RiskFinding

    doc = FinancialDocument(id=205, total_amount=Decimal("200.00"), status="SUBMITTED")
    report = ReviewReport(
        id=205, overall_risk_level="high", final_score=60,
        full_report_payload={"audit_completeness": "COMPLETE"}
    )
    veto_finding = RiskFinding(
        report_id=205,
        finding_id="veto-01",
        rule_code="R01_AMOUNT_MISMATCH",
        rule_name="发票价税严重不平账(欺诈嫌疑)",
        risk_level="high",
        agent_role="amount_agent",
        title="严重不平账",
        description="差额巨大且无法解释",
        suggestion="直接退单并上报稽核",
        is_overridable=False  # 一票否决项
    )
    report.findings = [veto_finding]

    decision = await ApprovalEngine.evaluate_transition(doc, report)
    assert decision.action == ApprovalDecisionAction.REJECT
    assert decision.target_state == "REJECTED"
    assert "一票否决" in decision.reason


@pytest.mark.asyncio
async def test_decision_gate_overridable_high_risk_routes_to_cfo_manual_review():
    """4. 验证可覆盖的 HIGH 风险必须进入 MANUAL_REVIEW (含 CFO 终审)，严禁放行"""
    from app.services.approval_engine import ApprovalDecisionAction
    from app.models.audit import ReviewReport, RiskFinding

    doc = FinancialDocument(id=206, total_amount=Decimal("200.00"), status="SUBMITTED")
    report = ReviewReport(
        id=206, overall_risk_level="high", final_score=75,
        full_report_payload={"audit_completeness": "COMPLETE"}
    )
    overridable_finding = RiskFinding(
        report_id=206,
        finding_id="over-01",
        rule_code="R05_POLICY_EXCEEDED",
        rule_name="住宿超标50%以上",
        risk_level="high",
        agent_role="policy_agent",
        title="差旅超标",
        description="单晚超标70%",
        suggestion="建议自理或总监特批",
        is_overridable=True  # 允许特批
    )
    report.findings = [overridable_finding]

    decision = await ApprovalEngine.evaluate_transition(doc, report)
    assert decision.action == ApprovalDecisionAction.MANUAL_REVIEW
    assert decision.target_state == "PENDING_APPROVAL"
    assert "CFO" in decision.required_nodes


@pytest.mark.asyncio
async def test_decision_gate_degraded_and_non_overridable_high_rejects():
    """验证组合场景：DEGRADED + HIGH + is_overridable=False -> blocking HIGH 优先于 completeness，最终裁定 REJECT"""
    from app.services.approval_engine import ApprovalDecisionAction
    from app.models.audit import ReviewReport, RiskFinding

    doc = FinancialDocument(id=207, total_amount=Decimal("300.00"), status="SUBMITTED")
    report = ReviewReport(
        id=207,
        overall_risk_level="high",
        final_score=50,
        full_report_payload={"audit_completeness": "DEGRADED"}  # 存在部分核验降级
    )
    veto_finding = RiskFinding(
        report_id=207,
        finding_id="veto-deg-01",
        rule_code="R13_DISHONEST_SUPPLIER",
        rule_name="失信被执行人黑名单",
        risk_level="high",
        agent_role="supplier_agent",
        title="严重司法失信",
        description="供应商被纳入最高法失信被执行人名单",
        suggestion="严禁付款，立即终止合作",
        is_overridable=False  # 一票否决
    )
    report.findings = [veto_finding]

    decision = await ApprovalEngine.evaluate_transition(doc, report)
    # 核心断言：已核验的一票否决 blocking HIGH 优先于 DEGRADED，必须直接 REJECT，绝不能退化为 MANUAL_REVIEW
    assert decision.action == ApprovalDecisionAction.REJECT
    assert decision.target_state == "REJECTED"
    assert "一票否决" in decision.reason
    assert "优先于完整度直接驳回" in decision.reason


