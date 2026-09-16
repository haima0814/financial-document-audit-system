"""
backend/app/api/v1/sse.py
基于 SSE (Server-Sent Events) 的智能审核流水线单向实时事件流
实现轻量级、原生支持断点重连 (Last-Event-ID) 的事件推送通道
"""
import asyncio
import json
import logging
from typing import Optional
from fastapi import APIRouter, Request, Header, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.schemas.auth import TokenPayload
from app.services.auth_service import get_current_user
from app.models.audit import AnalysisTask
from app.models.document import FinancialDocument
from engines.contract.event_bus import event_bus
from engines.contract.events import BaseEventEnvelope

router = APIRouter(prefix="/audits", tags=["智能审计-SSE"])
logger = logging.getLogger("api.sse")

@router.get("/events/{task_id}", summary="单据智能审查流水线 SSE 实时事件流 (单向推流/断线重连)")
async def audit_events_sse(
    task_id: str,
    request: Request,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # 鉴权与租户数据安全校验
    is_privileged = any(r in (current_user.roles or []) for r in ["ADMIN", "MANAGER", "FINANCE", "CFO"])
    task_stmt = select(AnalysisTask).where(AnalysisTask.task_id == task_id)
    task_obj = (await db.execute(task_stmt)).scalars().first()

    if task_obj:
        doc = await db.get(FinancialDocument, task_obj.document_id)
        if doc and doc.applicant_id != current_user.user_id and not is_privileged:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该单据的实时审查事件流")
    else:
        # 若数据库尚未落库，但内存事件总线存在历史或用户具备管理权限，则允许推流；否则 404
        if not (is_privileged or event_bus.get_history(task_id)):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="指定审查任务不存在")
    """
    客户端建立 SSE 长连接以实时监听指定任务的流水线演进状态：
    - Stage 1: TASK_STARTED
    - Stage 2: TASK_PROGRESS (70%)
    - Stage 3: REVIEW_REFLECT / STAGE_3_REVIEW_DONE (85%)
    - Stage 4 / 落库: TASK_COMPLETED (100%)
    支持标准 Last-Event-ID 请求头重连历史回放。
    """
    async def event_generator():
        queue = event_bus.subscribe(task_id)
        try:
            # 1. 断点续传历史回放
            history = event_bus.get_history(task_id)
            if history:
                replay_needed = False
                for ev in history:
                    should_yield = False
                    if last_event_id:
                        if replay_needed:
                            should_yield = True
                        elif ev.event_id == last_event_id:
                            replay_needed = True
                    else:
                        should_yield = True

                    if should_yield:
                        data_json = json.dumps(ev.model_dump(mode="json"), ensure_ascii=False)
                        yield f"id: {ev.event_id}\nevent: {ev.event.value}\ndata: {data_json}\n\n"
                        if ev.event.value in ["task_completed", "task_failed"]:
                            yield f"event: close\ndata: {{\"task_id\": \"{task_id}\", \"status\": \"finished\"}}\n\n"
                            return

            # 2. 持续消费实时事件
            while True:
                if await request.is_disconnected():
                    logger.info(f"[SSE] 客户端主动断开连接: task_id={task_id}")
                    break

                try:
                    ev: BaseEventEnvelope = await asyncio.wait_for(queue.get(), timeout=15.0)
                    data_json = json.dumps(ev.model_dump(mode="json"), ensure_ascii=False)
                    yield f"id: {ev.event_id}\nevent: {ev.event.value}\ndata: {data_json}\n\n"

                    # 终态事件后优雅关闭推流
                    if ev.event.value in ["task_completed", "task_failed"]:
                        yield f"event: close\ndata: {{\"task_id\": \"{task_id}\", \"status\": \"finished\"}}\n\n"
                        break
                except asyncio.TimeoutError:
                    # 发送保活心跳注释行，防止网关 504 Gateway Timeout
                    yield ": ping\n\n"

        finally:
            event_bus.unsubscribe(task_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
