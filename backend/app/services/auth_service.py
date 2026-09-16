"""
backend/app/services/auth_service.py
认证鉴权与用户权限服务 (JWT Token 生成与校验、密码加盐哈希)
"""
import hashlib
import hmac
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings
from app.models.user import User, Role, UserRole
from app.schemas.auth import Token, TokenPayload

security = HTTPBearer()

class AuthService:
    @staticmethod
    def hash_password(password: str, salt: str = "financial_risk_audit_salt_2026") -> str:
        """加盐密码哈希 (PBKDF2-HMAC-SHA256)"""
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100000
        ).hex()

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        return hmac.compare_digest(AuthService.hash_password(plain_password), hashed_password)

    @staticmethod
    def create_access_token(user_id: int, username: str, roles: List[str]) -> str:
        """签发 JWT Token"""
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        payload = {
            "sub": username,
            "user_id": user_id,
            "roles": roles,
            "exp": int(expire.timestamp())
        }
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        return token

    @staticmethod
    def decode_access_token(token: str) -> TokenPayload:
        """校验并解析 JWT Token"""
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            return TokenPayload(**payload)
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效或过期的访问令牌",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @staticmethod
    async def authenticate_user(db: AsyncSession, username: str, password: str) -> Optional[User]:
        """认证用户账密"""
        stmt = select(User).where(User.username == username)
        res = await db.execute(stmt)
        user = res.scalars().first()
        if not user:
            return None
        if not AuthService.verify_password(password, user.hashed_password):
            return None
        return user

    @staticmethod
    async def get_user_roles(db: AsyncSession, user_id: int) -> List[str]:
        """查询用户关联的所有角色代码"""
        stmt = (
            select(Role.role_code)
            .join(UserRole, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user_id)
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> TokenPayload:
    """FastAPI 依赖注入：提取并校验当前登录用户 Token"""
    token = credentials.credentials
    return AuthService.decode_access_token(token)
