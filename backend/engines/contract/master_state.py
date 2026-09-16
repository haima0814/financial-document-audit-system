"""
backend/engines/contract/master_state.py
LangGraph 顶层 MasterAuditState 极简契约定义
"""
from typing import TypedDict, List, Dict, Any, Annotated, Optional
import operator
from .evidence import EvidenceRecord
from .finding import RiskFindingContract

class MasterAuditState(TypedDict):
    """
    主编排图顶层状态 (极简契约设计)
    绝不包含任何子图内部临时变量！
    """
    # 1. 任务不可变元数据
    task_id: str                              # 任务 UUID
    document_id: int                          # 单据 ID
    document_type: str                        # 单据类型 (CORP_PAYMENT 等)
    current_version: int                      # 单据版本号
    
    # 2. 全局事实账本 (通过 operator.add 实现并行子图安全合流)
    evidence_records: Annotated[List[EvidenceRecord], operator.add]
    risk_findings: Annotated[List[RiskFindingContract], operator.add]
    
    # 3. 门禁与反思决策标记
    needs_retry: bool                         # 质检门禁是否打回
    retry_count: int                          # 重试计数器 (防死循环，上限 2 次)
    retry_feedback: Optional[str]             # 质检不通过时的补充反思指令
    
    # 4. 终审结果落库指引
    final_report_id: Optional[int]            # 报告主表 ID
    overall_risk_level: Optional[str]         # 综合风险等级 (high/medium/low)
    execution_status: str                     # PENDING / PROCESSING / COMPLETED / FAILED
