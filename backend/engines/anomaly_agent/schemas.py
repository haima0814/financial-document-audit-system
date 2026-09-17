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
    """时空物理事件点 (消费/住宿/打车等业务事件)"""
    event_time: datetime
    city_name: str
    latitude: float
    longitude: float
    source_desc: str               # 来源描述，如 "北京全聚德餐饮发票"
    attachment_id: int = 1
    invoice_number: str = ""


class TravelSegment(BaseModel):
    """合法交通移动行程段 (交通工具真实运行轨迹)"""
    departure_city: str
    arrival_city: str
    departure_time: Optional[datetime] = None
    arrival_time: Optional[datetime] = None
    travel_date: Optional[str] = None       # 格式: YYYY-MM-DD (若无具体发到时分秒)
    transport_mode: Optional[str] = None     # "TRAIN", "FLIGHT", "HIGH_SPEED_RAIL" 等
    transport_no: Optional[str] = None       # 如 "G13"
    attachment_id: int = 1
    invoice_number: Optional[str] = ""
    source_desc: str = ""

