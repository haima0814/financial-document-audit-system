"""
backend/engines/contract/settings.py
引擎推理超参数、阈值与熔断配置
"""
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field

class EngineSettings(BaseModel):
    """多 Agent 推理引擎配置项"""
    # 基础运行环境与网络
    RUN_MODE: str = Field(default="local", description="运行模式: local (单进程TaskManager) / production (Celery分布式)")
    REDIS_URL: Optional[str] = Field(default="redis://localhost:6379/0", description="Redis 连接串 (用于 Streams 与分布式任务)")

    # 模型接入配置
    default_model_name: str = Field(default="deepseek-chat")
    model_temperature: float = Field(default=0.1, description="严谨审核场景统一采用极低采样温度")
    max_tokens: int = Field(default=4096)
    
    # 确定性核算阈值
    amount_tolerance: Decimal = Field(default=Decimal("0.00"), description="严禁容差，必须毫厘不差")
    price_deviation_warning_pct: Decimal = Field(default=Decimal("0.15"), description="市价偏离预警阈值 (15%)")
    
    # 工商风控阈值
    supplier_min_registered_years: int = Field(default=1, description="供应商成立时间不足1年高危预警")
    
    # 熔断与超时配置
    global_task_timeout_seconds: int = Field(default=180, description="单次审核全图超时上限 3 分钟")
    single_agent_timeout_seconds: float = Field(default=20.0, description="单 Agent 硬超时上限 20 秒")
    circuit_breaker_enabled: bool = Field(default=True, description="子 Agent 超时允许软降级跳过")

ENGINE_CONFIG = EngineSettings()
engine_settings = ENGINE_CONFIG  # 别名兼容
