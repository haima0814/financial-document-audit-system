"""
backend/app/models/__init__.py
统一导出所有 ORM 实体数据模型
"""
from .user import User, Role, UserRole
from .document import (
    FinancialDocument,
    DocumentLineItem,
    DocumentAttachment,
    DocumentVersion,
    DocumentStatusLog
)
from .invoice import AttachmentParseResult, InvoiceRecord
from .policy import PolicyUnit, PolicyChunk
from .supplier import SupplierProfile, MarketPriceReference
from .workflow import (
    ApprovalWorkflow,
    ApprovalWorkflowNode,
    ApprovalInstance,
    ApprovalTask,
    WorkflowStatusLog
)
from .audit import (
    AnalysisTask,
    ReviewReport,
    RiskFinding,
    AuditChatSession,
    AuditChatMessage
)

__all__ = [
    # user
    "User",
    "Role",
    "UserRole",
    # document
    "FinancialDocument",
    "DocumentLineItem",
    "DocumentAttachment",
    "DocumentVersion",
    "DocumentStatusLog",
    # invoice
    "AttachmentParseResult",
    "InvoiceRecord",
    # policy
    "PolicyUnit",
    "PolicyChunk",
    # supplier
    "SupplierProfile",
    "MarketPriceReference",
    # workflow
    "ApprovalWorkflow",
    "ApprovalWorkflowNode",
    "ApprovalInstance",
    "ApprovalTask",
    "WorkflowStatusLog",
    # audit
    "AnalysisTask",
    "ReviewReport",
    "RiskFinding",
    "AuditChatSession",
    "AuditChatMessage",
]
