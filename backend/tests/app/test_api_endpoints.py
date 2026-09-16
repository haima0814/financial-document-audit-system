"""
backend/tests/app/test_api_endpoints.py
FastAPI REST API 端点集成测试
"""
import pytest
from httpx import AsyncClient, ASGITransport
from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from main import app
from app.core.database import Base, get_db
from app.models.user import User, Role, UserRole
from app.models.workflow import ApprovalWorkflow, ApprovalWorkflowNode
from app.services.auth_service import AuthService

@pytest.fixture
async def api_test_client():
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
        u = User(id=1, username="test_emp", hashed_password=pwd_hash, real_name="测试员工", department_name="销售部")
        r = Role(id=1, role_code="EMPLOYEE", role_name="普通员工")
        ur = UserRole(user_id=1, role_id=1)
        session.add_all([u, r, ur])

        # 预置审批流与节点
        wf = ApprovalWorkflow(id=1, workflow_code="WF_TRAVEL", workflow_name="差旅流", document_type="TRAVEL_REIMBURSEMENT")
        session.add(wf)
        await session.flush()
        node1 = ApprovalWorkflowNode(id=1, workflow_id=1, node_order=1, node_name="部门主管", approver_type="USER", user_id=1, is_final=True)
        session.add(node1)

        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()

@pytest.mark.asyncio
async def test_health_check(api_test_client: AsyncClient):
    resp = await api_test_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "UP"

@pytest.mark.asyncio
async def test_login_and_get_me(api_test_client: AsyncClient):
    # 登录
    login_resp = await api_test_client.post(
        "/api/v1/auth/login",
        json={"username": "test_emp", "password": "123456"}
    )
    assert login_resp.status_code == 200
    token_data = login_resp.json()
    assert "access_token" == "access_token" in token_data
    token = token_data["access_token"]

    # 查询个人信息
    me_resp = await api_test_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["username"] == "test_emp"
    assert me_data["real_name"] == "测试员工"

@pytest.mark.asyncio
async def test_create_and_query_document(api_test_client: AsyncClient):
    # 1. 登录
    login_resp = await api_test_client.post(
        "/api/v1/auth/login",
        json={"username": "test_emp", "password": "123456"}
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. 创建平账单据
    doc_payload = {
        "document_type": "TRAVEL_REIMBURSEMENT",
        "title": "上海客户商务差旅报销",
        "total_amount": 1200.00,
        "currency": "CNY",
        "department_name": "销售部",
        "line_items": [
            {
                "line_no": 1,
                "expense_type": "交通费",
                "item_desc": "高铁二等座",
                "amount": 550.00,
                "city_name": "上海"
            },
            {
                "line_no": 2,
                "expense_type": "住宿费",
                "item_desc": "亚朵酒店2晚",
                "amount": 650.00,
                "city_name": "上海"
            }
        ]
    }
    create_resp = await api_test_client.post("/api/v1/documents", json=doc_payload, headers=headers)
    assert create_resp.status_code == 200
    created = create_resp.json()
    doc_id = created["id"]
    assert created["status"] == "DRAFT"
    assert len(created["line_items"]) == 2

    # 3. 查列表
    list_resp = await api_test_client.get("/api/v1/documents", headers=headers)
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1

    # 4. 查详情
    detail_resp = await api_test_client.get(f"/api/v1/documents/{doc_id}", headers=headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["id"] == doc_id

    # 5. 测试前置不平账硬拦截
    bad_payload = {
        "document_type": "TRAVEL_REIMBURSEMENT",
        "title": "算不平的单据",
        "total_amount": 1000.00,
        "line_items": [
            {"line_no": 1, "expense_type": "交通费", "item_desc": "车票", "amount": 800.00}
        ]
    }
    bad_resp = await api_test_client.post("/api/v1/documents", json=bad_payload, headers=headers)
    assert bad_resp.status_code == 400
    assert "前置算术校验失败" in bad_resp.json()["detail"]

@pytest.mark.asyncio
async def test_submit_document_and_pipeline(api_test_client: AsyncClient):
    # 1. 登录
    login_resp = await api_test_client.post(
        "/api/v1/auth/login",
        json={"username": "test_emp", "password": "123456"}
    )
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. 创建平账单据
    doc_payload = {
        "document_type": "TRAVEL_REIMBURSEMENT",
        "title": "测试提交审查单据",
        "total_amount": 500.00,
        "currency": "CNY",
        "line_items": [
            {
                "line_no": 1,
                "expense_type": "交通费",
                "item_desc": "市内交通",
                "amount": 500.00,
                "city_name": "北京"
            }
        ]
    }
    create_resp = await api_test_client.post("/api/v1/documents", json=doc_payload, headers=headers)
    assert create_resp.status_code == 200
    doc_id = create_resp.json()["id"]

    # 3. 提交审查
    submit_resp = await api_test_client.post(f"/api/v1/documents/{doc_id}/submit", headers=headers)
    assert submit_resp.status_code == 200
    sub_data = submit_resp.json()
    assert "task_id" in sub_data
    assert sub_data["document_id"] == doc_id

