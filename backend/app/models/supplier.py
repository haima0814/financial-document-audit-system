"""
backend/app/models/supplier.py
供应商画像、工商资信与市场公允基准价模型
"""
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from decimal import Decimal
from sqlalchemy import String, Integer, Numeric, Boolean, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base, CompatibleJSONB

class SupplierProfile(Base):
    """供应商资信与风险画像表"""
    __tablename__ = "supplier_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supplier_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    uscc: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True) # 统一社会信用代码 18位
    legal_person: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    registered_capital: Mapped[Optional[str]] = mapped_column(String(64), nullable=True) # 如 "1000万元人民币"
    establishment_date: Mapped[Optional[str]] = mapped_column(String(16), nullable=True) # YYYY-MM-DD
    operating_status: Mapped[str] = mapped_column(String(32), default="存续") # 存续, 注销, 吊销, 异常
    
    bank_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    bank_account: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    
    is_dishonest: Mapped[bool] = mapped_column(Boolean, default=False) # 是否为失信被执行人
    is_shell_company: Mapped[bool] = mapped_column(Boolean, default=False) # 是否疑似空壳
    business_scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # 经营范围
    risk_tags: Mapped[Optional[Dict[str, Any]]] = mapped_column(CompatibleJSONB, default=dict)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class MarketPriceReference(Base):
    """大宗物资与办公品类市场参考公允价表"""
    __tablename__ = "market_price_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False) # 办公耗材, IT设备, 广告会务
    item_name: Mapped[str] = mapped_column(String(128), nullable=False) # 如 "ThinkPad T14 笔记本电脑"
    model_spec: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    benchmark_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    price_unit: Mapped[str] = mapped_column(String(16), default="台")
    source_channel: Mapped[str] = mapped_column(String(64), default="京东企业购")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
