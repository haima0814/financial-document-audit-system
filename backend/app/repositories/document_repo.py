"""
backend/app/repositories/document_repo.py
财务单据仓储：单据主子表 CRUD、乐观锁 CAS 与不可变快照固化
"""
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update
from sqlalchemy.orm import selectinload

from app.models.document import FinancialDocument, DocumentLineItem, DocumentAttachment, DocumentVersion
from .base import BaseRepository

class DocumentRepository(BaseRepository[FinancialDocument]):
    def __init__(self):
        super().__init__(FinancialDocument)

    async def get_by_document_no(self, db: AsyncSession, document_no: str) -> Optional[FinancialDocument]:
        stmt = select(FinancialDocument).where(FinancialDocument.document_no == document_no)
        res = await db.execute(stmt)
        return res.scalars().first()

    async def get_with_details(self, db: AsyncSession, document_id: int) -> Optional[FinancialDocument]:
        """级联拉取单据、明细项与附件"""
        stmt = (
            select(FinancialDocument)
            .where(FinancialDocument.id == document_id)
            .options(
                selectinload(FinancialDocument.line_items),
                selectinload(FinancialDocument.attachments)
            )
        )
        res = await db.execute(stmt)
        return res.scalars().first()

    async def save_version_snapshot_cas(
        self,
        db: AsyncSession,
        document_id: int,
        expected_version_lock: int,
        trigger_action: str,
        operator_id: int,
        change_summary: Optional[str] = None
    ) -> DocumentVersion:
        """
        采用乐观锁 CAS 执行版本固化与快照落库：
        1. 尝试原子自增 version_lock: WHERE id = :id AND version_lock = :expected
        2. 若受影响行数为 0，说明发生并发冲突，抛出并发异常
        3. 打包当前单据全量子表数据固化为 DocumentVersion 不可变快照
        """
        # 1. 尝试乐观锁 CAS 更新
        stmt = (
            update(FinancialDocument)
            .where(
                and_(
                    FinancialDocument.id == document_id,
                    FinancialDocument.version_lock == expected_version_lock
                )
            )
            .values(
                version_lock=expected_version_lock + 1,
                current_version=FinancialDocument.current_version + 1
            )
        )
        result = await db.execute(stmt)
        if result.rowcount == 0:
            raise ValueError(f"单据[ID:{document_id}]并发冲突或已被修改，版本锁不匹配！")

        # 2. 重新加载最新详情组装快照 JSON
        doc = await self.get_with_details(db, document_id)
        if not doc:
            raise ValueError(f"单据[ID:{document_id}]不存在！")

        snapshot_dict = {
            "document_id": doc.id,
            "document_no": doc.document_no,
            "document_type": doc.document_type,
            "title": doc.title,
            "total_amount": str(doc.total_amount),
            "currency": doc.currency,
            "status": doc.status,
            "current_version": doc.current_version,
            "is_manual_override": doc.is_manual_override,
            "line_items": [
                {
                    "line_no": item.line_no,
                    "expense_type": item.expense_type,
                    "item_desc": item.item_desc,
                    "amount": str(item.amount),
                    "city_name": item.city_name
                }
                for item in doc.line_items
            ],
            "attachments": [
                {
                    "file_name": att.file_name,
                    "file_hash": att.file_hash,
                    "file_size": att.file_size_bytes
                }
                for att in doc.attachments
            ]
        }

        # 3. 追加写入不可变版本快照表
        version_record = DocumentVersion(
            document_id=doc.id,
            version_no=doc.current_version,
            trigger_action=trigger_action,
            snapshot_payload=snapshot_dict,
            change_summary=change_summary,
            created_by=operator_id
        )
        db.add(version_record)
        await db.flush()
        return version_record

    async def transition_status(
        self,
        db: AsyncSession,
        document_id: int,
        target_status: str,
        expected_current_status: Optional[List[str]] = None
    ) -> bool:
        """安全流转单据主状态"""
        doc = await self.get(db, document_id)
        if not doc:
            return False
        if expected_current_status and doc.status not in expected_current_status:
            return False
        doc.status = target_status
        await db.flush()
        return True

document_repo = DocumentRepository()
