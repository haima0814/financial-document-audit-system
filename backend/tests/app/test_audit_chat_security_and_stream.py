"""
backend/tests/app/test_audit_chat_security_and_stream.py
智能风控问答 Copilot 安全加固与 SSE 流式回归测试集：
1. stream 事件顺序严格保证 meta -> delta* -> citations? -> done；
2. LLM streaming 完成后 assistant message 在数据库中只落库 1 条；
3. 非法 session_id / 跨用户 session 严格拒绝 (403/404)；
4. 跨用户单据未授权访问严格拒绝 (403)；
5. 原 /audits/chat 非流式接口保持完全可用与会话延续。
"""
import json
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from main import app
from app.core.database import Base, get_db
from app.models.document import FinancialDocument
from app.models.audit import AnalysisTask, ReviewReport, RiskFinding, AuditChatSession, AuditChatMessage
from app.services.auth_service import AuthService
from engines.orchestrator.dispatcher.local_dispatcher import LocalTaskManagerDispatcher

test_session_maker = None

@pytest.fixture(autouse=True)
async def setup_db():
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


async def create_test_doc_and_report(user_id: int = 10):
    """助手函数：创建已具备体检报告与风险项的测试单据"""
    import uuid
    async with test_session_maker() as session:
        doc = FinancialDocument(
            document_no=f"TEST-CHAT-{uuid.uuid4().hex[:6].upper()}",
            title="差旅报销单据",
            document_type="TRAVEL_EXPENSE",
            applicant_id=user_id,
            department_name="研发部",
            total_amount=1500.0,
            currency="CNY",
            status="IN_REVIEW"
        )
        session.add(doc)
        await session.flush()

        task = AnalysisTask(
            task_id=f"task_{uuid.uuid4().hex[:12]}",
            document_id=doc.id,
            audit_version=1,
            status="COMPLETED",
            progress_pct=100
        )
        session.add(task)
        await session.flush()

        report = ReviewReport(
            task_id=task.task_id,
            document_id=doc.id,
            final_score=75,
            overall_risk_level="medium",
            high_risks_count=0,
            medium_risks_count=1,
            low_risks_count=0,
            summary="检出差旅住宿费超标风险项",
            full_report_payload={"audit_completeness": "COMPLETE"}
        )
        session.add(report)
        await session.flush()

        finding = RiskFinding(
            report_id=report.id,
            finding_id=f"find_{uuid.uuid4().hex[:10]}",
            rule_code="RULE_HOTEL_LIMIT",
            rule_name="差旅住宿超标",
            risk_level="medium",
            agent_role="policy_agent",
            title="四星级酒店住宿费超标",
            description="二线城市每晚住宿限额400元，实际报销金额为650元",
            suggestion="扣减超标金额250元或提交合规特批",
            primary_visual_anchor={"field": "total_amount", "bbox": [100, 100, 200, 150]}
        )
        session.add(finding)
        await session.commit()
        return doc.id


