"""
backend/app/api/v1/sse.py
基于 SSE (Server-Sent Events) 的智能审核流水线单向实时事件流
实现轻量级、原生支持断点重连 (Last-Event-ID) 的事件推送通道
"""
import asyncio
import json
import logging
from typing import Optional
from fastapi import APIRouter, Request, Header
from fastapi.responses import StreamingResponse

from engines.contract.event_bus import event_bus
from engines.contract.events import BaseEventEnvelope

router = APIRouter(prefix="/audits", tags=["智能审计-SSE"])
logger = logging.getLogger("api.sse")

@router.get("/events/{task_id}", summary="单据智能审查流水线 SSE 实时事件流 (单向推流/断线重连)")
async def audit_events_sse(
    task_id: str,
    request: Request,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID")
):
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
