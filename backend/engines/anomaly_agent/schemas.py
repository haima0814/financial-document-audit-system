"""
backend/engines/anomaly_agent/schemas.py
异常反欺诈智能体事实载荷定义
"""
from typing import Optional
from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel, Field

class InvoiceFact(BaseModel):
    """用于反欺诈比对的发票事实对象"""
    invoice_id: Optional[int] = None
    attachment_id: int = 1
    invoice_code: str = ""
    invoice_number: str = ""
    total_amount: Decimal = Decimal("0.00")
    issue_date: str = ""                 # 格式: YYYY-MM-DD
    seller_tax_id: str = ""
    seller_name: str = ""
    buyer_tax_id: str = ""
    expense_type: Optional[str] = None
    city_name: Optional[str] = None

class SpatioPoint(BaseModel):
    """时空物理事件点"""
    event_time: datetime
    city_name: str
    latitude: float
    longitude: float
    source_desc: str               # 来源描述，如 "北京全聚德餐饮发票"
    attachment_id: int = 1
    invoice_number: str = ""
