"""
backend/app/api/v1/audits.py
智能风控体检报告查询与 AI 交互对话接口
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.audit import ReviewReportOut, AuditChatReq, AuditChatResp
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

@router.post("/chat", response_model=AuditChatResp, summary="基于审查报告与证据链进行智能人机问答")
async def chat_with_audit(
    req: AuditChatReq,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = AuditService(db)
    res = await service.chat_with_audit_context(user_id=current_user.user_id, req=req)
    return res
