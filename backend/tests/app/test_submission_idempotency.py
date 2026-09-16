"""
backend/tests/app/test_submission_idempotency.py
端到端与服务层两级提交幂等性自动化测试套件
覆盖：
1. Document 创建级幂等（同一 idempotency_key 返回同一单据，数据库唯一约束保证）
2. AnalysisTask 提交级幂等（同一单据同一版本顺序调用，返回相同 task_id，reused=True）
3. 并发提交压力测试（asyncio.gather 5 路并发，数据库 UNIQUE 约束 + 异常捕获保证仅生成 1 个 task）
4. 驳回后重新提交（audit_version 升级至 2，生成新 task_id，多版本历史 task 与审批实例完整保留）
"""
import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from main import app
from app.core.database import Base, get_db
from app.models.user import User, Role, UserRole
from app.models.document import FinancialDocument, DocumentLineItem
from app.models.audit import AnalysisTask
from app.models.workflow import ApprovalWorkflow, ApprovalWorkflowNode
from app.services.auth_service import AuthService

@pytest.fixture
async def idempotency_test_client():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

    async def override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    # 预置种子数据
    async with session_maker() as session:
        pwd_hash = AuthService.hash_password("123456")
        u = User(id=1, username="test_submitter", hashed_password=pwd_hash, real_name="申报人小张", department_name="研发部")
        r = Role(id=1, role_code="EMPLOYEE", role_name="普通员工")
        ur = UserRole(user_id=1, role_id=1)
        wf = ApprovalWorkflow(id=1, workflow_code="WF_TRAVEL", workflow_name="差旅报销流程", document_type="TRAVEL_REIMBURSEMENT")
        node = ApprovalWorkflowNode(id=1, workflow_id=1, node_order=1, node_name="主管初审", approver_type="ROLE", role_code="EMPLOYEE")
        session.add_all([u, r, ur, wf, node])
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_maker

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_document_creation_idempotency_key(idempotency_test_client):
    """测试单据创建级幂等：同一 idempotency_key 多次调用仅创建一张单据"""
    client, session_maker = idempotency_test_client

    token = AuthService.create_access_token(1, "test_submitter", ["EMPLOYEE"])
    headers = {"Authorization": f"Bearer {token}"}

    idem_key = "key_create_test_uuid_9999"
    doc_payload = {
        "document_type": "TRAVEL_REIMBURSEMENT",
        "title": "北京-上海商务考察",
        "total_amount": 500.00,
        "currency": "CNY",
        "department_name": "研发部",
        "idempotency_key": idem_key,
        "line_items": [
            {"line_no": 1, "expense_type": "交通费", "item_desc": "高铁二等座", "amount": 500.00}
        ]
    }

    # 第一次创建
    resp1 = await client.post("/api/v1/documents", json=doc_payload, headers=headers)
    assert resp1.status_code == 200
    doc1 = resp1.json()
    assert doc1["idempotency_key"] == idem_key
    doc_id = doc1["id"]

    # 第二次使用完全相同的 idempotency_key 创建（模拟连击）
    resp2 = await client.post("/api/v1/documents", json=doc_payload, headers=headers)
    assert resp2.status_code == 200
    doc2 = resp2.json()

    # 验证返回的是同一张单据
    assert doc2["id"] == doc_id
    assert doc2["document_no"] == doc1["document_no"]

    # 查库验证仅有 1 条单据
    async with session_maker() as session:
        result = await session.execute(select(FinancialDocument).where(FinancialDocument.idempotency_key == idem_key))
        docs = result.scalars().all()
        assert len(docs) == 1


@pytest.mark.asyncio
async def test_document_submit_sequential_idempotency(idempotency_test_client):
    """测试单据提交级幂等：同一单据同一版本多次提交返回相同的 task_id 且 reused=True"""
    client, session_maker = idempotency_test_client
    token = AuthService.create_access_token(1, "test_submitter", ["EMPLOYEE"])
    headers = {"Authorization": f"Bearer {token}"}

    # 创建草稿单据
    doc_resp = await client.post("/api/v1/documents", json={
        "document_type": "TRAVEL_REIMBURSEMENT",
        "title": "连续点击提交测试单据",
        "total_amount": 300.00,
        "currency": "CNY",
        "department_name": "研发部",
        "idempotency_key": "key_submit_seq_001",
        "line_items": [
            {"line_no": 1, "expense_type": "打车费", "item_desc": "市内交通", "amount": 300.00}
        ]
    }, headers=headers)
    doc_id = doc_resp.json()["id"]

    # 首次提交审查
    sub_resp1 = await client.post(f"/api/v1/documents/{doc_id}/submit", headers=headers)
    assert sub_resp1.status_code == 200
    data1 = sub_resp1.json()
    assert "task_id" in data1
    assert data1.get("reused") is False
    assert data1.get("audit_version") == 1
    task_id_1 = data1["task_id"]

    # 第二次重复提交审查
    sub_resp2 = await client.post(f"/api/v1/documents/{doc_id}/submit", headers=headers)
    assert sub_resp2.status_code == 200
    data2 = sub_resp2.json()
    assert data2["task_id"] == task_id_1
    assert data2.get("reused") is True
    assert data2.get("audit_version") == 1

    # 查库验证 tasks 数量
    async with session_maker() as session:
        res = await session.execute(
            select(AnalysisTask).where(AnalysisTask.document_id == doc_id)
        )
        tasks = res.scalars().all()
        assert len(tasks) == 1
        assert tasks[0].task_id == task_id_1
        assert tasks[0].audit_version == 1


