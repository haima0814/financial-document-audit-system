"""
backend/main.py
财务单据智能风险审核系统 - FastAPI 核心入口
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.api.v1 import api_v1_router
from app.api.v1.ws import router as ws_router
import app.services.audit_handler # 注册领域事件监听器 (AuditCompletionHandler)


# 配置日志格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化数据库表，关闭时清理资源"""
    logger.info("Initializing database tables...")
    await init_db()
    logger.info("Database initialized successfully.")
    yield
    logger.info("Application shutting down...")

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="基于确定性规则与大模型多智能体架构的财务单据智能审核系统",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 API V1 路由
app.include_router(api_v1_router, prefix=settings.API_V1_STR)

# 挂载根级别 WebSocket 路由 (支持 /ws/audit/{task_id} 与 /api/v1/ws/audit/{task_id})
app.include_router(ws_router)

# 挂载静态文件目录 (发票与凭证原件访问)
import os
from fastapi.staticfiles import StaticFiles
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/health", tags=["系统探活"])
async def health_check():
    return {
        "status": "UP",
        "project": settings.PROJECT_NAME,
        "run_mode": settings.RUN_MODE,
        "version": "1.0.0"
    }

@app.get("/", tags=["系统根目录"])
async def root():
    return {
        "message": f"欢迎使用 {settings.PROJECT_NAME} API 服务",
        "docs": "/docs",
        "api_v1": settings.API_V1_STR
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)
