"""
backend/engines/contract/events.py
WebSocket / Redis Streams 实时流式事件协议定义
"""
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field, ConfigDict

from .agent_role import AgentRoleEnum
from .evidence import EvidenceRecord
from .finding import RiskFindingContract, RiskLevelEnum

class EventTypeEnum(str, Enum):
    """PRD 对齐的 9 种 WebSocket 消息类型"""
    TASK_STARTED = "task_started"             # 分析任务已受理启动
    TASK_PROGRESS = "task_progress"           # 全局分析进度更新 (0-100%)
    NODE_STATUS = "node_status"               # 某个 Agent 节点进入/完成
    EVIDENCE_FOUND = "evidence_found"         # 实时捕获到新的高维证据 (原图红框跳出)
    RISK_DETECTED = "risk_detected"           # 检出新的风险发现项
    ROLE_ERROR = "role_error"                 # 单个 Agent 发生异常 (触发软降级)
    REVIEW_REFLECT = "review_reflect"         # 终审质检进行反思仲裁
    TASK_COMPLETED = "task_completed"         # 全流程分析完毕，报告生成
    TASK_FAILED = "task_failed"               # 任务遭遇不可恢复的崩溃

class BaseEventEnvelope(BaseModel):
    """WebSocket 统一消息外层信封"""
    model_config = ConfigDict(frozen=True)
    
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event: EventTypeEnum = Field(..., description="事件类型")
    task_id: str = Field(..., description="所属分析任务 task_id")
    document_id: int = Field(..., description="单据 ID")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: Dict[str, Any] = Field(default_factory=dict, description="事件具体业务载荷")

# --- 具体的事件业务载荷 DTO (Data Payloads) ---

class TaskProgressPayload(BaseModel):
    percent: int = Field(..., ge=0, le=100, description="总体进度百分比")
    current_stage: str = Field(..., description="当前所处阶段描述")
    active_roles: List[AgentRoleEnum] = Field(default_factory=list, description="正在活跃计算的角色")

class NodeStatusPayload(BaseModel):
    role: AgentRoleEnum = Field(..., description="节点角色")
    status: str = Field(..., description="状态: RUNNING / COMPLETED / FAILED / SKIPPED")
    message: str = Field(..., description="节点状态说明")
    elapsed_ms: int = Field(default=0, description="耗时毫秒")

class EvidenceFoundPayload(BaseModel):
    evidence: EvidenceRecord = Field(..., description="捕获到的证据对象")
    highlight_message: str = Field(..., description="前端高亮气泡文字")

class RiskDetectedPayload(BaseModel):
    finding: RiskFindingContract = Field(..., description="检出的风险项")
    realtime_badge: RiskLevelEnum = Field(..., description="用于前端界面右上角弹出的告警角标")

class TaskCompletedPayload(BaseModel):
    report_id: int = Field(..., description="落库生成的审计报告 report_id")
    overall_risk_level: RiskLevelEnum = Field(..., description="综合风险评级")
    risk_score: int = Field(..., ge=0, le=100, description="风控加权总评分")
    high_risks_count: int = Field(..., description="高危数量")
    medium_risks_count: int = Field(..., description="中危数量")
    low_risks_count: int = Field(..., description="低危数量")
    summary: str = Field(..., description="执行摘要草拟文本")

class TaskFailedPayload(BaseModel):
    error_code: str = Field(..., description="错误编码")
    error_detail: str = Field(..., description="人类可读的错误排查指引")

# =========================================================================
# 领域事件 (Domain Events - 强一致性业务事实，用于跨进程 Celery/EventBus 解耦回流)
# =========================================================================

class DomainEvent(BaseModel):
    """领域事件基类 (要求强可靠投递与业务事务流转)"""
    model_config = ConfigDict(frozen=True)
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AuditCompletedEvent(DomainEvent):
    """
    领域事件：多 Agent 认知推理流水线执行完毕
    由 Celery Worker 或本地协程广播，携带 AuditResultDTO 结果由 Application Service 统一收口落库
    """
    task_id: str = Field(..., description="分析任务 UUID")
    document_id: int = Field(..., description="业务单据 ID")
    audit_version: int = Field(default=1, description="单据审查版本快照号")
    result: Any = Field(..., description="标准化不可变审计结果 DTO (AuditResultDTO)")
