"""
backend/app/api/v1/router.py
API V1 根路由聚合器
"""
from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.documents import router as documents_router
from app.api.v1.approvals import router as approvals_router
from app.api.v1.audits import router as audits_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.ws import router as ws_router
from app.api.v1.sse import router as sse_router

api_v1_router = APIRouter()

api_v1_router.include_router(auth_router)
api_v1_router.include_router(documents_router)
api_v1_router.include_router(approvals_router)
api_v1_router.include_router(audits_router)
api_v1_router.include_router(dashboard_router)
api_v1_router.include_router(ws_router)
api_v1_router.include_router(sse_router)
