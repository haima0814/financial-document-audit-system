"""
backend/engines/contract/finding.py
智能体风险发现项统一输出契约 (Risk Finding Contract)
"""
from enum import Enum
from typing import Optional, List, Any, Dict
from decimal import Decimal
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field, ConfigDict

from .evidence import EvidenceRecord, VisualAnchor
from .agent_role import AgentRoleEnum

class RiskLevelEnum(str, Enum):
    """风险严重等级"""
    HIGH = "high"          # 高危风险：一票否决/涉嫌欺诈/税号假冒/金额严重不符
    MEDIUM = "medium"      # 中危风险：超标报销/缺少附件/成立时间不足1年
    LOW = "low"            # 低危提示：信息轻微不全/事由模糊建议补充

class RiskFindingContract(BaseModel):
    """
    跨 Agent 与主图流转的标准风险契约对象
    采用“引用与主图账本分离”模式：主图只存放轻量 evidence_ids 索引，避免全量嵌套导致深层反序列化与内存开销
    """
    model_config = ConfigDict(frozen=True)

    finding_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="风险全局指纹 ID")
    rule_code: str = Field(..., description="风控规则编码 (如 R01_AMOUNT_MISMATCH / R05_POLICY_EXCEEDED)")
    rule_name: str = Field(..., description="风控规则名称")
    risk_level: RiskLevelEnum = Field(..., description="风险等级")
    agent_role: AgentRoleEnum = Field(..., description="检出该风险的智能体角色")
    
    title: str = Field(..., max_length=128, description="风险短标题")
    description: str = Field(..., description="详细风险阐述与上下文事实")
    
    # 申报事实与标准基准快照
    actual_value: Dict[str, Any] = Field(default_factory=dict, description="申报事实快照 (如 {'claimed_amount': 700.00})")
    expected_value: Dict[str, Any] = Field(default_factory=dict, description="标准基准快照 (如 {'policy_limit': 400.00})")
    discrepancy_amount: Optional[Decimal] = Field(default=None, description="差异/超标金额数值")
    
    # 轻量化引用关联 (方案 B：引用与主图账本分离)
    evidence_ids: List[str] = Field(default_factory=list, description="关联的不可变证据指纹 ID 列表 (指向主图账本)")
    primary_visual_anchor: Optional[VisualAnchor] = Field(default=None, description="首选视觉原图高亮锚点 (便于前端秒级框选，无需展开完整链条)")
    
    # 完整证据链 (仅在报告落库持久化阶段按需回填组装)
    evidence_chain: List[EvidenceRecord] = Field(default_factory=list, description="持久化时的完整证据链条快照")
    
    # 审批人建议与处置约束
    suggestion: str = Field(..., description="给财务审批人的具名处置建议")
    is_overridable: bool = Field(default=True, description="人工审批人是否允许具名签字强制放行")
    produced_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="检出时间 (UTC)")


class AgentFindingList(list):
    """
    智能体产出的风险发现项列表包装器 (继承原生 list，100% 兼容现有列表接口)
    附带降级执行与来源元数据，供 AgentHarness 与主图裁决完整度 (DEGRADED)
    """
    def __init__(
        self,
        items=None,
        is_degraded: bool = False,
        degraded_reason: Optional[str] = None,
        source: Optional[str] = None
    ):
        super().__init__(items or [])
        self.is_degraded = is_degraded
        self.degraded_reason = degraded_reason
        self.source = source

