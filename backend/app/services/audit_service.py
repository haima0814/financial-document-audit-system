"""
backend/app/services/audit_service.py
风控体检报告查询、证据链提取与智能 AI 问答服务
"""
import uuid
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.user import User
from app.models.audit import AnalysisTask, ReviewReport, RiskFinding, AuditChatSession, AuditChatMessage
from app.models.document import FinancialDocument
from app.models.workflow import ApprovalWorkflow
from app.schemas.audit import ReviewReportOut, RiskFindingOut, AuditChatReq, AuditChatResp, AuditChatMessageOut
from app.repositories import audit_repo, document_repo
from app.services.approval_engine import ApprovalEngine
from engines.contract.result import AuditResultDTO
from engines.contract.events import EventTypeEnum
from engines.orchestrator.stream_producer import StreamProducer

class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db
        # 确保生命周期中领域事件消费者已稳定挂载 (杜绝手工单独 import 依赖)
        from app.services.audit_handler import register_audit_handler
        register_audit_handler()

    async def start_audit(
        self,
        document_id: int,
        applicant_id: Optional[int] = None,
        tenant_id: int = 1,
        audit_version: int = 1
    ) -> str:
        """
        审核启动统一入口 (业务生命周期高内聚闭环):
        1. 幂等预检：检索当前 (document_id, audit_version) 是否已存在任务；
        2. 写入带有 audit_version 的 AnalysisTask，捕获 IntegrityError 并发冲突兜底；
        3. 由 AuditContextBuilder 统一从数据库装配不可变的 AuditExecutionContext 内存快照;
        4. 物理阻断 Agent 运行时直连 DB 的后门 (Agent 全程 0 SQL / 0 ORM);
        5. 异步派发至 TaskDispatcher (Local / Celery);
        6. 返回审查唯一 task_id。
        """
        import uuid
        from sqlalchemy.exc import IntegrityError
        from app.services.audit_context_builder import AuditContextBuilder
        from app.models.audit import AnalysisTask
        from engines.orchestrator.master_graph import MasterOrchestrator
        from engines.orchestrator.dispatcher import LocalTaskManagerDispatcher

        # 1. 预检已有同版本任务
        stmt = (
            select(AnalysisTask)
            .where(
                AnalysisTask.document_id == document_id,
                AnalysisTask.audit_version == audit_version
            )
            .order_by(desc(AnalysisTask.id))
        )
        existing = (await self.db.execute(stmt)).scalars().first()
        if existing:
            return existing.task_id

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        analysis_task = AnalysisTask(
            task_id=task_id,
            document_id=document_id,
            audit_version=audit_version,
            status="PENDING",
            current_stage="STAGE_1_PARSED",
            progress_pct=10,
        )
        self.db.add(analysis_task)
        try:
            await self.db.flush()
        except IntegrityError:
            # 数据库 UNIQUE(document_id, audit_version) 冲突兜底 (并发两请求同时达到)
            await self.db.rollback()
            retry_stmt = (
                select(AnalysisTask)
                .where(
                    AnalysisTask.document_id == document_id,
                    AnalysisTask.audit_version == audit_version
                )
                .order_by(desc(AnalysisTask.id))
            )
            existing_retry = (await self.db.execute(retry_stmt)).scalars().first()
            if existing_retry:
                return existing_retry.task_id
            raise

        context = await AuditContextBuilder.build(
            db=self.db,
            document_id=document_id,
            task_id=task_id,
            audit_version=audit_version
        )

        actual_applicant_id = applicant_id or context.applicant_id
        actual_tenant_id = tenant_id or context.tenant_id

        async def _run_analysis_pipeline():
            try:
                await MasterOrchestrator.run(
                    task_id=task_id,
                    document_id=document_id,
                    applicant_id=actual_applicant_id,
                    context=context
                )
            except Exception as e:
                import logging
                logger = logging.getLogger("audit_service")
                logger.error(f"审核流水线异步执行失败: {e}", exc_info=True)

                # 状态与错误落库 (独立 session 隔离保护，防止事务污染)
                try:
                    from app.core.database import AsyncSessionLocal
                    async with AsyncSessionLocal() as err_session:
                        task_stmt = select(AnalysisTask).where(AnalysisTask.task_id == task_id)
                        task_rec = (await err_session.execute(task_stmt)).scalars().first()
                        if task_rec and task_rec.status != "COMPLETED":
                            task_rec.status = "FAILED"
                            task_rec.current_stage = "FAILED"
                            task_rec.error_message = str(e)
                            await err_session.commit()
                except Exception as db_err:
                    logger.error(f"写入任务失败状态异常: {db_err}")

                # 广播 TASK_FAILED 实时事件
                try:
                    await StreamProducer.publish_event(
                        task_id=task_id,
                        document_id=document_id,
                        event_type=EventTypeEnum.TASK_FAILED,
                        payload={
                            "error_code": "PIPELINE_FAILED",
                            "error_detail": f"审核流水线执行失败: {str(e)}"
                        }
                    )
                except Exception as sse_err:
                    logger.error(f"广播 TASK_FAILED 异常: {sse_err}")

                # 重新抛出异常，驱动 LocalTaskManagerDispatcher 正确标记 FAILED
                raise e

        await LocalTaskManagerDispatcher.dispatch_audit_task(
            task_id=task_id,
            document_id=document_id,
            applicant_id=actual_applicant_id,
            coro=_run_analysis_pipeline()
        )

        return task_id

    async def handle_audit_completed(
        self,
        task_id: str,
        document_id: int,
        result: AuditResultDTO,
        event_id: Optional[str] = None,
        audit_version: int = 1
    ) -> Optional[ReviewReport]:
        """
        处理 Agent 流水线审查完毕（Service 层统一收口事务与状态推进）：
        1. 在单一原子事务 (Unit of Work) 内插入 ProcessedEvent 幂等防线，依据 event_id 唯一约束阻断并发竞态；
        2. 由 audit_repo.save_audit_result 写入 report 和 findings，并更新 task 为 COMPLETED；
        3. 由 ApprovalEngine 依据体检事实 (ApprovalDecision) 推进单据状态与生成待办任务；
        4. 统一事务提交 commit；
        5. 广播 TASK_COMPLETED 过程事件至 RealtimeEventBus 与 StreamProducer。
        """
        import logging
        logger = logging.getLogger("audit_service")

        # 0. 校验关联单据是否存在 (防止单体测试或无效事件引发外键异常)
        target_doc_id = document_id or getattr(result, "document_id", None)
        if target_doc_id:
            doc = await self.db.get(FinancialDocument, target_doc_id)
            if not doc:
                logger.warning(f"[AuditService] 单据[{target_doc_id}]在数据库中不存在，跳过归档处理。")
                return None

        # 1. 事务型幂等登记 (Unit of Work 事务防线)
        if event_id:
            from app.models.audit import ProcessedEvent
            from sqlalchemy.exc import IntegrityError
            try:
                processed_rec = ProcessedEvent(
                    event_id=event_id,
                    task_id=task_id,
                    audit_version=audit_version
                )
                self.db.add(processed_rec)
                await self.db.flush()
            except IntegrityError:
                await self.db.rollback()
                logger.warning(f"[AuditService] 数据库唯一约束触发幂等拦截！事件[{event_id}]已处理，跳过重复落库。")
                return None

        # 2. 统一由 audit_repo 原子落库 (情况 A: 报告落库失败保护)
        try:
            report = await audit_repo.save_audit_result(self.db, result)
        except Exception as e:
            logger.exception(f"[AuditService] 审核报告原子落库失败: {e}")
            await self.db.rollback()

            # 打开独立会话将 AnalysisTask 置为 FAILED 并持久化真实错误摘要
            try:
                from app.core.database import AsyncSessionLocal
                async with AsyncSessionLocal() as err_session:
                    task_stmt = select(AnalysisTask).where(AnalysisTask.task_id == task_id)
                    task_rec = (await err_session.execute(task_stmt)).scalars().first()
                    if task_rec:
                        task_rec.status = "FAILED"
                        task_rec.current_stage = "FAILED"
                        task_rec.error_message = f"审核报告落库失败: {str(e)}"
                        await err_session.commit()
            except Exception as db_err:
                logger.error(f"[AuditService] 记录任务失败状态异常: {db_err}")

            # 发送 TASK_FAILED 实时事件，通知前端停止转圈等待
            try:
                await StreamProducer.publish_event(
                    task_id=task_id,
                    document_id=document_id,
                    event_type=EventTypeEnum.TASK_FAILED,
                    payload={
                        "error_code": "SAVE_REPORT_FAILED",
                        "error_detail": f"审核体检报告持久化失败: {str(e)}"
                    }
                )
            except Exception as sse_err:
                logger.error(f"[AuditService] 广播 TASK_FAILED 异常: {sse_err}")

            raise e

        # 3. 触发审批引擎智能流转 (情况 B: Savepoint / begin_nested 隔离保护)
        workflow_initialized = True
        workflow_error = None
        try:
            async with self.db.begin_nested():
                doc = await self.db.get(FinancialDocument, document_id)
                doc_type = doc.document_type if doc else "TRAVEL_REIMBURSEMENT"
                wf_stmt = select(ApprovalWorkflow).where(
                    and_(ApprovalWorkflow.document_type == doc_type, ApprovalWorkflow.is_active == True)
                )
                wf = (await self.db.execute(wf_stmt)).scalars().first()
                if not wf:
                    wf = (await self.db.execute(select(ApprovalWorkflow).where(ApprovalWorkflow.is_active == True))).scalars().first()
                wf_id = wf.id if wf else 1

                await ApprovalEngine.start_workflow(
                    db=self.db,
                    document_id=document_id,
                    workflow_id=wf_id,
                    report_id=report.id
                )
        except Exception as exc:
            workflow_initialized = False
            workflow_error = str(exc)
            logger.exception(f"[AuditService] 触发审批流失败，已通过 Savepoint 隔离回滚，审核事实报告保留: {exc}")

        # 4. 挂载 workflow 状态信息至报告 payload
        # Savepoint 回滚会导致 session 实体属性 expired，使用 await refresh 异步刷新实体避免 MissingGreenlet
        if report:
            await self.db.refresh(report)
            report_payload = dict(report.full_report_payload) if (report.full_report_payload and isinstance(report.full_report_payload, dict)) else {}
            report_payload["workflow_initialized"] = workflow_initialized
            report_payload["workflow_error"] = workflow_error
            report.full_report_payload = report_payload
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(report, "full_report_payload")

        # 5. 提交数据库事务 (确保 ReviewReport, RiskFinding, AnalysisTask COMPLETED 成功落库)
        await self.db.commit()
        await self.db.refresh(report)

        # 6. 发布 TASK_COMPLETED 领域事件 (解耦通知 WebSocket / SSE / MQ)
        decision_info = report.full_report_payload.get("approval_decision") if report.full_report_payload else None
        completed_payload = {
            "report_id": report.id,
            "task_id": task_id,
            "percent": 100,
            "overall_risk_level": result.overall_risk_level,
            "risk_score": result.risk_score,
            "final_score": result.final_score,
            "high_risks_count": result.high_risks_count,
            "medium_risks_count": result.medium_risks_count,
            "low_risks_count": result.low_risks_count,
            "summary": result.summary or "",
            "audit_completeness": getattr(result, "audit_completeness", "COMPLETE"),
            "decision": decision_info,
            "workflow_initialized": workflow_initialized,
            "workflow_error": workflow_error,
        }
        await StreamProducer.publish_event(
            task_id=task_id,
            document_id=document_id,
            event_type=EventTypeEnum.TASK_COMPLETED,
            payload=completed_payload
        )

        return report

    # 兼容别名
    handle_audit_completion = handle_audit_completed

    async def get_latest_task_by_document(self, document_id: int) -> Optional[AnalysisTask]:
        """按单据 ID 及当前最新版本获取审核任务 (禁止跨版本串连)"""
        doc = await self.db.get(FinancialDocument, document_id)
        if not doc:
            return None
        return await audit_repo.get_latest_task_by_document(
            self.db,
            document_id=document_id,
            audit_version=doc.current_version
        )

    async def get_report_by_document(self, document_id: int) -> Optional[ReviewReport]:
        """查询指定单据的最新风控综合体检报告 (附带 findings)"""
        stmt = (
            select(ReviewReport)
            .options(selectinload(ReviewReport.findings))
            .where(ReviewReport.document_id == document_id)
            .order_by(desc(ReviewReport.created_at))
        )
        res = await self.db.execute(stmt)
        return res.scalars().first()

    async def get_report_by_task_id(self, task_id: str) -> Optional[ReviewReport]:
        """精准按 task_id 查询单据风控体检报告 (附带 findings，彻底杜绝多轮重审历史串味)"""
        stmt = (
            select(ReviewReport)
            .options(selectinload(ReviewReport.findings))
            .where(ReviewReport.task_id == task_id)
        )
        res = await self.db.execute(stmt)
        return res.scalars().first()

    async def get_task_by_id(self, task_id: str):
        """精准按 task_id 查询审核分析任务状态"""
        from app.models.audit import AnalysisTask
        stmt = select(AnalysisTask).where(AnalysisTask.task_id == task_id)
        res = await self.db.execute(stmt)
        return res.scalars().first()

    async def get_findings(self, report_id: int) -> List[RiskFinding]:
        """获取报告下的所有风险判定项"""
        stmt = select(RiskFinding).where(RiskFinding.report_id == report_id).order_by(RiskFinding.id)
        res = await self.db.execute(stmt)
        return list(res.scalars().all())

    async def check_document_access_permission(
        self,
        doc: FinancialDocument,
        user_id: int,
        roles: List[str]
    ) -> bool:
        """
        验证当前用户是否有权访问该单据的风控审查上下文：
        1. ADMIN 或 超级用户: 拥有全量数据访问权限；
        2. 单据经办人本人 (applicant_id == user_id): 拥有访问权限；
        3. CFO: 金额 >= 10,000 或 判定为高危 (overall_risk_level == 'high') 的单据 或 本人单据；
        4. FINANCE: 所有非草稿单据 (status != 'DRAFT')；
        5. MANAGER: 本部门非草稿单据 (department_name == user.department_name)；
        6. 其他 (普通员工): 严禁跨越访问他人单据。
        """
        if "ADMIN" in roles:
            return True
        if doc.applicant_id == user_id:
            return True
        if "CFO" in roles:
            if doc.total_amount and doc.total_amount >= 10000:
                return True
            report = await self.get_report_by_document(doc.id)
            if report and report.overall_risk_level == "high":
                return True
        if "FINANCE" in roles:
            if doc.status != "DRAFT":
                return True
        if "MANAGER" in roles:
            user = await self.db.get(User, user_id)
            if user and user.department_name and doc.department_name == user.department_name:
                if doc.status != "DRAFT":
                    return True
        return False

    async def validate_chat_access(
        self,
        user_id: int,
        roles: List[str],
        req: AuditChatReq
    ) -> Tuple[FinancialDocument, Optional[AuditChatSession]]:
        """
        统一执行单据数据权限与 session_id 严格校验 (Fail-Closed):
        - document 必须通过当前用户的数据权限校验，禁止只凭 document_id 读取任意报告上下文；
        - 已有 session_id 必须验证属于当前 user_id 且绑定当前 document_id；
        - 若 session_id 不存在或属于他人，立即拒绝。
        """
        # 1. 校验单据存在性
        doc = await self.db.get(FinancialDocument, req.document_id)
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"单据 [ID={req.document_id}] 不存在"
            )

        # 2. 校验当前用户单据数据权限 (RBAC)
        has_perm = await self.check_document_access_permission(doc, user_id, roles)
        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="数据权限拒绝：您无权访问该单据的风控审查内容"
            )

        # 3. 校验 session_id 合法性与归属性
        chat_session = None
        if req.session_id:
            stmt = select(AuditChatSession).where(AuditChatSession.session_id == req.session_id)
            chat_session = (await self.db.execute(stmt)).scalars().first()
            if not chat_session:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="指定的问答会话不存在或已失效"
                )
            if chat_session.user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="会话权限拒绝：该会话属于其他用户，禁止跨用户访问"
                )
            if chat_session.document_id != req.document_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="会话冲突：该会话未绑定当前单据"
                )

        return doc, chat_session

    async def chat_with_audit_context(
        self,
        user_id: int,
        req: AuditChatReq,
        roles: Optional[List[str]] = None
    ) -> AuditChatResp:
        """
        基于当前单据的风控体检报告和证据链，进行智能问答 (带严格权限校验)
        """
        doc, chat_session = await self.validate_chat_access(
            user_id=user_id,
            roles=roles or [],
            req=req
        )

        # 1. 查找或创建 Session
        if not chat_session:
            session_id = f"sess_{uuid.uuid4().hex[:12]}"
            chat_session = AuditChatSession(
                session_id=session_id,
                document_id=req.document_id,
                user_id=user_id
            )
            self.db.add(chat_session)
            await self.db.flush()
        else:
            session_id = chat_session.session_id

        # 2. 记录用户提问消息
        user_msg = AuditChatMessage(
            session_id=session_id,
            role="user",
            content=req.message,
            citations=[]
        )
        self.db.add(user_msg)

        # 3. 加载风控报告上下文
        report = await self.get_report_by_document(req.document_id)

        # 4. 基于证据链与大模型 (LLM RAG + 规则模板优雅兜底) 生成回答
        reply_content = ""
        citations = []

        if not report:
            reply_content = "当前单据尚未完成 AI 审查流水线，暂未出具风险体检报告。请稍候提交审核或等待分析完成。"
        else:
            findings = report.findings or []
            # 提取所有关联依据锚点
            matched_findings = []
            q_lower = req.message.lower()
            for f in findings:
                if any(kw in q_lower for kw in [f.rule_name.lower(), f.title.lower(), "超标", "发票", "金额", "公司", "连号", "行程"]):
                    matched_findings.append(f)

            if not matched_findings and findings:
                matched_findings = findings[:2] # 兜底关联前两项

            for mf in matched_findings:
                citations.append({
                    "finding_id": mf.finding_id,
                    "rule_code": mf.rule_code,
                    "title": mf.title,
                    "primary_visual_anchor": mf.primary_visual_anchor
                })

            # 优先尝试调用真实大模型 (OpenAI / DeepSeek / Qwen 等)
            from app.core.llm_client import LLMClient
            findings_summary = [
                {
                    "rule_code": f.rule_code,
                    "rule_name": f.rule_name,
                    "risk_level": f.risk_level,
                    "description": f.description,
                    "suggestion": f.suggestion
                }
                for f in findings
            ]

            llm_response = await LLMClient.ask_audit_copilot(
                document_title=doc.title if doc else "未知单据",
                document_type=doc.document_type if doc else "未知类型",
                total_amount=str(doc.total_amount if doc else "0.00"),
                findings_summary=findings_summary,
                user_query=req.message
            )

            if llm_response:
                reply_content = llm_response
            else:
                # 规则模板兜底
                if matched_findings:
                    points = []
                    for idx, mf in enumerate(matched_findings, 1):
                        points.append(
                            f"{idx}. **[{mf.rule_name}]** ({mf.risk_level.upper()}级风险)：{mf.description}。\n   - 建议处理：{mf.suggestion}"
                        )
                    reply_content = (
                        f"针对单据【{doc.title if doc else ''}】，系统检出的相关风险发现如下：\n\n"
                        + "\n\n".join(points)
                        + "\n\n如需特批放行，请审批人在审批意见中注明合规依据或经办说明。"
                    )
                else:
                    reply_content = (
                        f"经多智能体联合核查，单据【{doc.title if doc else ''}】各项指标合规，未发现明显异常或违规风险。"
                        f"综合评分为 {report.final_score} 分（{report.overall_risk_level.upper()}风险等级）。"
                    )

        # 5. 记录 AI 回复消息
        ai_msg = AuditChatMessage(
            session_id=session_id,
            role="assistant",
            content=reply_content,
            citations=citations
        )
        self.db.add(ai_msg)
        await self.db.commit()
        await self.db.refresh(ai_msg)

        return AuditChatResp(
            session_id=session_id,
            message=AuditChatMessageOut(
                id=ai_msg.id,
                role=ai_msg.role,
                content=ai_msg.content,
                citations=ai_msg.citations,
                created_at=ai_msg.created_at
            )
        )

    async def chat_with_audit_context_stream(
        self,
        user_id: int,
        req: AuditChatReq,
        roles: Optional[List[str]] = None
    ):
        """
        流式 AI 审查问答生成器 (SSE 协议规范)
        支持真实大模型流式输出与离线高质量知识模板增量平滑推送
        事件协议：
        1. event: meta (携带 session_id)
        2. event: delta (增量返回 token/chunk: {"delta": "..."})
        3. event: citations (携带关联风险发现项与视觉锚点)
        4. event: done (完成通知，并在后台一次性落库完整回答)
        """
        import asyncio
        import json
        import uuid
        from app.models.audit import AuditChatSession, AuditChatMessage
        from app.core.llm_client import LLMClient

        doc, chat_session = await self.validate_chat_access(
            user_id=user_id,
            roles=roles or [],
            req=req
        )

        try:
            # 1. 查找或创建 Session
            if not chat_session:
                session_id = f"sess_{uuid.uuid4().hex[:12]}"
                chat_session = AuditChatSession(
                    session_id=session_id,
                    document_id=req.document_id,
                    user_id=user_id
                )
                self.db.add(chat_session)
                await self.db.flush()
            else:
                session_id = chat_session.session_id

            # 发送 meta 事件
            yield f"event: meta\ndata: {json.dumps({'session_id': session_id}, ensure_ascii=False)}\n\n"

            # 2. 记录用户提问
            user_msg = AuditChatMessage(
                session_id=session_id,
                role="user",
                content=req.message,
                citations=[]
            )
            self.db.add(user_msg)
            await self.db.flush()

            # 3. 加载风控报告上下文
            report = await self.get_report_by_document(req.document_id)

            citations = []
            full_content_parts = []

            if not report:
                not_ready = "当前单据尚未完成 AI 审查流水线，暂未出具风险体检报告。请稍候提交审核或等待分析完成。"
                full_content_parts.append(not_ready)
                yield f"event: delta\ndata: {json.dumps({'delta': not_ready}, ensure_ascii=False)}\n\n"
            else:
                findings = report.findings or []
                matched_findings = []
                q_lower = req.message.lower()
                for f in findings:
                    if any(kw in q_lower for kw in [f.rule_name.lower(), f.title.lower(), "超标", "发票", "金额", "公司", "连号", "行程"]):
                        matched_findings.append(f)

                if not matched_findings and findings:
                    matched_findings = findings[:2]

                for mf in matched_findings:
                    citations.append({
                        "finding_id": mf.finding_id,
                        "rule_code": mf.rule_code,
                        "title": mf.title,
                        "primary_visual_anchor": mf.primary_visual_anchor
                    })

                findings_summary = [
                    {
                        "rule_code": f.rule_code,
                        "rule_name": f.rule_name,
                        "risk_level": f.risk_level,
                        "description": f.description,
                        "suggestion": f.suggestion
                    }
                    for f in findings
                ]

                streamed_any = False
                if LLMClient.is_configured():
                    try:
                        async for chunk in LLMClient.stream_audit_copilot(
                            document_title=doc.title if doc else "未知单据",
                            document_type=doc.document_type if doc else "未知类型",
                            total_amount=str(doc.total_amount if doc else "0.00"),
                            findings_summary=findings_summary,
                            user_query=req.message
                        ):
                            streamed_any = True
                            full_content_parts.append(chunk)
                            yield f"event: delta\ndata: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
                    except Exception as e:
                        logger.warning(f"大模型流式调用异常: {e}")

                if not streamed_any:
                    # 规则引擎模板流式输出 (支持离线与本地演示真实增量输出)
                    if matched_findings:
                        points = []
                        for idx, mf in enumerate(matched_findings, 1):
                            points.append(
                                f"{idx}. **[{mf.rule_name}]** ({mf.risk_level.upper()}级风险)：{mf.description}。\n   - 建议处理：{mf.suggestion}"
                            )
                        fallback_text = (
                            f"针对单据【{doc.title if doc else ''}】，系统检出的相关风险发现如下：\n\n"
                            + "\n\n".join(points)
                            + "\n\n如需特批放行，请审批人在审批意见中注明合规依据或经办说明。"
                        )
                    else:
                        fallback_text = (
                            f"经多智能体联合核查，单据【{doc.title if doc else ''}】各项指标合规，未发现明显异常或违规风险。"
                            f"综合评分为 {report.final_score} 分（{report.overall_risk_level.upper()}风险等级）。"
                        )

                    import re
                    # 依据词句片段平滑增量推送
                    tokens = re.findall(r"\S+|\n+", fallback_text)
                    for idx, t in enumerate(tokens):
                        suffix = " " if not t.endswith("\n") else ""
                        chunk = t + suffix
                        full_content_parts.append(chunk)
                        yield f"event: delta\ndata: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
                        await asyncio.sleep(0.015)

            # 4. 发送 citations 事件
            if citations:
                yield f"event: citations\ndata: {json.dumps({'citations': citations}, ensure_ascii=False)}\n\n"

            # 5. 落库持久化完整回复消息 (仅在流完成后一次性提交，禁止每 token 写入)
            final_reply = "".join(full_content_parts)
            ai_msg = AuditChatMessage(
                session_id=session_id,
                role="assistant",
                content=final_reply,
                citations=citations
            )
            self.db.add(ai_msg)
            await self.db.commit()

            # 6. 发送 done 结束事件
            yield f"event: done\ndata: {json.dumps({'session_id': session_id, 'citations': citations}, ensure_ascii=False)}\n\n"

        except Exception as exc:
            logger.exception(f"流式问答异常: {exc}")
            yield f"event: error\ndata: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
