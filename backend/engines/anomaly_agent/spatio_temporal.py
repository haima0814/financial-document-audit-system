"""
backend/engines/anomaly_agent/spatio_temporal.py
跨单物理时空轨迹碰撞分析器
"""
import math
from typing import List
from datetime import datetime
from engines.contract.finding import RiskFindingContract, RiskLevelEnum
from engines.contract.agent_role import AgentRoleEnum
from .schemas import SpatioPoint

class SpatioTemporalVerifier:
    EARTH_RADIUS_KM = 6371.0

    @staticmethod
    def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """使用 Haversine 公式计算两个经纬度坐标之间的地表大圆球面距离 (千米)"""
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lng2 - lng1)

        a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return SpatioTemporalVerifier.EARTH_RADIUS_KM * c

    @staticmethod
    def detect_spatio_temporal_collisions(points: List[SpatioPoint]) -> List[RiskFindingContract]:
        """时空轨迹碰撞检测算法"""
        findings: List[RiskFindingContract] = []
        if len(points) < 2:
            return findings

        # 按事件时间升序排序
        sorted_points = sorted(points, key=lambda p: p.event_time)

        for i in range(len(sorted_points) - 1):
            p1 = sorted_points[i]
            p2 = sorted_points[i + 1]

            dist_km = SpatioTemporalVerifier.haversine_distance(
                p1.latitude, p1.longitude, p2.latitude, p2.longitude
            )
            # 若两地物理直线距离 < 100km，不视为跨城位移冲突
            if dist_km < 100.0:
                continue

            delta_time = (p2.event_time - p1.event_time).total_seconds()
            delta_hours = delta_time / 3600.0

            # 1. 时间完全相同 (delta_hours <= 0)：单独作为同时异地事件异常处理，不人为修改成 0.001h 计算伪造速度
            if delta_hours <= 0:
                findings.append(RiskFindingContract(
                    rule_code="R09_SPATIO_TEMPORAL_COLLISION",
                    rule_name="跨单行程时空物理轨迹碰撞",
                    risk_level=RiskLevelEnum.HIGH,
                    agent_role=AgentRoleEnum.ANOMALY,
                    title=f"在同一时间于 {p1.city_name} 与 {p2.city_name} 发生异地消费",
                    description=(
                        f"申请人在同一时间（{p1.event_time.strftime('%Y-%m-%d %H:%M')}）分别于"
                        f"[{p1.city_name}]（发票: {p1.invoice_number or '无票号'}）与"
                        f"[{p2.city_name}]（发票: {p2.invoice_number or '无票号'}）产生消费，"
                        f"两地物理直线距离为 {dist_km:.1f} 公里。短时间跨城市高速位移异常，需要核实交通凭证和真实行程。"
                    ),
                    actual_value={
                        "point_1": {"city": p1.city_name, "time": str(p1.event_time), "invoice": p1.invoice_number},
                        "point_2": {"city": p2.city_name, "time": str(p2.event_time), "invoice": p2.invoice_number},
                        "distance_km": round(dist_km, 2),
                        "delta_hours": 0.0,
                        "is_simultaneous": True
                    },
                    expected_value={"allow_simultaneous_remote_transactions": False},
                    suggestion="短时间跨城市高速位移异常，需要核实交通凭证和真实行程。",
                    is_overridable=True
                ))
                continue

            # 2. 短时间跨城市高速位移异常 (0 < delta_hours < 2.0 且 speed > 250 km/h)
            speed_kmh = dist_km / delta_hours
            if delta_hours < 2.0 and speed_kmh > 250.0:
                findings.append(RiskFindingContract(
                    rule_code="R09_SPATIO_TEMPORAL_COLLISION",
                    rule_name="跨单行程时空物理轨迹碰撞",
                    risk_level=RiskLevelEnum.HIGH,
                    agent_role=AgentRoleEnum.ANOMALY,
                    title=f"在 {p1.city_name} 与 {p2.city_name} 出现短时间跨城市高速位移异常",
                    description=(
                        f"申请人在 {p1.event_time.strftime('%Y-%m-%d %H:%M')} 于[{p1.city_name}]产生消费（发票: {p1.invoice_number or '无票号'}），"
                        f"随后在 {p2.event_time.strftime('%Y-%m-%d %H:%M')}（间隔 {delta_hours:.1f} 小时）"
                        f"于[{p2.city_name}]再次产生消费（发票: {p2.invoice_number or '无票号'}）。"
                        f"两地物理直线距离为 {dist_km:.1f} 公里，折合移动时速达 {speed_kmh:.1f} km/h。"
                        f"短时间跨城市高速位移异常，需要核实交通凭证和真实行程。"
                    ),
                    actual_value={
                        "point_1": {"city": p1.city_name, "time": str(p1.event_time), "invoice": p1.invoice_number},
                        "point_2": {"city": p2.city_name, "time": str(p2.event_time), "invoice": p2.invoice_number},
                        "distance_km": round(dist_km, 2),
                        "speed_kmh": round(speed_kmh, 2)
                    },
                    expected_value={"max_realistic_speed_kmh": 250.0},
                    suggestion="短时间跨城市高速位移异常，需要核实交通凭证和真实行程。",
                    is_overridable=True
                ))

        return findings
