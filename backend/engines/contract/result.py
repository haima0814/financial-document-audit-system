"""
backend/engines/contract/result.py
多 Agent 审查终审输出结果契约 (AuditResultDTO) 与单个智能体执行结果 (AgentExecutionResult)
作为 Agent 推理引擎的纯数据交付出参，严禁引擎直接操作数据库
"""
from enum import Enum
from decimal import Decimal
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field, ConfigDict

from .agent_role import AgentRoleEnum
from .finding import RiskFindingContract

class AgentExecutionStatus(str, Enum):
    """智能体执行状态"""
    PLANNED = "PLANNED"       # 规划初态（已规划待执行）
    SUCCESS = "SUCCESS"       # 正常执行完毕
    SKIPPED = "SKIPPED"       # 依据规则或要素缺失合理跳过
    TIMEOUT = "TIMEOUT"       # 执行超时被 Harness 熔断拦截
    FAILED = "FAILED"         # 抛出未捕获异常
    DEGRADED = "DEGRADED"     # 降级运行（如部分要素缺失或降级处理）

class AgentExecutionResult(BaseModel):
    """单个 Agent 执行结果强类型记录 (状态与风险发现解耦)"""
    model_config = ConfigDict(frozen=True)

    role: AgentRoleEnum = Field(..., description="智能体角色标识")
    status: AgentExecutionStatus = Field(..., description="执行状态: SUCCESS / SKIPPED / TIMEOUT / FAILED / DEGRADED")
    findings: List[RiskFindingContract] = Field(default_factory=list, description="检出的风险发现项列表")
    reason: Optional[str] = Field(default=None, description="状态详细说明，如跳过原因或超时/异常摘要")
    duration_ms: int = Field(default=0, description="执行耗时 (毫秒)")
    capabilities_run: List[str] = Field(default_factory=list, description="实际启用的能力项")
    source: Optional[str] = Field(default=None, description="执行数据源/模式: LLM_INFERENCE / HEURISTIC_RULE / DETERMINISTIC_RULE")
    is_degraded: bool = Field(default=False, description="是否为降级审核 (如 LLM 不可用退化为启发式规则)")

class AuditResultDTO(BaseModel):
    """
    智能风控流水线标准化出参对象 (不可变领域结果传输对象)
    由多 Agent 编排流水线最终产出，直接交付给 AuditService 进行数据库事务落库与审批流流转。
    """
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(..., description="所属分析任务 task_id")
    document_id: int = Field(..., description="业务单据 ID")
    report_id: Optional[int] = Field(default=None, description="落库生成的审查报告 ID (落库前为 None)")
    overall_risk_level: str = Field(..., description="综合风险等级: high / medium / low")
    final_score: int = Field(..., ge=0, le=100, description="综合风控评级分 (0-100)")
    risk_score: int = Field(default=100, ge=0, le=100, description="业务风险评分 (0-100，只反映已发现的业务风险)")
    
    high_risks_count: int = Field(default=0, description="高危风险数")
    medium_risks_count: int = Field(default=0, description="中危风险数")
    low_risks_count: int = Field(default=0, description="低危提示数")
    
    # 审核完整度: COMPLETE(完整) / DEGRADED(有非核心要素降级) / INCOMPLETE(关键核心项超时或失败)
    audit_completeness: str = Field(default="COMPLETE", description="审核完整度: COMPLETE | DEGRADED | INCOMPLETE")
    
    # 高管体检摘要 (CFO 水准研判)
    summary: str = Field(..., description="单据风控体检高管摘要与定性结论")
    
    # 门禁与反思消歧质检后的最终合规风险项列表
    verified_findings: List[RiskFindingContract] = Field(default_factory=list, description="经过 Stage 3 仲裁消歧的风险发现项")
    
    # 二阶反思消歧决策日志
    disambiguation_logs: List[Dict[str, Any]] = Field(default_factory=list, description="二阶反思消歧决策轨迹记录")
    
    # 各智能体执行状态明细 (解耦 Findings 与 Execution Status)
    agent_execution_results: List[AgentExecutionResult] = Field(default_factory=list, description="各智能体细粒度执行状态明细")
    
    # 审核规划快照
    execution_plan: Optional[Dict[str, Any]] = Field(default=None, description="审核计划方案快照")
    
    # 全量报告元数据负载 (供前端展示与归档)
    full_report_payload: Dict[str, Any] = Field(default_factory=dict, description="完整审查报告结构化快照")
    
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="审核完成时间 (UTC)")
    execution_elapsed_ms: int = Field(default=0, description="审查流水线端到端执行耗时(毫秒)")
