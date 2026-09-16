"""
backend/app/services/approval_service.py
审批业务门面服务 (提供待办列表、审批历史与动作触发)
"""
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.models.workflow import ApprovalTask, ApprovalInstance, WorkflowStatusLog, ApprovalWorkflowNode
from app.models.document import FinancialDocument
from app.schemas.approval import ApprovalActionReq, ApprovalTaskOut, ApprovalInstanceOut, WorkflowStatusLogOut
from app.services.approval_engine import ApprovalEngine

class ApprovalService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_user_pending_tasks(self, user_id: int) -> List[Dict[str, Any]]:
        """获取指定用户的待办任务列表 (包含单据标题、金额等核心摘要)"""
        stmt = (
            select(ApprovalTask, FinancialDocument, ApprovalWorkflowNode)
            .join(ApprovalInstance, ApprovalTask.instance_id == ApprovalInstance.id)
            .join(FinancialDocument, ApprovalInstance.document_id == FinancialDocument.id)
            .join(ApprovalWorkflowNode, ApprovalTask.node_id == ApprovalWorkflowNode.id)
            .where(
                and_(
                    ApprovalTask.assignee_id == user_id,
                    ApprovalTask.status == "PENDING"
                )
            )
            .order_by(desc(ApprovalTask.created_at))
        )
        res = await self.db.execute(stmt)
        rows = res.all()

        results = []
        for task, doc, node in rows:
            results.append({
                "task_id": task.id,
                "instance_id": task.instance_id,
                "document_id": doc.id,
                "document_no": doc.document_no,
                "document_type": doc.document_type,
                "title": doc.title,
                "total_amount": float(doc.total_amount),
                "node_name": node.node_name,
                "node_order": node.node_order,
                "task_status": task.status,
                "created_at": task.created_at.isoformat() if task.created_at else None
            })
        return results

    async def get_instance_detail(self, document_id: int) -> Optional[Dict[str, Any]]:
        """获取指定单据的审批流程实例详情（包含全部任务节点与日志）"""
        stmt = select(ApprovalInstance).where(ApprovalInstance.document_id == document_id)
        res = await self.db.execute(stmt)
        instance = res.scalars().first()
        if not instance:
            return None

        # 查询所有任务
        tasks_stmt = (
            select(ApprovalTask, ApprovalWorkflowNode)
            .join(ApprovalWorkflowNode, ApprovalTask.node_id == ApprovalWorkflowNode.id)
            .where(ApprovalTask.instance_id == instance.id)
            .order_by(ApprovalTask.id)
        )
        tasks_res = await self.db.execute(tasks_stmt)
        tasks_data = []
        for t, node in tasks_res.all():
            tasks_data.append({
                "id": t.id,
                "node_id": t.node_id,
                "node_name": node.node_name,
                "assignee_id": t.assignee_id,
                "status": t.status,
                "comment": t.comment,
                "extra_data": t.extra_data,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "end_time": t.end_time.isoformat() if t.end_time else None,
            })

        # 查询审批日志
        logs_stmt = select(WorkflowStatusLog).where(
            WorkflowStatusLog.instance_id == instance.id
        ).order_by(WorkflowStatusLog.created_at)
        logs_res = await self.db.execute(logs_stmt)
        logs_data = []
        for l in logs_res.scalars().all():
            logs_data.append({
                "id": l.id,
                "task_id": l.task_id,
                "operator_id": l.operator_id,
                "action": l.action,
                "comment": l.comment,
                "extra_data": l.extra_data,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            })

        return {
            "id": instance.id,
            "workflow_id": instance.workflow_id,
            "document_id": instance.document_id,
            "status": instance.status,
            "current_node_id": instance.current_node_id,
            "start_time": instance.start_time.isoformat() if instance.start_time else None,
            "end_time": instance.end_time.isoformat() if instance.end_time else None,
            "tasks": tasks_data,
            "logs": logs_data
        }

    async def handle_task_action(
        self,
        task_id: int,
        operator_id: int,
        req: ApprovalActionReq
    ) -> Dict[str, Any]:
        """执行具体的审批任务动作"""
        result = await ApprovalEngine.execute_action(
            db=self.db,
            task_id=task_id,
            operator_id=operator_id,
            action_req=req
        )
        return result
