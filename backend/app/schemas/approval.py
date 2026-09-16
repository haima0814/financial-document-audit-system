"""
backend/app/schemas/approval.py
审批流相关的请求与响应 Pydantic 模型
"""
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field

class ApprovalActionEnum(str, Enum):
    APPROVE = "APPROVE"          # 同意通过
    REJECT = "REJECT"            # 驳回修改
    TRANSFER = "TRANSFER"        # 转交他人
    ADD_SIGN = "ADD_SIGN"        # 征询加签
    REVOKE = "REVOKE"            # 经办人撤销单据

class AddSignTypeEnum(str, Enum):
    BEFORE = "BEFORE"            # 前置加签 (加签人先审，通过后回到我)
    AFTER = "AFTER"              # 后置加签 (我先审，通过后送加签人审)

class ApprovalActionReq(BaseModel):
    """审批操作入参"""
    action: ApprovalActionEnum = Field(..., description="审批动作")
    comment: str = Field(..., min_length=2, max_length=500, description="审批意见/驳回原因")
    
    # 转交或加签时必填
    target_user_id: Optional[int] = Field(default=None, description="转交或加签目标人 ID")
    add_sign_type: Optional[AddSignTypeEnum] = Field(default=None, description="加签类型 (BEFORE 或 AFTER)")
    
    # 高危风险强行特批时必填
    override_reason: Optional[str] = Field(default=None, description="高危风险具名特批放行理由")

class ApprovalNodeOut(BaseModel):
    id: int
    node_order: int
    node_name: str
    approver_type: str
    role_code: Optional[str] = None
    user_id: Optional[int] = None
    is_final: bool = False

    model_config = {"from_attributes": True}

class ApprovalTaskOut(BaseModel):
    id: int
    instance_id: int
    node_id: int
    node_name: Optional[str] = None
    assignee_id: int
    assignee_name: Optional[str] = None
    status: str
    comment: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None
    created_at: datetime
    end_time: Optional[datetime] = None

    model_config = {"from_attributes": True}

class WorkflowStatusLogOut(BaseModel):
    id: int
    instance_id: int
    task_id: Optional[int] = None
    operator_id: int
    operator_name: Optional[str] = None
    action: str
    comment: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}

class ApprovalInstanceOut(BaseModel):
    id: int
    workflow_id: int
    document_id: int
    status: str
    current_node_id: Optional[int] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    tasks: List[ApprovalTaskOut] = []
    logs: List[WorkflowStatusLogOut] = []

    model_config = {"from_attributes": True}
