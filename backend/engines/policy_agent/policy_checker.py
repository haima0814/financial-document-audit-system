"""
backend/engines/policy_agent/policy_checker.py
制度规则执行器：差旅标准双门禁核验与优雅降级兜底
"""
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any
from engines.contract.finding import RiskFindingContract, RiskLevelEnum
from engines.contract.agent_role import AgentRoleEnum
from .city_geo import get_city_geo


class PolicyChecker:
    @staticmethod
    def _safe_decimal(val: Any) -> Decimal | None:
        """安全转换 Decimal，非法或缺失值返回 None，严禁默认伪造为 0"""
        if val is None:
            return None
        if isinstance(val, Decimal):
            return val
        s = str(val).strip()
        if not s or s.lower() in ("none", "null", "nan", "undefined"):
            return None
        try:
            return Decimal(s)
        except (InvalidOperation, TypeError, ValueError):
            return None

    @staticmethod
    def verify_policy_compliance(
        document_type: str,
        line_items: List[Dict[str, Any]]
    ) -> List[RiskFindingContract]:
        """对单据明细执行制度合规审查"""
        findings: List[RiskFindingContract] = []

        for idx, item in enumerate(line_items, 1):
            expense_type = str(item.get("expense_type", ""))
            city_name = item.get("city_name")

            # 1. 差旅住宿费限额核验
            if "住宿" in expense_type:
                amount = PolicyChecker._safe_decimal(item.get("amount"))
                # 若金额缺失或无法解析，跳过对应金额数学/限额核验，严禁假设为 0 制造虚假报警
                if amount is None:
                    continue

                if not city_name:
                    # 城市缺失：三层降级兜底 R14 (零崩溃)
                    findings.append(RiskFindingContract(
                        rule_code="R14_POLICY_NOT_FOUND",
                        rule_name="差旅城市缺失或无对应制度标准",
                        risk_level=RiskLevelEnum.LOW,
                        agent_role=AgentRoleEnum.POLICY,
                        title=f"第 {idx} 行住宿费缺少出差城市标识",
                        description=f"明细项[{item.get('item_desc', '住宿')}]未指定出差城市，系统知识库无法定位精准住宿上限，已自动降级转为人工财务复核。",
                        actual_value={"claimed_amount": str(amount), "city_name": None},
                        suggestion="建议发起人补充出差目的城市，请财务审批人核对实际住宿城市标准。",
                        is_overridable=True
                    ))
                    continue

                # 直接通过 get_city_geo 查询城市地理实体
                geo = get_city_geo(city_name)
                if geo is None:
                    # 未收录城市：触发 R14 柔性降级，禁止假设为 TIER_3 / 260 元制造超标假阳性
                    findings.append(RiskFindingContract(
                        rule_code="R14_POLICY_NOT_FOUND",
                        rule_name="差旅城市缺失或无对应制度标准",
                        risk_level=RiskLevelEnum.LOW,
                        agent_role=AgentRoleEnum.POLICY,
                        title=f"第 {idx} 行出差城市[{city_name}]未收录或无对应标准",
                        description=f"明细项[{item.get('item_desc', '住宿')}]出差城市为[{city_name}]，系统制度知识库尚未收录该城市差旅定额标准，已自动降级转为人工财务复核，避免误判超标。",
                        actual_value={"claimed_amount": str(amount), "city_name": city_name},
                        suggestion="建议发起人补充出差目的城市制度说明，请财务审批人核对实际住宿城市标准。",
                        is_overridable=True
                    ))
                    continue

                standard_limit = Decimal(str(geo.hotel_limit))
                city_tier = geo.tier

                # 住宿间夜多晚折算：优先只读取 stay_nights 或 nights
                raw_nights = item.get("stay_nights") if item.get("stay_nights") is not None else item.get("nights")
                nights: int | None = None
                if raw_nights is not None:
                    try:
                        n = int(Decimal(str(raw_nights)))
                        if n > 0:
                            nights = n
                    except Exception:
                        nights = None

                # quantity 只有在明确标识为“住宿间夜数”时才作为 fallback，避免把房间数/人数误认作晚数
                if nights is None and item.get("quantity") is not None:
                    unit = str(item.get("unit") or item.get("unit_name") or "").strip()
                    item_desc = str(item.get("item_desc") or "").strip()
                    is_night_unit = (
                        unit in ("晚", "夜", "间夜", "间/夜", "间·夜")
                        or "间夜" in item_desc
                        or "晚" in unit
                    )
                    if is_night_unit:
                        try:
                            n = int(Decimal(str(item.get("quantity"))))
                            if n > 0:
                                nights = n
                        except Exception:
                            nights = None

                if nights is not None and nights > 0:
                    # 存在有效住宿晚数：按单晚均摊金额与标准比较
                    nightly_amount = amount / Decimal(str(nights))
                    if nightly_amount > standard_limit:
                        nightly_diff = nightly_amount - standard_limit
                        total_diff = amount - (standard_limit * Decimal(str(nights)))
                        overrun_pct = nightly_diff / standard_limit
                        risk_lvl = RiskLevelEnum.HIGH if overrun_pct >= Decimal("0.50") else RiskLevelEnum.MEDIUM

                        findings.append(RiskFindingContract(
                            rule_code="R05_POLICY_EXCEEDED",
                            rule_name="差旅住宿费用超标",
                            risk_level=risk_lvl,
                            agent_role=AgentRoleEnum.POLICY,
                            title=f"在[{geo.short_name}]住宿费超标 {total_diff:.2f} 元 (超标率 {overrun_pct*100:.1f}%)",
                            description=(
                                f"单据第 {idx} 行住宿申报总额为 {amount:.2f} 元，共 {nights} 晚，折合单晚 {nightly_amount:.2f} 元/间夜。"
                                f"根据公司差旅标准，{geo.name}属于[{city_tier}]城市，住宿标准上限为 {standard_limit:.2f} 元/间夜（总限额 {standard_limit * Decimal(str(nights)):.2f} 元）。"
                                f"本次报销超标 {total_diff:.2f} 元。"
                            ),
                            actual_value={"claimed_amount": str(amount), "stay_nights": nights, "nightly_amount": str(nightly_amount), "city_name": city_name, "city_tier": city_tier},
                            expected_value={"standard_limit": str(standard_limit), "total_limit": str(standard_limit * Decimal(str(nights)))},
                            discrepancy_amount=total_diff,
                            suggestion="超出标准部分建议按公司制度自理扣除，或由分管总监具名特批放行。",
                            is_overridable=True
                        ))
                else:
                    # 未指定晚数：按单笔金额核验，文案避免写成'元/间夜'
                    if amount > standard_limit:
                        diff = amount - standard_limit
                        overrun_pct = diff / standard_limit
                        risk_lvl = RiskLevelEnum.HIGH if overrun_pct >= Decimal("0.50") else RiskLevelEnum.MEDIUM

                        findings.append(RiskFindingContract(
                            rule_code="R05_POLICY_EXCEEDED",
                            rule_name="差旅住宿费用超标",
                            risk_level=risk_lvl,
                            agent_role=AgentRoleEnum.POLICY,
                            title=f"在[{geo.short_name}]住宿费超标 {diff:.2f} 元 (超标率 {overrun_pct*100:.1f}%)",
                            description=(
                                f"单据第 {idx} 行住宿申报金额为 {amount:.2f} 元（未指定住宿晚数，按单笔比对）。"
                                f"根据公司差旅标准，{geo.name}属于[{city_tier}]城市，住宿标准上限为 {standard_limit:.2f} 元。"
                                f"本次报销超标 {diff:.2f} 元。"
                            ),
                            actual_value={"claimed_amount": str(amount), "city_name": city_name, "city_tier": city_tier},
                            expected_value={"standard_limit": str(standard_limit)},
                            discrepancy_amount=diff,
                            suggestion="超出标准部分建议按公司制度自理扣除，或由分管总监具名特批放行。",
                            is_overridable=True
                        ))

        return findings