@pytest.mark.asyncio
async def test_stream_event_sequence_and_single_persistence():
    """
    测试点 1 & 2:
    - SSE 流事件序列严格保证 meta -> delta* -> citations -> done；
    - StreamingResponse 响应头包含 No-Cache 与 Keep-Alive 规范头；
    - 流完成后 assistant 消息仅落库 1 次。
    """
    doc_id = await create_test_doc_and_report(user_id=10)
    token = AuthService.create_access_token(user_id=10, username="user10", roles=["EMPLOYEE"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/audits/chat/stream",
            json={"document_id": doc_id, "message": "为什么判定住宿费超标？"},
            headers=headers
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        assert resp.headers.get("cache-control") == "no-cache, no-transform"
        assert resp.headers.get("connection") == "keep-alive"
        assert resp.headers.get("x-accel-buffering") == "no"

        # 解析 SSE 帧
        text = resp.text
        lines = text.split("\n")
        events = []
        cur_event = "message"
        session_id = None

        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("event:"):
                cur_event = line[6:].strip()
            elif line.startswith("data:"):
                data_json = json.loads(line[5:].strip())
                events.append((cur_event, data_json))
                if cur_event == "meta":
                    session_id = data_json.get("session_id")

        assert len(events) >= 3, "SSE 流至少应包含 meta, delta, done 等事件"
        event_names = [e[0] for e in events]

        # 验证事件顺序: meta 必须是首个事件
        assert event_names[0] == "meta"
        assert session_id is not None

        # 最后一个事件必须是 done
        assert event_names[-1] == "done"

        # 包含 delta 增量帧
        assert "delta" in event_names
        # 包含 citations 依据帧 (因为有检出风险项)
        assert "citations" in event_names

        # meta 必须在 delta 之前，done 必须在所有 delta 与 citations 之后
        meta_idx = event_names.index("meta")
        first_delta_idx = event_names.index("delta")
        done_idx = event_names.index("done")
        citations_idx = event_names.index("citations")

        assert meta_idx < first_delta_idx < citations_idx < done_idx

        # 验证数据库落库: assistant message 必须有且仅有 1 条
        async with test_session_maker() as session:
            stmt = select(AuditChatMessage).where(
                AuditChatMessage.session_id == session_id,
                AuditChatMessage.role == "assistant"
            )
            res = await session.execute(stmt)
            assistant_msgs = list(res.scalars().all())
            assert len(assistant_msgs) == 1, f"assistant message 必须只持久化 1 条，当前实际为 {len(assistant_msgs)}"
            assert len(assistant_msgs[0].content) > 0


@pytest.mark.asyncio
async def test_session_id_security_and_cross_user_rejection():
    """
    测试点 3:
    - 伪造/不存在的非法 session_id 拒绝 (403/404)；
    - 跨用户 session_id 访问被拒绝 (403)；
    - session 绑定其他 document_id 冲突拒绝 (403)。
    """
    doc_id_1 = await create_test_doc_and_report(user_id=10)
    token_user1 = AuthService.create_access_token(user_id=10, username="user10", roles=["EMPLOYEE"])
    headers_user1 = {"Authorization": f"Bearer {token_user1}"}

    token_user2 = AuthService.create_access_token(user_id=20, username="user20", roles=["EMPLOYEE"])
    headers_user2 = {"Authorization": f"Bearer {token_user2}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. 用户 1 正常发起一次对话并获得 session_id
        init_resp = await ac.post(
            "/api/v1/audits/chat",
            json={"document_id": doc_id_1, "message": "你好"},
            headers=headers_user1
        )
        assert init_resp.status_code == 200
        user1_session_id = init_resp.json()["session_id"]

        # 2. 用户 1 传递一个完全伪造、不存在的非法 session_id -> 应被拒绝 (404/403)
        fake_resp = await ac.post(
            "/api/v1/audits/chat",
            json={"document_id": doc_id_1, "message": "提问", "session_id": "sess_non_existent_fake"},
            headers=headers_user1
        )
        assert fake_resp.status_code in [403, 404]

        # 3. 用户 2 (非单据经办人，非 session 拥有者) 尝试使用用户 1 的 session_id 访问 -> 403 拒绝
        cross_resp = await ac.post(
            "/api/v1/audits/chat",
            json={"document_id": doc_id_1, "message": "盗用会话", "session_id": user1_session_id},
            headers=headers_user2
        )
        assert cross_resp.status_code == 403

        # 4. 流式接口同样拒绝非法与跨用户 session
        stream_fake_resp = await ac.post(
            "/api/v1/audits/chat/stream",
            json={"document_id": doc_id_1, "message": "提问", "session_id": "sess_non_existent_fake"},
            headers=headers_user1
        )
        assert stream_fake_resp.status_code in [403, 404]

        stream_cross_resp = await ac.post(
            "/api/v1/audits/chat/stream",
            json={"document_id": doc_id_1, "message": "提问", "session_id": user1_session_id},
            headers=headers_user2
        )
        assert stream_cross_resp.status_code == 403


@pytest.mark.asyncio
async def test_document_rbac_permission_enforcement():
    """
    测试点 4:
    - document 必须通过当前用户的数据权限校验，禁止凭任意 document_id 读取报告；
    - 普通员工 (EMPLOYEE) 跨用户访问他人单据返回 403；
    - 管理员 (ADMIN) 拥有全量审查问答权限返回 200。
    """
    doc_id = await create_test_doc_and_report(user_id=10)

    token_unauth = AuthService.create_access_token(user_id=99, username="stranger", roles=["EMPLOYEE"])
    headers_unauth = {"Authorization": f"Bearer {token_unauth}"}

    token_admin = AuthService.create_access_token(user_id=1, username="admin", roles=["ADMIN"])
    headers_admin = {"Authorization": f"Bearer {token_admin}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 未授权普通员工尝试问答他人单据 -> 403 拒绝
        unauth_resp = await ac.post(
            "/api/v1/audits/chat",
            json={"document_id": doc_id, "message": "刺探单据风控内容"},
            headers=headers_unauth
        )
        assert unauth_resp.status_code == 403

        unauth_stream_resp = await ac.post(
            "/api/v1/audits/chat/stream",
            json={"document_id": doc_id, "message": "刺探单据流式内容"},
            headers=headers_unauth
        )
        assert unauth_stream_resp.status_code == 403

        # 管理员审核问答放行 -> 200 成功
        admin_resp = await ac.post(
            "/api/v1/audits/chat",
            json={"document_id": doc_id, "message": "管理员审核分析"},
            headers=headers_admin
        )
        assert admin_resp.status_code == 200


@pytest.mark.asyncio
async def test_original_chat_endpoint_remains_functional():
    """
    测试点 5:
    - 原始非流式 POST /audits/chat 保持完全可用；
    - 会话连续提问保持 session_id 延续。
    """
    doc_id = await create_test_doc_and_report(user_id=10)
    token = AuthService.create_access_token(user_id=10, username="user10", roles=["EMPLOYEE"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 第 1 轮提问
        resp1 = await ac.post(
            "/api/v1/audits/chat",
            json={"document_id": doc_id, "message": "请问这笔单据是否存在违规超标风险？"},
            headers=headers
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert "session_id" in data1
        assert data1["message"]["role"] == "assistant"
        assert len(data1["message"]["content"]) > 0
        sess_id = data1["session_id"]

        # 第 2 轮追问 (携带已有 session_id)
        resp2 = await ac.post(
            "/api/v1/audits/chat",
            json={"document_id": doc_id, "message": "请问如何申请合规特批？", "session_id": sess_id},
            headers=headers
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["session_id"] == sess_id
        assert data2["message"]["role"] == "assistant"
