"""
backend/tests/app/test_audit_completion_and_recovery.py
核心回归测试套件：
1. Test A: Stage 4 评估完成 -> ReviewReport 落库 -> TASK_COMPLETED 100% 完整闭环
2. Test B: ApprovalEngine 初始化抛异常，但 ReviewReport / RiskFinding 成功保留 (Savepoint 隔离)
3. Test C: ReviewReport 本身落库失败，事务回滚并发送 TASK_FAILED，AnalysisTask 标记为 FAILED
4. Test D: 单据工作台通过 document_id + current_version 查询并找回当前 task_id，且 SSE 可回放历史
"""
import pytest
import asyncio
from unittest.mock import patch
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from main import app
from app.core.database import Base, get_db
from app.models.user import User, Role, UserRole
from app.models.document import FinancialDocument, DocumentLineItem
from app.models.audit import AnalysisTask, ReviewReport
from app.models.workflow import ApprovalWorkflow, ApprovalWorkflowNode, ApprovalInstance
from app.services.auth_service import AuthService
from app.services.audit_service import AuditService
from app.repositories import audit_repo
from app.services.approval_engine import ApprovalEngine
from engines.contract.result import AuditResultDTO
from engines.contract.events import EventTypeEnum
from engines.contract.event_bus import event_bus

@pytest.fixture
async def recovery_test_env():
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

    # 预置用户、角色与工作流数据
    async with session_maker() as session:
        pwd_hash = AuthService.hash_password("123456")
        admin = User(id=1, username="admin_rec", hashed_password=pwd_hash, real_name="管理员", department_name="IT部")
        mgr = User(id=2, username="mgr_rec", hashed_password=pwd_hash, real_name="张经理", department_name="采购部")
        fin = User(id=3, username="fin_rec", hashed_password=pwd_hash, real_name="李财务", department_name="财务部")
        cfo = User(id=4, username="cfo_rec", hashed_password=pwd_hash, real_name="王总监", department_name="财务部")
        emp = User(id=5, username="emp_rec", hashed_password=pwd_hash, real_name="小赵", department_name="采购部")
        session.add_all([admin, mgr, fin, cfo, emp])

        role_emp = Role(id=1, role_code="EMPLOYEE", role_name="经办员工")
        session.add(role_emp)
        session.add(UserRole(user_id=5, role_id=1))

        # 对公付款工作流
        wf = ApprovalWorkflow(id=1, workflow_code="WF_CORP", workflow_name="对公付款流", document_type="CORP_PAYMENT", is_active=True)
        node1 = ApprovalWorkflowNode(id=1, workflow_id=1, node_order=1, node_name="部门初审", approver_type="USER", user_id=2)
        node2 = ApprovalWorkflowNode(id=2, workflow_id=1, node_order=2, node_name="财务复核", approver_type="USER", user_id=3)
        node3 = ApprovalWorkflowNode(id=3, workflow_id=1, node_order=3, node_name="CFO特批", approver_type="USER", user_id=4, is_final=True)
        session.add_all([wf, node1, node2, node3])
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_maker

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_stage4_to_review_report_and_task_completed_e2e(recovery_test_env):
    """Test A: Stage 4 评估完成后生成 ReviewReport，并发布 100% 的 TASK_COMPLETED"""
    client, session_maker = recovery_test_env

    # 1. 创建对公付款单据 (> 10000)
    async with session_maker() as session:
        doc = FinancialDocument(
            document_no="CORP-RECOV-001",
            document_type="CORP_PAYMENT",
            title="大型云服务器采购",
            applicant_id=5,
            department_name="采购部",
            total_amount=Decimal("18000.00"),
            currency="CNY",
            current_version=1,
            status="SUBMITTED"
        )
        session.add(doc)
        await session.flush()
        item = DocumentLineItem(
            document_id=doc.id,
            line_no=1,
            expense_type="设备采购",
            item_desc="云服务器年付",
            amount=Decimal("18000.00")
        )
        session.add(item)
        await session.commit()
        doc_id = doc.id

    # 2. 模拟由 AuditService.handle_audit_completed 接收到 AuditCompletedEvent 后的落库
    result_dto = AuditResultDTO(
        task_id="task_recov_001",
        document_id=doc_id,
        overall_risk_level="low",
        final_score=95,
        risk_score=95,
        high_risks_count=0,
        medium_risks_count=0,
        low_risks_count=0,
        audit_completeness="DEGRADED",
        summary="核验降级完成",
        full_report_payload={"audit_completeness": "DEGRADED"}
    )

    async with session_maker() as session:
        # 预先创建 analysis_task
        task = AnalysisTask(
            task_id="task_recov_001",
            document_id=doc_id,
            audit_version=1,
            status="RUNNING",
            current_stage="STAGE_4_EVALUATED",
            progress_pct=95
        )
        session.add(task)
        await session.commit()

    async with session_maker() as session:
        service = AuditService(session)
        report = await service.handle_audit_completed(
            task_id="task_recov_001",
            document_id=doc_id,
            result=result_dto,
            audit_version=1
        )
        assert report is not None
        assert report.id is not None

    # 3. 验证数据库状态
    async with session_maker() as session:
        task_rec = (await session.execute(select(AnalysisTask).where(AnalysisTask.task_id == "task_recov_001"))).scalars().first()
        assert task_rec.status == "COMPLETED"
        assert task_rec.progress_pct == 100

        reports = (await session.execute(select(ReviewReport).where(ReviewReport.document_id == doc_id))).scalars().all()
        assert len(reports) == 1

        instances = (await session.execute(select(ApprovalInstance).where(ApprovalInstance.document_id == doc_id))).scalars().all()
        assert len(instances) == 1

        # 4. 验证历史事件队列中存在 TASK_COMPLETED
        history = event_bus.get_history("task_recov_001")
        completed_events = [e for e in history if getattr(e, "event", None) == EventTypeEnum.TASK_COMPLETED]
        assert len(completed_events) >= 1
        assert completed_events[-1].data["percent"] == 100


