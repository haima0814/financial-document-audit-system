"""
backend/tests/app/test_lifecycle_and_llm.py
测试单据生命周期状态机 (撤回、驳回后重提自动升级V2快照) 与大模型网关兜底能力
"""
import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from main import app
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.database import Base, get_db, AsyncSessionLocal
from app.models.document import FinancialDocument, DocumentVersion
from app.services.auth_service import AuthService
from app.core.llm_client import LLMClient
from engines.orchestrator.dispatcher.local_dispatcher import LocalTaskManagerDispatcher

from sqlalchemy.pool import StaticPool

test_session_maker = None

@pytest.fixture(autouse=True)
async def setup_db(monkeypatch):
    global test_session_maker
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    test_session_maker = session_maker

    async def override_get_db():
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    async def fake_dispatch(*args, **kwargs):
        coro = kwargs.get("coro")
        if coro:
            coro.close()
    with patch.object(LocalTaskManagerDispatcher, "dispatch_audit_task", side_effect=fake_dispatch):
        yield

    app.dependency_overrides.clear()
    await test_engine.dispose()

@pytest.mark.asyncio
async def test_document_cancellation():
    """测试经办人撤回流转中的单据"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = AuthService.create_access_token(user_id=10, username="tester_cancel", roles=["EMPLOYEE"])
        headers = {"Authorization": f"Bearer {token}"}

        # 1. 创建单据
        doc_payload = {
            "document_type": "EXPENSE_REIMBURSEMENT",
            "title": "待撤回测试报销单",
            "total_amount": 300.0,
            "currency": "CNY",
            "line_items": [
                {"line_no": 1, "expense_type": "办公用品", "item_desc": "打印纸耗材", "amount": 300.0}
            ]
        }
        res_create = await ac.post("/api/v1/documents", json=doc_payload, headers=headers)
        assert res_create.status_code == 200
        doc_id = res_create.json()["id"]

        # 2. 提交单据 (进入 SUBMITTED)
        res_submit = await ac.post(f"/api/v1/documents/{doc_id}/submit", headers=headers)
        assert res_submit.status_code == 200

        # 3. 撤回单据
        res_cancel = await ac.post(
            f"/api/v1/documents/{doc_id}/cancel",
            json={"reason": "发票税号填错，经办人主动撤回"},
            headers=headers
        )
        assert res_cancel.status_code == 200
        cancel_data = res_cancel.json()
        assert cancel_data["status"] == "CANCELLED"

        # 4. 再次撤回已撤回单据，校验防呆硬拦截
        res_cancel_again = await ac.post(
            f"/api/v1/documents/{doc_id}/cancel",
            json={"reason": "重复撤回"},
            headers=headers
        )
        assert res_cancel_again.status_code == 400

@pytest.mark.asyncio
async def test_document_resubmit_version_bump():
    """测试单据被驳回后经办人重新提交，自动升版为 V2 并固化不可变快照"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = AuthService.create_access_token(user_id=11, username="tester_resubmit", roles=["EMPLOYEE"])
        headers = {"Authorization": f"Bearer {token}"}

        # 1. 创建单据 (初始 V1)
        doc_payload = {
            "document_type": "TRAVEL_REIMBURSEMENT",
            "title": "版本升版测试差旅单",
            "total_amount": 1000.0,
            "currency": "CNY",
            "line_items": [
                {"line_no": 1, "expense_type": "交通费", "item_desc": "机票", "amount": 1000.0}
            ]
        }
        res_create = await ac.post("/api/v1/documents", json=doc_payload, headers=headers)
        assert res_create.status_code == 200
        doc_id = res_create.json()["id"]
        assert res_create.json()["current_version"] == 1

        # 2. 模拟单据被审批人驳回为 REJECTED 状态
        async with test_session_maker() as session:
            doc = await session.get(FinancialDocument, doc_id)
            doc.status = "REJECTED"
            await session.commit()

        # 3. 经办人重新提交单据
        res_resubmit = await ac.post(f"/api/v1/documents/{doc_id}/submit", headers=headers)
        assert res_resubmit.status_code == 200

        # 4. 验证单据详情中当前版本已升至 V2
        res_detail = await ac.get(f"/api/v1/documents/{doc_id}", headers=headers)
        assert res_detail.status_code == 200
        assert res_detail.json()["current_version"] == 2

        # 5. 查询版本历史接口 GET /documents/{id}/versions
        res_versions = await ac.get(f"/api/v1/documents/{doc_id}/versions", headers=headers)
        assert res_versions.status_code == 200
        versions = res_versions.json()
        assert len(versions) >= 2
        # 最新一条为 V2
        assert versions[0]["version_no"] == 2
        assert versions[0]["trigger_action"] == "RESUBMIT"
        assert versions[0]["snapshot_payload"]["total_amount"] == 1000.0

@pytest.mark.asyncio
async def test_llm_client_gateway_and_fallback(monkeypatch):
    """测试 LLM 客户端网关在未配真实 Key 时的检测与优雅降级兜底"""
    # 模拟未配真实 Key 的环境 (mock-key)
    monkeypatch.setattr("app.core.config.settings.OPENAI_API_KEY", "mock-key")
    assert LLMClient.is_configured() is False

    # 尝试调用对话补全，验证返回 None 并安全降级
    res = await LLMClient.chat_completion(messages=[{"role": "user", "content": "你好"}])
    assert res is None

    # 测试风控 Copilot RAG 问答接口在兜底模式下的正常响应
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token = AuthService.create_access_token(user_id=12, username="tester_llm", roles=["EMPLOYEE"])
        headers = {"Authorization": f"Bearer {token}"}

        # 创建一个单据
        doc_payload = {
            "document_type": "EXPENSE_REIMBURSEMENT",
            "title": "风控问答测试单据",
            "total_amount": 500.0,
            "currency": "CNY",
            "line_items": [
                {"line_no": 1, "expense_type": "办公用品", "item_desc": "文具", "amount": 500.0}
            ]
        }
        res_create = await ac.post("/api/v1/documents", json=doc_payload, headers=headers)
        doc_id = res_create.json()["id"]

        # 发送风控问答
        chat_resp = await ac.post(
            "/api/v1/audits/chat",
            json={
                "document_id": doc_id,
                "message": "请问这笔单据是否存在违规超标风险？"
            },
            headers=headers
        )
        assert chat_resp.status_code == 200
        data = chat_resp.json()
        assert "message" in data
        assert len(data["message"]["content"]) > 0
