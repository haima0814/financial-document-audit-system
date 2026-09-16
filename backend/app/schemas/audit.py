"""
backend/app/schemas/audit.py
审核分析任务、风控体检报告、证据项及 AI 交互问答 Pydantic 模型
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field

class RiskFindingOut(BaseModel):
    id: int
    finding_id: str
    rule_code: str
    rule_name: str
    risk_level: str
    agent_role: str
    title: str
    description: str
    actual_value: Optional[Dict[str, Any]] = None
    expected_value: Optional[Dict[str, Any]] = None
    discrepancy_amount: Optional[Decimal] = None
    evidence_ids: Optional[List[str]] = None
    primary_visual_anchor: Optional[Dict[str, Any]] = None
    evidence_chain: Optional[List[Dict[str, Any]]] = None
    suggestion: str
    is_overridable: bool = True
    created_at: datetime

    model_config = {"from_attributes": True}

class ReviewReportOut(BaseModel):
    id: int
    task_id: str
    document_id: int
    overall_risk_level: str
    final_score: int
    high_risks_count: int
    medium_risks_count: int
    low_risks_count: int
    summary: Optional[str] = None
    full_report_payload: Optional[Dict[str, Any]] = None
    findings: List[RiskFindingOut] = []
    created_at: datetime

    model_config = {"from_attributes": True}

class AnalysisTaskOut(BaseModel):
    id: int
    task_id: str
    document_id: int
    status: str
    current_stage: str
    progress_pct: int
    error_message: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

class AuditChatReq(BaseModel):
    document_id: int = Field(..., description="关联合同/单据 ID")
    message: str = Field(..., min_length=1, description="用户提问内容，如：为什么判定第2笔住宿费超标？")
    session_id: Optional[str] = Field(default=None, description="会话 ID（多轮对话）")

class AuditChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    citations: Optional[List[Dict[str, Any]]] = None
    created_at: datetime

    model_config = {"from_attributes": True}

class AuditChatResp(BaseModel):
    session_id: str
    message: AuditChatMessageOut
