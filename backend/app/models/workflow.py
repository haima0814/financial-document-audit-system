"""
backend/app/models/workflow.py
审批流定义、流转实例、待办任务与状态流转审计日志实体模型
"""
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base, CompatibleJSONB

class ApprovalWorkflow(Base):
    """审批流程模板表"""
    __tablename__ = "approval_workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True) # 如 WF_TRAVEL_STANDARD
    workflow_name: Mapped[str] = mapped_column(String(128), nullable=False)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    nodes: Mapped[List["ApprovalWorkflowNode"]] = relationship("ApprovalWorkflowNode", back_populates="workflow", cascade="all, delete-orphan")

class ApprovalWorkflowNode(Base):
    """审批流节点配置表"""
    __tablename__ = "approval_workflow_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[int] = mapped_column(Integer, ForeignKey("approval_workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    node_order: Mapped[int] = mapped_column(Integer, nullable=False) # 1, 2, 3...
    node_name: Mapped[str] = mapped_column(String(64), nullable=False) # 如 "部门直属主管审批", "财务专员复核"
    
    approver_type: Mapped[str] = mapped_column(String(32), default="ROLE") # ROLE, USER, MANAGER, DYNAMIC_VP
    role_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True) # 如 "FINANCE", "CFO"
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    condition_expression: Mapped[Optional[Dict[str, Any]]] = mapped_column(CompatibleJSONB, default=dict)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    workflow: Mapped["ApprovalWorkflow"] = relationship("ApprovalWorkflow", back_populates="nodes")

class ApprovalInstance(Base):
    """审批运行实例主表 (宏观维度)"""
    __tablename__ = "approval_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[int] = mapped_column(Integer, ForeignKey("approval_workflows.id"), nullable=False)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("financial_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    report_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("review_reports.id", ondelete="SET NULL"), nullable=True, index=True)
    audit_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    
    # 状态: RUNNING, COMPLETED, TERMINATED, SUSPENDED, CANCELLED
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", index=True)
    current_node_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    start_time: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    tasks: Mapped[List["ApprovalTask"]] = relationship("ApprovalTask", back_populates="instance", cascade="all, delete-orphan")

class ApprovalTask(Base):
    """个人待办任务卡片表 (微观维度)"""
    __tablename__ = "approval_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(Integer, ForeignKey("approval_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id: Mapped[int] = mapped_column(Integer, ForeignKey("approval_workflow_nodes.id"), nullable=False)
    assignee_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # 状态: PENDING, APPROVED, REJECTED, TRANSFERRED, ADD_SIGN, AUTO_PASSED
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # 扩展数据：加签关系 (parent_task_id, add_sign_type: BEFORE/AFTER) 等
    extra_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(CompatibleJSONB, default=dict)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    instance: Mapped["ApprovalInstance"] = relationship("ApprovalInstance", back_populates="tasks")
    assignee: Mapped[Optional["User"]] = relationship("User", foreign_keys=[assignee_id])
    node: Mapped["ApprovalWorkflowNode"] = relationship("ApprovalWorkflowNode", foreign_keys=[node_id])

class WorkflowStatusLog(Base):
    """审批流不可变审计操作日志"""
    __tablename__ = "workflow_status_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(Integer, ForeignKey("approval_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    operator_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True) # None 或 0 为系统自动处理
    action: Mapped[str] = mapped_column(String(32), nullable=False) # SUBMIT, APPROVE, REJECT, TRANSFER, ADD_SIGN, AUTO_PASS, AUTO_REJECT, NEED_SUPPLEMENT, REVOKE
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(CompatibleJSONB, default=dict) # 记录 override_reason, target_user_id 等
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
