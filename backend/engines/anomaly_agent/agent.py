"""
backend/engines/anomaly_agent/agent.py
Anomaly Agent 子图入口
"""
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from engines.contract.finding import RiskFindingContract, AgentFindingList
from engines.contract.result import CapabilityStatus, CapabilityExecutionResult
from .schemas import InvoiceFact, SpatioPoint, TravelSegment
from .hash_verifier import HashVerifier
from .spatio_temporal import SpatioTemporalVerifier
from .sequential_detector import SequentialDetector
from .travel_verifier import TravelSegmentVerifier

class AnomalyAgent:
    """异常行为与反欺诈智能体"""

    @staticmethod
    async def run(
        db: Optional[AsyncSession] = None,
        document_id: int = 0,
        invoices: Optional[List[InvoiceFact]] = None,
        spatio_points: Optional[List[SpatioPoint]] = None,
        travel_segments: Optional[List[TravelSegment]] = None,
        line_items: Optional[List[Dict[str, Any]]] = None,
        historical_fingerprints: Optional[Dict[str, Dict[str, Any]]] = None,
        capabilities: Optional[List[str]] = None
    ) -> AgentFindingList:
        """
        执行能力级完整度反欺诈流水线检测：
        1. 细粒度原子核验能力执行与状态报告：
           - duplicate_invoice_hash_check (发票防重)
           - sequential_invoice_number_check (连号发票)
           - spatio_temporal_trajectory_conflict (离散时空碰撞)
           - travel_route_consistency (交通行程起终点与申报城市一致性)
           - travel_date_consistency (行程日期与申报住宿日期一致性)
           - departure_time_check (发车/出发时刻完备性)
           - in_transit_collision_check (在途时间区间碰撞与重叠，增强可选核验)
        2. 顶层 Agent 状态由 capability 结果聚合：
           - 存在 mandatory 为 BLOCKED / FAILED -> DEGRADED (is_degraded=True)
           - mandatory 全部 VERIFIED，仅 optional/enhanced 为 PARTIAL -> SUCCESS (is_degraded=False, 带受限说明)
           - 全部为 NOT_APPLICABLE -> SUCCESS
           - 全部 VERIFIED -> SUCCESS
        """
        findings: List[RiskFindingContract] = []
        inv_list = invoices or []
        seg_list = travel_segments or []
        cap_results: List[CapabilityExecutionResult] = []

        # 1. Capabilities 门禁映射
        run_dup_check = (
            capabilities is None
            or "duplicate_invoice_hash_check" in capabilities
            or "cross_document_duplicate_check" in capabilities
        )
        run_spatio_check = (
            capabilities is None
            or "spatio_temporal_trajectory_conflict" in capabilities
        )
        run_travel_check = (
            capabilities is None
            or "travel_segment_consistency" in capabilities
            or "travel_route_consistency" in capabilities
            or "travel_date_consistency" in capabilities
            or "departure_time_check" in capabilities
            or "in_transit_collision_check" in capabilities
        )
        run_seq_check = (
            capabilities is None
            or "sequential_invoice_number_check" in capabilities
        )

        source: str = "DETERMINISTIC_RULE"

        # 2. 发票哈希防重查验 (R08)
        if run_dup_check:
            planned_cross_check = (
                capabilities is not None and (
                    "cross_document_duplicate_check" in capabilities
                    or "cross_document_invoice_check" in capabilities
                )
            )
            if planned_cross_check:
                if historical_fingerprints is None:
                    cap_results.append(CapabilityExecutionResult(
                        capability="cross_document_duplicate_check",
                        status=CapabilityStatus.BLOCKED,
                        mandatory=True,
                        reason="规划了跨单全局发票查重但历史发票数据源未就绪，仅完成单内查重，审核已降级",
                        missing_fields=["historical_fingerprints"]
                    ))
                else:
                    cap_results.append(CapabilityExecutionResult(
                        capability="cross_document_duplicate_check",
                        status=CapabilityStatus.VERIFIED,
                        mandatory=True,
                        reason="跨单全局发票防重查验完成"
                    ))

            if inv_list:
                dup_findings = HashVerifier.detect_duplicate_invoices(
                    invoices=inv_list,
                    historical_fingerprints=historical_fingerprints
                )
                findings.extend(dup_findings)
                cap_results.append(CapabilityExecutionResult(
                    capability="duplicate_invoice_hash_check",
                    status=CapabilityStatus.VERIFIED,
                    mandatory=True,
                    reason="发票防重与指纹查验完成"
                ))
            else:
                cap_results.append(CapabilityExecutionResult(
                    capability="duplicate_invoice_hash_check",
                    status=CapabilityStatus.NOT_APPLICABLE,
                    mandatory=False,
                    reason="单据无发票凭证"
                ))

        # 3. 交通行程段一致性核验 (分能力判定)
        if run_travel_check:
            if seg_list:
                # 3.1 travel_route_consistency (mandatory=True)
                missing_route = []
                for i, s in enumerate(seg_list):
                    if not s.departure_city or not s.departure_city.strip() or not s.arrival_city or not s.arrival_city.strip():
                        missing_route.append(f"segment_{i+1}(route)")
                if missing_route:
                    cap_results.append(CapabilityExecutionResult(
                        capability="travel_route_consistency",
                        status=CapabilityStatus.BLOCKED,
                        mandatory=True,
                        reason=f"缺少行程出发或到达城市: {', '.join(missing_route)}",
                        missing_fields=missing_route
                    ))
                else:
                    route_findings = TravelSegmentVerifier.verify_route_consistency(seg_list, line_items)
                    findings.extend(route_findings)
                    cap_results.append(CapabilityExecutionResult(
                        capability="travel_route_consistency",
                        status=CapabilityStatus.VERIFIED,
                        mandatory=True,
                        reason="交通行程路线与报销申报城市一致性核验完成"
                    ))

                # 3.2 travel_date_consistency (mandatory=True)
                missing_date = []
                for i, s in enumerate(seg_list):
                    t_date = s.travel_date or (s.departure_time.strftime("%Y-%m-%d") if s.departure_time else None)
                    if not t_date:
                        missing_date.append(f"segment_{i+1}(travel_date)")
                if missing_date:
                    cap_results.append(CapabilityExecutionResult(
                        capability="travel_date_consistency",
                        status=CapabilityStatus.BLOCKED,
                        mandatory=True,
                        reason=f"缺少出行日期: {', '.join(missing_date)}",
                        missing_fields=missing_date
                    ))
                else:
                    date_findings = TravelSegmentVerifier.verify_date_consistency(seg_list, line_items)
                    findings.extend(date_findings)
                    cap_results.append(CapabilityExecutionResult(
                        capability="travel_date_consistency",
                        status=CapabilityStatus.VERIFIED,
                        mandatory=True,
                        reason="交通行程日期与申报明细及住宿日期一致性核验完成"
                    ))

                # 3.3 departure_time_check (mandatory=True)
                missing_dep = []
                for i, s in enumerate(seg_list):
                    if s.departure_time is None:
                        missing_dep.append(f"segment_{i+1}(departure_time)")
                if missing_dep:
                    cap_results.append(CapabilityExecutionResult(
                        capability="departure_time_check",
                        status=CapabilityStatus.BLOCKED,
                        mandatory=True,
                        reason="PARTIAL: TRAVEL_TIME_MISSING",
                        missing_fields=missing_dep
                    ))
                else:
                    cap_results.append(CapabilityExecutionResult(
                        capability="departure_time_check",
                        status=CapabilityStatus.VERIFIED,
                        mandatory=True,
                        reason="交通出发时刻核验完成"
                    ))

                # 3.4 in_transit_collision_check (mandatory=False 增强/可选能力)
                missing_dep_for_transit = any(s.departure_time is None for s in seg_list)
                missing_arr_for_transit = any(s.arrival_time is None for s in seg_list)
                if missing_dep_for_transit:
                    cap_results.append(CapabilityExecutionResult(
                        capability="in_transit_collision_check",
                        status=CapabilityStatus.BLOCKED,
                        mandatory=False,
                        reason="缺少出发时刻，无法开展在途时空碰撞核验",
                        missing_fields=["departure_time"]
                    ))
                elif missing_arr_for_transit:
                    cap_results.append(CapabilityExecutionResult(
                        capability="in_transit_collision_check",
                        status=CapabilityStatus.PARTIAL,
                        mandatory=False,
                        reason="原始凭证未提供到达时刻，未执行完整在途时间区间碰撞核验",
                        missing_fields=["arrival_time"]
                    ))
                else:
                    transit_findings = TravelSegmentVerifier.verify_in_transit_collisions(seg_list, spatio_points)
                    findings.extend(transit_findings)
                    cap_results.append(CapabilityExecutionResult(
                        capability="in_transit_collision_check",
                        status=CapabilityStatus.VERIFIED,
                        mandatory=False,
                        reason="在途时间区间碰撞与多段行程重叠核验完成"
                    ))

                # 兼容旧能力名 travel_segment_consistency
                travel_statuses = [c.status for c in cap_results if c.capability in ("travel_route_consistency", "travel_date_consistency", "departure_time_check", "in_transit_collision_check")]
                if CapabilityStatus.BLOCKED in travel_statuses:
                    comb_status = CapabilityStatus.BLOCKED
                    comb_reason = "PARTIAL: TRAVEL_TIME_MISSING" if any(c.status == CapabilityStatus.BLOCKED and c.capability == "departure_time_check" for c in cap_results) else "交通行程关键要素缺失"
                elif CapabilityStatus.PARTIAL in travel_statuses:
                    comb_status = CapabilityStatus.PARTIAL
                    comb_reason = "PARTIAL: TRAVEL_ARRIVAL_TIME_NOT_PROVIDED"
                else:
                    comb_status = CapabilityStatus.VERIFIED
                    comb_reason = "交通行程全量核验完成"
                cap_results.append(CapabilityExecutionResult(
                    capability="travel_segment_consistency",
                    status=comb_status,
                    mandatory=True,
                    reason=comb_reason
                ))
            else:
                cap_results.append(CapabilityExecutionResult(
                    capability="travel_segment_consistency",
                    status=CapabilityStatus.NOT_APPLICABLE,
                    mandatory=False,
                    reason="无交通行程凭证"
                ))

        # 4. 时空物理碰撞检测 (R09)
        if run_spatio_check:
            if spatio_points and len(spatio_points) >= 2:
                st_findings = SpatioTemporalVerifier.detect_spatio_temporal_collisions(spatio_points)
                findings.extend(st_findings)
                cap_results.append(CapabilityExecutionResult(
                    capability="spatio_temporal_trajectory_conflict",
                    status=CapabilityStatus.VERIFIED,
                    mandatory=False,
                    reason="时空物理碰撞检测完成"
                ))
            else:
                cap_results.append(CapabilityExecutionResult(
                    capability="spatio_temporal_trajectory_conflict",
                    status=CapabilityStatus.NOT_APPLICABLE,
                    mandatory=False,
                    reason="时空轨迹点少于2个，不适用时空物理碰撞检测"
                ))

        # 5. 连号发票拆单检测 (R10)
        if run_seq_check:
            if len(inv_list) >= 2:
                seq_findings = SequentialDetector.detect_sequential_invoices(inv_list)
                findings.extend(seq_findings)
                cap_results.append(CapabilityExecutionResult(
                    capability="sequential_invoice_number_check",
                    status=CapabilityStatus.VERIFIED,
                    mandatory=False,
                    reason="连号发票拆单检测完成"
                ))
            else:
                cap_results.append(CapabilityExecutionResult(
                    capability="sequential_invoice_number_check",
                    status=CapabilityStatus.NOT_APPLICABLE,
                    mandatory=False,
                    reason="发票少于2张，不适用连号发票拆单检测"
                ))

        # 6. 顶层状态由能力级结果聚合
        blocked_mandatories = [
            c for c in cap_results
            if c.mandatory and c.status in (CapabilityStatus.BLOCKED, CapabilityStatus.FAILED)
        ]
        partial_optionals = [
            c for c in cap_results
            if c.status == CapabilityStatus.PARTIAL
        ]

        if blocked_mandatories:
            is_degraded = True
            dep_blocked = [c for c in blocked_mandatories if c.capability == "departure_time_check"]
            if dep_blocked:
                degraded_reason = dep_blocked[0].reason or "PARTIAL: TRAVEL_TIME_MISSING"
            else:
                degraded_reason = blocked_mandatories[0].reason or "关键核验能力受阻降级"
        elif partial_optionals:
            is_degraded = False
            degraded_reason = f"能力受限：{partial_optionals[0].reason or '部分核验完成'}"
        else:
            is_degraded = False
            degraded_reason = "执行成功"

        return AgentFindingList(
            findings,
            is_degraded=is_degraded,
            degraded_reason=degraded_reason,
            source=source,
            capability_results=cap_results
        )
