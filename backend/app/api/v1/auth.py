"""
backend/app/api/v1/auth.py
用户登录与身份认证接口
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.auth import UserLoginReq, Token, UserOut, TokenPayload
from app.services.auth_service import AuthService, get_current_user
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["认证鉴权"])

@router.post("/login", response_model=Token, summary="用户账号密码登录")
async def login(req: UserLoginReq, db: AsyncSession = Depends(get_db)):
    """支持管理员、经理、财务人员和员工账密登录，返回 Bearer JWT Token"""
    user = await AuthService.authenticate_user(db, req.username, req.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    roles = await AuthService.get_user_roles(db, user.id)
    token = AuthService.create_access_token(user_id=user.id, username=user.username, roles=roles)
    return Token(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        username=user.username,
        real_name=user.real_name,
        roles=roles,
    )

@router.get("/me", response_model=UserOut, summary="获取当前登录用户信息")
async def get_me(
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user = await db.get(User, current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    roles = await AuthService.get_user_roles(db, user.id)
    return UserOut(
        id=user.id,
        username=user.username,
        real_name=user.real_name,
        email=user.email,
        department=user.department_name,
        job_level="P4",
        is_active=user.is_active,
        roles=roles,
        created_at=user.created_at
    )
