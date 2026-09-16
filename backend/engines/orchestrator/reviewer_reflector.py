import logging
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any, Tuple, Optional
from engines.contract.finding import RiskFindingContract, RiskLevelEnum
from engines.contract.agent_role import AgentRoleEnum

logger = logging.getLogger("orchestrator.reviewer_reflector")

class ReviewerReflector:
    """
    终审质检反思执行器
    核心职责：
    1. 交叉比对 AmountAgent 的未平账差额与 Policy 允许的包干免票津贴 (解决差旅/外勤免票津贴被误报为高危不平账的核心痛点)
    2. 执行二阶反思消歧：必须具备 Policy/Context 明确免票制度证据才允许消歧，无制度证据严格保留原 R02 违规项
    3. 完全消歧后不作为 LOW 风险项扣分，仅保留 reflection log
    4. 采用严格 Fail-Safe 机制：任何反思异常均全量回退保留原始 Findings
    """

    # 常见免票定额包干津贴与补助关键词 (仅用于初筛 candidate allowance)
    ALLOWANCE_KEYWORDS = ["津贴", "补贴", "补助", "包干", "餐补", "油补", "车补", "房补", "外勤"]
    
    # 明确定义免票标准费用类别
    ALLOWANCE_EXPENSE_TYPES = [
        "差旅津贴", "交通补贴", "伙食补贴", "通讯补贴", "定额补助",
        "出差补贴", "加班餐补", "市内交通补贴", "包干补贴", "自驾油补", "误餐补助"
    ]

    @staticmethod
    def _safe_decimal(val: Any) -> Optional[Decimal]:
        """安全转换 Decimal，解析失败返回 None，严禁崩溃"""
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

    @classmethod
    def is_candidate_allowance(cls, item: Dict[str, Any]) -> bool:
        """判定明细项是否属于候选定额免票津贴/包干项目"""
        if not isinstance(item, dict):
            return False
        exp_type = str(item.get("expense_type", "")).strip()
        item_desc = str(item.get("item_desc", "")).strip()

        if exp_type in cls.ALLOWANCE_EXPENSE_TYPES:
            return True

        for kw in cls.ALLOWANCE_KEYWORDS:
            if kw in exp_type or kw in item_desc:
                return True

        return False

    # 保持向后兼容别名
    is_allowance_item = is_candidate_allowance

    @classmethod
    def has_allowance_policy_evidence(
        cls,
        document_facts: Dict[str, Any],
        candidate_items: List[Dict[str, Any]]
    ) -> bool:
        """
        严格校验 Policy/Context 中是否存在明确的免票制度验证依据：
        1. document_facts 显式包含 allowance_policy_verified 或 policy_allowance_exempt / allowance_verified
        2. extra_context 包含 allowance_policy_verified 或 policy_allowance_exempt / allowance_verified
        3. 候选行项中明确声明已通过津贴制度验证 (如 allowance_verified=True, allowance_policy_verified=True, policy_allowance_exempt=True)
        4. rules 清单中包含明确的免票条款 (如 POLICY_ALLOWANCE_EXEMPT 或 POL_TRAVEL_ALLOWANCE)
        """
        if not document_facts:
            return False

        # 1. facts 顶级标记
        if any(document_facts.get(k) is True for k in [
            "allowance_policy_verified",
            "policy_allowance_exempt",
            "allowance_policy_exempt",
            "allowance_verified"
        ]):
            return True

        # 2. extra_context 标记
        extra_ctx = document_facts.get("extra_context") or {}
        if isinstance(extra_ctx, dict):
            if any(extra_ctx.get(k) is True for k in [
                "allowance_policy_verified",
                "policy_allowance_exempt",
                "allowance_policy_exempt",
                "allowance_verified"
            ]):
                return True

        # 3. 行项明细级免票制度背书 (避免过于泛化的 is_exempt，优先使用明确的津贴制度字段)
        for item in candidate_items:
            if isinstance(item, dict) and any(item.get(k) is True for k in [
                "allowance_verified",
                "allowance_policy_verified",
                "policy_allowance_exempt",
                "is_allowance_exempt"
            ]):
                return True

        # 4. 制度规则清单包含免票条款
        rules = document_facts.get("rules", [])
        if isinstance(rules, list):
            for r in rules:
                if isinstance(r, dict):
                    code = str(r.get("rule_code", "")).upper()
                    if any(kw in code for kw in ["ALLOWANCE_EXEMPT", "POL_TRAVEL_ALLOWANCE", "POLICY_ALLOWANCE"]):
                        return True
                    if r.get("allowance_exempt") is True or r.get("allowance_verified") is True:
                        return True

        return False

    @classmethod
    def reflect_and_disambiguate(
        cls,
        findings: List[RiskFindingContract],
        document_facts: Dict[str, Any]
    ) -> Tuple[List[RiskFindingContract], List[Dict[str, Any]]]:
        """
        执行二阶交叉反思消歧回路 (Fail-Safe 强化防线)
        :param findings: 阶段 2 并行智能体产出的原始风险发现项
        :param document_facts: 单据主子表与发票轻量级全局事实
        :return: (消歧后的风险项列表, 反思决策痕迹日志)
        """
        try:
            line_items = (document_facts or {}).get("line_items", [])
            candidate_items = [item for item in line_items if cls.is_candidate_allowance(item)]
            
            # 计算候选津贴总额 (安全 Decimal 累加，杜绝崩溃)
            allowance_total = Decimal("0.00")
            for item in candidate_items:
                amt = cls._safe_decimal(item.get("amount"))
                if amt is not None and amt > Decimal("0.00"):
                    allowance_total += amt

            # 校验是否存在明确免票制度证据
            has_policy_evidence = cls.has_allowance_policy_evidence(document_facts or {}, candidate_items)

            resolved_findings: List[RiskFindingContract] = []
            reflection_logs: List[Dict[str, Any]] = []

            for f in findings:
                # 仅处理有规则代码与标题的有效发现项
                if not f.rule_code or not f.title:
                    continue

                # 反思场景 1：发票价税合计与单据总额不符 (R02_INVOICE_SUM_MISMATCH)
                if f.rule_code == "R02_INVOICE_SUM_MISMATCH":
                    try:
                        diff = cls._safe_decimal(f.discrepancy_amount)
                        if diff is None or diff == Decimal("0.00"):
                            actual_inv = cls._safe_decimal((f.actual_value or {}).get("sum_invoices")) or Decimal("0.00")
                            expected_doc = cls._safe_decimal((f.expected_value or {}).get("document_total")) or Decimal("0.00")
                            diff = abs(expected_doc - actual_inv)
                    except Exception:
                        diff = None

                    # 若存在候选津贴和差额，触发制度核验
                    if diff is not None and diff > Decimal("0.00") and allowance_total > Decimal("0.00"):
                        # 核心防线：无制度依据时保留原 R02，不得仅凭“补贴/津贴”等文字自动放行
                        if not has_policy_evidence:
                            logger.info(
                                f"[ReviewerReflector] 检出候选津贴 ¥{allowance_total:.2f} 与差额 ¥{diff:.2f}，"
                                f"但缺少 Policy/Context 明确免票制度证据，严格保留原始 R02 违规项。"
                            )
                            resolved_findings.append(f)
                            continue

                        # 分支 A：差额与免票津贴完全平账 (公差 <= 0.01 元)
                        if abs(diff - allowance_total) <= Decimal("0.01"):
                            # 核心改进：完全消歧后不再作为 LOW 风险项扣 3 分，只保留 reflection log
                            log_entry = {
                                "action": "AUTO_RESOLVED_FULL",
                                "original_rule": "R02_INVOICE_SUM_MISMATCH",
                                "resolved_rule": "R02_ALLOWANCE_AUTO_RESOLVED",
                                "diff_amount": str(diff),
                                "allowance_total": str(allowance_total),
                                "reason": "发票差额与申报明细中的合规免票津贴完全匹配且具备制度验证证据，成功消除假阳性误报"
                            }
                            reflection_logs.append(log_entry)
                            logger.info(f"[ReviewerReflector] {log_entry['reason']}: ¥{diff}")
                            # 不再 append 风险项到 resolved_findings，避免扣除 3 分
                            continue

                        # 分支 B：免票津贴部分覆盖差额 (津贴 < 差额)
                        elif allowance_total < diff:
                            remaining_gap = diff - allowance_total
                            allowance_names = [
                                f"{i.get('expense_type', '补贴')}(¥{cls._safe_decimal(i.get('amount')) or 0:.2f})"
                                for i in candidate_items
                            ]
                            partial_finding = RiskFindingContract(
                                finding_id=f.finding_id,
                                rule_code="R02_INVOICE_SUM_MISMATCH",
                                rule_name=f.rule_name,
                                risk_level=RiskLevelEnum.HIGH,
                                agent_role=AgentRoleEnum.REVIEWER,
                                title=f"发票总额存在核减后未平账差额 ¥{remaining_gap:.2f} 元",
                                description=(
                                    f"申报总额与发票存在 ¥{diff:.2f} 元原始差额。终审反思回路已自动核减具备制度证据的合规免票津贴【{', '.join(allowance_names)}】"
                                    f"合计 ¥{allowance_total:.2f} 元，扣减后仍有 ¥{remaining_gap:.2f} 元既无发票亦无津贴依据，属于实质性未平账风险。"
                                ),
                                actual_value={
                                    "original_diff": str(diff),
                                    "allowance_deducted": str(allowance_total),
                                    "actual_unexplained_gap": str(remaining_gap)
                                },
                                expected_value={
                                    "required_invoice_amount": str(remaining_gap)
                                },
                                discrepancy_amount=remaining_gap,
                                suggestion=f"系统已自动抵扣免票津贴 ¥{allowance_total:.2f} 元。请经办人针对剩余未平账的 ¥{remaining_gap:.2f} 元补充合规发票或修改申报金额。",
                                is_overridable=False
                            )
                            resolved_findings.append(partial_finding)
                            log_entry = {
                                "action": "PARTIAL_DEDUCTED",
                                "original_rule": "R02_INVOICE_SUM_MISMATCH",
                                "original_diff": str(diff),
                                "allowance_deducted": str(allowance_total),
                                "remaining_gap": str(remaining_gap),
                                "reason": f"免票津贴部分平账，已自动核减 ¥{allowance_total}，重新标定风险敞口为 ¥{remaining_gap}"
                            }
                            reflection_logs.append(log_entry)
                            logger.info(f"[ReviewerReflector] {log_entry['reason']}")
                            continue

                # 其余常规检查项直接保留
                resolved_findings.append(f)

            return resolved_findings, reflection_logs

        except Exception as e:
            # 关键 Fail-safe 保障：反思逻辑抛出任何未捕获异常时，一律安全保留原 findings，绝不丢弃风险项
            logger.exception(f"[ReviewerReflector] 反思消歧过程发生异常，触发 Fail-Safe 保护全量保留原始风险项: {e}")
            return list(findings), []
