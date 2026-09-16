"""
backend/engines/policy_agent/city_tier.py
城市等级映射字典与标准化解析器 (向后兼容代理，底层统一依托 city_geo.py)
"""
from typing import Dict, Optional
from .city_geo import get_city_geo

# 向后兼容导出
CITY_TIER_DICT: Dict[str, str] = {
    "北京": "TIER_1", "北京市": "TIER_1",
    "上海": "TIER_1", "上海市": "TIER_1",
    "广州": "TIER_1", "广州市": "TIER_1",
    "深圳": "TIER_1", "深圳市": "TIER_1",
    "杭州": "TIER_2", "南京": "TIER_2", "成都": "TIER_2", "武汉": "TIER_2",
    "西安": "TIER_2", "重庆": "TIER_2", "苏州": "TIER_2", "天津": "TIER_2",
    "长沙": "TIER_2", "郑州": "TIER_2", "青岛": "TIER_2", "大连": "TIER_2",
}

CITY_TIER_LIMITS: Dict[str, float] = {
    "TIER_1": 500.00,
    "TIER_2": 350.00,
    "TIER_3": 260.00,
}

def get_city_tier(city_name: str) -> str:
    """解析城市所属等级，未收录城市归为三类/其他 TIER_3"""
    geo = get_city_geo(city_name)
    if geo:
        return geo.tier
    return "TIER_3"

def get_city_hotel_limit(city_name: str) -> float:
    geo = get_city_geo(city_name)
    if geo:
        return geo.hotel_limit
    return CITY_TIER_LIMITS.get("TIER_3", 260.00)
