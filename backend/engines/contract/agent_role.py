"""
backend/engines/contract/agent_role.py
定义多智能体系统的角色枚举与元数据
"""
from enum import Enum
from dataclasses import dataclass
from typing import Dict

class AgentRoleEnum(str, Enum):
    """智能体角色枚举 (符合 PRD 与概要设计规范)"""
    SUPERVISOR = "supervisor"          # 主控中枢：意图识别与动态 DAG 编排
    DOCUMENT = "document_agent"        # 票据专家：OCR、版面分析与归一化 BBox 提取
    AMOUNT = "amount_agent"            # 精算专家：确定性 Decimal 核算与五方交叉比对
    POLICY = "policy_agent"            # 制度合规：财务知识库 RAG 检索与超标/合理性判定
    SUPPLIER = "supplier_agent"        # 工商风控：失信穿透、空壳企业与银行账户变更核验
    ANOMALY = "anomaly_agent"          # 反欺诈专家：行程时空冲突、发票全局查重
    REVIEWER = "reviewer_agent"        # 终审质检：证据链门禁质检、反思纠偏与仲裁
    REPORT = "report_agent"            # 报告专家：综合风险打分、报告结构化渲染与摘要草拟

@dataclass(frozen=True, slots=True)
class AgentRoleMeta:
    """角色元数据规约"""
    role: AgentRoleEnum
    name_cn: str                       # 中文名称 (用于前端展示与日志)
    description: str                   # 职责描述
    timeout_seconds: float             # 硬超时时间 (秒)
    max_retries: int                   # 失败重试上限
    stage_order: int                   # 默认编排阶段顺序 (1-前置解析, 2-并行核算, 3-终审总结)

AGENT_ROLE_REGISTRY: Dict[AgentRoleEnum, AgentRoleMeta] = {
    AgentRoleEnum.SUPERVISOR: AgentRoleMeta(
        role=AgentRoleEnum.SUPERVISOR,
        name_cn="主控调度中枢",
        description="解析单据意图，拆解动态审查 DAG 计划",
        timeout_seconds=10.0,
        max_retries=2,
        stage_order=0
    ),
    AgentRoleEnum.DOCUMENT: AgentRoleMeta(
        role=AgentRoleEnum.DOCUMENT,
        name_cn="票据结构化解析智能体",
        description="多模态 OCR、版面分块与坐标提取",
        timeout_seconds=60.0,          # OCR 属于重型计算，宽限超时
        max_retries=1,
        stage_order=1
    ),
    AgentRoleEnum.AMOUNT: AgentRoleMeta(
        role=AgentRoleEnum.AMOUNT,
        name_cn="金额确定性精算智能体",
        description="Python Decimal 纯代码核算与五方交叉对账",
        timeout_seconds=15.0,
        max_retries=2,
        stage_order=2
    ),
    AgentRoleEnum.POLICY: AgentRoleMeta(
        role=AgentRoleEnum.POLICY,
        name_cn="制度合规匹配智能体",
        description="企业财务制度 RAG 语义检索与合规裁定",
        timeout_seconds=30.0,
        max_retries=2,
        stage_order=2
    ),
    AgentRoleEnum.SUPPLIER: AgentRoleMeta(
        role=AgentRoleEnum.SUPPLIER,
        name_cn="供应商工商风控智能体",
        description="工商信用穿透、空壳与账户突变排查",
        timeout_seconds=20.0,
        max_retries=2,
        stage_order=2
    ),
    AgentRoleEnum.ANOMALY: AgentRoleMeta(
        role=AgentRoleEnum.ANOMALY,
        name_cn="时空与行为反欺诈智能体",
        description="跨单据时空轨迹冲突与发票哈希防重碰撞",
        timeout_seconds=20.0,
        max_retries=2,
        stage_order=2
    ),
    AgentRoleEnum.REVIEWER: AgentRoleMeta(
        role=AgentRoleEnum.REVIEWER,
        name_cn="证据质检与仲裁智能体",
        description="证据完整性门禁过滤、冲突仲裁与反思",
        timeout_seconds=25.0,
        max_retries=1,
        stage_order=3
    ),
    AgentRoleEnum.REPORT: AgentRoleMeta(
        role=AgentRoleEnum.REPORT,
        name_cn="报告生成与综合评分智能体",
        description="风险加权打分、多维证据报告渲染与摘要生成",
        timeout_seconds=30.0,
        max_retries=2,
        stage_order=4
    ),
}
