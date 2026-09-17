"""
backend/app/services/audit_context_builder.py
执行上下文装配器 (AuditContextBuilder)
负责在单据提交后，由业务应用层一次性从数据仓储 (Repository) 和数据库中提取单据实体、明细、发票票面、
申请人历史特征、激活的内控制度规则与预算额度，组装为不可变强类型 AuditExecutionContext 快照。
以此达成：Agent 内部 0 数据库直接读写与 0 外部网络副作用。
"""
import logging
from decimal import Decimal
from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload, joinedload

from app.models.document import FinancialDocument, DocumentLineItem
from app.models.invoice import InvoiceRecord
from app.models.supplier import SupplierProfile
from app.models.user import User
from engines.contract.context import AuditExecutionContext

logger = logging.getLogger("app.audit_context_builder")

class AuditContextBuilder:
    """
    业务应用层上下文装配工厂：
    将单据实体聚合（主表、行项、发票、经办人、预算及生效规则）一次性投影装配为只读不可变的 AuditExecutionContext 快照。
    """

    @classmethod
    async def build(
        cls,
        db: AsyncSession,
        document_id: int,
        task_id: str = "",
        audit_version: int = 1
    ) -> AuditExecutionContext:
        """
        根据 document_id 从数据库抽取全量子实体并装配成不可变执行快照
        """
        # 1. 预加载单据主表、行项明细与申请人信息
        stmt = (
            select(FinancialDocument)
            .options(
                selectinload(FinancialDocument.line_items),
                joinedload(FinancialDocument.applicant)
            )
            .where(FinancialDocument.id == document_id)
        )
        res = await db.execute(stmt)
        doc = res.scalars().first()
        if not doc:
            raise ValueError(f"[AuditContextBuilder] 单据[ID:{document_id}]不存在，无法装配审查上下文！")

        # 2. 查询关联的结构化发票记录
        inv_stmt = select(InvoiceRecord).where(InvoiceRecord.document_id == document_id)
        invoices_orm = list((await db.execute(inv_stmt)).scalars().all())

        # 3. 投影单据行项明细 (只读不可变字典列表)
        line_items_data: List[Dict[str, Any]] = [
            {
                "line_no": item.line_no,
                "expense_type": item.expense_type,
                "item_desc": item.item_desc,
                "amount": float(item.amount),
                "city_name": item.city_name,
                "start_date": item.start_date.isoformat() if item.start_date else None,
                "end_date": item.end_date.isoformat() if item.end_date else None,
                "invoice_count": item.invoice_count,
                "invoice_amount_sum": float(item.invoice_amount_sum) if item.invoice_amount_sum else float(item.amount),
                "extra_data": item.extra_data or {}
            }
            for item in (doc.line_items or [])
        ]

        # 4. 投影结构化发票记录 (含税额、销方税号与 BBox 坐标)
        invoices_data: List[Dict[str, Any]] = [
            {
                "invoice_id": inv.id,
                "invoice_code": inv.invoice_code,
                "invoice_number": inv.invoice_number,
                "total_amount": float(inv.total_amount) if inv.total_amount is not None else None,
                "untaxed_amount": float(inv.untaxed_amount) if inv.untaxed_amount is not None else None,
                "tax_amount": float(inv.tax_amount) if inv.tax_amount is not None else None,
                "tax_rate": float(inv.tax_rate) if inv.tax_rate is not None else None,
                "issue_date": str(inv.issue_date) if inv.issue_date else None,
                "seller_tax_id": inv.seller_tax_id,
                "seller_name": inv.seller_name,
                "buyer_tax_id": inv.buyer_tax_id,
                "invoice_type": inv.invoice_type,
                "raw_ocr_data": getattr(inv, "raw_payload", None) or {},
                "departure_city": (getattr(inv, "raw_payload", None) or {}).get("departure_city"),
                "arrival_city": (getattr(inv, "raw_payload", None) or {}).get("arrival_city"),
                "departure_time": (getattr(inv, "raw_payload", None) or {}).get("departure_time"),
                "arrival_time": (getattr(inv, "raw_payload", None) or {}).get("arrival_time"),
                "train_no": (getattr(inv, "raw_payload", None) or {}).get("train_no"),
                "flight_no": (getattr(inv, "raw_payload", None) or {}).get("flight_no"),
                "raw_payload": getattr(inv, "raw_payload", None) or {}
            }
            for inv in invoices_orm
        ]

        # 5. 构建差旅时空轨迹点 (若为差旅单据)
        spatio_points: List[Dict[str, Any]] = []
        if doc.document_type == "TRAVEL_REIMBURSEMENT":
            for item in line_items_data:
                if item.get("city_name") and item.get("start_date"):
                    city = item["city_name"]
                    lat, lng = (39.9042, 116.4074) if "北京" in city else (31.2304, 121.4737)
                    spatio_points.append({
                        "event_time": item["start_date"],
                        "city_name": city,
                        "latitude": lat,
                        "longitude": lng,
                        "source_desc": item.get("item_desc", "")
                    })

        # 5.5 构建交通票据行程段 (行程路线事实)
        from engines.orchestrator.master_graph import MasterOrchestrator
        travel_segments = MasterOrchestrator._extract_travel_segments(
            invoices=invoices_data,
            line_items=line_items_data
        )

        # 6. 装配经办人画像与历史行为特征 (由应用层查询聚合，防 Agent 读库)
        applicant = doc.applicant
        applicant_profile = {
            "applicant_id": doc.applicant_id,
            "real_name": applicant.real_name if applicant else "经办人",
            "username": applicant.username if applicant else "",
            "department_name": doc.department_name or "业务部",
            "credit_level": "A",
            "monthly_claim_count": 3
        }

        # 7. 装配有效制度规则与部门预算上下文
        active_rules = [
            {"rule_code": "R01", "rule_name": "申报总额与发票求和平账校验", "threshold": 0.01},
            {"rule_code": "R05", "rule_name": "差旅住宿超标门禁", "city_tier_limits": {"一线城市": 500.0, "新一线城市": 400.0, "二线及以下": 300.0}},
            {"rule_code": "R10", "rule_name": "同批次连号发票防拆单排查", "max_consecutive_allowed": 2},
            {"rule_code": "R12", "rule_name": "供应商失信黑名单与经营状态审查", "block_on_untrusted": True}
        ]

        approval_context = {
            "department_budget_remaining": 85000.00,
            "auto_approve_limit": 500.00,
            "requires_cfo_threshold": 10000.00
        }

        # 7.5 查询并装配历史有效发票指纹字典 (采用稳定 identity_key，供 AnomalyAgent 跨单防重与防篡改核验)
        historical_fingerprints = await cls.build_historical_fingerprints(
            db=db,
            current_document_id=document_id
        )

        # 7.6 查询并装配关联供应商真实资信画像字典 (供 SupplierAgent 真实事实穿透)
        supplier_profiles = await cls.build_supplier_profiles(
            db=db,
            invoices_data=invoices_data
        )

        extra_ctx = dict(doc.extra_attributes or {})
        extra_ctx["historical_fingerprints"] = historical_fingerprints
        extra_ctx["supplier_profiles"] = supplier_profiles

        # 8. 封包生成不可变 AuditExecutionContext
        context = AuditExecutionContext(
            task_id=task_id,
            document_id=doc.id,
            document_no=doc.document_no,
            document_type=doc.document_type,
            title=doc.title,
            total_amount=doc.total_amount,
            department_name=doc.department_name,
            applicant_id=doc.applicant_id,
            tenant_id=doc.tenant_id if hasattr(doc, "tenant_id") else 1,
            audit_version=audit_version,
            line_items=line_items_data,
            invoices=invoices_data,
            spatio_points=spatio_points,
            travel_segments=travel_segments,
            applicant_profile=applicant_profile,
            rules=active_rules,
            approval_context=approval_context,
            extra_context=extra_ctx
        )

        logger.info(
            f"[AuditContextBuilder] 成功装配单据[{doc.document_no}]不可变快照: "
            f"明细={len(line_items_data)}条, 发票={len(invoices_data)}张, 历史发票指纹={len(historical_fingerprints)}条, 规则={len(active_rules)}条"
        )
        return context

    @classmethod
    def generate_fingerprints_from_records(
        cls,
        records: List[Any]
    ) -> Dict[str, Dict[str, Any]]:
        """
        从历史发票原始数据生成标准跨单指纹字典：
        1. 统一使用 identity_key: normalize(invoice_code) + "#" + normalize(invoice_number) 作为字典主 Key；
        2. 严禁使用旧的 code+number+amount+date SHA256 作为主查重 Key；
        3. Value 保留 total_amount, issue_date, document_no, status 等，用于判断同票号内容是否被篡改/修改。
        """
        from engines.anomaly_agent.hash_verifier import InvoiceFingerprintCalculator
        result = {}
        for rec in records:
            if isinstance(rec, dict):
                code = rec.get("invoice_code")
                number = rec.get("invoice_number")
                doc_no = rec.get("document_no", "HIST-DOC")
                status = rec.get("status", "APPROVED")
                amt = rec.get("total_amount", rec.get("amount", 0.0))
                date = rec.get("issue_date", "")
                tax_id = rec.get("seller_tax_id", "")
                name = rec.get("seller_name", "")
            else:
                code = getattr(rec, "invoice_code", "")
                number = getattr(rec, "invoice_number", "")
                doc_no = getattr(rec, "document_no", "HIST-DOC")
                status = getattr(rec, "status", "APPROVED")
                amt = getattr(rec, "total_amount", 0.0)
                date = getattr(rec, "issue_date", "")
                tax_id = getattr(rec, "seller_tax_id", "")
                name = getattr(rec, "seller_name", "")

            id_key = InvoiceFingerprintCalculator.compute_identity_key(code, number)
            if not id_key:
                continue

            result[id_key] = {
                "document_no": doc_no,
                "status": status,
                "invoice_code": (code or "").strip().upper(),
                "invoice_number": (number or "").strip().upper(),
                "identity_key": id_key,
                "total_amount": float(Decimal(str(amt))) if amt is not None else 0.0,
                "issue_date": str(date) if date else "",
                "seller_tax_id": str(tax_id) if tax_id else "",
                "seller_name": str(name) if name else "",
            }
        return result

    @classmethod
    async def build_historical_fingerprints(
        cls,
        db: AsyncSession,
        current_document_id: int
    ) -> Dict[str, Dict[str, Any]]:
        """
        从数据库查询已生效或审批中的历史发票记录，构建跨单历史指纹字典：
        自动排除当前单据 (document_id != current_document_id)。
        """
        stmt = (
            select(InvoiceRecord, FinancialDocument.document_no, FinancialDocument.status)
            .join(FinancialDocument, InvoiceRecord.document_id == FinancialDocument.id)
            .where(
                and_(
                    InvoiceRecord.document_id != current_document_id,
                    FinancialDocument.status.in_(["APPROVED", "PENDING_APPROVAL", "IN_REVIEW"])
                )
            )
        )
        res = await db.execute(stmt)
        records_to_process = []
        for row in res.all():
            inv: InvoiceRecord = row[0]
            doc_no: str = row[1]
            doc_status: str = row[2]
            records_to_process.append({
                "invoice_code": inv.invoice_code,
                "invoice_number": inv.invoice_number,
                "total_amount": inv.total_amount,
                "issue_date": inv.issue_date,
                "seller_tax_id": inv.seller_tax_id,
                "seller_name": inv.seller_name,
                "document_no": doc_no,
                "status": doc_status
            })
        return cls.generate_fingerprints_from_records(records_to_process)

    @classmethod
    async def build_supplier_profiles(
        cls,
        db: AsyncSession,
        invoices_data: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        根据单据发票的销售方税号 (USCC) 预加载真实供应商资信与风险画像字典
        Key: 规范化 USCC (大写去空格)
        Value: 供应商画像属性字典
        """
        usccs = {
            inv["seller_tax_id"].strip().upper()
            for inv in invoices_data
            if inv.get("seller_tax_id") and inv["seller_tax_id"].strip()
        }
        if not usccs:
            return {}

        stmt = select(SupplierProfile).where(SupplierProfile.uscc.in_(usccs))
        res = await db.execute(stmt)
        profiles = res.scalars().all()

        profiles_dict = {}
        for p in profiles:
            norm_uscc = p.uscc.strip().upper()
            scope = getattr(p, "business_scope", None)
            if not scope and isinstance(p.risk_tags, dict):
                scope = p.risk_tags.get("business_scope")

            profiles_dict[norm_uscc] = {
                "id": p.id,
                "uscc": norm_uscc,
                "supplier_name": p.supplier_name,
                "is_dishonest": bool(p.is_dishonest),
                "operating_status": p.operating_status or "存续",
                "is_shell_company": bool(p.is_shell_company),
                "registered_capital": p.registered_capital,
                "business_scope": scope,
                "risk_tags": p.risk_tags or {}
            }
        return profiles_dict