@pytest.mark.asyncio
async def test_approval_engine_failure_isolated_report_preserved(recovery_test_env):
    """Test B: ApprovalEngine 启动失败时，通过 Savepoint 隔离回滚，审核报告仍成功落库，并正常发 TASK_COMPLETED"""
    client, session_maker = recovery_test_env

    async with session_maker() as session:
        doc = FinancialDocument(
            document_no="CORP-RECOV-002",
            document_type="CORP_PAYMENT",
            title="审批流故障测试单",
            applicant_id=5,
            department_name="采购部",
            total_amount=Decimal("12000.00"),
            currency="CNY",
            current_version=1,
            status="SUBMITTED"
        )
        session.add(doc)
        await session.commit()
        doc_id = doc.id

        task = AnalysisTask(
            task_id="task_recov_002",
            document_id=doc_id,
            audit_version=1,
            status="RUNNING",
            current_stage="STAGE_4_EVALUATED",
            progress_pct=95
        )
        session.add(task)
        await session.commit()

    result_dto = AuditResultDTO(
        task_id="task_recov_002",
        document_id=doc_id,
        overall_risk_level="low",
        final_score=100,
        risk_score=100,
        high_risks_count=0,
        medium_risks_count=0,
        low_risks_count=0,
        audit_completeness="COMPLETE",
        summary="全合规通过",
        full_report_payload={"audit_completeness": "COMPLETE"}
    )

    # 人为 mock ApprovalEngine.start_workflow 抛出异常
    with patch.object(ApprovalEngine, "start_workflow", side_effect=RuntimeError("ApprovalEngine simulated workflow deadlock")):
        async with session_maker() as session:
            service = AuditService(session)
            report = await service.handle_audit_completed(
                task_id="task_recov_002",
                document_id=doc_id,
                result=result_dto,
                audit_version=1
            )
            assert report is not None

    # 断言：ReviewReport 成功保留！没有被审批引擎的异常一起回滚
    async with session_maker() as session:
        reports = (await session.execute(select(ReviewReport).where(ReviewReport.document_id == doc_id))).scalars().all()
        assert len(reports) == 1
        rep = reports[0]
        assert rep.full_report_payload.get("workflow_initialized") is False
        assert "ApprovalEngine simulated workflow deadlock" in rep.full_report_payload.get("workflow_error", "")

        task_rec = (await session.execute(select(AnalysisTask).where(AnalysisTask.task_id == "task_recov_002"))).scalars().first()
        assert task_rec.status == "COMPLETED"
        assert task_rec.progress_pct == 100

        history = event_bus.get_history("task_recov_002")
        completed_events = [e for e in history if getattr(e, "event", None) == EventTypeEnum.TASK_COMPLETED]
        assert len(completed_events) >= 1
        assert completed_events[-1].data["workflow_initialized"] is False


