"""
backend/engines/anomaly_agent/sequential_detector.py
同商户连号发票与集中拆单套现检测
"""
import re
from typing import List, Dict, Optional
from decimal import Decimal
from engines.contract.finding import RiskFindingContract, RiskLevelEnum
from engines.contract.agent_role import AgentRoleEnum
from .schemas import InvoiceFact

class SequentialDetector:
    @staticmethod
    def _extract_number_suffix(number_str: str) -> Optional[int]:
        """提取发票号码末尾的纯数字部分，如 'No.00394821' -> 394821"""
        match = re.search(r'(\d+)$', number_str.strip())
        return int(match.group(1)) if match else None

    @staticmethod
    def detect_sequential_invoices(
        invoices: List[InvoiceFact],
        split_amount_threshold: Decimal = Decimal("3000.00")
    ) -> List[RiskFindingContract]:
        """
        连号拆单检测算法：
        1. 仅当连续张数 >= 3 且累计金额 >= split_amount_threshold 时触发；
        2. >= 4 张或累计金额 >= 10000 元时判定为 HIGH 高危，否则为 MEDIUM 中危；
        3. 真实使用 split_amount_threshold 参数作为过滤防线；
        4. 文案客观中立，定性为“存在拆单风险特征，建议核实”，避免过度定性。
        """
        findings: List[RiskFindingContract] = []
        vendor_groups: Dict[str, List[InvoiceFact]] = {}

        for inv in invoices:
            if not inv.seller_tax_id:
                continue
            vendor_groups.setdefault(inv.seller_tax_id, []).append(inv)

        for tax_id, group in vendor_groups.items():
            # 至少 3 张发票才具备连号拆单分析价值
            if len(group) < 3:
                continue

            numbered_invoices = []
            for inv in group:
                num = SequentialDetector._extract_number_suffix(inv.invoice_number)
                if num is not None:
                    numbered_invoices.append((num, inv))

            if len(numbered_invoices) < 3:
                continue

            numbered_invoices.sort(key=lambda x: x[0])

            # 寻找连续子序列 (num[i+1] - num[i] == 1)
            seq_chains: List[List[InvoiceFact]] = []
            current_chain = [numbered_invoices[0][1]]

            for i in range(len(numbered_invoices) - 1):
                cur_num, _ = numbered_invoices[i]
                next_num, next_inv = numbered_invoices[i + 1]

                if next_num - cur_num == 1:
                    current_chain.append(next_inv)
                else:
                    if len(current_chain) >= 3:
                        seq_chains.append(current_chain)
                    current_chain = [next_inv]

            if len(current_chain) >= 3:
                seq_chains.append(current_chain)

            for chain in seq_chains:
                chain_total = sum((inv.total_amount for inv in chain), Decimal("0.00"))
                # 至少要求连续张数 >= 3 且累计金额 >= split_amount_threshold 才触发
                if len(chain) < 3 or chain_total < split_amount_threshold:
                    continue

                invoice_nums = [inv.invoice_number for inv in chain]
                vendor_name = chain[0].seller_name or tax_id

                # >=4 张或累计金额 >= 10000 时 HIGH，否则 MEDIUM
                risk_lvl = RiskLevelEnum.HIGH if (chain_total >= Decimal("10000.00") or len(chain) >= 4) else RiskLevelEnum.MEDIUM

                findings.append(RiskFindingContract(
                    rule_code="R10_SEQUENTIAL_INVOICES",
                    rule_name="同商户连号发票拆单风险",
                    risk_level=risk_lvl,
                    agent_role=AgentRoleEnum.ANOMALY,
                    title=f"检测到商户[{vendor_name}]存在 {len(chain)} 张连号发票，存在拆单风险特征",
                    description=(
                        f"在当前单据中，来自商户[{vendor_name}]（税号: {tax_id}）的发票号码连续（{', '.join(invoice_nums)}），"
                        f"共计 {len(chain)} 张，累计金额 {chain_total:.2f} 元（达到拆单核验阈值 {split_amount_threshold} 元）。"
                        f"存在拆单风险特征，建议核实是否为真实分笔业务或存在拆单开票规避审批门槛情形。"
                    ),
                    actual_value={
                        "seller_name": vendor_name,
                        "seller_tax_id": tax_id,
                        "sequential_count": len(chain),
                        "invoice_numbers": invoice_nums,
                        "total_amount": float(chain_total)
                    },
                    expected_value={"allow_sequential_invoices": False, "split_threshold": float(split_amount_threshold)},
                    discrepancy_amount=chain_total,
                    suggestion="建议审批人要求报销人提供对应明细销货清单与流水，核实是否存在拆单开票规避审批行为。",
                    is_overridable=True
                ))

        return findings
