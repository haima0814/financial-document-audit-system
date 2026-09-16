"""
backend/app/services/audit_service.py
风控体检报告查询、证据链提取与智能 AI 问答服务
"""
import uuid
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import selectinload

from app.models.audit import ReviewReport, RiskFinding, AuditChatSession, AuditChatMessage
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

    async def start_audit(
        self,
        document_id: int,
        applicant_id: Optional[int] = None,
        tenant_id: int = 1,
        audit_version: int = 1
    ) -> str:
        """
        审核启动统一入口 (业务生命周期高内聚闭环):
        1. 由 AuditContextBuilder 统一从数据库装配不可变的 AuditExecutionContext 内存快照;
        2. 物理阻断 Agent 运行时直连 DB 的后门 (Agent 全程 0 SQL / 0 ORM);
        3. 记录 AnalysisTask 状态;
        4. 异步派发至 TaskDispatcher (Local / Celery);
        5. 返回审查唯一 task_id。
        """
        import uuid
        from app.services.audit_context_builder import AuditContextBuilder
        from app.models.audit import AnalysisTask
        from engines.orchestrator.master_graph import MasterOrchestrator
        from engines.orchestrator.dispatcher import LocalTaskManagerDispatcher

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        analysis_task = AnalysisTask(
            task_id=task_id,
            document_id=document_id,
            status="PENDING",
            current_stage="STAGE_1_PARSED",
            progress_pct=10,
        )
        self.db.add(analysis_task)

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
                logging.getLogger("audit_service").error(f"审核流水线异步执行失败: {e}", exc_info=True)

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

        # 2. 统一由 audit_repo 原子落库
        report = await audit_repo.save_audit_result(self.db, result)

        # 2. 移交审批状态机（状态转移由 ApprovalEngine 拥有全权：小额低危免审直通 / 待人工审批流转）

        # 3. 触发审批引擎智能流转
        try:
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
        except Exception as e:
            import logging
            logging.getLogger("audit_service").warning(f"触发审批流失败或已被处理: {e}")

        # 4. 提交数据库事务
        await self.db.commit()
        await self.db.refresh(report)

        # 5. 发布 TASK_COMPLETED 领域事件 (解耦通知 WebSocket / SSE / MQ)
        await StreamProducer.publish_event(
            task_id=task_id,
            document_id=document_id,
            event_type=EventTypeEnum.TASK_COMPLETED,
            payload={
                "report_id": report.id,
                "overall_risk_level": result.overall_risk_level,
                "risk_score": result.final_score,
                "high_count": result.high_risks_count,
                "medium_count": result.medium_risks_count,
                "low_count": result.low_risks_count
            }
        )

        return report

    # 兼容别名
    handle_audit_completion = handle_audit_completed

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

    async def get_findings(self, report_id: int) -> List[RiskFinding]:
        """获取报告下的所有风险判定项"""
        stmt = select(RiskFinding).where(RiskFinding.report_id == report_id).order_by(RiskFinding.id)
        res = await self.db.execute(stmt)
        return list(res.scalars().all())

    async def chat_with_audit_context(
        self,
        user_id: int,
        req: AuditChatReq
    ) -> AuditChatResp:
        """
        基于当前单据的风控体检报告和证据链，进行智能问答
        """
        # 1. 查找或创建 Session
        session_id = req.session_id
        if not session_id:
            session_id = f"sess_{uuid.uuid4().hex[:12]}"
            chat_session = AuditChatSession(
                session_id=session_id,
                document_id=req.document_id,
                user_id=user_id
            )
            self.db.add(chat_session)
            await self.db.flush()

        # 2. 记录用户提问消息
        user_msg = AuditChatMessage(
            session_id=session_id,
            role="user",
            content=req.message,
            citations=[]
        )
        self.db.add(user_msg)

        # 3. 加载单据与风控报告上下文
        doc = await self.db.get(FinancialDocument, req.document_id)
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
