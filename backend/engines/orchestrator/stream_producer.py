"""
backend/engines/orchestrator/stream_producer.py
实时审计流式事件分发器 (兼容内存总线与 Redis Streams)
"""
import logging
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from engines.contract.events import BaseEventEnvelope, EventTypeEnum
from engines.contract.settings import engine_settings

logger = logging.getLogger("orchestrator.stream")

class StreamProducer:
    # 内存级事件暂存队列 (供无 Redis 开发环境使用，支持断点续传)
    _memory_streams: Dict[str, List[BaseEventEnvelope]] = {}
    _subscribers: Dict[str, List[asyncio.Queue]] = {}

    @classmethod
    def subscribe(cls, task_id: str) -> asyncio.Queue:
        """为 WebSocket 客户端订阅实时事件队列"""
        q = asyncio.Queue()
        cls._subscribers.setdefault(task_id, []).append(q)
        return q

    @classmethod
    def unsubscribe(cls, task_id: str, q: asyncio.Queue):
        """移除 WebSocket 客户端订阅"""
        if task_id in cls._subscribers and q in cls._subscribers[task_id]:
            cls._subscribers[task_id].remove(q)

    @classmethod
    async def publish_event(
        cls,
        task_id: str,
        document_id: int,
        event_type: EventTypeEnum,
        payload: Dict[str, Any]
    ) -> BaseEventEnvelope:
        """分发标准化审计事件并入列暂存"""
        envelope = BaseEventEnvelope(
            event=event_type,
            task_id=task_id,
            document_id=document_id,
            data=payload
        )

        # 1. 存入内存事件队列
        cls._memory_streams.setdefault(task_id, []).append(envelope)

        # 2. 实时广播给已连接的 WebSocket 订阅者
        for q in cls._subscribers.get(task_id, []):
            await q.put(envelope)

        # 3. 广播给 EventBus 领域事件消费者 (供 SSE、审批流等解耦消费)
        try:
            from engines.contract.event_bus import event_bus
            await event_bus.publish(envelope)
        except Exception as e:
            logger.warning(f"EventBus 发布失败: {e}")

        logger.info(f"[AUDIT STREAM] task={task_id} event={event_type.value}")
        return envelope

    @classmethod
    def get_events_since(cls, task_id: str, last_event_id: Optional[str] = None) -> List[BaseEventEnvelope]:
        """按 last_event_id 断点续传重放未收到的事件"""
        all_events = cls._memory_streams.get(task_id, [])
        if not last_event_id:
            return all_events

        # 找到 last_event_id 之后的所有事件
        found = False
        res = []
        for ev in all_events:
            if found:
                res.append(ev)
            elif ev.event_id == last_event_id:
                found = True
        return res if found else all_events
