"""
backend/app/repositories/invoice_repo.py
发票记录仓储：发票检索、跨单哈希防重查验与生命周期过滤
"""
from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.models.invoice import InvoiceRecord
from app.models.document import FinancialDocument
from .base import BaseRepository

class InvoiceRepository(BaseRepository[InvoiceRecord]):
    def __init__(self):
        super().__init__(InvoiceRecord)

    async def get_by_hash(self, db: AsyncSession, invoice_hash: str) -> Optional[InvoiceRecord]:
        stmt = select(InvoiceRecord).where(InvoiceRecord.invoice_hash == invoice_hash)
        res = await db.execute(stmt)
        return res.scalars().first()

    async def find_active_conflicts(
        self,
        db: AsyncSession,
        invoice_hash: str,
        current_document_id: int
    ) -> List[Tuple[InvoiceRecord, FinancialDocument]]:
        """
        跨单检索有效状态的冲突发票：
        自动排除本单自身，且过滤掉被打回或已撤销的单据 (DRAFT, REJECTED, CANCELLED)，
        仅当历史单据处于 APPROVED, PENDING_APPROVAL, IN_REVIEW 时报警。
        """
        stmt = (
            select(InvoiceRecord, FinancialDocument)
            .join(FinancialDocument, InvoiceRecord.document_id == FinancialDocument.id)
            .where(
                and_(
                    InvoiceRecord.invoice_hash == invoice_hash,
                    InvoiceRecord.document_id != current_document_id,
                    FinancialDocument.status.in_(["APPROVED", "PENDING_APPROVAL", "IN_REVIEW"])
                )
            )
        )
        res = await db.execute(stmt)
        return list(res.all())

    async def list_by_document(self, db: AsyncSession, document_id: int) -> List[InvoiceRecord]:
        stmt = select(InvoiceRecord).where(InvoiceRecord.document_id == document_id)
        res = await db.execute(stmt)
        return list(res.scalars().all())

invoice_repo = InvoiceRepository()
