"""
backend/engines/anomaly_agent/agent.py
Anomaly Agent 子图入口
"""
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from engines.contract.finding import RiskFindingContract, AgentFindingList
from .schemas import InvoiceFact, SpatioPoint
from .hash_verifier import HashVerifier
from .spatio_temporal import SpatioTemporalVerifier
from .sequential_detector import SequentialDetector

class AnomalyAgent:
    """异常行为与反欺诈智能体"""

    @staticmethod
    async def run(
        db: Optional[AsyncSession] = None,
        document_id: int = 0,
        invoices: Optional[List[InvoiceFact]] = None,
        spatio_points: Optional[List[SpatioPoint]] = None,
        historical_fingerprints: Optional[Dict[str, Dict[str, Any]]] = None,
        capabilities: Optional[List[str]] = None
    ) -> AgentFindingList:
        """
        执行全套反欺诈流水线检测：
        1. 修复跨单发票查重，真实传入 historical_fingerprints；
        2. 若规划了跨单全局查重但历史数据不可用，标记为 DEGRADED，不得声称完成全局查重；
        3. 严格通过 capabilities 控制细粒度原子核验项：
           - duplicate_invoice_hash_check → 控制 R08
           - sequential_invoice_number_check → 控制 R10
           - spatio_temporal_trajectory_conflict → 控制 R09
           - capabilities=None 时保持原有全量执行行为，向后兼容。
        """
        findings: List[RiskFindingContract] = []
        inv_list = invoices or []

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
        run_seq_check = (
            capabilities is None
            or "sequential_invoice_number_check" in capabilities
        )

        is_degraded = False
        degraded_reason: Optional[str] = None
        source: str = "DETERMINISTIC_RULE"

        # 判断是否显式规划了跨单发票查重
        planned_cross_check = (
            capabilities is not None and (
                "cross_document_duplicate_check" in capabilities
                or "cross_document_invoice_check" in capabilities
            )
        )
        if planned_cross_check and historical_fingerprints is None:
            is_degraded = True
            degraded_reason = "规划了跨单全局发票查重但历史发票数据源未就绪，仅完成单内查重，审核已降级"

        # 2. 发票哈希防重查验 (R08)
        if run_dup_check and inv_list:
            dup_findings = HashVerifier.detect_duplicate_invoices(
                invoices=inv_list,
                historical_fingerprints=historical_fingerprints
            )
            findings.extend(dup_findings)

        # 3. 时空物理碰撞检测 (R09)
        if run_spatio_check and spatio_points and len(spatio_points) >= 2:
            st_findings = SpatioTemporalVerifier.detect_spatio_temporal_collisions(spatio_points)
            findings.extend(st_findings)

        # 4. 连号发票拆单检测 (R10)
        if run_seq_check and len(inv_list) >= 2:
            seq_findings = SequentialDetector.detect_sequential_invoices(inv_list)
            findings.extend(seq_findings)

        return AgentFindingList(
            findings,
            is_degraded=is_degraded,
            degraded_reason=degraded_reason,
            source=source
        )
