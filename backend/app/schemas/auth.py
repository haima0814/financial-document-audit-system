"""
backend/app/schemas/auth.py
用户鉴权与角色权限相关 Pydantic 模型
"""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field

class UserLoginReq(BaseModel):
    username: str = Field(..., description="登录账号/用户名")
    password: str = Field(..., description="密码")

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    real_name: str
    roles: List[str]

class TokenPayload(BaseModel):
    sub: str # username
    user_id: int
    roles: List[str] = []
    exp: Optional[int] = None

class RoleOut(BaseModel):
    id: int
    role_code: str
    role_name: str
    description: Optional[str] = None

    model_config = {"from_attributes": True}

class UserOut(BaseModel):
    id: int
    username: str
    real_name: str
    email: Optional[str] = None
    department: Optional[str] = None
    job_level: Optional[str] = None
    manager_id: Optional[int] = None
    is_active: bool
    roles: List[str] = []
    created_at: datetime

    model_config = {"from_attributes": True}

class UserCreateReq(BaseModel):
    username: str
    password: str
    real_name: str
    email: Optional[str] = None
    department: Optional[str] = None
    job_level: Optional[str] = "P3"
    manager_id: Optional[int] = None
    roles: List[str] = ["EMPLOYEE"]