@pytest.mark.asyncio
async def test_document_submit_concurrent_gather(idempotency_test_client):
    """测试高并发提交幂等性：asyncio.gather 5 路并发请求，保证最终仅生成一个 AnalysisTask"""
    client, session_maker = idempotency_test_client
    token = AuthService.create_access_token(1, "test_submitter", ["EMPLOYEE"])
    headers = {"Authorization": f"Bearer {token}"}

    # 创建草稿单据
    doc_resp = await client.post("/api/v1/documents", json={
        "document_type": "TRAVEL_REIMBURSEMENT",
        "title": "高并发点击测试单据",
        "total_amount": 400.00,
        "currency": "CNY",
        "department_name": "研发部",
        "idempotency_key": "key_concurrent_gather_002",
        "line_items": [
            {"line_no": 1, "expense_type": "住宿费", "item_desc": "快捷酒店", "amount": 400.00}
        ]
    }, headers=headers)
    doc_id = doc_resp.json()["id"]

    # 模拟 5 个并发请求同时撞击提交接口
    coros = [
        client.post(f"/api/v1/documents/{doc_id}/submit", headers=headers)
        for _ in range(5)
    ]
    responses = await asyncio.gather(*coros)

    task_ids = []
    for r in responses:
        assert r.status_code == 200, f"并发提交出错: {r.text}"
        res_data = r.json()
        assert "task_id" in res_data
        assert res_data.get("audit_version") == 1
        task_ids.append(res_data["task_id"])

    # 所有 5 个请求返回的 task_id 必须绝对一致
    assert len(set(task_ids)) == 1, f"并发生成了不同的 task_id: {task_ids}"

    # 查库验证 AnalysisTask 只有 1 条记录
    async with session_maker() as session:
        res = await session.execute(
            select(AnalysisTask).where(AnalysisTask.document_id == doc_id)
        )
        tasks = res.scalars().all()
        assert len(tasks) == 1
        assert tasks[0].task_id == task_ids[0]
        assert tasks[0].audit_version == 1


@pytest.mark.asyncio
async def test_resubmit_after_rejection_increments_version(idempotency_test_client):
    """测试驳回后重新提交：版本递增至 2，生成新 task_id，历史版本与当前版本共存"""
    client, session_maker = idempotency_test_client
    token = AuthService.create_access_token(1, "test_submitter", ["EMPLOYEE"])
    headers = {"Authorization": f"Bearer {token}"}

    # 1. 创建单据
    doc_resp = await client.post("/api/v1/documents", json={
        "document_type": "TRAVEL_REIMBURSEMENT",
        "title": "驳回重提测试单据",
        "total_amount": 450.00,
        "currency": "CNY",
        "department_name": "研发部",
        "idempotency_key": "key_resubmit_003",
        "line_items": [
            {"line_no": 1, "expense_type": "火车票", "item_desc": "北京-天津", "amount": 450.00}
        ]
    }, headers=headers)
    doc_id = doc_resp.json()["id"]

    # 2. V1 提交
    sub_resp1 = await client.post(f"/api/v1/documents/{doc_id}/submit", headers=headers)
    assert sub_resp1.status_code == 200
    v1_data = sub_resp1.json()
    task_id_v1 = v1_data["task_id"]
    assert v1_data.get("audit_version") == 1

    # 3. 模拟审批驳回
    async with session_maker() as session:
        doc = await session.get(FinancialDocument, doc_id)
        doc.status = "REJECTED"
        await session.commit()

    # 4. 驳回后再次提交
    sub_resp2 = await client.post(f"/api/v1/documents/{doc_id}/submit", headers=headers)
    assert sub_resp2.status_code == 200
    v2_data = sub_resp2.json()
    task_id_v2 = v2_data["task_id"]
    assert v2_data.get("audit_version") == 2
    assert v2_data.get("reused") is False
    assert task_id_v2 != task_id_v1

    # 5. 查库验证存在 2 条不同版本的 AnalysisTask 记录
    async with session_maker() as session:
        res = await session.execute(
            select(AnalysisTask).where(AnalysisTask.document_id == doc_id).order_by(AnalysisTask.audit_version)
        )
        all_tasks = res.scalars().all()
        assert len(all_tasks) == 2
        assert all_tasks[0].task_id == task_id_v1
        assert all_tasks[0].audit_version == 1
        assert all_tasks[1].task_id == task_id_v2
        assert all_tasks[1].audit_version == 2