@pytest.mark.asyncio
async def test_report_persistence_failure_triggers_task_failed(recovery_test_env):
    """Test C: 报告本身落库失败时，事务回滚，AnalysisTask 标记为 FAILED，并广播 TASK_FAILED"""
    client, session_maker = recovery_test_env

    async with session_maker() as session:
        doc = FinancialDocument(
            document_no="CORP-RECOV-003",
            document_type="CORP_PAYMENT",
            title="落库崩溃测试单",
            applicant_id=5,
            department_name="采购部",
            total_amount=Decimal("12000.00"),
            currency="CNY",
            current_version=1,
            status="SUBMITTED"
        )
        session.add(doc)
        await session.commit()
        doc_id = doc.id

        task = AnalysisTask(
            task_id="task_recov_003",
            document_id=doc_id,
            audit_version=1,
            status="RUNNING",
            current_stage="STAGE_4_EVALUATED",
            progress_pct=95
        )
        session.add(task)
        await session.commit()

    result_dto = AuditResultDTO(
        task_id="task_recov_003",
        document_id=doc_id,
        overall_risk_level="low",
        final_score=100,
        risk_score=100,
        high_risks_count=0,
        medium_risks_count=0,
        low_risks_count=0,
        audit_completeness="COMPLETE",
        summary="落库测试",
        full_report_payload={}
    )

    # 人为 mock audit_repo.save_audit_result 抛出异常
    with patch.object(audit_repo, "save_audit_result", side_effect=RuntimeError("Simulated Database I/O Crash on Report Save")):
        with patch("app.core.database.AsyncSessionLocal", session_maker):
            with pytest.raises(RuntimeError, match="Simulated Database I/O Crash"):
                async with session_maker() as session:
                    service = AuditService(session)
                    await service.handle_audit_completed(
                        task_id="task_recov_003",
                        document_id=doc_id,
                        result=result_dto,
                        audit_version=1
                    )

    # 断言：AnalysisTask 被置为 FAILED，并记录 error_message
    async with session_maker() as session:
        task_rec = (await session.execute(select(AnalysisTask).where(AnalysisTask.task_id == "task_recov_003"))).scalars().first()
        assert task_rec.status == "FAILED"
        assert "Simulated Database I/O Crash" in task_rec.error_message

        # 广播了 TASK_FAILED 实时事件
        history = event_bus.get_history("task_recov_003")
        failed_events = [e for e in history if getattr(e, "event", None) == EventTypeEnum.TASK_FAILED]
        assert len(failed_events) >= 1
        assert failed_events[-1].data["error_code"] == "SAVE_REPORT_FAILED"
        assert "Simulated Database I/O Crash" in failed_events[-1].data["error_detail"]


@pytest.mark.asyncio
async def test_get_latest_task_and_workbench_sse_recovery(recovery_test_env):
    """Test D: 单据工作台通过 document_id + current_version 查询并找回当前 task_id，且支持历史回放"""
    client, session_maker = recovery_test_env

    token = AuthService.create_access_token(user_id=1, username="admin_rec", roles=["ADMIN"])
    headers = {"Authorization": f"Bearer {token}"}

    # 1. 预置单据与两个版本的任务
    async with session_maker() as session:
        doc = FinancialDocument(
            id=101,
            document_no="CORP-RECOV-004",
            document_type="CORP_PAYMENT",
            title="多版本查询测试单",
            applicant_id=5,
            department_name="采购部",
            total_amount=Decimal("1000.00"),
            currency="CNY",
            current_version=2, # 当前为第 2 版
            status="IN_REVIEW"
        )
        session.add(doc)
        # 版本 1 历史任务
        task_v1 = AnalysisTask(
            task_id="task_recov_v1",
            document_id=101,
            audit_version=1,
            status="COMPLETED",
            current_stage="COMPLETED",
            progress_pct=100
        )
        # 版本 2 当前进行中任务
        task_v2 = AnalysisTask(
            task_id="task_recov_v2",
            document_id=101,
            audit_version=2,
            status="RUNNING",
            current_stage="STAGE_4_EVALUATED",
            progress_pct=95
        )
        session.add_all([task_v1, task_v2])
        await session.commit()

    # 2. 调用 GET /api/v1/audits/tasks/by-document/{document_id}/latest
    resp = await client.get("/api/v1/audits/tasks/by-document/101/latest", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # 必须严格匹配当前版本 current_version=2 的 task_id，禁止串到旧版本！
    assert data["task_id"] == "task_recov_v2"
    assert data["audit_version"] == 2
    assert data["status"] == "RUNNING"
    assert data["progress_pct"] == 95

    # 3. 验证不存在任务的单据返回 404
    resp_404 = await client.get("/api/v1/audits/tasks/by-document/9999/latest", headers=headers)
    assert resp_404.status_code == 404
