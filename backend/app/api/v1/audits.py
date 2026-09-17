"""
backend/app/api/v1/audits.py
智能风控体检报告查询与 AI 交互对话接口
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.audit import ReviewReportOut, AnalysisTaskOut, AuditChatReq, AuditChatResp
from app.schemas.auth import TokenPayload
from app.services.auth_service import get_current_user
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audits", tags=["智能审计"])

@router.get("/reports/{document_id}", response_model=ReviewReportOut, summary="获取指定单据的最新风控综合体检报告")
async def get_review_report(
    document_id: int,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = AuditService(db)
    report = await service.get_report_by_document(document_id)
    if not report:
        raise HTTPException(status_code=404, detail="该单据尚未生成风控体检报告")
    return report

@router.get("/reports/by-task/{task_id}", response_model=ReviewReportOut, summary="精准按 task_id 获取审核体检报告")
async def get_review_report_by_task(
    task_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = AuditService(db)
    report = await service.get_report_by_task_id(task_id)
    if not report:
        raise HTTPException(status_code=404, detail="该任务尚未生成风控体检报告")
    return report

@router.get("/tasks/{task_id}", response_model=AnalysisTaskOut, summary="获取指定审查任务执行状态与进度")
async def get_analysis_task(
    task_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = AuditService(db)
    task = await service.get_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="未找到指定审查任务")
    return task

@router.get("/tasks/by-document/{document_id}/latest", response_model=AnalysisTaskOut, summary="按单据ID及当前版本获取最新审核任务")
async def get_latest_task_by_document(
    document_id: int,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = AuditService(db)
    task = await service.get_latest_task_by_document(document_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"未找到单据 [ID={document_id}] 当前版本的有效审查任务")
    return task

@router.post("/chat", response_model=AuditChatResp, summary="基于审查报告与证据链进行智能人机问答")
async def chat_with_audit(
    req: AuditChatReq,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = AuditService(db)
    res = await service.chat_with_audit_context(user_id=current_user.user_id, req=req)
    return res
