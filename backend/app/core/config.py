"""
backend/app/core/config.py
全局配置管理 (Pydantic Settings)
"""
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    PROJECT_NAME: str = "财务单据智能风险审核系统"
    API_V1_STR: str = "/api/v1"
    
    # 运行模式: local (本地轻量秒起 SQLite + TaskManager) / production (PostgreSQL 15+ + Celery + Redis)
    RUN_MODE: str = Field(default="local")
    PORT: int = Field(default=8001)
    
    # 数据库连接：默认使用本地 sqlite，若配置了 DATABASE_URL 则自动切换至 PostgreSQL
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///./financial_audit.db")
    
    # Redis 连接
    REDIS_URL: Optional[str] = Field(default="redis://localhost:6379/0")
    
    # 安全密钥与 JWT
    SECRET_KEY: str = Field(default="dev-secret-key-financial-risk-audit-2026-super-secure")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 # 24小时
    
    # CORS 跨域允许来源
    CORS_ORIGINS: List[str] = ["*"]
    
    # 大模型 API 接入 (OpenAI兼容协议)
    OPENAI_API_KEY: str = Field(default="mock-key")
    OPENAI_BASE_URL: str = Field(default="https://api.deepseek.com/v1")
    DEFAULT_LLM_MODEL: str = Field(default="deepseek-chat")

settings = Settings()
