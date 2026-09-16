"""
backend/app/core/database.py
SQLAlchemy 2.0 异步数据库引擎与会话工厂
"""
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON, TypeDecorator
from .config import settings

# 兼容 SQLite 与 PostgreSQL 的 JSONB 类型装饰器
class CompatibleJSONB(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())

# 引擎参数自适应配置：根据协议头自动区分生产环境 PostgreSQL 与本地轻量 SQLite
engine_kwargs = {
    "echo": False,
    "future": True,
}

from sqlalchemy import event

if settings.DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {
        "check_same_thread": False,
        "timeout": 30.0
    }
elif settings.DATABASE_URL.startswith("postgresql"):
    engine_kwargs["pool_size"] = 10
    engine_kwargs["max_overflow"] = 20
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_recycle"] = 3600

engine = create_async_engine(
    settings.DATABASE_URL,
    **engine_kwargs
)

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False
)

class Base(DeclarativeBase):
    """ORM 基础实体"""
    pass

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入：获取数据库会话"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db() -> None:
    """初始化数据库所有实体表"""
    # 导入所有模型确保 Base.metadata 注册完全
    import app.models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

