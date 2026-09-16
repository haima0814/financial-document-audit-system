"""
backend/app/repositories/__init__.py
统一导出所有仓储实例
"""
from .base import BaseRepository
from .document_repo import document_repo, DocumentRepository
from .invoice_repo import invoice_repo, InvoiceRepository
from .workflow_repo import workflow_repo, WorkflowRepository
from .audit_repo import audit_repo, AuditRepository

__all__ = [
    "BaseRepository",
    "document_repo",
    "DocumentRepository",
    "invoice_repo",
    "InvoiceRepository",
    "workflow_repo",
    "WorkflowRepository",
    "audit_repo",
    "AuditRepository",
]
