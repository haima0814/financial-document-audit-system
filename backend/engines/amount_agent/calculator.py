"""
backend/engines/amount_agent/calculator.py
纯代码高精度 Decimal 计算核算器
"""
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any
from engines.contract.finding import RiskFindingContract, RiskLevelEnum, AgentFindingList
from engines.contract.agent_role import AgentRoleEnum


class AmountCalculator:
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
    def compute_tax_tolerance(invoice_count: int = 1) -> Decimal:
        """单张发票价税一致性固定公差: 0.01 元 (不再按发票张数累加放大)"""
        return Decimal("0.01")

    @staticmethod
    def verify_five_way_reconciliation(
        doc_total: Any,
        line_items_amounts: List[Any],
        invoices: List[Dict[str, Any]]
    ) -> AgentFindingList:
        """执行五方交叉核算与公差核验"""
        findings: List[RiskFindingContract] = []
        safe_doc_total = AmountCalculator._safe_decimal(doc_total)
        is_degraded = False
        degraded_reasons = []

        # 1. 校验 1：单据总额 vs 明细行累加和
        # 仅在申报总额和明细金额均有效解析时进行比对；缺失数据跳过数学核验并标记降级
        if safe_doc_total is not None and line_items_amounts is not None and len(line_items_amounts) > 0:
            valid_lines = [AmountCalculator._safe_decimal(item) for item in line_items_amounts]
            if all(item is not None for item in valid_lines):
                sum_lines = sum(valid_lines, Decimal("0.00"))
                line_diff = abs(safe_doc_total - sum_lines)
                if line_diff > Decimal("0.00"):
                    findings.append(RiskFindingContract(
                        rule_code="R01_HEADER_LINE_MISMATCH",
                        rule_name="单据总额与明细行累加不符",
                        risk_level=RiskLevelEnum.HIGH,
                        agent_role=AgentRoleEnum.AMOUNT,
                        title=f"明细累加与申报总额相差 {line_diff:.2f} 元",
                        description=f"单据抬头申报金额为 {safe_doc_total:.2f} 元，但各明细行项累加合计为 {sum_lines:.2f} 元，存在差额。",
                        actual_value={"sum_line_items": str(sum_lines)},
                        expected_value={"document_total": str(safe_doc_total)},
                        discrepancy_amount=line_diff,
                        suggestion="单据明细算术不平，请经办人重新核对明细分摊后再行提交。",
                        is_overridable=False
                    ))
            else:
                is_degraded = True
                degraded_reasons.append("部分明细行金额缺失或非法，跳过明细累加比对")

        # 2. 校验 2：单据总额 vs 发票实际金额合计
        # 仅在所有发票的 total_amount 均有效提取时才执行全量求和比对；缺失或非法则跳过数学核验并标记降级
        if safe_doc_total is not None and invoices:
            inv_totals = [AmountCalculator._safe_decimal(inv.get("total_amount")) for inv in invoices]
            if all(t is not None for t in inv_totals):
                sum_invs = sum(inv_totals, Decimal("0.00"))
                inv_diff = abs(safe_doc_total - sum_invs)
                if inv_diff > Decimal("0.00"):
                    findings.append(RiskFindingContract(
                        rule_code="R02_INVOICE_SUM_MISMATCH",
                        rule_name="发票价税合计与申报总额不符",
                        risk_level=RiskLevelEnum.HIGH,
                        agent_role=AgentRoleEnum.AMOUNT,
                        title=f"发票总额与单据申报相差 {inv_diff:.2f} 元",
                        description=f"单据申报金额为 {safe_doc_total:.2f} 元，实际上传发票总额为 {sum_invs:.2f} 元，相差 {inv_diff:.2f} 元。",
                        actual_value={"sum_invoices": str(sum_invs)},
                        expected_value={"document_total": str(safe_doc_total)},
                        discrepancy_amount=inv_diff,
                        suggestion="发票总额与报销申报额不平，请补传发票或调整申报金额。",
                        is_overridable=False
                    ))
            else:
                is_degraded = True
                degraded_reasons.append("部分发票金额缺失或非法，跳过发票汇总比对")

        # 针对发票逐张核查
        if invoices:
            tax_tolerance = AmountCalculator.compute_tax_tolerance(len(invoices))
            for inv in invoices:
                tot = AmountCalculator._safe_decimal(inv.get("total_amount"))

                # 4. 校验 4：人工修改标记 (Human-in-the-loop)
                # 优先于税额 None 校验执行：即使税额字段缺失，仍然必须产生 R07
                if inv.get("is_manual_modified"):
                    orig_val = inv.get("original_extracted_amount")
                    if orig_val is not None and str(orig_val).strip() != "":
                        orig_str = str(orig_val)
                    else:
                        orig_str = "UNKNOWN"

                    tot_str = f"{tot:.2f}" if tot is not None else "UNKNOWN"

                    findings.append(RiskFindingContract(
                        rule_code="R07_MANUAL_OVERRIDE_FLAG",
                        rule_name="经办人手动修正票面数据提示",
                        risk_level=RiskLevelEnum.MEDIUM,
                        agent_role=AgentRoleEnum.AMOUNT,
                        title=f"发票[{inv.get('invoice_number', '未知')}]存在经办人手工校准修改",
                        description=f"经办人在提交前手工修改了该发票数据（OCR识别原值: {orig_str}元，手动修正为: {tot_str}元）。",
                        actual_value={"manual_amount": tot_str},
                        expected_value={"ocr_extracted_amount": orig_str},
                        suggestion="请审批人在审批工作台对比 OCR 识别原图与修改值，肉眼审验原件真实金额。",
                        is_overridable=True
                    ))

                # 3. 校验 3：单张发票价税一致性 (固定公差 0.01 元)
                raw_untaxed = inv.get("untaxed_amount")
                raw_tax = inv.get("tax_amount")
                untaxed = AmountCalculator._safe_decimal(raw_untaxed)
                tax = AmountCalculator._safe_decimal(raw_tax)

                # 宁可缺失也不可假设：若要素字段为 None 则不执行价税勾稽硬比对 (防止制造 0 + tax != tot 虚假报警)
                if tot is None or untaxed is None or tax is None:
                    continue

                calc_sum = untaxed + tax
                diff = abs(tot - calc_sum)

                if diff > tax_tolerance and (untaxed > Decimal("0.00") or tax > Decimal("0.00")):
                    findings.append(RiskFindingContract(
                        rule_code="R05_TAX_AMOUNT_MISMATCH",
                        rule_name="发票不含税金额与税额之和异常",
                        risk_level=RiskLevelEnum.MEDIUM,
                        agent_role=AgentRoleEnum.AMOUNT,
                        title=f"发票[{inv.get('invoice_number', '未知')}]价税计算异常，尾差 {diff:.2f} 元",
                        description=f"发票票面总额 {tot:.2f} 元，不含税 {untaxed:.2f} 元与税额 {tax:.2f} 元之和为 {calc_sum:.2f} 元，超过系统允许公差 {tax_tolerance:.2f} 元。",
                        actual_value={"untaxed_plus_tax": str(calc_sum)},
                        expected_value={"total_amount": str(tot), "tolerance": str(tax_tolerance)},
                        discrepancy_amount=diff,
                        suggestion="发票存在超差算术尾差，建议审核人复核发票真伪与税额。",
                        is_overridable=True
                    ))

        return AgentFindingList(
            findings,
            is_degraded=is_degraded,
            degraded_reason="；".join(degraded_reasons) if degraded_reasons else None,
            source="DETERMINISTIC_RULE"
        )
