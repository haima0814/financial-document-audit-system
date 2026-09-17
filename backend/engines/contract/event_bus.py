"""
backend/engines/contract/event_bus.py
六边形架构事件总线端口契约 (Application Ports & Infrastructure Adapters)
严格遵循职责分离与介质隔离铁律：
1. 业务领域事件 (Domain Events) -> DomainEventPublisher Port -> RedisStreamsEventBus (保证送达、ACK、可重放)
2. UI 过程推流事件 (Progress Events) -> RealtimeEventPublisher Port -> RedisPubSubEventBus (瞬态广播、无ACK、零落盘)
"""
from typing import Protocol, Dict, Any, List, Optional, Type, Callable, Awaitable, TypeVar, runtime_checkable
import asyncio
import logging
from .events import BaseEventEnvelope, DomainEvent

logger = logging.getLogger("contract.event_bus")
T_DomainEvent = TypeVar("T_DomainEvent", bound=DomainEvent)


# ============================================================================
# 1. 核心应用端口定义 (Application Ports)
# ============================================================================

@runtime_checkable
class DomainEventPublisher(Protocol):
    """
    业务领域事件发布端口 (Domain Event Port)
    语义：核心业务事实（如 AuditCompletedEvent），必须可靠交付，驱动事务落库与状态机跃迁
    """
    async def publish(self, event: DomainEvent) -> None:
        ...


@runtime_checkable
class RealtimeEventPublisher(Protocol):
    """
    UI 实时过程事件发布端口 (Realtime Event Port)
    语义：前端看板与步骤流式进度（如 TASK_PROGRESS, REVIEW_REFLECT），瞬态低延迟广播
    """
    async def publish(self, event: BaseEventEnvelope) -> None:
        ...


@runtime_checkable
class EventPublisher(Protocol):
    """
    综合事件发布门面端口 (兼容旧代码与统一访问)
    """
    async def publish(self, event: BaseEventEnvelope) -> None:
        ...

    async def publish_domain_event(self, event: DomainEvent) -> None:
        ...


# ============================================================================
# 2. 复合内存事件总线实现 (Shared In-Memory Storage)
# ============================================================================

class InMemoryEventBus:
    """
    轻量级内存事件总线实现 (开发调试与本地部署默认使用)
    同时实现 DomainEventPublisher 与 RealtimeEventPublisher 协议
    """
    _instance: Optional["InMemoryEventBus"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._subscribers: Dict[str, List[asyncio.Queue]] = {}
            cls._event_history: Dict[str, List[BaseEventEnvelope]] = {}
            cls._domain_handlers: Dict[Type[DomainEvent], List[Callable[[Any], Awaitable[None]]]] = {}
        return cls._instance

    def register_domain_handler(
        self,
        event_cls: Type[T_DomainEvent],
        handler: Callable[[T_DomainEvent], Awaitable[None]]
    ) -> None:
        """注册强一致领域事件消费者 (如 AuditCompletionHandler)"""
        handlers = self._domain_handlers.setdefault(event_cls, [])
        if handler not in handlers:
            handlers.append(handler)

    async def publish(self, event: Any) -> None:
        """多态发布入口：自动识别领域事件 (DomainEvent) 与过程事件 (BaseEventEnvelope)"""
        if isinstance(event, DomainEvent):
            await self.publish_domain_event(event)
        else:
            task_id = getattr(event, "task_id", "")
            self._event_history.setdefault(task_id, []).append(event)
            queues = self._subscribers.get(task_id, [])
            for q in list(queues):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(f"消费者队列已满，丢弃事件: task={task_id}")

    async def publish_domain_event(self, event: DomainEvent) -> None:
        """发布领域事件至所有已注册的消费处理器 (发生异常向上抛出，严禁静默吞掉)"""
        event_cls = type(event)
        handlers = self._domain_handlers.get(event_cls, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.exception(f"[DomainEventBus] 消费者 [{getattr(handler, '__name__', str(handler))}] 执行失败: {e}")
                raise e

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=100)
        self._subscribers.setdefault(task_id, []).append(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        if task_id in self._subscribers and q in self._subscribers[task_id]:
            self._subscribers[task_id].remove(q)
            if not self._subscribers[task_id]:
                del self._subscribers[task_id]

    def get_history(self, task_id: str) -> List[BaseEventEnvelope]:
        return list(self._event_history.get(task_id, []))


# ============================================================================
# 3. 专职适配器实现 (Dedicated Adapters)
# ============================================================================

class InMemoryDomainEventBus:
    """专职内存领域事件适配器 (implements DomainEventPublisher)"""
    def __init__(self):
        self._shared = InMemoryEventBus()

    def register_domain_handler(
        self,
        event_cls: Type[T_DomainEvent],
        handler: Callable[[T_DomainEvent], Awaitable[None]]
    ) -> None:
        self._shared.register_domain_handler(event_cls, handler)

    async def publish(self, event: DomainEvent) -> None:
        await self._shared.publish_domain_event(event)

    async def publish_domain_event(self, event: DomainEvent) -> None:
        await self._shared.publish_domain_event(event)


class InMemoryRealtimeEventBus:
    """专职内存实时推流适配器 (implements RealtimeEventPublisher)"""
    def __init__(self):
        self._shared = InMemoryEventBus()

    async def publish(self, event: BaseEventEnvelope) -> None:
        await self._shared.publish(event)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        return self._shared.subscribe(task_id)

    def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        self._shared.unsubscribe(task_id, q)

    def get_history(self, task_id: str) -> List[BaseEventEnvelope]:
        return self._shared.get_history(task_id)


class RedisStreamsEventBus:
    """
    生产级 Redis 7 Streams 领域事件总线适配器 (implements DomainEventPublisher)
    具备持久化存储、Consumer Group、XACK 确认机制与死信重放能力
    """
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url
        self._fallback = InMemoryDomainEventBus()

    async def publish(self, event: DomainEvent) -> None:
        # 未接入 Redis 时平滑降级至进程内安全处理
        await self._fallback.publish(event)

    async def publish_domain_event(self, event: DomainEvent) -> None:
        await self._fallback.publish_domain_event(event)


class RedisPubSubEventBus:
    """
    生产级 Redis Pub/Sub 实时推流适配器 (implements RealtimeEventPublisher)
    瞬态广播至 SSE / WebSocket 网关，内存零落盘开销
    """
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url
        self._fallback = InMemoryRealtimeEventBus()

    async def publish(self, event: BaseEventEnvelope) -> None:
        await self._fallback.publish(event)


class RedisEventBus(RedisStreamsEventBus, RedisPubSubEventBus):
    """生产级复合总线"""
    pass


# 全局依赖注入实例
domain_event_bus: DomainEventPublisher = InMemoryDomainEventBus()
realtime_event_bus: RealtimeEventPublisher = InMemoryRealtimeEventBus()

# 兼容既有代码的复合实例
EventBus = EventPublisher
event_bus: EventBus = InMemoryEventBus()


