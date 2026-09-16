"""
backend/app/models/invoice.py
发票记录、OCR版面分析结果与发票明细实体模型
"""
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from decimal import Decimal
from sqlalchemy import String, Integer, Numeric, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base, CompatibleJSONB

class AttachmentParseResult(Base):
    """附件 OCR 版面分析结果 (保留原始字符与全图归一化 BBox 坐标)"""
    __tablename__ = "attachment_parse_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    attachment_id: Mapped[int] = mapped_column(Integer, ForeignKey("document_attachments.id", ondelete="CASCADE"), unique=True, nullable=False)
    ocr_engine: Mapped[str] = mapped_column(String(32), default="PaddleOCR") # PaddleOCR, MinerU, E-Invoice-XML
    raw_ocr_payload: Mapped[Dict[str, Any]] = mapped_column(CompatibleJSONB, nullable=False)
    bbox_positions: Mapped[Dict[str, Any]] = mapped_column(CompatibleJSONB, default=dict) # 各字段 BBox [ymin, xmin, ymax, xmax]
    overall_confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.950"))
    parse_status: Mapped[str] = mapped_column(String(32), default="SUCCESS")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class InvoiceRecord(Base):
    """结构化发票台账 (带全局联合哈希查重与多维度字段)"""
    __tablename__ = "invoice_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("financial_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    attachment_id: Mapped[int] = mapped_column(Integer, ForeignKey("document_attachments.id", ondelete="CASCADE"), nullable=False)
    
    invoice_code: Mapped[str] = mapped_column(String(32), default="NONE", nullable=False, index=True) # 数电票填 NONE
    invoice_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    invoice_type: Mapped[str] = mapped_column(String(64), default="增值税电子普通发票") # 增值税专用发票, 电子普票, 全电专票, 机动车, 行程单
    
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)       # 价税合计 (总金额)
    untaxed_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True, default=None) # 不含税金额
    tax_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True, default=None)     # 税额
    tax_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4), nullable=True, default=None)      # 税率 (如 0.0600)
    
    seller_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, default=None)
    seller_tax_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default=None, index=True)
    buyer_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, default=None)
    buyer_tax_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default=None)
    
    issue_date: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True) # YYYY-MM-DD
    check_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True) # 校验码后6位
    
    # 全局唯一防重指纹: SHA256(code#number#amount#date)
    invoice_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    
    is_manual_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    original_extracted_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    
    raw_payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(CompatibleJSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_invoice_unique_pair", "invoice_code", "invoice_number"),
    )
