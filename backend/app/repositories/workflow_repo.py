"""
backend/app/repositories/workflow_repo.py
工作流与待办任务仓储
"""
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from app.models.workflow import ApprovalWorkflow, ApprovalWorkflowNode, ApprovalInstance, ApprovalTask, WorkflowStatusLog
from .base import BaseRepository

class WorkflowRepository(BaseRepository[ApprovalWorkflow]):
    def __init__(self):
        super().__init__(ApprovalWorkflow)

    async def get_by_code(self, db: AsyncSession, workflow_code: str) -> Optional[ApprovalWorkflow]:
        stmt = (
            select(ApprovalWorkflow)
            .where(ApprovalWorkflow.workflow_code == workflow_code)
            .options(selectinload(ApprovalWorkflow.nodes))
        )
        res = await db.execute(stmt)
        return res.scalars().first()

    async def get_instance_by_document(self, db: AsyncSession, document_id: int) -> Optional[ApprovalInstance]:
        stmt = (
            select(ApprovalInstance)
            .where(ApprovalInstance.document_id == document_id)
            .options(selectinload(ApprovalInstance.tasks))
        )
        res = await db.execute(stmt)
        return res.scalars().first()

    async def get_pending_tasks_for_user(self, db: AsyncSession, user_id: int) -> List[ApprovalTask]:
        """获取指定审批人名下所有待处理的任务卡片"""
        stmt = (
            select(ApprovalTask)
            .where(and_(ApprovalTask.assignee_id == user_id, ApprovalTask.status == "PENDING"))
            .options(selectinload(ApprovalTask.instance), selectinload(ApprovalTask.node))
            .order_by(ApprovalTask.created_at.desc())
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

workflow_repo = WorkflowRepository()
