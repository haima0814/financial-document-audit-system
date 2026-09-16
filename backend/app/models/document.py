"""
backend/app/models/document.py
财务单据、明细、版本快照与附件模型
"""
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from decimal import Decimal
from sqlalchemy import String, Integer, Numeric, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base, CompatibleJSONB

class FinancialDocument(Base):
    __tablename__ = "financial_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True) # CORP_PAYMENT, ADVANCE_PAYMENT, BATCH_PAYMENT, EXPENSE_REIMBURSEMENT, TRAVEL_REIMBURSEMENT
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    
    applicant_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    department_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    department_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    
    # 宏观状态: DRAFT, SUBMITTED, IN_REVIEW, PENDING_APPROVAL, APPROVED, REJECTED, CANCELLED
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    
    # 版本快照与并发乐观锁
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    version_lock: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    
    # 人机交互与手动调整标记
    is_manual_override: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_override_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    manual_override_fields: Mapped[Optional[Dict[str, Any]]] = mapped_column(CompatibleJSONB, default=dict)

    # 业务扩展字段 (如出差城市、事由、关联合同号)
    extra_attributes: Mapped[Optional[Dict[str, Any]]] = mapped_column(CompatibleJSONB, default=dict)

    submission_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # 关联
    applicant: Mapped["User"] = relationship("User", foreign_keys=[applicant_id])
    line_items: Mapped[List["DocumentLineItem"]] = relationship("DocumentLineItem", back_populates="document", cascade="all, delete-orphan")
    attachments: Mapped[List["DocumentAttachment"]] = relationship("DocumentAttachment", back_populates="document", cascade="all, delete-orphan")
    versions: Mapped[List["DocumentVersion"]] = relationship("DocumentVersion", back_populates="document", cascade="all, delete-orphan")

class DocumentLineItem(Base):
    __tablename__ = "document_line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("financial_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    expense_type: Mapped[str] = mapped_column(String(64), nullable=False) # 如 "交通费", "住宿费", "餐饮费", "办公用品"
    item_desc: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    invoice_count: Mapped[int] = mapped_column(Integer, default=0)
    invoice_amount_sum: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    
    # 差旅专项属性 (如城市名、开始结束时间)
    city_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    extra_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(CompatibleJSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    document: Mapped["FinancialDocument"] = relationship("FinancialDocument", back_populates="line_items")

class DocumentAttachment(Base):
    __tablename__ = "document_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("financial_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False) # PDF, PNG, JPG, OFD
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True) # SHA-256
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    is_invoice: Mapped[bool] = mapped_column(Boolean, default=True)
    ocr_status: Mapped[str] = mapped_column(String(32), default="PENDING") # PENDING, SUCCESS, FAILED
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    document: Mapped["FinancialDocument"] = relationship("FinancialDocument", back_populates="attachments")

class DocumentVersion(Base):
    """单据不可变全量历史快照 (V1, V2, ...)"""
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("financial_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_action: Mapped[str] = mapped_column(String(64), nullable=False) # SUBMIT, REJECT_EDIT, RESUBMIT
    snapshot_payload: Mapped[Dict[str, Any]] = mapped_column(CompatibleJSONB, nullable=False)
    change_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("document_id", "version_no", name="uq_doc_version"),)
    document: Mapped["FinancialDocument"] = relationship("FinancialDocument", back_populates="versions")

class DocumentStatusLog(Base):
    __tablename__ = "document_status_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("financial_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    operator_id: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
