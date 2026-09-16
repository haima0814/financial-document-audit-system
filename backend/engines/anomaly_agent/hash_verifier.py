"""
backend/engines/anomaly_agent/hash_verifier.py
发票唯一性指纹确定性计算器 (InvoiceFingerprintCalculator)
纯确定性领域计算工具 (Deterministic Tool)：0 网络、0 数据库依赖
"""
import hashlib
from decimal import Decimal
from typing import List, Dict, Optional, Any

from engines.contract.finding import RiskFindingContract, RiskLevelEnum
from engines.contract.agent_role import AgentRoleEnum
from .schemas import InvoiceFact

class InvoiceFingerprintCalculator:
    """
    发票防重唯一指纹计算与纯内存查重工具 (Deterministic Pure Tool)
    严格遵循零网络、零数据库依赖规范
    """

    @staticmethod
    def compute_identity_key(code: Optional[str], number: Optional[str]) -> Optional[str]:
        """
        计算发票身份唯一标识 (Identity Key):
        基于 invoice_code + invoice_number，去前后空格转大写。
        若缺少有效的发票代码或发票号码（为空、None 或纯空白），返回 None，防止生成不可靠指纹。
        """
        clean_code = (code or "").strip().upper()
        clean_num = (number or "").strip().upper()
        if not clean_code or not clean_num:
            return None
        return f"{clean_code}#{clean_num}"

    @staticmethod
    def compute_content_hash(
        code: Optional[str],
        number: Optional[str],
        amount: Optional[Decimal] = None,
        issue_date: Optional[str] = ""
    ) -> Optional[str]:
        """计算发票内容一致性哈希 (用于一致性证据比对)"""
        id_key = InvoiceFingerprintCalculator.compute_identity_key(code, number)
        if not id_key:
            return None
        amt_str = f"{Decimal(str(amount)):.2f}" if amount is not None else "0.00"
        date_str = (issue_date or "").strip()
        payload = f"{id_key}#{amt_str}#{date_str}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_fingerprint(
        code: str,
        number: str,
        amount: Optional[Decimal] = None,
        issue_date: str = ""
    ) -> str:
        """
        计算发票防伪指纹 (SHA-256):
        优先检查有效代码与号码，缺少时返回空串；
        存在有效身份时结合金额与日期生成指纹。
        """
        id_key = InvoiceFingerprintCalculator.compute_identity_key(code, number)
        if not id_key:
            return ""
        if amount is not None or issue_date:
            amt_str = f"{Decimal(str(amount)):.2f}" if amount is not None else "0.00"
            payload = f"{id_key}#{amt_str}#{str(issue_date).strip()}"
        else:
            payload = id_key
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # 兼容别名
    compute_invoice_hash = compute_fingerprint

    @classmethod
    def detect_duplicate_invoices(
        cls,
        invoices: List[InvoiceFact],
        historical_fingerprints: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> List[RiskFindingContract]:
        """
        纯内存发票防重查验：
        1. 优先基于 invoice_code + invoice_number 稳定 identity_key 进行发票身份查重；
        2. 金额、日期作为内容哈希与一致性证据，金额被篡改依然能准确识别重复；
        3. 缺少有效发票代码/号码时直接跳过，绝不生成不可靠指纹；
        4. 支持单内查重与基于 historical_fingerprints 注入的跨单红线查重。
        """
        findings: List[RiskFindingContract] = []
        seen_identities: Dict[str, InvoiceFact] = {}
        historical = historical_fingerprints or {}

        for inv in invoices:
            id_key = cls.compute_identity_key(inv.invoice_code, inv.invoice_number)
            # 缺少有效发票代码或号码时不要生成不可靠指纹
            if not id_key:
                continue

            fp = cls.compute_fingerprint(inv.invoice_code, inv.invoice_number, inv.total_amount, inv.issue_date)

            # 1. 单内重复检测 (基于稳定 identity_key)
            if id_key in seen_identities:
                prev_inv = seen_identities[id_key]
                is_amount_modified = (inv.total_amount != prev_inv.total_amount)
                desc = f"当前报销单内存在两张发票代码与号码完全相同的发票（代码: {inv.invoice_code}，号码: {inv.invoice_number}）。"
                if is_amount_modified:
                    desc += f" 且发票金额被修改（前一张: {prev_inv.total_amount}元，当前: {inv.total_amount}元），涉嫌篡改金额重复报销。"
                else:
                    desc += f" 金额: {inv.total_amount}元，涉嫌单内重复报销。"

                findings.append(RiskFindingContract(
                    rule_code="R08_INVOICE_DUPLICATE",
                    rule_name="发票单内重复报销",
                    risk_level=RiskLevelEnum.HIGH,
                    agent_role=AgentRoleEnum.ANOMALY,
                    title=f"发票号码[{inv.invoice_number}]在当前单据中重复提交",
                    description=desc,
                    actual_value={
                        "invoice_code": inv.invoice_code,
                        "invoice_number": inv.invoice_number,
                        "identity_key": id_key,
                        "duplicate_count": 2,
                        "is_amount_modified": is_amount_modified,
                        "current_amount": float(inv.total_amount),
                        "previous_amount": float(prev_inv.total_amount)
                    },
                    expected_value={"max_allowed_submission": 1},
                    discrepancy_amount=inv.total_amount,
                    suggestion="请核实并剔除单内重复上传的发票附件后再行提交。",
                    is_overridable=False
                ))
                continue
            seen_identities[id_key] = inv

            # 2. 跨单据上下文比对 (支持 identity_key 索引、SHA-256 索引或字典元数据遍历)
            conflict_info = None
            if id_key in historical:
                conflict_info = historical[id_key]
            elif fp and fp in historical:
                conflict_info = historical[fp]
            else:
                for k, v in historical.items():
                    if isinstance(v, dict):
                        h_code = v.get("invoice_code")
                        h_num = v.get("invoice_number")
                        h_id_key = v.get("identity_key") or (cls.compute_identity_key(h_code, h_num) if h_code and h_num else None)
                        if h_id_key == id_key:
                            conflict_info = v
                            break

            if conflict_info:
                doc_no = conflict_info.get("document_no", "未知历史单据")
                doc_status = conflict_info.get("status", "已生效")
                h_amt = conflict_info.get("total_amount", conflict_info.get("amount"))
                is_amount_modified = False

                desc = (
                    f"发票（代码: {inv.invoice_code}, 号码: {inv.invoice_number}）"
                    f"已在历史单据[{doc_no}]（状态: {doc_status}）"
                    f"中完成报销或正在审批中。严禁一票多报或跨部门套现！"
                )
                if h_amt is not None:
                    try:
                        if Decimal(str(h_amt)) != Decimal(str(inv.total_amount)):
                            is_amount_modified = True
                            desc += f"（历史单据金额: {h_amt}元，当前申报金额: {inv.total_amount}元，疑似修改金额重复报销）"
                    except Exception:
                        pass

                findings.append(RiskFindingContract(
                    rule_code="R08_INVOICE_DUPLICATE",
                    rule_name="发票跨单重复报销 (全局红线)",
                    risk_level=RiskLevelEnum.HIGH,
                    agent_role=AgentRoleEnum.ANOMALY,
                    title=f"发票[{inv.invoice_number}]已被历史单据[{doc_no}]报销",
                    description=desc,
                    actual_value={
                        "invoice_code": inv.invoice_code,
                        "invoice_number": inv.invoice_number,
                        "identity_key": id_key,
                        "conflict_document_no": doc_no,
                        "conflict_status": doc_status,
                        "is_amount_modified": is_amount_modified,
                        "historical_amount": float(Decimal(str(h_amt))) if h_amt is not None else None,
                        "current_amount": float(inv.total_amount)
                    },
                    expected_value={"is_previously_claimed": False},
                    discrepancy_amount=inv.total_amount,
                    suggestion="系统检测到发票已被使用，属于一票否决高危红线，财务审批人严禁放行。",
                    is_overridable=False
                ))

        return findings

    @classmethod
    async def verify_duplicate_invoices(
        cls,
        db: Optional[Any],
        current_document_id: int,
        invoices: List[InvoiceFact],
        historical_fingerprints: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> List[RiskFindingContract]:
        """兼容性异步门禁：纯计算查重"""
        return cls.detect_duplicate_invoices(invoices, historical_fingerprints)

# 兼容保留原有类名
HashVerifier = InvoiceFingerprintCalculator
