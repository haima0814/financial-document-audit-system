"""
backend/engines/anomaly_agent/travel_verifier.py
交通票据行程移动段一致性核验器 (TravelSegmentVerifier)
核验合法交通移动路径与单据申报明细、住宿日期、多行程重叠及离散消费事件的勾稽一致性
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from engines.contract.finding import RiskFindingContract, RiskLevelEnum
from engines.contract.agent_role import AgentRoleEnum
from engines.policy_agent.city_geo import get_city_geo
from .schemas import TravelSegment, SpatioPoint
from .spatio_temporal import SpatioTemporalVerifier

class TravelSegmentVerifier:
    """交通票据行程段一致性核验器"""

    @staticmethod
    def verify_travel_consistency(
        segments: List[TravelSegment],
        line_items: Optional[List[Dict[str, Any]]] = None,
        spatio_points: Optional[List[SpatioPoint]] = None
    ) -> List[RiskFindingContract]:
        findings: List[RiskFindingContract] = []
        if not segments:
            return findings

        line_items = line_items or []
        spatio_points = spatio_points or []

        # 提取申报明细中的目的地城市集合
        claim_cities = set()
        for item in line_items:
            c = item.get("city_name")
            if c:
                geo = get_city_geo(c)
                claim_cities.add(geo.short_name if geo else c)

        # 1. 核验：交通票起终点与报销申报城市一致性
        if claim_cities:
            for seg in segments:
                dep_geo = get_city_geo(seg.departure_city)
                arr_geo = get_city_geo(seg.arrival_city)
                dep_name = dep_geo.short_name if dep_geo else seg.departure_city
                arr_name = arr_geo.short_name if arr_geo else seg.arrival_city

                # 交通票的出发地或到达地必须至少有一个与申报明细中的城市匹配
                # 否则说明车票目的地与本次出差申报目的地完全脱节
                matched = any(
                    (c in dep_name or dep_name in c or c in arr_name or arr_name in c)
                    for c in claim_cities
                )
                if not matched:
                    findings.append(RiskFindingContract(
                        rule_code="R09_TRAVEL_DESTINATION_MISMATCH",
                        rule_name="交通行程目的地与报销申报城市不一致",
                        risk_level=RiskLevelEnum.HIGH,
                        agent_role=AgentRoleEnum.ANOMALY,
                        title=f"交通行程目的地 [{seg.arrival_city}] 与单据申报出差城市不符",
                        description=(
                            f"交通票据行程为 [{seg.departure_city} -> {seg.arrival_city}]（{seg.transport_no or '交通凭证'}），"
                            f"但单据明细申报出差城市为 [{', '.join(claim_cities)}]，行程路线与申报出差目的地脱节。"
                        ),
                        actual_value={
                            "ticket_from": seg.departure_city,
                            "ticket_to": seg.arrival_city,
                            "claim_cities": list(claim_cities)
                        },
                        expected_value={"destination_matches_claim": True},
                        suggestion="请核实交通票据是否属于本笔出差申请或补充行程变更说明。",
                        is_overridable=True
                    ))

        # 2. 核验：行程日期与住宿日期冲突
        # 如果住宿明细所在的城市与交通行程时间明显冲突
        for item in line_items:
            exp_type = str(item.get("expense_type") or "")
            item_desc = str(item.get("item_desc") or "")
            if "住宿" in exp_type or "酒店" in exp_type or "住宿" in item_desc or "酒店" in item_desc:
                hotel_city = item.get("city_name")
                hotel_date_str = item.get("start_date")
                if hotel_city and hotel_date_str:
                    h_geo = get_city_geo(hotel_city)
                    h_name = h_geo.short_name if h_geo else hotel_city
                    for seg in segments:
                        # 若交通票有到达日期或到达时间
                        t_date = None
                        if seg.arrival_time:
                            t_date = seg.arrival_time.strftime("%Y-%m-%d")
                        elif seg.travel_date:
                            t_date = seg.travel_date
                        
                        # 若住宿城市是本次交通的到达城市，但住宿日期早于到达日期
                        if t_date and hotel_date_str < t_date:
                            arr_geo = get_city_geo(seg.arrival_city)
                            arr_name = arr_geo.short_name if arr_geo else seg.arrival_city
                            if h_name == arr_name or h_name in arr_name or arr_name in h_name:
                                findings.append(RiskFindingContract(
                                    rule_code="R09_TRAVEL_HOTEL_DATE_CONFLICT",
                                    rule_name="交通行程时间与住宿日期冲突",
                                    risk_level=RiskLevelEnum.HIGH,
                                    agent_role=AgentRoleEnum.ANOMALY,
                                    title=f"申报住宿日期 ({hotel_date_str}) 早于交通票到达目的地日期 ({t_date})",
                                    description=(
                                        f"申请人在 [{hotel_city}] 申报了 {hotel_date_str} 的住宿费，"
                                        f"但抵达该城市的交通车票（{seg.transport_no or '车票'}）到达时间为 {t_date}，"
                                        f"存在尚未抵达成行城市即发生同城住宿费用的异常。"
                                    ),
                                    actual_value={"hotel_date": hotel_date_str, "arrival_date": t_date},
                                    expected_value={"hotel_date_after_or_on_arrival": True},
                                    suggestion="请核实实际行程日期与住宿发票开具日期是否一致。",
                                    is_overridable=True
                                ))

        # 3. 核验：多段车票是否存在时间重叠
        if len(segments) >= 2:
            time_segments = [s for s in segments if s.departure_time and s.arrival_time]
            time_segments.sort(key=lambda s: s.departure_time)
            for i in range(len(time_segments) - 1):
                s1 = time_segments[i]
                s2 = time_segments[i + 1]
                # 段 2 出发时间早于段 1 到达时间
                if s2.departure_time < s1.arrival_time:
                    findings.append(RiskFindingContract(
                        rule_code="R09_TRAVEL_SEGMENT_OVERLAP",
                        rule_name="多段交通行程时间重叠冲突",
                        risk_level=RiskLevelEnum.HIGH,
                        agent_role=AgentRoleEnum.ANOMALY,
                        title=f"行程 [{s1.transport_no or '行程1'}] 与 [{s2.transport_no or '行程2'}] 时间重叠冲突",
                        description=(
                            f"行程1 [{s1.departure_city} -> {s1.arrival_city}] 运行时间为 "
                            f"{s1.departure_time.strftime('%Y-%m-%d %H:%M')} 至 {s1.arrival_time.strftime('%Y-%m-%d %H:%M')}，"
                            f"而行程2 [{s2.departure_city} -> {s2.arrival_city}] 发车时间为 "
                            f"{s2.departure_time.strftime('%Y-%m-%d %H:%M')}，在行程1尚未到达前已发车，存在不可能的时间重叠。"
                        ),
                        actual_value={
                            "segment_1": {"no": s1.transport_no, "dep": str(s1.departure_time), "arr": str(s1.arrival_time)},
                            "segment_2": {"no": s2.transport_no, "dep": str(s2.departure_time), "arr": str(s2.arrival_time)}
                        },
                        expected_value={"overlap_allowed": False},
                        suggestion="请核查多张车票是否存在拆单退票或代报销他人车票情形。",
                        is_overridable=True
                    ))

        # 4. 核验：离散消费事件点与合法交通路径明显矛盾 (同时间另一城市餐饮/消费)
        # 例如：在车票运行区间内，在远距离另一城市发生离散消费
        for seg in segments:
            if not seg.departure_time or not seg.arrival_time:
                continue
            dep_geo = get_city_geo(seg.departure_city)
            arr_geo = get_city_geo(seg.arrival_city)
            for p in spatio_points:
                # 检查点是否落在车票时间区间内
                if seg.departure_time <= p.event_time <= seg.arrival_time:
                    dist_to_dep = SpatioTemporalVerifier.haversine_distance(p.latitude, p.longitude, dep_geo.latitude, dep_geo.longitude) if dep_geo else 999.0
                    dist_to_arr = SpatioTemporalVerifier.haversine_distance(p.latitude, p.longitude, arr_geo.latitude, arr_geo.longitude) if arr_geo else 999.0
                    # 如果距离出发地和到达地均超过 100km，属于在列车行进途中在完全无关的第三地发生消费
                    if dist_to_dep >= 100.0 and dist_to_arr >= 100.0:
                        findings.append(RiskFindingContract(
                            rule_code="R09_SPATIO_TEMPORAL_COLLISION",
                            rule_name="交通行程期间异地消费冲突",
                            risk_level=RiskLevelEnum.HIGH,
                            agent_role=AgentRoleEnum.ANOMALY,
                            title=f"交通行程期间在异地城市 [{p.city_name}] 发生消费冲突",
                            description=(
                                f"申请人在行程 [{seg.transport_no or '交通'}]（{seg.departure_city} -> {seg.arrival_city}，"
                                f"{seg.departure_time.strftime('%H:%M')}~{seg.arrival_time.strftime('%H:%M')}）运行期间，"
                                f"于 {p.event_time.strftime('%Y-%m-%d %H:%M')} 在异地城市 [{p.city_name}] 发生消费记录（发票: {p.invoice_number or '无票号'}），"
                                f"与合法交通路径严重矛盾。"
                            ),
                            actual_value={
                                "point_city": p.city_name,
                                "point_time": str(p.event_time),
                                "point_invoice": p.invoice_number,
                                "segment_from": seg.departure_city,
                                "segment_to": seg.arrival_city,
                                "segment_window": f"{seg.departure_time} ~ {seg.arrival_time}"
                            },
                            expected_value={"simultaneous_transit_and_remote_consumption": False},
                            suggestion="短时间跨城市高速位移异常，需要核实交通凭证和真实行程。",
                            is_overridable=True
                        ))

        return findings
