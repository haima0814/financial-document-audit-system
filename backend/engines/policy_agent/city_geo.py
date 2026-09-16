"""
backend/engines/policy_agent/city_geo.py
标准城市行政区划、地理空间坐标与差旅等级数据库
支持空间时空轨迹分析与精准城市定额匹配
"""
from typing import Optional, Dict, List
from pydantic import BaseModel, Field

class CityGeo(BaseModel):
    """标准行政区划与地理实体"""
    code: str = Field(..., description="国家统计局 6 位行政区划代码")
    name: str = Field(..., description="标准城市全称，如 北京市")
    short_name: str = Field(..., description="城市简称，如 北京")
    tier: str = Field(default="TIER_3", description="差旅标准等级: TIER_1, TIER_2, TIER_3")
    latitude: float = Field(..., description="地心坐标纬度 WGS84")
    longitude: float = Field(..., description="地心坐标经度 WGS84")
    hotel_limit: float = Field(default=260.00, description="差旅住宿标准限额 (元/人·天)")

# 全国核心重点城市行政地理底座
_CITY_DATABASE: List[CityGeo] = [
    # 一线城市 (TIER_1: 500元/天)
    CityGeo(code="110000", name="北京市", short_name="北京", tier="TIER_1", latitude=39.9042, longitude=116.4074, hotel_limit=500.00),
    CityGeo(code="310000", name="上海市", short_name="上海", tier="TIER_1", latitude=31.2304, longitude=121.4737, hotel_limit=500.00),
    CityGeo(code="440100", name="广州市", short_name="广州", tier="TIER_1", latitude=23.1291, longitude=113.2644, hotel_limit=500.00),
    CityGeo(code="440300", name="深圳市", short_name="深圳", tier="TIER_1", latitude=22.5431, longitude=114.0579, hotel_limit=500.00),

    # 二线与新一线重点城市 (TIER_2: 350元/天)
    CityGeo(code="330100", name="杭州市", short_name="杭州", tier="TIER_2", latitude=30.2741, longitude=120.1551, hotel_limit=350.00),
    CityGeo(code="320100", name="南京市", short_name="南京", tier="TIER_2", latitude=32.0603, longitude=118.7969, hotel_limit=350.00),
    CityGeo(code="510100", name="成都市", short_name="成都", tier="TIER_2", latitude=30.5728, longitude=104.0668, hotel_limit=350.00),
    CityGeo(code="420100", name="武汉市", short_name="武汉", tier="TIER_2", latitude=30.5928, longitude=114.3055, hotel_limit=350.00),
    CityGeo(code="610100", name="西安市", short_name="西安", tier="TIER_2", latitude=34.3416, longitude=108.9398, hotel_limit=350.00),
    CityGeo(code="500000", name="重庆市", short_name="重庆", tier="TIER_2", latitude=29.5630, longitude=106.5516, hotel_limit=350.00),
    CityGeo(code="120000", name="天津市", short_name="天津", tier="TIER_2", latitude=39.0842, longitude=117.2009, hotel_limit=350.00),
    CityGeo(code="320500", name="苏州市", short_name="苏州", tier="TIER_2", latitude=31.2990, longitude=120.5853, hotel_limit=350.00),
    CityGeo(code="430100", name="长沙市", short_name="长沙", tier="TIER_2", latitude=28.2282, longitude=112.9388, hotel_limit=350.00),
    CityGeo(code="410100", name="郑州市", short_name="郑州", tier="TIER_2", latitude=34.7466, longitude=113.6253, hotel_limit=350.00),
    CityGeo(code="370200", name="青岛市", short_name="青岛", tier="TIER_2", latitude=36.0671, longitude=120.3826, hotel_limit=350.00),
    CityGeo(code="210200", name="大连市", short_name="大连", tier="TIER_2", latitude=38.9140, longitude=121.6147, hotel_limit=350.00),
    CityGeo(code="330200", name="宁波市", short_name="宁波", tier="TIER_2", latitude=29.8683, longitude=121.5440, hotel_limit=350.00),
    CityGeo(code="350200", name="厦门市", short_name="厦门", tier="TIER_2", latitude=24.4798, longitude=118.0894, hotel_limit=350.00),
    CityGeo(code="350100", name="福州市", short_name="福州", tier="TIER_2", latitude=26.0745, longitude=119.2965, hotel_limit=350.00),
    CityGeo(code="370100", name="济南市", short_name="济南", tier="TIER_2", latitude=36.6512, longitude=117.1201, hotel_limit=350.00),
    CityGeo(code="450100", name="南宁市", short_name="南宁", tier="TIER_2", latitude=22.8170, longitude=108.3665, hotel_limit=350.00),
    CityGeo(code="530100", name="昆明市", short_name="昆明", tier="TIER_2", latitude=24.8801, longitude=102.8329, hotel_limit=350.00),
    CityGeo(code="520100", name="贵阳市", short_name="贵阳", tier="TIER_2", latitude=26.6470, longitude=106.6302, hotel_limit=350.00),
    CityGeo(code="360100", name="南昌市", short_name="南昌", tier="TIER_2", latitude=28.6829, longitude=115.8582, hotel_limit=350.00),
    CityGeo(code="340100", name="合肥市", short_name="合肥", tier="TIER_2", latitude=31.8206, longitude=117.2272, hotel_limit=350.00),
    CityGeo(code="130100", name="石家庄市", short_name="石家庄", tier="TIER_2", latitude=38.0428, longitude=114.5149, hotel_limit=350.00),
    CityGeo(code="140100", name="太原市", short_name="太原", tier="TIER_2", latitude=37.8706, longitude=112.5489, hotel_limit=350.00),
    CityGeo(code="210100", name="沈阳市", short_name="沈阳", tier="TIER_2", latitude=41.8057, longitude=123.4315, hotel_limit=350.00),
    CityGeo(code="220100", name="长春市", short_name="长春", tier="TIER_2", latitude=43.8868, longitude=125.3245, hotel_limit=350.00),
    CityGeo(code="230100", name="哈尔滨市", short_name="哈尔滨", tier="TIER_2", latitude=45.8038, longitude=126.5349, hotel_limit=350.00),
    CityGeo(code="460100", name="海口市", short_name="海口", tier="TIER_2", latitude=20.0440, longitude=110.1999, hotel_limit=350.00),
    CityGeo(code="460200", name="三亚市", short_name="三亚", tier="TIER_2", latitude=18.2528, longitude=109.5119, hotel_limit=350.00),
]

_CITY_LOOKUP: Dict[str, CityGeo] = {}
for _c in _CITY_DATABASE:
    _CITY_LOOKUP[_c.name] = _c
    _CITY_LOOKUP[_c.short_name] = _c
    _CITY_LOOKUP[_c.code] = _c

def get_city_geo(city_input: Optional[str]) -> Optional[CityGeo]:
    """
    根据城市输入文本，智能模糊解析标准地理行政实体
    示例: '上海' / '上海市' / '上海陆家嘴' -> CityGeo(上海市)
    """
    if not city_input:
        return None
    cleaned = city_input.strip()
    # 1. 精确匹配
    if cleaned in _CITY_LOOKUP:
        return _CITY_LOOKUP[cleaned]
    # 2. 包含匹配 (优先长词)
    for c in sorted(_CITY_DATABASE, key=lambda x: len(x.short_name), reverse=True):
        if c.short_name in cleaned or c.name in cleaned:
            return c
    return None
