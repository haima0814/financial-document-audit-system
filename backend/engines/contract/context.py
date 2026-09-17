"""
backend/engines/contract/context.py
不可变单据审查执行上下文快照 (AuditExecutionContext / DocumentContext)
作为 Agent 推理引擎的纯数据入参契约，严禁携带数据库会话与可变状态
"""
from decimal import Decimal
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field, ConfigDict

class AuditExecutionContext(BaseModel):
    """
    多智能体审查执行不可变上下文快照 (AuditExecutionContext)
    由 Application Service (AuditContextBuilder) 在派发任务前统一装配封包，
    包含单据、发票、明细、申请人历史行为画像、制度定额与预算限额等全量只读事实，
    供多 Agent 认知推理流水线执行纯内存核算，保证引擎与底层数据库物理隔离。
    """
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(default="", description="分析任务 UUID")
    document_id: int = Field(..., description="业务单据 ID")
    document_no: str = Field(..., description="业务单据编码 (如 TRV-2026-001)")
    document_type: str = Field(..., description="单据类型 (如 TRAVEL_REIMBURSEMENT / CORP_PAYMENT)")
    title: str = Field(..., description="单据申报事由与标题")
    total_amount: Decimal = Field(..., description="申报总金额 (高精度 Decimal)")
    department_name: Optional[str] = Field(default=None, description="申报部门名称")
    applicant_id: int = Field(default=1, description="经办人用户 ID")
    tenant_id: int = Field(default=1, description="所属企业租户 ID")
    audit_version: int = Field(default=1, description="审查快照版本号")
    
    # 结构化行项明细列表
    line_items: List[Dict[str, Any]] = Field(default_factory=list, description="单据费用/采购明细列表")
    
    # 关联上传的发票记录快照
    invoices: List[Dict[str, Any]] = Field(default_factory=list, description="票据结构化信息与税额列表")
    
    # 时空轨迹与行为上下文点 (差旅轨迹)
    spatio_points: List[Dict[str, Any]] = Field(default_factory=list, description="行程轨迹与时空事件点列表")
    
    # 交通票据合法移动行程段
    travel_segments: List[Dict[str, Any]] = Field(default_factory=list, description="交通票据行程移动段")
    
    # 经办人画像与历史行为特征 (供 AnomalyAgent 查重、频率与异常突变推演)
    applicant_profile: Dict[str, Any] = Field(default_factory=dict, description="经办人岗位、职级、历史核销信誉与月度频次")

    # 预加载的有效制度条款与标准定额 (供 PolicyAgent 匹配，0 外部 SQL 查询)
    rules: List[Dict[str, Any]] = Field(default_factory=list, description="激活生效的合规规则与差旅定额条目")

    # 预审批流上下文与部门预算限额 (供审批与风控双轨决策)
    approval_context: Dict[str, Any] = Field(default_factory=dict, description="部门剩余预算额度、申请人免审阈值配置")

    # 扩展上下文字典 (如合同预算、关联供应商工商快照)
    extra_context: Dict[str, Any] = Field(default_factory=dict, description="只读扩展事实字典")
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="快照封包时间 (UTC)")

# 保持对现有系统 DocumentContext 命名的 100% 向后兼容
DocumentContext = AuditExecutionContext
