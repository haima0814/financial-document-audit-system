"""
backend/app/repositories/audit_repo.py
多 Agent 分析任务、体检报告与风险项仓储
"""
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.audit import AnalysisTask, ReviewReport, RiskFinding
from .base import BaseRepository

class AuditRepository(BaseRepository[ReviewReport]):
    def __init__(self):
        super().__init__(ReviewReport)

    async def get_report_by_document(self, db: AsyncSession, document_id: int) -> Optional[ReviewReport]:
        """获取指定单据最新的体检报告与全部风险项"""
        stmt = (
            select(ReviewReport)
            .where(ReviewReport.document_id == document_id)
            .options(selectinload(ReviewReport.findings))
            .order_by(ReviewReport.created_at.desc())
        )
        res = await db.execute(stmt)
        return res.scalars().first()

    async def get_task_by_id(self, db: AsyncSession, task_id: str) -> Optional[AnalysisTask]:
        stmt = select(AnalysisTask).where(AnalysisTask.task_id == task_id)
        res = await db.execute(stmt)
        return res.scalars().first()

    async def get_latest_task_by_document(
        self,
        db: AsyncSession,
        document_id: int,
        audit_version: int
    ) -> Optional[AnalysisTask]:
        """严格按 document_id 与当前 audit_version 获取审查任务"""
        stmt = (
            select(AnalysisTask)
            .where(
                AnalysisTask.document_id == document_id,
                AnalysisTask.audit_version == audit_version
            )
            .order_by(AnalysisTask.id.desc())
        )
        res = await db.execute(stmt)
        return res.scalars().first()

    async def save_audit_result(
        self,
        db: AsyncSession,
        result: "AuditResultDTO"
    ) -> ReviewReport:
        """
        原子化落库智能体分析结果：
        1. 写入 review_reports 表；
        2. 批量写入 risk_findings 表；
        3. 更新/新增 analysis_tasks 记录状态为 COMPLETED；
        """
        # 1. 写入或更新 review_reports 表 (幂等防线)
        report_stmt = select(ReviewReport).where(ReviewReport.task_id == result.task_id)
        report = (await db.execute(report_stmt)).scalars().first()
        if not report:
            report = ReviewReport(
                task_id=result.task_id,
                document_id=result.document_id,
                overall_risk_level=result.overall_risk_level,
                final_score=result.final_score,
                high_risks_count=result.high_risks_count,
                medium_risks_count=result.medium_risks_count,
                low_risks_count=result.low_risks_count,
                summary=result.summary,
                full_report_payload=result.full_report_payload
            )
            db.add(report)
            await db.flush()
        else:
            report.overall_risk_level = result.overall_risk_level
            report.final_score = result.final_score
            report.high_risks_count = result.high_risks_count
            report.medium_risks_count = result.medium_risks_count
            report.low_risks_count = result.low_risks_count
            report.summary = result.summary
            report.full_report_payload = result.full_report_payload
            await db.flush()
            from sqlalchemy import delete
            await db.execute(delete(RiskFinding).where(RiskFinding.report_id == report.id))

        # 2. 写入 risk_findings 详情表
        for f in result.verified_findings:
            finding_record = RiskFinding(
                report_id=report.id,
                finding_id=f.finding_id,
                rule_code=f.rule_code,
                rule_name=f.rule_name,
                risk_level=f.risk_level.value if hasattr(f.risk_level, "value") else str(f.risk_level),
                agent_role=f.agent_role.value if hasattr(f.agent_role, "value") else str(f.agent_role),
                title=f.title,
                description=f.description,
                actual_value=f.actual_value,
                expected_value=f.expected_value,
                discrepancy_amount=f.discrepancy_amount,
                evidence_ids=f.evidence_ids,
                primary_visual_anchor=f.primary_visual_anchor.model_dump(mode="json") if f.primary_visual_anchor else {},
                evidence_chain=[e.model_dump(mode="json") for e in f.evidence_chain] if f.evidence_chain else [],
                suggestion=f.suggestion,
                is_overridable=f.is_overridable
            )
            db.add(finding_record)

        # 3. 更新或创建 analysis_tasks 记录
        task_stmt = select(AnalysisTask).where(AnalysisTask.task_id == result.task_id)
        task_record = (await db.execute(task_stmt)).scalars().first()
        if not task_record:
            task_record = AnalysisTask(
                task_id=result.task_id,
                document_id=result.document_id,
                status="COMPLETED",
                current_stage="COMPLETED",
                progress_pct=100,
                completed_at=result.completed_at
            )
            db.add(task_record)
        else:
            task_record.status = "COMPLETED"
            task_record.current_stage = "COMPLETED"
            task_record.progress_pct = 100
            task_record.completed_at = result.completed_at

        await db.flush()
        return report

audit_repo = AuditRepository()
