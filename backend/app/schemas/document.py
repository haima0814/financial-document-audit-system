"""
backend/app/schemas/document.py
单据、明细项与附件请求响应 Pydantic 模型
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field

class LineItemIn(BaseModel):
    line_no: int = Field(..., description="行号")
    expense_type: str = Field(..., description="费用类型，如 交通费、住宿费、办公用品")
    item_desc: str = Field(..., description="明细说明")
    amount: Decimal = Field(..., description="明细申报金额")
    invoice_count: int = Field(default=0, description="对应发票张数")
    invoice_amount_sum: Decimal = Field(default=Decimal("0.00"), description="对应发票金额之和")
    city_name: Optional[str] = Field(default=None, description="差旅城市")
    start_date: Optional[datetime] = Field(default=None, description="起始时间")
    end_date: Optional[datetime] = Field(default=None, description="结束时间")
    extra_data: Optional[Dict[str, Any]] = Field(default_factory=dict)

class LineItemOut(LineItemIn):
    id: int
    document_id: int
    created_at: datetime

    model_config = {"from_attributes": True}

class AttachmentIn(BaseModel):
    file_name: str
    file_type: str # PDF, PNG, JPG
    file_path: Optional[str] = None
    file_hash: str
    file_size_bytes: int
    is_invoice: bool = True

class AttachmentOut(AttachmentIn):
    id: int
    document_id: int
    ocr_status: str
    created_at: datetime

    model_config = {"from_attributes": True}

class InvoiceRecordIn(BaseModel):
    invoice_code: Optional[str] = "NONE"
    invoice_number: Optional[str] = None
    invoice_type: Optional[str] = "增值税电子普通发票"
    total_amount: Optional[Decimal] = None
    untaxed_amount: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None
    seller_name: Optional[str] = None
    seller_tax_id: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_tax_id: Optional[str] = None
    issue_date: Optional[str] = None
    invoice_hash: Optional[str] = None
    ocr_confidence: Optional[float] = 0.98
    bbox_positions: Optional[Dict[str, Any]] = Field(default_factory=dict)
    file_path: Optional[str] = None

class InvoiceRecordOut(InvoiceRecordIn):
    id: int
    document_id: int
    attachment_id: Optional[int] = None
    file_path: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}

class FinancialDocumentCreateReq(BaseModel):
    document_type: str = Field(..., description="单据类型: CORP_PAYMENT, ADVANCE_PAYMENT, BATCH_PAYMENT, EXPENSE_REIMBURSEMENT, TRAVEL_REIMBURSEMENT")
    title: str = Field(..., min_length=2, max_length=255, description="单据标题/报销事由")
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    total_amount: Decimal = Field(..., description="单据总申报金额")
    currency: str = "CNY"
    extra_attributes: Optional[Dict[str, Any]] = Field(default_factory=dict)
    line_items: List[LineItemIn] = Field(default_factory=list)
    attachments: List[AttachmentIn] = Field(default_factory=list)
    invoices: List[InvoiceRecordIn] = Field(default_factory=list)
    idempotency_key: Optional[str] = Field(default=None, description="前端请求幂等防重键")


class FinancialDocumentUpdateReq(BaseModel):
    title: Optional[str] = None
    total_amount: Optional[Decimal] = None
    extra_attributes: Optional[Dict[str, Any]] = None
    line_items: Optional[List[LineItemIn]] = None
    is_manual_override: Optional[bool] = None
    manual_override_reason: Optional[str] = None
    manual_override_fields: Optional[Dict[str, Any]] = None

class FinancialDocumentListItemOut(BaseModel):
    id: int
    document_no: str
    idempotency_key: Optional[str] = None
    document_type: str
    title: str
    applicant_id: int
    applicant_name: Optional[str] = None
    department_name: Optional[str] = None
    total_amount: Decimal
    currency: str
    status: str
    current_version: int
    submission_time: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

class FinancialDocumentDetailOut(FinancialDocumentListItemOut):
    is_manual_override: bool
    manual_override_reason: Optional[str] = None
    manual_override_fields: Optional[Dict[str, Any]] = None
    extra_attributes: Optional[Dict[str, Any]] = None
    line_items: List[LineItemOut] = []
    attachments: List[AttachmentOut] = []
    invoices: List[InvoiceRecordOut] = []

    model_config = {"from_attributes": True}

class DocumentCancelReq(BaseModel):
    reason: Optional[str] = Field(default="经办人主动撤回单据", description="撤回原因")

class DocumentVersionOut(BaseModel):
    id: int
    document_id: int
    version_no: int
    trigger_action: str
    snapshot_payload: Dict[str, Any]
    change_summary: Optional[str] = None
    created_by: int
    created_at: datetime

    model_config = {"from_attributes": True}

