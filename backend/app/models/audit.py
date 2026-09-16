"""
backend/app/models/audit.py
多 Agent 分析任务、风险发现项、综合体检报告与 AI 对话交互实体模型
"""
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from decimal import Decimal
from sqlalchemy import String, Integer, Numeric, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base, CompatibleJSONB

class AnalysisTask(Base):
    """异步多 Agent 审核分析任务表"""
    __tablename__ = "analysis_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True) # UUID
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("financial_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    audit_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, index=True)
    
    # 状态: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    current_stage: Mapped[str] = mapped_column(String(64), default="INIT")
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("document_id", "audit_version", name="uq_task_document_audit_version"),
    )

class ReviewReport(Base):
    """综合风控体检报告主表"""
    __tablename__ = "review_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("analysis_tasks.task_id"), unique=True, nullable=False, index=True)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("financial_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    
    overall_risk_level: Mapped[str] = mapped_column(String(16), default="low", index=True) # low, medium, high
    final_score: Mapped[int] = mapped_column(Integer, default=100) # 0-100分
    high_risks_count: Mapped[int] = mapped_column(Integer, default=0)
    medium_risks_count: Mapped[int] = mapped_column(Integer, default=0)
    low_risks_count: Mapped[int] = mapped_column(Integer, default=0)
    
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    full_report_payload: Mapped[Dict[str, Any]] = mapped_column(CompatibleJSONB, default=dict)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    findings: Mapped[List["RiskFinding"]] = relationship("RiskFinding", back_populates="report", cascade="all, delete-orphan")

class RiskFinding(Base):
    """风险判定证据项详情表 (1:1 对齐 RiskFindingContract)"""
    __tablename__ = "risk_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(Integer, ForeignKey("review_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    finding_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True) # UUID
    
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True) # 如 R01_AMOUNT_MISMATCH
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True) # high, medium, low
    agent_role: Mapped[str] = mapped_column(String(32), nullable=False)
    
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    
    actual_value: Mapped[Optional[Dict[str, Any]]] = mapped_column(CompatibleJSONB, default=dict)
    expected_value: Mapped[Optional[Dict[str, Any]]] = mapped_column(CompatibleJSONB, default=dict)
    discrepancy_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    
    # 证据链与视觉锚点
    evidence_ids: Mapped[Optional[List[str]]] = mapped_column(CompatibleJSONB, default=list)
    primary_visual_anchor: Mapped[Optional[Dict[str, Any]]] = mapped_column(CompatibleJSONB, default=dict)
    evidence_chain: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(CompatibleJSONB, default=list)
    
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    is_overridable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    report: Mapped["ReviewReport"] = relationship("ReviewReport", back_populates="findings")

class AuditChatSession(Base):
    """智能审核对话 Session 会话表"""
    __tablename__ = "audit_chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("financial_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    messages: Mapped[List["AuditChatMessage"]] = relationship("AuditChatMessage", back_populates="session", cascade="all, delete-orphan")

class AuditChatMessage(Base):
    """对话消息历史表"""
    __tablename__ = "audit_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("audit_chat_sessions.session_id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False) # user, assistant, system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(CompatibleJSONB, default=list) # 引用证据/制度快照
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    session: Mapped["AuditChatSession"] = relationship("AuditChatSession", back_populates="messages")


class ProcessedEvent(Base):
    """
    已消费处理的领域事件幂等登记表
    用于 Unit of Work 事务幂等防线，杜绝 Worker 并发重试时产生重复报告与重复审批任务
    """
    __tablename__ = "processed_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    audit_version: Mapped[int] = mapped_column(Integer, default=1)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("task_id", "audit_version", name="uq_processed_events_task_version"),
    )
