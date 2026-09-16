"""
backend/engines/contract/__init__.py
多智能体契约与事件协议包统一导出
"""
from .agent_role import AgentRoleEnum, AgentRoleMeta, AGENT_ROLE_REGISTRY
from .evidence import (
    EvidenceCategoryEnum,
    BoundingBox,
    VisualAnchor,
    CalculationProof,
    PolicyProof,
    EvidenceRecord
)
from .finding import RiskLevelEnum, RiskFindingContract
from .events import (
    EventTypeEnum,
    BaseEventEnvelope,
    TaskProgressPayload,
    NodeStatusPayload,
    EvidenceFoundPayload,
    RiskDetectedPayload,
    TaskCompletedPayload,
    TaskFailedPayload
)
from .master_state import MasterAuditState
from .settings import EngineSettings, ENGINE_CONFIG, engine_settings
from .context import DocumentContext
from .result import AuditResultDTO, AgentExecutionStatus, AgentExecutionResult
from .event_bus import EventPublisher, InMemoryEventBus, event_bus

__all__ = [
    # agent_role
    "AgentRoleEnum",
    "AgentRoleMeta",
    "AGENT_ROLE_REGISTRY",
    # evidence
    "EvidenceCategoryEnum",
    "BoundingBox",
    "VisualAnchor",
    "CalculationProof",
    "PolicyProof",
    "EvidenceRecord",
    # finding
    "RiskLevelEnum",
    "RiskFindingContract",
    # events
    "EventTypeEnum",
    "BaseEventEnvelope",
    "TaskProgressPayload",
    "NodeStatusPayload",
    "EvidenceFoundPayload",
    "RiskDetectedPayload",
    "TaskCompletedPayload",
    "TaskFailedPayload",
    # master_state
    "MasterAuditState",
    # settings
    "EngineSettings",
    "ENGINE_CONFIG",
    "engine_settings",
]
