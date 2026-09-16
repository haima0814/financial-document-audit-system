"""
backend/app/api/v1/approvals.py
审批中心接口 (待办列表、动作处理、流转进度与审计留痕)
"""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.approval import ApprovalActionReq
from app.schemas.auth import TokenPayload
from app.services.auth_service import get_current_user
from app.services.approval_service import ApprovalService

router = APIRouter(prefix="/approvals", tags=["审批中心"])

@router.get("/tasks/pending", summary="获取当前登录用户的待办审批任务列表")
async def get_pending_tasks(
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = ApprovalService(db)
    tasks = await service.get_user_pending_tasks(user_id=current_user.user_id)
    return {"tasks": tasks, "total": len(tasks)}

@router.post("/tasks/{task_id}/action", summary="执行审批动作 (同意/驳回/转交/加签/撤回)")
async def handle_approval_action(
    task_id: int,
    req: ApprovalActionReq,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = ApprovalService(db)
    try:
        res = await service.handle_task_action(
            task_id=task_id,
            operator_id=current_user.user_id,
            req=req
        )
        await db.commit()
        return res
    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))

@router.get("/instances/{document_id}", summary="获取单据对应的审批流实例与流转轨迹")
async def get_instance_by_document(
    document_id: int,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = ApprovalService(db)
    instance_detail = await service.get_instance_detail(document_id)
    if not instance_detail:
        raise HTTPException(status_code=404, detail="该单据尚未启动审批流程或不存在审批实例")
    return instance_detail
