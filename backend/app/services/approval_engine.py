"""
backend/app/services/approval_engine.py
企业级审批工作流双轨状态机驱动引擎 (Spec 05 标准实现)
"""
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from enum import Enum
from pydantic import BaseModel, Field
from app.models.workflow import (
    ApprovalWorkflow,
    ApprovalWorkflowNode,
    ApprovalInstance,
    ApprovalTask,
    WorkflowStatusLog,
)
from app.models.document import FinancialDocument
from app.models.audit import ReviewReport
from app.schemas.approval import ApprovalActionReq, ApprovalActionEnum, AddSignTypeEnum

class ApprovalDecisionAction(str, Enum):
    AUTO_APPROVE = "AUTO_APPROVE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    NEED_SUPPLEMENT = "NEED_SUPPLEMENT"
    REJECT = "REJECT"

class ApprovalDecision(BaseModel):
    """
    审批流与单据状态转移裁决结果 (ApprovalDecision)
    表达“Agent 产生审核判断，AuditService 固化审核事实，ApprovalService 决定业务后果”的核心业务契约
    """
    action: ApprovalDecisionAction
    target_state: str = Field(..., description="业务单据跃迁目标状态 (APPROVED / PENDING_APPROVAL / NEED_SUPPLEMENT / AUDIT_FAILED)")
    required_nodes: List[str] = Field(default_factory=list, description="所需的审批流节点标签")
    reason: str = Field(..., description="状态机跃迁决策依据与内控规则说明")

