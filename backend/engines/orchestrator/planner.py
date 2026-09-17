"""
backend/engines/orchestrator/planner.py
智能审核计划器 (AuditPlanner) 与执行规划模型 (AuditExecutionPlan)
实现 Agent 粗粒度路由与细粒度 Capability 剪枝规划，赋予风控流水线全透明的审计可解释性
"""
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field, ConfigDict

from engines.contract.agent_role import AgentRoleEnum
from engines.contract.result import AgentExecutionStatus

class PlannedAgentTask(BaseModel):
    """单个智能体能力规划明细"""
    model_config = ConfigDict(frozen=True)

    role: AgentRoleEnum = Field(..., description="智能体角色")
    enabled: bool = Field(..., description="是否规划执行")
    mandatory: bool = Field(default=False, description="是否为核心必验项 (若必验项失败则禁止自动放行)")
    status: AgentExecutionStatus = Field(default=AgentExecutionStatus.PLANNED, description="规划初态: PLANNED / SKIPPED")
    reason: str = Field(..., description="规划依据说明 (如 mandatory / missing_facts / not_applicable)")
    capabilities: List[str] = Field(default_factory=list, description="细粒度启用的核验能力标签清单")
    meta: Dict[str, Any] = Field(default_factory=dict, description="执行辅助元数据 (如候选供应商清单、轨迹点数量)")

class AuditExecutionPlan(BaseModel):
    """智能风控全流程可解释执行计划快照"""
    model_config = ConfigDict(frozen=True)

    document_id: int = Field(..., description="所属单据 ID")
    document_type: str = Field(..., description="单据业务大类")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tasks: List[PlannedAgentTask] = Field(default_factory=list, description="规划的智能体任务清单")
    total_capabilities_count: int = Field(default=0, description="规划执行的总核验能力项数量")
    explanation_summary: str = Field(..., description="可解释执行策略总述")

    def get_task(self, role: AgentRoleEnum) -> Optional[PlannedAgentTask]:
        for t in self.tasks:
            if t.role == role:
                return t
        return None

