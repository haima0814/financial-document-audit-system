"""
backend/engines/orchestrator/dispatcher/base.py
统一任务调度器抽象基类
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class TaskDispatcher(ABC):
    @abstractmethod
    async def dispatch_audit_task(
        self,
        task_id: str,
        document_id: Optional[int] = None,
        applicant_id: int = 1,
        tenant_id: int = 1,
        coro: Optional[Any] = None
    ) -> bool:
        """异步派发智能风险审查任务"""
        pass

    @abstractmethod
    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """查询任务执行状态"""
        pass
