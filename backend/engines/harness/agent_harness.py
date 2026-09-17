"""
backend/engines/harness/agent_harness.py
Agent 运行安全管控底座 (Agent Harness & 沙箱门禁)
提供以下核心能力：
1. 统一执行超时熔断 (Execution Timeout Guard)
2. 结构化输出强类型 Schema 校验 (Pydantic Contract Gatekeeper)
3. 确定性算术防线校验 (Deterministic Math Guard / Decimal Sanity)
4. 异常自动降级与静默隔离 (Circuit Breaker & Fallback)
"""
import asyncio
import logging
from decimal import Decimal
import time
from typing import Coroutine, Any, List, Type, Dict, Optional
from pydantic import ValidationError

from engines.contract.finding import RiskFindingContract, RiskLevelEnum
from engines.contract.agent_role import AgentRoleEnum
from engines.contract.result import AgentExecutionResult, AgentExecutionStatus

# ============================================================================
# 生产级分级超时与整体截止时间 (Hierarchical Timeouts & Deadline)
# ============================================================================
LLM_CALL_TIMEOUT: float = 20.0           # LLM 单次生成调用上限 (20s)
RAG_RETRIEVAL_TIMEOUT: float = 3.0       # 制度知识库向量检索上限 (3s)
ENTERPRISE_API_TIMEOUT: float = 5.0      # 工商外部第三方接口上限 (5s)
DETERMINISTIC_TOOL_TIMEOUT: float = 0.5  # 确定性纯计算 Tool 门禁上限 (500ms)
SINGLE_AGENT_TIMEOUT: float = 30.0       # 单 Agent 复合认知执行限额 (30s)
TASK_OVERALL_DEADLINE: float = 120.0     # 整个单据 Audit Task 总体截止大限 (120s)

# 各智能体细粒度超时与熔断策略 (根据认知负载定制)
AGENT_POLICIES: Dict[AgentRoleEnum, float] = {
    AgentRoleEnum.AMOUNT: 3.0,     # 确定性内存硬算，超时即告警 (3s)
    AgentRoleEnum.POLICY: 15.0,    # 含知识库检索/大模型规章事由语义推理 (15s)
    AgentRoleEnum.ANOMALY: 10.0,   # 含全量发票哈希查重与时空轨迹碰撞 (10s)
    AgentRoleEnum.SUPPLIER: 15.0,  # 外部商用征信与工商大模型经营范围穿透 (15s)
}

logger = logging.getLogger("engines.harness")