class AuditPlanner:
    """
    智能审核规划中枢 (Audit Planner):
    依据 document_type 业务矩阵与不可变事实的要素探测 (Field Presence Probe)，
    先行规划出确定性、可审计、细粒度的 Execution Plan。
    """

    @classmethod
    def build_plan(cls, document_id: int, document_type: str, facts: Dict[str, Any]) -> AuditExecutionPlan:
        tasks: List[PlannedAgentTask] = []
        invoices = facts.get("invoices") or []
        line_items = facts.get("line_items") or []
        spatio_points = facts.get("spatio_points") or []

        # -------------------------------------------------------------
        # 1. AmountAgent (金额算术核验 - 全单据一票否决必检项)
        # 仅挂载真实实现的确定性五方交叉核算能力
        # -------------------------------------------------------------
        amount_caps = ["five_way_reconciliation"]
        tasks.append(PlannedAgentTask(
            role=AgentRoleEnum.AMOUNT,
            enabled=True,
            mandatory=True,
            status=AgentExecutionStatus.PLANNED,
            reason="MANDATORY_DETERMINISTIC_RECONCILIATION",
            capabilities=amount_caps,
            meta={"items_count": len(line_items), "invoices_count": len(invoices)}
        ))

        # -------------------------------------------------------------
        # 2. PolicyAgent (规章制度合规 - 依据业务类型动态绑定真实实现规则)
        # -------------------------------------------------------------
        if document_type == "TRAVEL_REIMBURSEMENT":
            policy_caps = [
                "travel_hotel_limit",
                "business_purpose_check"
            ]
            policy_reason = "TRAVEL_POLICY_RULESET"
        elif document_type in ["CORP_PAYMENT", "ADVANCE_PAYMENT", "BATCH_PAYMENT"]:
            policy_caps = [
                "business_purpose_check"
            ]
            policy_reason = "CORPORATE_PROCUREMENT_RULESET"
        else:
            policy_caps = [
                "business_purpose_check"
            ]
            policy_reason = "GENERAL_EXPENSE_RULESET"

        tasks.append(PlannedAgentTask(
            role=AgentRoleEnum.POLICY,
            enabled=True,
            mandatory=True,
            status=AgentExecutionStatus.PLANNED,
            reason=policy_reason,
            capabilities=policy_caps,
            meta={"document_type": document_type}
        ))

        # -------------------------------------------------------------
        # 3. AnomalyAgent (异常行为与时序欺诈 - 依据发票、交通行程与时空数据细粒度激活)
        # -------------------------------------------------------------
        anomaly_caps: List[str] = []
        travel_segments = facts.get("travel_segments") or []

        if len(invoices) >= 1:
            anomaly_caps.append("duplicate_invoice_hash_check")
        if len(invoices) >= 2:
            anomaly_caps.append("sequential_invoice_number_check")
        if len(travel_segments) >= 1:
            anomaly_caps.append("travel_segment_consistency")
        if len(spatio_points) >= 2:
            anomaly_caps.append("spatio_temporal_trajectory_conflict")

        if anomaly_caps:
            # 判断激活原因：有交通行程但缺失精确发到时刻
            has_exact_time = any(
                seg.get("departure_time") and seg.get("arrival_time")
                for seg in travel_segments
            )
            if travel_segments and not has_exact_time and len(invoices) <= 1:
                anomaly_reason = "PARTIAL: TRAVEL_TIME_MISSING"
            elif travel_segments:
                anomaly_reason = "TRAVEL_SEGMENT_DETECTED"
            else:
                anomaly_reason = "INVOICE_OR_SPATIO_FACTS_DETECTED"

            tasks.append(PlannedAgentTask(
                role=AgentRoleEnum.ANOMALY,
                enabled=True,
                mandatory=False,
                status=AgentExecutionStatus.PLANNED,
                reason=anomaly_reason,
                capabilities=anomaly_caps,
                meta={
                    "invoices_count": len(invoices),
                    "spatio_points_count": len(spatio_points),
                    "travel_segments_count": len(travel_segments)
                }
            ))
        else:
            if document_type == "TRAVEL_REIMBURSEMENT" and len(line_items) > 0:
                skip_reason = "SKIPPED_INSUFFICIENT_FACTS: 差旅明细仅包含单一行程城市，缺少起止轨迹与时间"
            else:
                skip_reason = "NOT_APPLICABLE: NO_INVOICE_AND_INSUFFICIENT_TRAJECTORY_POINTS"

            tasks.append(PlannedAgentTask(
                role=AgentRoleEnum.ANOMALY,
                enabled=False,
                mandatory=False,
                status=AgentExecutionStatus.SKIPPED,
                reason=skip_reason,
                capabilities=[],
                meta={"invoices_count": len(invoices), "spatio_points_count": len(spatio_points)}
            ))

        # -------------------------------------------------------------
        # 4. SupplierAgent (供应商工商资质与司法失信穿透 - 仅对公付款且需真实供应商主体)
        # -------------------------------------------------------------
        if document_type not in ["CORP_PAYMENT", "ADVANCE_PAYMENT", "BATCH_PAYMENT"]:
            tasks.append(PlannedAgentTask(
                role=AgentRoleEnum.SUPPLIER,
                enabled=False,
                mandatory=False,
                status=AgentExecutionStatus.SKIPPED,
                reason="NOT_APPLICABLE: DOCUMENT_TYPE_EXEMPT",
                capabilities=[],
                meta={"document_type": document_type}
            ))
        else:
            # 提取去重供应商清单：按规范化 USCC 去重，不要按 (USCC, name) 重复执行同一家企业
            unique_suppliers_by_uscc: Dict[str, str] = {}
            for inv in invoices:
                tax_id = (inv.get("seller_tax_id") or "").strip().upper()
                name = (inv.get("seller_name") or "").strip()
                if tax_id and name and tax_id not in unique_suppliers_by_uscc:
                    unique_suppliers_by_uscc[tax_id] = name

            if not unique_suppliers_by_uscc:
                # 缺失供应商税号事实：对公付款不可伪造，必须显式声明跳过并标识为降级
                tasks.append(PlannedAgentTask(
                    role=AgentRoleEnum.SUPPLIER,
                    enabled=False,
                    mandatory=True,
                    status=AgentExecutionStatus.SKIPPED,
                    reason="DATA_MISSING: SUPPLIER_IDENTITY_MISSING",
                    capabilities=[],
                    meta={"error": "对公单据发票中未提取到有效销售方税号或供应商名称"}
                ))
            else:
                tasks.append(PlannedAgentTask(
                    role=AgentRoleEnum.SUPPLIER,
                    enabled=True,
                    mandatory=True,
                    status=AgentExecutionStatus.PLANNED,
                    reason="CORPORATE_PAYMENT_SUPPLIER_DILIGENCE",
                    capabilities=[
                        "uscc_checksum_validation",
                        "dishonest_debtor_check",
                        "shell_company_investigation",
                        "business_scope_matching"
                    ],
                    meta={"suppliers": [{"uscc": u, "name": n} for u, n in unique_suppliers_by_uscc.items()]}
                ))

        total_caps = sum(len(t.capabilities) for t in tasks if t.enabled)
        enabled_roles = [t.role.value for t in tasks if t.enabled]
        skipped_roles = [f"{t.role.value}({t.reason})" for t in tasks if not t.enabled]

        explanation = (
            f"依据单据类型[{document_type}]与要素探测完成规划：启用 {len(enabled_roles)} 个智能体 ({', '.join(enabled_roles)})，"
            f"共挂载 {total_caps} 项核验能力。"
        )
        if skipped_roles:
            explanation += f" 裁剪跳过: {', '.join(skipped_roles)}。"

        return AuditExecutionPlan(
            document_id=document_id,
            document_type=document_type,
            tasks=tasks,
            total_capabilities_count=total_caps,
            explanation_summary=explanation
        )
