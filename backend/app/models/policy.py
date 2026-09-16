"""
backend/app/models/policy.py
制度知识库、条款切片实体模型
"""
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from decimal import Decimal
from sqlalchemy import String, Integer, Numeric, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base, CompatibleJSONB

class PolicyUnit(Base):
    """企业制度大纲规范主表"""
    __tablename__ = "policy_units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True) # 如 POL_TRAVEL_2026
    policy_name: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_type: Mapped[str] = mapped_column(String(64), nullable=False) # 差旅管理, 采购付款, 招待接待, 通用费用
    version: Mapped[str] = mapped_column(String(32), default="2026-V1", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    effective_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    chunks: Mapped[List["PolicyChunk"]] = relationship("PolicyChunk", back_populates="policy", cascade="all, delete-orphan")

class PolicyChunk(Base):
    """制度条款结构化切片表 (支持结构化规则判定与 RAG 向量引用)"""
    __tablename__ = "policy_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_id: Mapped[int] = mapped_column(Integer, ForeignKey("policy_units.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True) # 格式: POL_TRAVEL_2026#CH_001
    clause_title: Mapped[str] = mapped_column(String(128), nullable=False) # 如 "第三条 一类城市住宿限额标准"
    clause_content: Mapped[str] = mapped_column(Text, nullable=False) # 条款正文
    article_order: Mapped[int] = mapped_column(Integer, default=1)
    
    # 结构化抽取参数 (辅助确定性快速判断，不完全依赖 RAG)
    expense_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True) # "住宿费", "机票", "餐饮", "预付款比例"
    applicable_city_tier: Mapped[Optional[str]] = mapped_column(String(32), nullable=True) # "TIER_1", "TIER_2", "TIER_3", "ALL"
    max_amount_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True) # 限额数值 (如 500.00)
    extra_rule_params: Mapped[Optional[Dict[str, Any]]] = mapped_column(CompatibleJSONB, default=dict)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    policy: Mapped["PolicyUnit"] = relationship("PolicyUnit", back_populates="chunks")
