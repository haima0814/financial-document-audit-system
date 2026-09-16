"""
backend/tests/app/test_repositories.py
测试数据实体创建与仓储层 CRUD、CAS 乐观锁与防重查验
"""
import pytest
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.database import Base
from app.models import (
    User,
    FinancialDocument,
    DocumentLineItem,
    InvoiceRecord,
    ApprovalWorkflow,
    ApprovalWorkflowNode,
    ApprovalInstance,
    ApprovalTask
)
from app.repositories import document_repo, invoice_repo, workflow_repo

# 使用独立的测试用内存 SQLite
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture
async def test_session():
    test_engine = create_async_engine(TEST_DB_URL, echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    SessionMaker = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    async with SessionMaker() as session:
        yield session
    
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()

@pytest.mark.asyncio
async def test_create_document_and_cas_snapshot(test_session: AsyncSession):
    """测试单据创建、乐观锁 CAS 自增与快照固化"""
    # 1. 创建测试用户
    user = User(username="zhangsan", hashed_password="pw", real_name="张三")
    test_session.add(user)
    await test_session.flush()

    # 2. 创建测试单据与明细
    doc = FinancialDocument(
        document_no="DOC-202609-0001",
        document_type="TRAVEL_REIMBURSEMENT",
        title="测试差旅单",
        applicant_id=user.id,
        total_amount=Decimal("1250.00"),
        status="DRAFT"
    )
    test_session.add(doc)
    await test_session.flush()

    item = DocumentLineItem(
        document_id=doc.id,
        line_no=1,
        expense_type="住宿费",
        item_desc="全季酒店2晚",
        amount=Decimal("1250.00"),
        city_name="北京"
    )
    test_session.add(item)
    await test_session.commit()

    # 3. 验证仓储层查询
    saved_doc = await document_repo.get_with_details(test_session, doc.id)
    assert saved_doc is not None
    assert saved_doc.document_no == "DOC-202609-0001"
    assert len(saved_doc.line_items) == 1
    assert saved_doc.current_version == 1
    assert saved_doc.version_lock == 1

    # 4. 执行乐观锁 CAS 升版快照落库
    version_snap = await document_repo.save_version_snapshot_cas(
        db=test_session,
        document_id=doc.id,
        expected_version_lock=1,
        trigger_action="SUBMIT",
        operator_id=user.id,
        change_summary="首次提交"
    )
    await test_session.commit()

    assert version_snap.version_no == 2
    assert version_snap.snapshot_payload["total_amount"] == "1250.00"
    assert len(version_snap.snapshot_payload["line_items"]) == 1

    # 5. 验证陈旧版本锁引发 CAS 冲突异常
    with pytest.raises(ValueError, match="并发冲突"):
        await document_repo.save_version_snapshot_cas(
            db=test_session,
            document_id=doc.id,
            expected_version_lock=1, # 实际已自增至 2，锁不匹配
            trigger_action="SUBMIT",
            operator_id=user.id
        )

@pytest.mark.asyncio
async def test_invoice_anti_duplicate_filtering(test_session: AsyncSession):
    """测试跨单发票哈希查重与被打回/撤销单据放行豁免"""
    user = User(username="lisi", hashed_password="pw", real_name="李四")
    test_session.add(user)
    await test_session.flush()

    # 创建第一张单据 (已被驳回 REJECTED)
    doc_rejected = FinancialDocument(
        document_no="DOC-HIST-REJECTED",
        document_type="EXPENSE_REIMBURSEMENT",
        title="历史打回单据",
        applicant_id=user.id,
        total_amount=Decimal("500.00"),
        status="REJECTED"
    )
    test_session.add(doc_rejected)
    await test_session.flush()

    inv_hash = "abc123hashfingerprint"
    inv_rejected = InvoiceRecord(
        document_id=doc_rejected.id,
        attachment_id=999,
        invoice_code="0110023",
        invoice_number="99887766",
        total_amount=Decimal("500.00"),
        issue_date="2026-09-01",
        seller_tax_id="91110108MA000000",
        invoice_hash=inv_hash
    )
    test_session.add(inv_rejected)
    await test_session.commit()

    # 当前单据再次提交同张发票
    conflicts_rejected = await invoice_repo.find_active_conflicts(
        test_session, invoice_hash=inv_hash, current_document_id=8888
    )
    # 因为历史单据为 REJECTED，查重应该自动豁免放行！
    assert len(conflicts_rejected) == 0

    # 将历史单据改为 APPROVED
    doc_rejected.status = "APPROVED"
    await test_session.commit()

    # 再次查重，此时必须精准报警并捕获到冲突单据！
    conflicts_approved = await invoice_repo.find_active_conflicts(
        test_session, invoice_hash=inv_hash, current_document_id=8888
    )
    assert len(conflicts_approved) == 1
    conflict_inv, conflict_doc = conflicts_approved[0]
    assert conflict_inv.invoice_number == "99887766"
    assert conflict_doc.document_no == "DOC-HIST-REJECTED"