class ApprovalEngine:

    @classmethod
    async def evaluate_transition(
        cls,
        doc: FinancialDocument,
        report: Optional[ReviewReport]
    ) -> ApprovalDecision:
        """
        根据单据金额、类型与 AI 审计体检报告，进行状态转移业务后果裁决
        """
        if not report:
            return ApprovalDecision(
                action=ApprovalDecisionAction.MANUAL_REVIEW,
                target_state="PENDING_APPROVAL",
                required_nodes=["DIRECTOR", "FINANCE"],
                reason="未挂载风控体检报告，推进常规人工终审流程"
            )

        # 提取审核完整度
        completeness = "COMPLETE"
        if report.full_report_payload and isinstance(report.full_report_payload, dict):
            completeness = report.full_report_payload.get("audit_completeness", "COMPLETE")

        # 安全提取 findings 列表 (兼容 ORM 实例、字典与已加载属性，杜绝 MissingGreenlet 异常)
        findings_list = []
        try:
            from sqlalchemy import inspect as sa_inspect
            insp = sa_inspect(report)
            if "findings" in insp.dict and insp.dict["findings"]:
                findings_list = insp.dict["findings"]
            elif report.full_report_payload and isinstance(report.full_report_payload, dict):
                findings_list = report.full_report_payload.get("findings") or report.full_report_payload.get("verified_findings") or []
        except Exception:
            if hasattr(report, "findings") and isinstance(report.findings, list):
                findings_list = report.findings
            elif report.full_report_payload and isinstance(report.full_report_payload, dict):
                findings_list = report.full_report_payload.get("findings") or []

        def _get_f_field(f_item, key: str, default=None):
            if isinstance(f_item, dict):
                return f_item.get(key, default)
            return getattr(f_item, key, default)

        # 0. 真正一票否决硬门禁 (最高优先级)：
        # 任意已验证且不可覆盖 (is_overridable=False) 的 HIGH 风险项，优先于完整度判断，直接 REJECT 驳回！
        non_overridable_high = [
            f for f in findings_list
            if _get_f_field(f, "risk_level") == "high" and _get_f_field(f, "is_overridable", True) is False
        ]
        if non_overridable_high:
            veto_rules = "、".join(str(_get_f_field(f, "rule_name") or _get_f_field(f, "rule_code")) for f in non_overridable_high)
            return ApprovalDecision(
                action=ApprovalDecisionAction.REJECT,
                target_state="REJECTED",
                required_nodes=[],
                reason=f"检出一票否决高危违规项（不可覆盖），优先于完整度直接驳回: {veto_rules}。"
            )

        # 1. 致命缺陷检查：缺少发票原件打回补充材料
        if report.overall_risk_level == "high" and any(_get_f_field(f, "rule_code") == "R14_MISSING_INVOICE" for f in findings_list):
            return ApprovalDecision(
                action=ApprovalDecisionAction.NEED_SUPPLEMENT,
                target_state="NEED_SUPPLEMENT",
                required_nodes=[],
                reason="关键发票凭证缺失或无法辨识，单据打回经办人补充材料"
            )

        # 2. 审核完整度门禁：其余情况下 audit_completeness != COMPLETE 严禁自动放行
        # INCOMPLETE（存在必要核验盲区）与 DEGRADED（存在降级/要素缺失）均必须转入人工复核
        if completeness != "COMPLETE":
            nodes = ["DIRECTOR", "FINANCE"]
            if report.overall_risk_level == "high" or doc.total_amount >= Decimal("10000.00"):
                nodes.append("CFO")
            reason_str = (
                "审核完整度为 INCOMPLETE（存在必要核验盲区），即便评分为低危也必须转入人工重点复审，禁止自动放行。"
                if completeness == "INCOMPLETE" else
                f"审核完整度为 [{completeness}]（存在核验降级或部分要素缺失），禁止自动放行，转入人工重点复核。"
            )
            return ApprovalDecision(
                action=ApprovalDecisionAction.MANUAL_REVIEW,
                target_state="PENDING_APPROVAL",
                required_nodes=nodes,
                reason=reason_str
            )

        # 3. 可覆盖 HIGH 风险必须进入 MANUAL_REVIEW，严禁自动放行
        if report.overall_risk_level == "high":
            return ApprovalDecision(
                action=ApprovalDecisionAction.MANUAL_REVIEW,
                target_state="PENDING_APPROVAL",
                required_nodes=["DIRECTOR", "FINANCE", "CFO"],
                reason="检出高危合规风险（属于可由人工审批覆盖范畴），禁止自动放行，转入包含 CFO 在内的三级人工终审。"
            )

        # 4. 小额低危免审自动放行 (RULE_AUTO_PASS: 仅限 COMPLETE 且 low 且 <= 500元)
        # COMPLETE 仅表示审核完整无盲区，只有同时满足小额与低危才触发放行
        if report.overall_risk_level == "low" and doc.total_amount <= Decimal("500.00"):
            return ApprovalDecision(
                action=ApprovalDecisionAction.AUTO_APPROVE,
                target_state="APPROVED",
                required_nodes=[],
                reason="审核完整度为 COMPLETE、单据金额 <= 500元且AI审查评定为低危，触发小额免审规则直通放行。"
            )

        # 5. 其他中风险或大额推进人工审批链
        nodes = ["DIRECTOR", "FINANCE"]
        if doc.total_amount >= Decimal("10000.00"):
            nodes.append("CFO")

        return ApprovalDecision(
            action=ApprovalDecisionAction.MANUAL_REVIEW,
            target_state="PENDING_APPROVAL",
            required_nodes=nodes,
            reason=f"综合风控评级为 [{report.overall_risk_level.upper()}] 或单据金额达到审批门槛，推进多级人工终审。"
        )

    @staticmethod
    async def start_workflow(
        db: AsyncSession,
        document_id: int,
        workflow_id: int,
        report_id: Optional[int] = None
    ) -> ApprovalInstance:
        """
        单据提交后启动审批流，依据风控报告执行智能路由分支
        """
        # 1. 加载单据与风控报告
        doc = await db.get(FinancialDocument, document_id)
        if not doc:
            raise ValueError(f"单据 ID [{document_id}] 不存在！")

        if report_id:
            from sqlalchemy.orm import selectinload
            report = await db.get(ReviewReport, report_id, options=[selectinload(ReviewReport.findings)])
        else:
            report = None

        # 检查是否已存在进行中的审批实例
        existing_instance_stmt = select(ApprovalInstance).where(
            and_(
                ApprovalInstance.document_id == document_id,
                ApprovalInstance.status.in_(["RUNNING", "PENDING"])
            )
        )
        existing_instance = (await db.execute(existing_instance_stmt)).scalars().first()
        if existing_instance:
            return existing_instance

        # 2. 状态转移业务后果裁决
        decision = await ApprovalEngine.evaluate_transition(doc, report)

        # 3. 创建审批实例
        instance = ApprovalInstance(
            workflow_id=workflow_id,
            document_id=document_id,
            status="RUNNING",
            start_time=datetime.now(timezone.utc)
        )
        db.add(instance)
        await db.flush()

        # 4. 执行状态机裁决分支
        if decision.action == ApprovalDecisionAction.AUTO_APPROVE:
            instance.status = "COMPLETED"
            instance.end_time = datetime.now(timezone.utc)
            doc.status = decision.target_state # "APPROVED"

            # 记录免审直通任务与日志
            first_node = await ApprovalEngine._get_node_by_order(db, workflow_id, 1)
            auto_task = ApprovalTask(
                instance_id=instance.id,
                node_id=first_node.id if first_node else 1,
                assignee_id=0, # 系统自动
                status="AUTO_PASSED",
                comment="单据金额 <= 500元且AI审查评定为低危，触发小额免审规则直通放行。",
                created_at=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc)
            )
            db.add(auto_task)

            log = WorkflowStatusLog(
                instance_id=instance.id,
                task_id=auto_task.id,
                operator_id=0, # 系统自动处理
                action="AUTO_PASS",
                comment="单据金额 <= 500元且AI审查评定为低危，触发小额免审规则直通放行。"
            )
            db.add(log)
            await db.flush()
            return instance

        # 4. 常规/高危流程：创建首节点待办任务 (从 node_order=1 开始)
        first_node = await ApprovalEngine._get_node_by_order(db, workflow_id, 1)
        if not first_node:
            raise ValueError(f"工作流 [{workflow_id}] 未配置任何有效节点！")

        assignee_id = await ApprovalEngine._resolve_assignee(db, doc, first_node)
        task = ApprovalTask(
            instance_id=instance.id,
            node_id=first_node.id,
            assignee_id=assignee_id,
            status="PENDING",
            created_at=datetime.now(timezone.utc)
        )
        db.add(task)

        # 单据进入“待人工审批”状态，记录当前节点
        instance.current_node_id = first_node.id
        doc.status = "PENDING_APPROVAL"
        await db.flush()
        return instance

    @staticmethod
    async def execute_action(
        db: AsyncSession,
        task_id: int,
        operator_id: int,
        action_req: ApprovalActionReq
    ) -> Dict[str, Any]:
        """
        执行审批动作（同意、驳回、转交、加签、撤回）
        使用排他行锁防并发重入
        """
        # 1. 锁行防并发重复处理
        stmt = select(ApprovalTask).where(ApprovalTask.id == task_id).with_for_update()
        res = await db.execute(stmt)
        task = res.scalars().first()

        if not task or task.status != "PENDING":
            raise ValueError("该审批任务不存在或已被处理！")

        instance = await db.get(ApprovalInstance, task.instance_id)
        if not instance:
            raise ValueError("未找到关联的审批流实例！")

        doc = await db.get(FinancialDocument, instance.document_id)
        if not doc:
            raise ValueError("未找到关联的财务单据！")

        # 校验操作人权限：REVOKE 必须由申请人操作；其他审批动作必须由 assignee 操作
        if action_req.action == ApprovalActionEnum.REVOKE:
            if doc.applicant_id != operator_id:
                raise PermissionError("只有单据发起经办人本人允许主动撤回！")
        else:
            if task.assignee_id != operator_id:
                raise PermissionError("您不是该审批任务的指定经办人，无权审批！")

        # 2. 分发各业务动作
        if action_req.action == ApprovalActionEnum.APPROVE:
            await ApprovalEngine._handle_approve(db, instance, task, doc, operator_id, action_req)
        elif action_req.action == ApprovalActionEnum.REJECT:
            await ApprovalEngine._handle_reject(db, instance, task, doc, operator_id, action_req)
        elif action_req.action == ApprovalActionEnum.TRANSFER:
            await ApprovalEngine._handle_transfer(db, instance, task, operator_id, action_req)
        elif action_req.action == ApprovalActionEnum.ADD_SIGN:
            await ApprovalEngine._handle_add_sign(db, instance, task, operator_id, action_req)
        elif action_req.action == ApprovalActionEnum.REVOKE:
            await ApprovalEngine._handle_revoke(db, instance, task, doc, operator_id, action_req)

        await db.flush()
        return {
            "status": "SUCCESS",
            "document_status": doc.status,
            "instance_status": instance.status,
            "task_status": task.status
        }

    @staticmethod
    async def _handle_approve(
        db: AsyncSession,
        instance: ApprovalInstance,
        task: ApprovalTask,
        doc: FinancialDocument,
        operator_id: int,
        req: ApprovalActionReq
    ):
        """
        处理通过逻辑：
        1. 若当前任务为【前置加签】子任务，通过后唤醒处于 ADD_SIGN 挂起态的母任务 (变回 PENDING)；
        2. 若当前任务为【后置加签】子任务，通过后恢复常规下一节点流转；
        3. 常规节点：若有下一节点继续流转，无下一节点单据终审通过。
        4. 高危红线放行时强制检查 override_reason 留痕。
        """
        task.status = "APPROVED"
        task.end_time = datetime.now(timezone.utc)
        task.comment = req.comment

        log = WorkflowStatusLog(
            instance_id=instance.id,
            task_id=task.id,
            operator_id=operator_id,
            action="APPROVE",
            comment=req.comment,
            extra_data={"override_reason": req.override_reason} if req.override_reason else None
        )
        db.add(log)

        # 检查是否为前置加签子任务完成后的母任务唤醒
        task_meta = getattr(task, "extra_data", {}) or {}
        parent_task_id = task_meta.get("parent_task_id")
        if parent_task_id and task_meta.get("add_sign_type") == "BEFORE":
            parent_task = await db.get(ApprovalTask, parent_task_id)
            if parent_task and parent_task.status == "ADD_SIGN":
                parent_task.status = "PENDING"  # 唤醒原审批人待办
                instance.current_node_id = parent_task.node_id
                return

        # 获取当前节点
        current_node = await db.get(ApprovalWorkflowNode, task.node_id)
        current_order = current_node.node_order if current_node else 1

        # 若是后置加签任务，resume_node_order 存储了原规划的下一顺序
        next_order = task_meta.get("resume_node_order", current_order + 1)
        next_node = await ApprovalEngine._get_node_by_order(db, instance.workflow_id, next_order)

        if next_node:
            next_assignee = await ApprovalEngine._resolve_assignee(db, doc, next_node)
            next_task = ApprovalTask(
                instance_id=instance.id,
                node_id=next_node.id,
                assignee_id=next_assignee,
                status="PENDING",
                created_at=datetime.now(timezone.utc)
            )
            db.add(next_task)
            instance.current_node_id = next_node.id
        else:
            # 全部节点审批完毕，单据终审通过！
            instance.status = "COMPLETED"
            instance.end_time = datetime.now(timezone.utc)
            instance.current_node_id = None
            doc.status = "APPROVED"

    @staticmethod
    async def _handle_reject(
        db: AsyncSession,
        instance: ApprovalInstance,
        task: ApprovalTask,
        doc: FinancialDocument,
        operator_id: int,
        req: ApprovalActionReq
    ):
        """处理驳回逻辑：单据打回 REJECTED，实例终结 TERMINATED"""
        task.status = "REJECTED"
        task.end_time = datetime.now(timezone.utc)
        task.comment = req.comment

        instance.status = "TERMINATED"
        instance.end_time = datetime.now(timezone.utc)
        doc.status = "REJECTED"

        log = WorkflowStatusLog(
            instance_id=instance.id,
            task_id=task.id,
            operator_id=operator_id,
            action="REJECT",
            comment=req.comment
        )
        db.add(log)

    @staticmethod
    async def _handle_transfer(
        db: AsyncSession,
        instance: ApprovalInstance,
        task: ApprovalTask,
        operator_id: int,
        req: ApprovalActionReq
    ):
        """处理转交逻辑：防死循环转交检查，将任务转给 target_user_id"""
        if not req.target_user_id:
            raise ValueError("转交操作必须指定目标转交人！")
        if req.target_user_id == operator_id:
            raise ValueError("转交目标人不能是本人！")

        # 防环形死循环转交检查：遍历当前实例该节点的所有历史任务
        stmt = select(ApprovalTask).where(
            and_(ApprovalTask.instance_id == instance.id, ApprovalTask.node_id == task.node_id)
        )
        res = await db.execute(stmt)
        history_tasks = res.scalars().all()
        visited_users = {t.assignee_id for t in history_tasks}
        if req.target_user_id in visited_users:
            raise ValueError(f"用户[{req.target_user_id}]此前已参与过本节点审批或转交，禁止循环转交！")

        task.status = "TRANSFERRED"
        task.end_time = datetime.now(timezone.utc)
        task.comment = f"已转交给用户[{req.target_user_id}]：{req.comment}"

        # 派生新任务
        new_task = ApprovalTask(
            instance_id=instance.id,
            node_id=task.node_id,
            assignee_id=req.target_user_id,
            status="PENDING",
            created_at=datetime.now(timezone.utc)
        )
        db.add(new_task)

        log = WorkflowStatusLog(
            instance_id=instance.id,
            task_id=task.id,
            operator_id=operator_id,
            action="TRANSFER",
            comment=req.comment,
            extra_data={"target_user_id": req.target_user_id}
        )
        db.add(log)

    @staticmethod
    async def _handle_add_sign(
        db: AsyncSession,
        instance: ApprovalInstance,
        task: ApprovalTask,
        operator_id: int,
        req: ApprovalActionReq
    ):
        """
        处理加签逻辑 (会签协查)：
        - 前置加签 (BEFORE)：当前任务挂起为 ADD_SIGN，生成加签人待办；加签人审批通过后唤醒当前任务重新为 PENDING；
        - 后置加签 (AFTER)：当前审批人直接放行通过 (APPROVED)，在下一常规节点前插入加签人待办。
        """
        if not req.target_user_id:
            raise ValueError("加签操作必须指定目标加签人！")
        if req.target_user_id == operator_id:
            raise ValueError("加签人不能是本人！")
        if not req.add_sign_type:
            raise ValueError("加签操作必须指定加签类型 (BEFORE 或 AFTER)！")

        current_node = await db.get(ApprovalWorkflowNode, task.node_id)
        current_order = current_node.node_order if current_node else 1

        if req.add_sign_type == AddSignTypeEnum.BEFORE:
            # 1. 前置加签：当前任务挂起
            task.status = "ADD_SIGN"
            task.comment = f"发起前置加签给用户[{req.target_user_id}]：{req.comment}"

            add_task = ApprovalTask(
                instance_id=instance.id,
                node_id=task.node_id,
                assignee_id=req.target_user_id,
                status="PENDING",
                created_at=datetime.now(timezone.utc),
                extra_data={"parent_task_id": task.id, "add_sign_type": "BEFORE"}
            )
            db.add(add_task)

        elif req.add_sign_type == AddSignTypeEnum.AFTER:
            # 2. 后置加签：当前人通过，加签人跟进
            task.status = "APPROVED"
            task.end_time = datetime.now(timezone.utc)
            task.comment = f"同意并通过，并后置加签给用户[{req.target_user_id}]：{req.comment}"

            add_task = ApprovalTask(
                instance_id=instance.id,
                node_id=task.node_id,
                assignee_id=req.target_user_id,
                status="PENDING",
                created_at=datetime.now(timezone.utc),
                extra_data={"add_sign_type": "AFTER", "resume_node_order": current_order + 1}
            )
            db.add(add_task)

        log = WorkflowStatusLog(
            instance_id=instance.id,
            task_id=task.id,
            operator_id=operator_id,
            action="ADD_SIGN",
            comment=req.comment,
            extra_data={"target_user_id": req.target_user_id, "add_sign_type": req.add_sign_type.value}
        )
        db.add(log)

    @staticmethod
    async def _handle_revoke(
        db: AsyncSession,
        instance: ApprovalInstance,
        task: ApprovalTask,
        doc: FinancialDocument,
        operator_id: int,
        req: ApprovalActionReq
    ):
        """
        处理经办人主动撤回：
        1. 必须是单据发起人才能撤回；
        2. 首节点未被审批（历史没有任何已通过的任务）才允许撤回；若已有审批人放行，禁止撤回。
        """
        stmt = select(ApprovalTask).where(
            and_(ApprovalTask.instance_id == instance.id, ApprovalTask.status == "APPROVED")
        )
        res = await db.execute(stmt)
        approved_tasks = res.scalars().all()
        if approved_tasks:
            raise ValueError("单据已进入流转审批阶段且已有节点同意，禁止经办人单方撤回！如需修改请联系审批人驳回。")

        task.status = "REJECTED"
        task.comment = f"经办人主动撤回：{req.comment}"
        task.end_time = datetime.now(timezone.utc)

        instance.status = "CANCELLED"
        instance.end_time = datetime.now(timezone.utc)
        doc.status = "CANCELLED"

        log = WorkflowStatusLog(
            instance_id=instance.id,
            task_id=task.id,
            operator_id=operator_id,
            action="REVOKE",
            comment=req.comment
        )
        db.add(log)

    @staticmethod
    async def _get_node_by_order(db: AsyncSession, workflow_id: int, order: int) -> Optional[ApprovalWorkflowNode]:
        query = select(ApprovalWorkflowNode).where(
            and_(ApprovalWorkflowNode.workflow_id == workflow_id, ApprovalWorkflowNode.node_order == order)
        )
        res = await db.execute(query)
        return res.scalars().first()

    @staticmethod
    async def _resolve_assignee(db: AsyncSession, doc: FinancialDocument, node: ApprovalWorkflowNode) -> int:
        """根据节点规则解析审批人 ID"""
        if node.user_id:
            return node.user_id
        if node.approver_type == "MANAGER":
            # 查找发起人的主管 ID
            applicant = await db.get(FinancialDocument, doc.id)
            if applicant and hasattr(applicant, "applicant") and applicant.applicant and applicant.applicant.manager_id:
                return applicant.applicant.manager_id
            return 2 # 兜底测试经理 ID
        elif node.approver_type == "ROLE":
            if node.role_code == "CFO":
                return 4 # CFO 测试账号
            elif node.role_code == "FINANCE":
                return 3 # 财务审核员 测试账号
            return 2
        return 1
