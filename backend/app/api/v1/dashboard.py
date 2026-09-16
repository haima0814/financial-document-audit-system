"""
backend/app/api/v1/dashboard.py
企业风控全景态势与运营监控大盘 API
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.auth import TokenPayload
from app.services.auth_service import get_current_user
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["风控态势大盘"])

@router.get("/metrics", summary="获取企业风控全景大盘态势核心指标")
async def get_dashboard_metrics(
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    提供企业级全局风控驾驶舱指标：
    1. 核心 KPI（累计审查单据量、申报总金额、系统拦截高危资金、AI自动通过率）；
    2. 风险等级分布（高/中/低危占比）；
    3. 高频违规规则 TOP 5（连号、超标、失信穿透等）；
    4. 部门合规率分布与审查走势。
    """
    service = DashboardService(db)
    return await service.get_dashboard_metrics()
