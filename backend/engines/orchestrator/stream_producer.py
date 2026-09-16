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
        payload: Any
    ) -> BaseEventEnvelope:
        """分发标准化审计事件并入列暂存 (支持 Pydantic Payload DTO 与 dict 强校验规范化)"""
        from pydantic import BaseModel
        from engines.contract.events import EVENT_PAYLOAD_SCHEMA_MAP

        if isinstance(payload, BaseModel):
            data_dict = payload.model_dump(mode="json")
        elif isinstance(payload, dict):
            # 兼容历史与现有调用中的字段命名漂移
            p_copy = dict(payload)
            if "stage" in p_copy and "current_stage" not in p_copy:
                p_copy["current_stage"] = p_copy["stage"]
            if "current_stage" in p_copy and "stage" not in p_copy:
                p_copy["stage"] = p_copy["current_stage"]
            if "high_count" in p_copy and "high_risks_count" not in p_copy:
                p_copy["high_risks_count"] = p_copy["high_count"]
            if "high_risks_count" in p_copy and "high_count" not in p_copy:
                p_copy["high_count"] = p_copy["high_risks_count"]
            if "medium_count" in p_copy and "medium_risks_count" not in p_copy:
                p_copy["medium_risks_count"] = p_copy["medium_count"]
            if "medium_risks_count" in p_copy and "medium_count" not in p_copy:
                p_copy["medium_count"] = p_copy["medium_risks_count"]
            if "low_count" in p_copy and "low_risks_count" not in p_copy:
                p_copy["low_risks_count"] = p_copy["low_count"]
            if "low_risks_count" in p_copy and "low_count" not in p_copy:
                p_copy["low_count"] = p_copy["low_risks_count"]
            if "elapsed_ms" in p_copy and "duration_ms" not in p_copy:
                p_copy["duration_ms"] = p_copy["elapsed_ms"]
            if "duration_ms" in p_copy and "elapsed_ms" not in p_copy:
                p_copy["elapsed_ms"] = p_copy["duration_ms"]
            if "overall_risk_level" in p_copy and hasattr(p_copy["overall_risk_level"], "value"):
                p_copy["overall_risk_level"] = p_copy["overall_risk_level"].value

            schema_cls = EVENT_PAYLOAD_SCHEMA_MAP.get(event_type)
            if schema_cls:
                dto = schema_cls.model_validate(p_copy)
                data_dict = dto.model_dump(mode="json")
            else:
                data_dict = p_copy
        else:
            data_dict = dict(payload)

        envelope = BaseEventEnvelope(
            event=event_type,
            task_id=task_id,
            document_id=document_id,
            data=data_dict
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
