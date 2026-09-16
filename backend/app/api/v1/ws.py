"""
backend/app/api/v1/ws.py
WebSocket 实时审计流水线事件总线接口
"""
import json
import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from engines.orchestrator.stream_producer import StreamProducer

logger = logging.getLogger("api.ws")
router = APIRouter(prefix="/ws", tags=["WebSocket 流式推送"])

@router.websocket("/audit/{task_id}")
async def audit_websocket_endpoint(
    websocket: WebSocket,
    task_id: str,
    last_event_id: Optional[str] = Query(None)
):
    """
    WebSocket 连接端点：
    1. 接收客户端连接；
    2. 若携带 last_event_id，重放历史漏收事件 (断点续传)；
    3. 实时监听 StreamProducer 分发的事件，推送至前端；
    4. 监听客户端断开并清理订阅队列。
    """
    await websocket.accept()
    logger.info(f"[WS CONNECTED] task_id={task_id}")

    # 1. 历史事件重放 (支持客户端重连补全)
    history_events = StreamProducer.get_events_since(task_id, last_event_id)
    for ev in history_events:
        await websocket.send_text(ev.model_dump_json())

    # 2. 注册订阅队列
    queue = StreamProducer.subscribe(task_id)

    try:
        while True:
            # 等待新事件或维持心跳
            try:
                # 30秒超时防卡死，定期处理心跳
                envelope = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_text(envelope.model_dump_json())
            except asyncio.TimeoutError:
                # 发送 ping 保活
                await websocket.send_text(json.dumps({"event": "PING", "timestamp": asyncio.get_event_loop().time()}))
    except WebSocketDisconnect:
        logger.info(f"[WS DISCONNECTED] task_id={task_id}")
    except Exception as e:
        logger.warning(f"[WS ERROR] task_id={task_id}: {e}")
    finally:
        StreamProducer.unsubscribe(task_id, queue)
