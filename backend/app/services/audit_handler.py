"""
backend/app/services/audit_handler.py
领域事件消费者：接收并处理 AuditCompletedEvent
连接跨进程/Celery/Local 编排流出的 AuditResultDTO，负责调用 Service 层的落库事务与状态机流转
"""
import logging
from typing import Set
from app.core.database import AsyncSessionLocal
from app.services.audit_service import AuditService
from engines.contract.events import AuditCompletedEvent
from engines.contract.event_bus import event_bus

logger = logging.getLogger("app.audit_handler")

class AuditCompletionHandler:
    """处理 AuditCompletedEvent 领域事件的统一消费处理器 (Domain Event Consumer - 带幂等防线)"""

    # 消费者内存/分布式幂等记录集合 (在生产集群对应 processed_events 表与 Redis 分布式锁/Key)
    _processed_event_ids: Set[str] = set()
    _processed_task_versions: Set[str] = set()

    @classmethod
    def is_processed(cls, event_id: str, task_id: str, audit_version: int = 1) -> bool:
        """检查事件是否已被消费处理过 (依据 event_id 或 (task_id, audit_version))"""
        if event_id and event_id in cls._processed_event_ids:
            return True
        key = f"{task_id}:{audit_version}"
        return key in cls._processed_task_versions

    @classmethod
    def mark_processed(cls, event_id: str, task_id: str, audit_version: int = 1) -> None:
        """标记该事件已成功完成事务消费"""
        if event_id:
            cls._processed_event_ids.add(event_id)
        cls._processed_task_versions.add(f"{task_id}:{audit_version}")

    @classmethod
    async def handle(cls, event: AuditCompletedEvent) -> None:
        """
        响应 AuditCompletedEvent (带消费者幂等安全防线)：
        1. 幂等拦截：判断 event_id 或 (task_id, audit_version) 是否已被消费；
        2. 打开应用层 AsyncSession 会话；
        3. 调用 AuditService 进行原子事务落库；
        4. 由 ApprovalEngine 审批状态机依据审核事实推进单据状态；
        5. 标记幂等完成，杜绝 Celery/Redis 重投导致的重复数据。
        """
        event_id = getattr(event, "event_id", "")
        task_id = event.task_id
        audit_version = getattr(event, "audit_version", 1)

        # 1. 幂等防线拦截
        if cls.is_processed(event_id, task_id, audit_version):
            logger.warning(
                f"[AuditCompletionHandler] 触发幂等拦截！事件[{event_id}] / 任务版本[{task_id}:{audit_version}] "
                f"已消费处理过，忽略本次重复投递！"
            )
            return

        logger.info(f"[AuditCompletionHandler] 接收到任务[{task_id}]审查完毕领域事件(event_id={event_id})，启动事务落库与状态机流转...")
        try:
            async with AsyncSessionLocal() as session:
                service = AuditService(session)
                report = await service.handle_audit_completed(
                    task_id=task_id,
                    document_id=event.document_id,
                    result=event.result,
                    event_id=str(event_id) if event_id else None,
                    audit_version=audit_version
                )
        except Exception as exc:
            logger.exception(f"[AuditCompletionHandler] 处理任务[{task_id}]审核完成领域事件发生致命异常: {exc}")
            raise exc

        # 2. 标记幂等消费 (仅在事务落库成功完成后登记)
        cls.mark_processed(str(event_id), task_id, audit_version)
        if report:
            logger.info(f"[AuditCompletionHandler] 任务[{task_id}]落库与状态机流转完成 (已登记幂等)！")
        else:
            logger.info(f"[AuditCompletionHandler] 任务[{task_id}]重复事件已幂等吸收！")

def register_audit_handler() -> None:
    """显式确保 AuditCompletionHandler 完成且仅完成一次注册"""
    from engines.contract.event_bus import domain_event_bus
    event_bus.register_domain_handler(AuditCompletedEvent, AuditCompletionHandler.handle)
    if hasattr(domain_event_bus, "register_domain_handler"):
        domain_event_bus.register_domain_handler(AuditCompletedEvent, AuditCompletionHandler.handle)

# 启动时向全局 EventBus 与 DomainEventBus 注册领域事件监听
register_audit_handler()

