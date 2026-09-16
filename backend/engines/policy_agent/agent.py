"""
backend/engines/policy_agent/agent.py
Policy Agent 子图入口
"""
from typing import List, Dict, Any, Optional
from engines.contract.finding import RiskFindingContract
from .policy_checker import PolicyChecker

class PolicyAgent:
    """制度合规审查智能体 (确定性城市限额比对 + 大模型事由合理性推理)"""

    @staticmethod
    async def run(
        document_type: str,
        line_items: List[Dict[str, Any]],
        document_title: str = "",
        department_name: Optional[str] = None,
        capabilities: Optional[List[str]] = None
    ) -> List[RiskFindingContract]:
        """执行制度合规审查"""
        findings: List[RiskFindingContract] = []

        # 1. 确定性制度核验 (差旅住宿限额)
        run_hotel_check = (
            capabilities is None
            or "travel_hotel_limit" in capabilities
            or "travel_hotel_tier_limit" in capabilities
        )
        if run_hotel_check:
            findings.extend(
                PolicyChecker.verify_policy_compliance(
                    document_type=document_type,
                    line_items=line_items
                )
            )

        # 2. 大模型深度审查：事由业务真实性与公款私用推演 (business_purpose_check)
        run_rationality_check = (
            capabilities is None
            or "business_purpose_check" in capabilities
        )
        is_degraded = False
        degraded_reason: Optional[str] = None
        source: Optional[str] = None

        if run_rationality_check:
            from app.core.llm_client import LLMClient
            from engines.contract.finding import RiskLevelEnum
            from engines.contract.agent_role import AgentRoleEnum

            rationality_res = await LLMClient.audit_policy_rationality(
                title=document_title or "公务支出",
                department_name=department_name,
                line_items=line_items
            )

            source = rationality_res.get("source", "LLM_INFERENCE")
            raw_val = rationality_res.get("is_rational")
            is_rational = LLMClient.parse_bool_safely(raw_val, default=None)

            # 若 parse_bool_safely 返回 None (无法识别)，执行启发式规则兜底判断，避免解析失败自动合规
            if is_rational is None:
                source = "HEURISTIC_RULE"
                sensitive_keywords = ["高尔夫", "游戏充值", "酒吧", "ktv", "美容美发", "奢侈品", "名牌包", "个人烟酒", "度假村套票"]
                found_sensitive = []
                for item in line_items:
                    desc = str(item.get("item_desc", "")) + str(item.get("expense_type", ""))
                    for kw in sensitive_keywords:
                        if kw in desc.lower():
                            found_sensitive.append(kw)
                is_rational = not bool(found_sensitive)

            if source == "HEURISTIC_RULE":
                is_degraded = True
                degraded_reason = "大模型服务不可用，事由合规退化为本地启发式规则降级审核"

            if not is_rational:
                desc_prefix = "【降级审核-启发式兜底】" if source == "HEURISTIC_RULE" else ""
                findings.append(RiskFindingContract(
                    finding_id=f"find_policy_rat_{len(findings)+1}",
                    rule_code="R16_BUSINESS_PURPOSE_MISMATCH",
                    rule_name="报销事由与明细偏离(公款私用嫌疑)",
                    risk_level=RiskLevelEnum.HIGH,
                    agent_role=AgentRoleEnum.POLICY,
                    title="报销事由与消费明细存在业务逻辑冲突",
                    description=desc_prefix + rationality_res.get("risk_analysis", "明细消费项与因公申报事由脱节，疑似个人消费因公报销。"),
                    actual_value={"detected_issue": "检出非因公或个人敏感消费特征", "source": source, "is_degraded": is_degraded},
                    expected_value={"is_business_related": True},
                    discrepancy_amount=0.0,
                    evidence_ids=["line_items"],
                    suggestion="要求经办人补充提供公务活动签到表、合同背书或商务招待同行人员审批单，若属个人消费应立即剔除并退单",
                    is_overridable=True
                ))

        from engines.contract.finding import AgentFindingList
        return AgentFindingList(
            findings,
            is_degraded=is_degraded,
            degraded_reason=degraded_reason,
            source=source
        )