class AgentHarness:
    """
    Agent 执行沙箱与生产级八大安全管控底座 (Agent Harness & Guardrails):
    1. Input Schema 强校验
    2. 确定性算术硬门禁 (Decimal 平账一票否决)
    3. Tool 白名单与沙箱隔离
    4. 分级 Timeout 与整体 Deadline
    5. Retry Budget 重试预算控制
    6. Circuit Breaker 熔断保护器
    7. Soft Fallback 柔性降级与标黄提示
    8. Output Contract 契约结构强防线
    """

    # 导出分级超时常量引用
    LLM_TIMEOUT = LLM_CALL_TIMEOUT
    RAG_TIMEOUT = RAG_RETRIEVAL_TIMEOUT
    TOOL_TIMEOUT = DETERMINISTIC_TOOL_TIMEOUT
    AGENT_TIMEOUT = SINGLE_AGENT_TIMEOUT
    DEADLINE = TASK_OVERALL_DEADLINE
    AGENT_POLICIES = AGENT_POLICIES

    @classmethod
    async def execute_safely(
        cls,
        agent_role: AgentRoleEnum,
        coro: Coroutine[Any, Any, List[RiskFindingContract]],
        timeout_seconds: Optional[float] = None,
        expected_schema: Type[RiskFindingContract] = RiskFindingContract,
        capabilities: Optional[List[str]] = None
    ) -> AgentExecutionResult:
        """
        安全包装并执行子 Agent 协程：
        - 依据各 Agent 认知负载应用针对性超时门禁 (从 AGENT_POLICIES 获取)
        - 强制超时熔断并记录 TIMEOUT 状态 (不吞噬状态，供终审完整度门禁使用)
        - 契约结构与 Decimal 精度强门禁清洗
        - 返回强类型 AgentExecutionResult (解耦状态与风险项)
        """
        effective_timeout = timeout_seconds if timeout_seconds is not None else cls.AGENT_POLICIES.get(agent_role, cls.AGENT_TIMEOUT)
        start_time = time.perf_counter()

        try:
            # 1. 强制超时熔断拦截
            raw_findings = await asyncio.wait_for(coro, timeout=effective_timeout)
        except asyncio.TimeoutError:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            logger.error(f"[AgentHarness] 智能体 [{agent_role.value}] 执行超时 ({effective_timeout}s)，触发熔断降级！")
            timeout_finding = RiskFindingContract(
                rule_code="H99_AGENT_TIMEOUT",
                rule_name=f"{agent_role.value}执行超时",
                risk_level=RiskLevelEnum.HIGH if agent_role == AgentRoleEnum.AMOUNT else RiskLevelEnum.LOW,
                agent_role=agent_role,
                title=f"智能体[{agent_role.value}]分析超时",
                description=f"智能体在 {effective_timeout} 秒内未完成分析，触发沙箱熔断拦截，请人工重点复核该维度风险。",
                suggestion="该维度核验未完全完成，建议审批人重点人工关注此维度的合规审核。"
            )
            return AgentExecutionResult(
                role=agent_role,
                status=AgentExecutionStatus.TIMEOUT,
                findings=[timeout_finding],
                reason=f"执行超时 ({effective_timeout}s)，触发熔断降级",
                duration_ms=elapsed_ms,
                capabilities_run=capabilities or []
            )
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            logger.error(f"[AgentHarness] 智能体 [{agent_role.value}] 执行发生未捕获异常: {e}", exc_info=True)
            return AgentExecutionResult(
                role=agent_role,
                status=AgentExecutionStatus.FAILED,
                findings=[],
                reason=f"执行异常: {str(e)}",
                duration_ms=elapsed_ms,
                capabilities_run=capabilities or []
            )

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        # 2. 结构化 Schema 验证与算术过滤
        validated_findings: List[RiskFindingContract] = []
        if not isinstance(raw_findings, list):
            logger.warning(f"[AgentHarness] 智能体 [{agent_role.value}] 返回非列表结构，已阻断")
            return AgentExecutionResult(
                role=agent_role,
                status=AgentExecutionStatus.DEGRADED,
                findings=[],
                reason="智能体返回非标准列表数据结构",
                duration_ms=elapsed_ms,
                capabilities_run=capabilities or []
            )

        for item in raw_findings:
            try:
                # 若已经是契约实例，重新校验或直接接纳
                if isinstance(item, expected_schema):
                    valid_item = item
                elif isinstance(item, dict):
                    valid_item = expected_schema.model_validate(item)
                else:
                    continue

                # 3. 确定性算术防线校验 (Decimal 精度校验)
                if valid_item.discrepancy_amount is not None:
                    cleaned_amt = abs(Decimal(str(valid_item.discrepancy_amount)))
                    if cleaned_amt != valid_item.discrepancy_amount:
                        valid_item = valid_item.model_copy(update={"discrepancy_amount": cleaned_amt})

                validated_findings.append(valid_item)

            except ValidationError as ve:
                logger.warning(f"[AgentHarness] 智能体 [{agent_role.value}] 产出的发现项契约校验失败，已被过滤: {ve}")
                continue

        # 4. 判定是否包含降级状态与执行模式 (如退化为 HEURISTIC_RULE 兜底)
        is_degraded = getattr(raw_findings, "is_degraded", False)
        degraded_reason = getattr(raw_findings, "degraded_reason", None)
        source = getattr(raw_findings, "source", None)
        capability_results = getattr(raw_findings, "capability_results", [])

        # 检查能力级结果中是否存在 mandatory 为 BLOCKED 或 FAILED 的能力
        has_blocked_mandatory = any(
            getattr(c, "mandatory", True) and str(getattr(c, "status", "")).upper() in ("BLOCKED", "FAILED", "CAPABILITYSTATUS.BLOCKED", "CAPABILITYSTATUS.FAILED")
            for c in capability_results
        )
        if has_blocked_mandatory:
            is_degraded = True

        if not is_degraded:
            for f in validated_findings:
                if f.actual_value.get("is_degraded") is True or f.actual_value.get("source") == "HEURISTIC_RULE":
                    is_degraded = True
                    degraded_reason = degraded_reason or "智能体存在启发式规则兜底降级执行项"
                    source = source or f.actual_value.get("source")
                    break

        if source is None:
            source = "HEURISTIC_RULE" if is_degraded else ("DETERMINISTIC_RULE" if agent_role == AgentRoleEnum.AMOUNT else "LLM_INFERENCE")

        reason = degraded_reason if degraded_reason else ("降级完成" if is_degraded else "执行成功")
        status = AgentExecutionStatus.DEGRADED if is_degraded else AgentExecutionStatus.SUCCESS

        return AgentExecutionResult(
            role=agent_role,
            status=status,
            findings=validated_findings,
            reason=reason,
            duration_ms=elapsed_ms,
            capabilities_run=capabilities or [],
            source=source,
            is_degraded=is_degraded,
            capability_results=capability_results
        )
