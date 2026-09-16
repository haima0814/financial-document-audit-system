"""
backend/engines/orchestrator/state.py
编排中枢状态模型
"""
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from engines.contract.finding import RiskFindingContract
from engines.contract.evidence import EvidenceRecord
from engines.contract.result import AgentExecutionResult
from .planner import AuditExecutionPlan

class MasterAuditState(BaseModel):
    """主图全局运行状态"""
    task_id: str
    document_id: int
    document_type: str = "TRAVEL_REIMBURSEMENT"
    tenant_id: int = 1
    applicant_id: int = 1

    current_stage: str = "INIT"
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    document_facts: Dict[str, Any] = Field(default_factory=dict)
    execution_plan: Optional[AuditExecutionPlan] = None
    agent_results: List[AgentExecutionResult] = Field(default_factory=list)
    findings: List[RiskFindingContract] = Field(default_factory=list)
    evidence_pool: List[EvidenceRecord] = Field(default_factory=list)

    verified_findings: List[RiskFindingContract] = Field(default_factory=list)
    disambiguation_logs: List[Dict[str, Any]] = Field(default_factory=list)
    audit_completeness: str = "COMPLETE"
    overall_risk_level: str = "low"
    final_score: int = 100
    risk_score: int = 100
    summary: str = ""
    report_id: Optional[int] = None
    error_message: Optional[str] = None

    def to_audit_result(self) -> "AuditResultDTO":
        """将内部运行态转换为不可变标准出参 DTO"""
        from engines.contract.result import AuditResultDTO
        high_cnt = sum(1 for f in self.verified_findings if f.risk_level.value == "high")
        med_cnt = sum(1 for f in self.verified_findings if f.risk_level.value == "medium")
        low_cnt = sum(1 for f in self.verified_findings if f.risk_level.value == "low")
        comp_at = self.completed_at or datetime.now(timezone.utc)
        elapsed_ms = max(0, int((comp_at - self.started_at).total_seconds() * 1000))

        return AuditResultDTO(
            task_id=self.task_id,
            document_id=self.document_id,
            report_id=self.report_id,
            overall_risk_level=self.overall_risk_level,
            final_score=self.final_score,
            risk_score=self.risk_score,
            high_risks_count=high_cnt,
            medium_risks_count=med_cnt,
            low_risks_count=low_cnt,
            audit_completeness=self.audit_completeness,
            summary=self.summary or f"智能风控审查完毕：检出高危风险 {high_cnt} 项，中危 {med_cnt} 项，低危提示 {low_cnt} 项，综合风控评分为 {self.final_score} 分。",
            verified_findings=self.verified_findings,
            disambiguation_logs=self.disambiguation_logs,
            agent_execution_results=self.agent_results,
            execution_plan=self.execution_plan.model_dump(mode="json") if self.execution_plan else None,
            full_report_payload={"findings": [f.model_dump(mode="json") for f in self.verified_findings]},
            completed_at=comp_at,
            execution_elapsed_ms=elapsed_ms
        )
