"""
backend/app/services/document_service.py
财务单据全生命周期业务服务 (制单前置算术硬校验、CAS 快照、草稿管理与审查提交)
"""
import os
import uuid
import asyncio
from collections import defaultdict
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.exc import IntegrityError

from app.models.user import User
from app.models.document import (
    FinancialDocument,
    DocumentLineItem,
    DocumentAttachment,
    DocumentVersion,
    DocumentStatusLog,
)
from app.models.invoice import InvoiceRecord
from app.models.audit import AnalysisTask, ReviewReport
from app.models.workflow import ApprovalWorkflow
from app.schemas.auth import TokenPayload
from app.schemas.document import (
    FinancialDocumentCreateReq,
    FinancialDocumentUpdateReq,
    LineItemIn,
    AttachmentIn,
    InvoiceRecordIn,
)

from app.repositories import document_repo
from engines.orchestrator.master_graph import MasterOrchestrator
from engines.orchestrator.dispatcher.local_dispatcher import LocalTaskManagerDispatcher
from app.services.approval_engine import ApprovalEngine

# 进程内单据级异步并发锁，保障多协程操作同一单据时的原子性
_doc_submission_locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

class DocumentNotFoundError(ValueError):
    """单据不存在 (HTTP 404)"""
    pass

class DocumentStateConflictError(ValueError):
    """单据状态冲突，不可执行该操作 (HTTP 409)"""
    pass

class DocumentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def generate_document_no(doc_type: str) -> str:
        """生成唯一单据编号，如 BX-20260914-A1B2"""
        prefix_map = {
            "TRAVEL_REIMBURSEMENT": "TRV",
            "EXPENSE_REIMBURSEMENT": "EXP",
            "CORP_PAYMENT": "CORP",
            "ADVANCE_PAYMENT": "ADV",
            "BATCH_PAYMENT": "BAT",
        }
        prefix = prefix_map.get(doc_type, "DOC")
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        rand_str = uuid.uuid4().hex[:6].upper()
        return f"{prefix}-{date_str}-{rand_str}"

    async def create_document(
        self,
        applicant_id: int,
        req: FinancialDocumentCreateReq
    ) -> FinancialDocument:
        """
        创建财务单据草稿 (含前置算术硬校验与 idempotency_key 幂等防重)
        """
        # 0. 幂等预检：若携带幂等键且已存在单据，直接返回现有单据
        if req.idempotency_key:
            stmt = select(FinancialDocument).where(FinancialDocument.idempotency_key == req.idempotency_key)
            existing_doc = (await self.db.execute(stmt)).scalars().first()
            if existing_doc:
                return existing_doc

        # 1. 前置硬拦截算术平账校验：明细项金额之和必须等于总金额 (公差 <= 0.01)
        if req.line_items:
            line_sum = sum(Decimal(str(item.amount)) for item in req.line_items)
            diff = abs(Decimal(str(req.total_amount)) - line_sum)
            if diff > Decimal("0.01"):
                raise ValueError(
                    f"前置算术校验失败：单据总金额({req.total_amount})与各项明细合计({line_sum})不一致，相差 {diff} 元！"
                )

        doc_no = self.generate_document_no(req.document_type)

        # 2. 构建主表对象
        doc = FinancialDocument(
            document_no=doc_no,
            idempotency_key=req.idempotency_key,
            document_type=req.document_type,
            title=req.title,
            applicant_id=applicant_id,
            department_id=req.department_id,
            department_name=req.department_name,
            total_amount=Decimal(str(req.total_amount)),
            currency=req.currency,
            status="DRAFT",
            current_version=1,
            version_lock=1,
            extra_attributes=req.extra_attributes or {},
        )
        self.db.add(doc)
        try:
            await self.db.flush()
        except IntegrityError:
            # 数据库 UNIQUE(idempotency_key) 冲突兜底 (高并发重复插入)
            await self.db.rollback()
            if req.idempotency_key:
                stmt = select(FinancialDocument).where(FinancialDocument.idempotency_key == req.idempotency_key)
                existing_doc = (await self.db.execute(stmt)).scalars().first()
                if existing_doc:
                    return existing_doc
            raise

        # 3. 添加明细项
        for item_req in req.line_items:
            line = DocumentLineItem(
                document_id=doc.id,
                line_no=item_req.line_no,
                expense_type=item_req.expense_type,
                item_desc=item_req.item_desc,
                amount=Decimal(str(item_req.amount)),
                invoice_count=item_req.invoice_count,
                invoice_amount_sum=Decimal(str(item_req.invoice_amount_sum)),
                city_name=item_req.city_name,
                start_date=item_req.start_date,
                end_date=item_req.end_date,
                extra_data=item_req.extra_data or {},
            )
            self.db.add(line)

        # 4. 添加附件与发票记录
        attachment_map = {}
        for att_req in req.attachments:
            att = DocumentAttachment(
                document_id=doc.id,
                file_name=att_req.file_name,
                file_type=att_req.file_type,
                file_path=att_req.file_path,
                file_hash=att_req.file_hash,
                file_size_bytes=att_req.file_size_bytes,
                is_invoice=att_req.is_invoice,
            )
            self.db.add(att)
            await self.db.flush()
            attachment_map[att_req.file_hash] = att.id

        # 4.2 存储 OCR 解析出的结构化发票
        for inv_in in req.invoices:
            att_id = attachment_map.get(inv_in.invoice_hash)
            file_path = inv_in.file_path
            if not att_id:
                # 若未传附件实体，自动关联发票附件并保留真实原图文件路径
                ext = ".png"
                if file_path and "." in file_path:
                    ext = os.path.splitext(file_path)[1]
                auto_att = DocumentAttachment(
                    document_id=doc.id,
                    file_name=f"发票_{inv_in.invoice_number}{ext}",
                    file_type=ext.replace(".", "").upper(),
                    file_path=file_path or f"/uploads/invoices/inv_{inv_in.invoice_number}{ext}",
                    file_hash=inv_in.invoice_hash,
                    file_size_bytes=245000,
                    is_invoice=True,
                    ocr_status="SUCCESS"
                )
                self.db.add(auto_att)
                await self.db.flush()
                att_id = auto_att.id

            inv_record = InvoiceRecord(
                document_id=doc.id,
                attachment_id=att_id,
                invoice_code=inv_in.invoice_code or "NONE",
                invoice_number=inv_in.invoice_number or f"UNKNOWN_{uuid.uuid4().hex[:8]}",
                invoice_type=inv_in.invoice_type or "增值税电子普通发票",
                total_amount=Decimal(str(inv_in.total_amount)) if inv_in.total_amount is not None else Decimal("0.00"),
                untaxed_amount=Decimal(str(inv_in.untaxed_amount)) if inv_in.untaxed_amount is not None else None,
                tax_amount=Decimal(str(inv_in.tax_amount)) if inv_in.tax_amount is not None else None,
                tax_rate=Decimal(str(inv_in.tax_rate)) if inv_in.tax_rate is not None else None,
                seller_name=inv_in.seller_name,
                seller_tax_id=inv_in.seller_tax_id,
                buyer_name=inv_in.buyer_name,
                buyer_tax_id=inv_in.buyer_tax_id,
                issue_date=inv_in.issue_date,
                invoice_hash=inv_in.invoice_hash,
                raw_payload={
                    **(inv_in.raw_payload or {}),
                    "bbox_positions": inv_in.bbox_positions or {},
                    "ocr_confidence": inv_in.ocr_confidence or 0.985,
                    "file_path": file_path,
                    **{k: getattr(inv_in, k) for k in ["departure_city", "arrival_city", "departure_time", "arrival_time", "train_no", "flight_no"] if getattr(inv_in, k, None) is not None}
                }
            )
            self.db.add(inv_record)

        # 5. 记录 V1 初始版本快照
        snapshot_payload = {
            "document_no": doc.document_no,
            "title": doc.title,
            "total_amount": float(doc.total_amount),
            "line_items": [
                {"line_no": i.line_no, "expense_type": i.expense_type, "amount": float(i.amount)}
                for i in req.line_items
            ],
        }
        v1 = DocumentVersion(
            document_id=doc.id,
            version_no=1,
            trigger_action="DRAFT_CREATE",
            snapshot_payload=snapshot_payload,
            change_summary="创建初始草稿单据",
            created_by=applicant_id,
        )
        self.db.add(v1)

        await self.db.commit()
        await self.db.refresh(doc)
        return doc

    async def get_document_detail(self, document_id: int) -> Optional[FinancialDocument]:
        """获取单据详情 (包含子明细、附件、发票与申请人姓名)"""
        stmt = (
            select(FinancialDocument)
            .options(
                selectinload(FinancialDocument.line_items),
                selectinload(FinancialDocument.attachments),
                joinedload(FinancialDocument.applicant)
            )
            .where(FinancialDocument.id == document_id)
        )
        res = await self.db.execute(stmt)
        doc = res.scalars().first()
        if not doc:
            return None

        # 动态组装结构化发票与申请人真实姓名
        inv_stmt = select(InvoiceRecord).where(InvoiceRecord.document_id == document_id)
        invoices = list((await self.db.execute(inv_stmt)).scalars().all())
        att_map = {att.id: att.file_path for att in (doc.attachments or [])}
        for inv in invoices:
            inv.file_path = (inv.raw_payload or {}).get("file_path") or att_map.get(inv.attachment_id)
            if inv.raw_payload and "bbox_positions" in inv.raw_payload:
                inv.bbox_positions = inv.raw_payload["bbox_positions"]
        doc.invoices = invoices
        doc.applicant_name = doc.applicant.real_name if doc.applicant else None
        return doc

    async def list_documents(
        self,
        current_user: Optional[TokenPayload] = None,
        applicant_id: Optional[int] = None,
        status: Optional[str] = None,
        document_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[FinancialDocument], int]:
        """
        分页筛选单据列表 (支持企业级 RBAC 数据范围隔离):
        1. ADMIN: 全量查看全系统单据；
        2. CFO: 聚焦风控特批视角 (金额 >= 10,000元 或 判定为高危 high 的单据 或 本人单据)；
        3. FINANCE: 查看全公司所有已提交流转的单据 (status != 'DRAFT') 及本人单据；
        4. MANAGER: 查看本部门已提交单据 (department_name == user_dept 且 status != 'DRAFT') 及本人单据；
        5. EMPLOYEE: 严格仅查看本人单据 (applicant_id == current_user.user_id)。
        """
        conditions = []

        if current_user:
            roles = current_user.roles or []
            if "ADMIN" in roles:
                # 管理员全量放行
                pass
            elif "CFO" in roles:
                # 财务总监：风控大额与高危过滤
                high_risk_subquery = (
                    select(ReviewReport.document_id)
                    .where(ReviewReport.overall_risk_level == "high")
                )
                conditions.append(
                    or_(
                        FinancialDocument.total_amount >= Decimal("10000.00"),
                        FinancialDocument.id.in_(high_risk_subquery),
                        FinancialDocument.applicant_id == current_user.user_id
                    )
                )
            elif "FINANCE" in roles:
                # 财务专员：已进入流程的所有单据 (过滤他人私有草稿)
                conditions.append(
                    or_(
                        FinancialDocument.status != "DRAFT",
                        FinancialDocument.applicant_id == current_user.user_id
                    )
                )
            elif "MANAGER" in roles:
                # 部门主管：本部门已提交单据 + 本人单据
                user = await self.db.get(User, current_user.user_id)
                user_dept = user.department_name if user else None
                if user_dept:
                    conditions.append(
                        or_(
                            and_(FinancialDocument.department_name == user_dept, FinancialDocument.status != "DRAFT"),
                            FinancialDocument.applicant_id == current_user.user_id
                        )
                    )
                else:
                    conditions.append(FinancialDocument.applicant_id == current_user.user_id)
            else:
                # 经办员工：严格隔离，只能查本人单据
                conditions.append(FinancialDocument.applicant_id == current_user.user_id)

        if applicant_id:
            conditions.append(FinancialDocument.applicant_id == applicant_id)
        if status:
            conditions.append(FinancialDocument.status == status)
        if document_type:
            conditions.append(FinancialDocument.document_type == document_type)

        where_clause = and_(*conditions) if conditions else True

        count_stmt = select(func.count(FinancialDocument.id)).where(where_clause)
        total = (await self.db.execute(count_stmt)).scalar() or 0

        query_stmt = (
            select(FinancialDocument)
            .options(joinedload(FinancialDocument.applicant))
            .where(where_clause)
            .order_by(desc(FinancialDocument.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        res = await self.db.execute(query_stmt)
        items = list(res.scalars().all())
        for it in items:
            it.applicant_name = it.applicant.real_name if it.applicant else None

        return items, total


    async def submit_document(self, document_id: int, user_id: int) -> Dict[str, Any]:
        """
        经办人提交单据 (保证严格提交幂等与并发安全)：
        1. 进程内锁 + with_for_update 行级锁；
        2. 检查当前单据在当前 audit_version 下是否已存在审核任务；
        3. 若已有任务，直接返回已有 task_id (reused=True)，禁止重复创建；
        4. 若无活动任务且处于 DRAFT / REJECTED / NEED_SUPPLEMENT：推进版本与状态，创建新任务 (reused=False)；
        5. 返回 document_id + audit_version + task_id。
        """
        async with _doc_submission_locks[document_id]:
            stmt = select(FinancialDocument).where(FinancialDocument.id == document_id).with_for_update()
            doc = (await self.db.execute(stmt)).scalars().first()
            if not doc:
                raise DocumentNotFoundError(f"单据[ID:{document_id}]不存在！")
            if doc.applicant_id != user_id:
                raise PermissionError("只有单据经办人本人才能提交审批！")

            # 计算本次提交的目标版本号 (若为驳回或待补充状态重提，目标版本递增)
            if doc.status in ["REJECTED", "NEED_SUPPLEMENT"]:
                target_version = doc.current_version + 1
            else:
                target_version = doc.current_version

            # 1. 检查目标版本是否已存在审核任务 (无论 PENDING/RUNNING/COMPLETED)
            task_stmt = (
                select(AnalysisTask)
                .where(
                    AnalysisTask.document_id == doc.id,
                    AnalysisTask.audit_version == target_version
                )
                .order_by(desc(AnalysisTask.id))
            )
            existing_task = (await self.db.execute(task_stmt)).scalars().first()
            if existing_task:
                return {
                    "document_id": doc.id,
                    "audit_version": target_version,
                    "document_no": doc.document_no,
                    "status": doc.status,
                    "task_id": existing_task.task_id,
                    "reused": True,
                    "message": "单据当前版本已有审核任务正在执行或已执行完毕，已复用现有任务。"
                }

            if doc.status not in ["DRAFT", "REJECTED", "NEED_SUPPLEMENT"]:
                # 若已处于 SUBMITTED / IN_REVIEW / PENDING_APPROVAL，检查当前生效版本任务
                curr_task_stmt = (
                    select(AnalysisTask)
                    .where(
                        AnalysisTask.document_id == doc.id,
                        AnalysisTask.audit_version == doc.current_version
                    )
                    .order_by(desc(AnalysisTask.id))
                )
                curr_task = (await self.db.execute(curr_task_stmt)).scalars().first()
                if curr_task:
                    return {
                        "document_id": doc.id,
                        "audit_version": doc.current_version,
                        "document_no": doc.document_no,
                        "status": doc.status,
                        "task_id": curr_task.task_id,
                        "reused": True,
                        "message": "单据当前版本已有审核任务正在执行或已执行完毕，已复用现有任务。"
                    }
                raise DocumentStateConflictError(f"单据当前状态为 [{doc.status}]，不可重复提交！")

            from_status = doc.status

            # 若是驳回或待补充材料后重新提交，自动递增版本号并保存 V2+ 快照
            if from_status in ["REJECTED", "NEED_SUPPLEMENT"]:
                doc.current_version = target_version
                doc.version_lock += 1

                # 抓取当前最新明细生成全量不可变快照
                line_stmt = select(DocumentLineItem).where(DocumentLineItem.document_id == doc.id)
                curr_lines = list((await self.db.execute(line_stmt)).scalars().all())
                snapshot_payload = {
                    "document_no": doc.document_no,
                    "title": doc.title,
                    "total_amount": float(doc.total_amount),
                    "line_items": [
                        {"line_no": i.line_no, "expense_type": i.expense_type, "amount": float(i.amount)}
                        for i in curr_lines
                    ],
                }
                summary_prefix = "补充材料后重新提交审批" if from_status == "NEED_SUPPLEMENT" else "驳回后修改重新提交审批"
                new_version = DocumentVersion(
                    document_id=doc.id,
                    version_no=doc.current_version,
                    trigger_action="RESUBMIT",
                    snapshot_payload=snapshot_payload,
                    change_summary=f"{summary_prefix}(第{doc.current_version}版)",
                    created_by=user_id,
                )
                self.db.add(new_version)

            doc.status = "SUBMITTED"
            doc.submission_time = datetime.now(timezone.utc)

            # 记录状态流转日志
            if from_status in ["REJECTED", "NEED_SUPPLEMENT"]:
                comment_str = f"经办人重新提交审批(升级为V{doc.current_version})"
            else:
                comment_str = "经办人提交单据审批"

            status_log = DocumentStatusLog(
                document_id=doc.id,
                from_status=from_status,
                to_status="SUBMITTED",
                operator_id=user_id,
                comment=comment_str,
            )
            self.db.add(status_log)
            await self.db.flush()

            # 委托 AuditService 统一启动审核 (生命周期高内聚闭环，自带 UNIQUE 与 IntegrityError 兜底)
            from app.services.audit_service import AuditService
            audit_service = AuditService(self.db)
            task_id = await audit_service.start_audit(
                document_id=doc.id,
                applicant_id=user_id,
                tenant_id=getattr(doc, "tenant_id", 1),
                audit_version=doc.current_version
            )

            await self.db.commit()

            return {
                "document_id": doc.id,
                "audit_version": doc.current_version,
                "document_no": doc.document_no,
                "status": doc.status,
                "task_id": task_id,
                "reused": False,
                "message": "单据已成功提交，AI 多智能体审查流水线已启动！"
            }

    async def cancel_document(
        self,
        document_id: int,
        user_id: int,
        reason: str = "经办人主动撤回单据",
        is_admin: bool = False
    ) -> FinancialDocument:
        """
        撤回单据：
        1. 仅允许经办人本人或管理员对流转中的单据进行撤回；
        2. 允许撤回的状态：SUBMITTED, IN_REVIEW, PENDING_APPROVAL；
        3. 状态变更为 CANCELLED，若存在 RUNNING 审批流实例则同步废止并取消待办任务；
        4. 记录单据状态审计日志与工作流废止审计。
        """
        doc = await self.db.get(FinancialDocument, document_id)
        if not doc:
            raise ValueError(f"单据[ID:{document_id}]不存在！")
        if not is_admin and doc.applicant_id != user_id:
            raise PermissionError("只有单据经办人本人或系统管理员才能撤回单据！")
        if doc.status not in ["SUBMITTED", "IN_REVIEW", "PENDING_APPROVAL"]:
            raise ValueError(f"当前单据状态为 [{doc.status}]，不可执行撤回操作！")

        from_status = doc.status
        doc.status = "CANCELLED"

        # 记录单据状态日志
        status_log = DocumentStatusLog(
            document_id=doc.id,
            from_status=from_status,
            to_status="CANCELLED",
            operator_id=user_id,
            comment=reason,
        )
        self.db.add(status_log)

        # 废止可能正在运行中的审批流实例与待办
        from app.models.workflow import ApprovalInstance, ApprovalTask, WorkflowStatusLog
        inst_stmt = select(ApprovalInstance).where(
            and_(ApprovalInstance.document_id == doc.id, ApprovalInstance.status == "RUNNING")
        )
        inst = (await self.db.execute(inst_stmt)).scalars().first()
        if inst:
            inst.status = "CANCELLED"
            inst.end_time = datetime.now(timezone.utc)

            # 取消关联的 PENDING 待办任务
            tasks_stmt = select(ApprovalTask).where(
                and_(ApprovalTask.instance_id == inst.id, ApprovalTask.status == "PENDING")
            )
            pending_tasks = list((await self.db.execute(tasks_stmt)).scalars().all())
            for t in pending_tasks:
                t.status = "CANCELLED"
                t.end_time = datetime.now(timezone.utc)
                t.comment = "单据已被撤回，待办自动失效"

            self.db.add(WorkflowStatusLog(
                instance_id=inst.id,
                operator_id=user_id,
                action="REVOKE",
                comment=f"单据撤回废止流程: {reason}"
            ))

        await self.db.commit()
        await self.db.refresh(doc)
        return doc

    async def list_document_versions(self, document_id: int) -> List[DocumentVersion]:
        """查询指定单据的全量历史不可变版本快照"""
        stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(desc(DocumentVersion.version_no))
        )
        res = await self.db.execute(stmt)
        return list(res.scalars().all())

    async def update_document(
        self,
        document_id: int,
        user_id: int,
        req: FinancialDocumentUpdateReq,
        is_admin: bool = False
    ) -> FinancialDocument:
        """
        编辑修改单据 (仅草稿 DRAFT 或被驳回 REJECTED 状态单据允许修改)
        """
        doc = await self.db.get(FinancialDocument, document_id)
        if not doc:
            raise ValueError(f"单据[ID:{document_id}]不存在！")
        if not is_admin and doc.applicant_id != user_id:
            raise PermissionError("只有单据经办人本人或管理员才能修改单据！")
        if doc.status not in ["DRAFT", "REJECTED"]:
            raise ValueError(f"当前单据状态为 [{doc.status}]，不允许修改！")

        if req.title is not None:
            doc.title = req.title
        if req.extra_attributes is not None:
            doc.extra_attributes = req.extra_attributes

        if req.line_items is not None:
            # 校验明细算术
            new_amount = req.total_amount if req.total_amount is not None else doc.total_amount
            line_sum = sum(Decimal(str(item.amount)) for item in req.line_items)
            diff = abs(Decimal(str(new_amount)) - line_sum)
            if diff > Decimal("0.01"):
                raise ValueError(
                    f"算术校验失败：总金额({new_amount})与各项明细合计({line_sum})不一致，相差 {diff} 元！"
                )
            doc.total_amount = Decimal(str(new_amount))

            # 清理旧明细，写入新明细
            old_items = (await self.db.execute(select(DocumentLineItem).where(DocumentLineItem.document_id == doc.id))).scalars().all()
            for oi in old_items:
                await self.db.delete(oi)
            await self.db.flush()

            for item_req in req.line_items:
                line = DocumentLineItem(
                    document_id=doc.id,
                    line_no=item_req.line_no,
                    expense_type=item_req.expense_type,
                    item_desc=item_req.item_desc,
                    amount=Decimal(str(item_req.amount)),
                    invoice_count=item_req.invoice_count,
                    invoice_amount_sum=Decimal(str(item_req.invoice_amount_sum)),
                    city_name=item_req.city_name,
                    start_date=item_req.start_date,
                    end_date=item_req.end_date,
                    extra_data=item_req.extra_data or {},
                )
                self.db.add(line)
        elif req.total_amount is not None:
            doc.total_amount = Decimal(str(req.total_amount))

        await self.db.commit()
        await self.db.refresh(doc)
        return doc

