"""
backend/engines/amount_agent/agent.py
Amount Agent 核心子图入口
"""
from decimal import Decimal
from typing import List, Dict, Any
from engines.contract.finding import RiskFindingContract
from .calculator import AmountCalculator

class AmountAgent:
    """金额确定性精算智能体"""

    @staticmethod
    async def run(
        doc_total: Any,
        line_items_amounts: List[Any],
        invoices: List[Dict[str, Any]]
    ) -> Any:
        """运行精算核查"""
        return AmountCalculator.verify_five_way_reconciliation(
            doc_total=doc_total,
            line_items_amounts=line_items_amounts,
            invoices=invoices
        )
