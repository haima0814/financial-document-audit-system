"""
backend/engines/orchestrator/dispatcher/local_dispatcher.py
基于原生 asyncio.create_task 的本地轻量调度器 (Windows 秒起、零依赖)
"""
import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from .base import TaskDispatcher

logger = logging.getLogger("orchestrator.local_dispatcher")

class LocalTaskInfo:
    def __init__(self, task_id: str, async_task: asyncio.Task):
        self.task_id = task_id
        self.async_task = async_task
        self.status = "RUNNING"
        self.start_time = datetime.now(timezone.utc)
        self.error: Optional[str] = None

class LocalTaskManagerDispatcher(TaskDispatcher):
    _instance: Optional["LocalTaskManagerDispatcher"] = None
    _tasks: Dict[str, LocalTaskInfo] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._tasks = {}
        return cls._instance

    @classmethod
    async def dispatch_audit_task(
        cls,
        task_id: str,
        document_id: Optional[int] = None,
        applicant_id: int = 1,
        tenant_id: int = 1,
        coro: Optional[Any] = None
    ) -> bool:
        """异步派发智能风险审查任务 (支持直接传入协程或通过 document_id 启动)"""
        instance = cls()
        return await instance._dispatch_internal(
            task_id=task_id,
            document_id=document_id,
            applicant_id=applicant_id,
            tenant_id=tenant_id,
            coro=coro
        )

    async def _dispatch_internal(
        self,
        task_id: str,
        document_id: Optional[int] = None,
        applicant_id: int = 1,
        tenant_id: int = 1,
        coro: Optional[Any] = None
    ) -> bool:
        async def _runner():
            task_info = self._tasks.get(task_id)
            try:
                if coro is not None:
                    logger.info(f"[LocalTaskManager] 任务[{task_id}] 执行传入异步审查流水线...")
                    await coro
                else:
                    logger.info(f"[LocalTaskManager] 任务[{task_id}] 启动异步审核 (单据ID: {document_id})...")
                    from engines.orchestrator.master_graph import MasterOrchestrator
                    await MasterOrchestrator.run(
                        task_id=task_id,
                        document_id=document_id,
                        applicant_id=applicant_id,
                        tenant_id=tenant_id
                    )
                if task_info:
                    task_info.status = "COMPLETED"
                logger.info(f"[LocalTaskManager] 任务[{task_id}] 审核成功完成！")
            except Exception as e:
                if task_info:
                    task_info.status = "FAILED"
                    task_info.error = str(e)
                logger.exception(f"[LocalTaskManager] 任务[{task_id}] 崩溃: {e}")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()

        task = loop.create_task(_runner())
        self._tasks[task_id] = LocalTaskInfo(task_id, task)
        return True

    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        task_info = self._tasks.get(task_id)
        if not task_info:
            return {"task_id": task_id, "status": "NOT_FOUND"}
        return {
            "task_id": task_id,
            "status": task_info.status,
            "start_time": task_info.start_time.isoformat(),
            "error": task_info.error
        }

local_dispatcher = LocalTaskManagerDispatcher()
