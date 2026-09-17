"""
backend/engines/contract/events.py
WebSocket / Redis Streams 实时流式事件协议定义
"""
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field, ConfigDict, model_validator

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

class TaskStartedPayload(BaseModel):
    current_stage: str = Field(default="STAGE_1_PARSED", description="当前所处阶段描述")
    items_count: int = Field(default=0, description="明细行项数")
    invoices_count: int = Field(default=0, description="关联发票张数")
    execution_plan: Optional[Dict[str, Any]] = Field(default=None, description="审核执行规划快照")

class TaskProgressPayload(BaseModel):
    percent: int = Field(..., ge=0, le=100, description="总体进度百分比")
    current_stage: Optional[str] = Field(default=None, description="当前所处阶段描述")
    stage: Optional[str] = Field(default=None, description="兼容旧版字段")
    active_roles: List[AgentRoleEnum] = Field(default_factory=list, description="正在活跃计算的角色")
    explanation: Optional[str] = Field(default=None, description="阶段执行说明")
    planned_tasks: List[str] = Field(default_factory=list, description="规划的任务列表")
    total_capabilities: int = Field(default=0, description="规划能力集总数")
    findings_count: int = Field(default=0, description="检出的风险项数量")
    verified_count: int = Field(default=0, description="复核消歧后有效风险项数")
    reflection_applied: Optional[bool] = Field(default=None, description="是否触发了反思消歧")
    agent_results: List[Dict[str, Any]] = Field(default_factory=list, description="各智能体执行结果简报")

    @model_validator(mode="before")
    @classmethod
    def _fill_stage_compat(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "stage" in data and not data.get("current_stage"):
                data["current_stage"] = data["stage"]
            elif "current_stage" in data and not data.get("stage"):
                data["stage"] = data["current_stage"]
        return data

    def model_post_init(self, __context: Any) -> None:
        if not self.stage and self.current_stage:
            self.stage = self.current_stage
        if not self.current_stage and self.stage:
            self.current_stage = self.stage

class NodeStatusPayload(BaseModel):
    role: AgentRoleEnum = Field(..., description="节点角色")
    status: str = Field(..., description="状态: PLANNED / RUNNING / SUCCESS / DEGRADED / FAILED / TIMEOUT / SKIPPED")
    message: str = Field(default="", description="节点状态说明")
    elapsed_ms: int = Field(default=0, description="耗时毫秒 (兼容字段)")
    duration_ms: int = Field(default=0, description="耗时毫秒")
    findings_count: int = Field(default=0, description="检出的风险项数量")
    reason: Optional[str] = Field(default=None, description="降级/跳过/失败原因")
    source: Optional[str] = Field(default="UNKNOWN", description="执行来源: DETERMINISTIC / LLM_INFERENCE / MOCK / UNKNOWN")
    capabilities_run: List[str] = Field(default_factory=list, description="实际执行的能力集列表")

    def model_post_init(self, __context: Any) -> None:
        if not self.duration_ms and self.elapsed_ms:
            self.duration_ms = self.elapsed_ms
        if not self.elapsed_ms and self.duration_ms:
            self.elapsed_ms = self.duration_ms

class EvidenceFoundPayload(BaseModel):
    evidence: EvidenceRecord = Field(..., description="捕获到的证据对象")
    highlight_message: str = Field(..., description="前端高亮气泡文字")

class RiskDetectedPayload(BaseModel):
    finding: RiskFindingContract = Field(..., description="检出的风险项")
    realtime_badge: RiskLevelEnum = Field(..., description="用于前端界面右上角弹出的告警角标")

class ReviewReflectPayload(BaseModel):
    current_stage: str = Field(default="STAGE_3_REVIEW_REFLECT", description="当前所处阶段描述")
    stage: Optional[str] = Field(default="STAGE_3_REVIEW_REFLECT", description="兼容旧版字段")
    disambiguated_count: int = Field(default=0, description="完成消歧核减的风险项数量")
    reflection_logs: List[Dict[str, Any]] = Field(default_factory=list, description="二阶反思消歧审计日志")

    def model_post_init(self, __context: Any) -> None:
        if not self.stage:
            self.stage = self.current_stage

class TaskCompletedPayload(BaseModel):
    report_id: int = Field(..., description="落库生成的审计报告 report_id")
    task_id: Optional[str] = Field(default=None, description="任务ID")
    percent: int = Field(default=100, description="完成百分比")
    overall_risk_level: str = Field(..., description="综合风险评级")
    risk_score: int = Field(..., ge=0, le=100, description="风控加权总评分")
    final_score: int = Field(default=100, ge=0, le=100, description="综合体检得分")
    high_risks_count: int = Field(..., description="高危数量")
    medium_risks_count: int = Field(..., description="中危数量")
    low_risks_count: int = Field(..., description="低危数量")
    high_count: Optional[int] = Field(default=None, description="兼容旧版字段")
    medium_count: Optional[int] = Field(default=None, description="兼容旧版字段")
    low_count: Optional[int] = Field(default=None, description="兼容旧版字段")
    summary: str = Field(default="", description="执行摘要草拟文本")
    audit_completeness: str = Field(default="COMPLETE", description="审核完整度状态")
    decision: Optional[Dict[str, Any]] = Field(default=None, description="审批流转决策摘要")
    workflow_initialized: Optional[bool] = Field(default=True, description="审批流是否成功初始化")
    workflow_error: Optional[str] = Field(default=None, description="审批流初始化异常摘要")

    def model_post_init(self, __context: Any) -> None:
        if self.high_count is None:
            self.high_count = self.high_risks_count
        if self.medium_count is None:
            self.medium_count = self.medium_risks_count
        if self.low_count is None:
            self.low_count = self.low_risks_count

class TaskFailedPayload(BaseModel):
    error_code: str = Field(..., description="错误编码")
    error_detail: str = Field(..., description="人类可读的错误排查指引")

EVENT_PAYLOAD_SCHEMA_MAP = {
    EventTypeEnum.TASK_STARTED: TaskStartedPayload,
    EventTypeEnum.TASK_PROGRESS: TaskProgressPayload,
    EventTypeEnum.NODE_STATUS: NodeStatusPayload,
    EventTypeEnum.EVIDENCE_FOUND: EvidenceFoundPayload,
    EventTypeEnum.RISK_DETECTED: RiskDetectedPayload,
    EventTypeEnum.REVIEW_REFLECT: ReviewReflectPayload,
    EventTypeEnum.TASK_COMPLETED: TaskCompletedPayload,
    EventTypeEnum.TASK_FAILED: TaskFailedPayload,
}

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
