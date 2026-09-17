/**
 * frontend/src/utils/auditFormatters.js
 * 智能风控前端统一展示格式化器与枚举业务中文映射层
 * 
 * 严格边界：
 * 1. 仅在展示层转换，不修改后端 enum、数据库字段及网络传输协议；
 * 2. 统一兼容 Array、Object、enum、string、null、undefined 等各种异构数据；
 * 3. 未知或无法识别的值统一输出为：未知 (RAW_VALUE)，绝不暴露裸技术代码或数组索引数字。
 */

// 1. 决策来源映射
export const decisionSourceMap = {
  DETERMINISTIC_RULE: '确定性规则',
  LLM_INFERENCE: '大模型推理',
  HEURISTIC_RULE: '启发式规则',
  KNOWLEDGE_BASE: '制度知识库检索',
  RAG: '制度知识库检索',
  SYSTEM: '系统内置',
  UNKNOWN: '系统推导'
}

// 2. 智能体执行状态映射
export const executionStatusMap = {
  SUCCESS: '执行成功',
  RUNNING: '执行中',
  DEGRADED: '降级完成',
  FAILED: '执行失败',
  SKIPPED: '已跳过',
  TIMEOUT: '执行超时',
  PLANNED: '规划中'
}

// 3. 风险等级映射
export const riskLevelMap = {
  HIGH: '高风险',
  MEDIUM: '中风险',
  LOW: '低风险',
  high: '高风险',
  medium: '中风险',
  low: '低风险'
}

// 4. 审核完整度映射
export const auditCompletenessMap = {
  COMPLETE: '完整审核',
  DEGRADED: '降级审核',
  INCOMPLETE: '审核未完备'
}

// 5. 审批状态机决策建议映射
export const approvalDecisionMap = {
  AUTO_APPROVE: '自动免审直通',
  MANUAL_REVIEW: '建议转人工复核',
  NEED_SUPPLEMENT: '要求补证重审',
  REJECT: '建议直接驳回'
}

// 6. 智能体角色中文业务名称映射
export const agentRoleNameMap = {
  // 契约底层枚举字符串 (AgentRoleEnum)
  document_agent: '票据结构化解析智能体',
  amount_agent: '金额确定性精算智能体',
  policy_agent: '制度合规匹配智能体',
  supplier_agent: '供应商工商风控智能体',
  anomaly_agent: '时空与行为反欺诈智能体',
  reviewer_agent: '证据质检与仲裁智能体',
  report_agent: '报告生成与综合评分智能体',
  supervisor: '主控调度中枢智能体',

  // 经典类名映射
  InvoiceOcrAgent: '票据结构化解析智能体',
  DocumentAgent: '票据结构化解析智能体',
  AmountAgent: '金额确定性精算智能体',
  PolicyAgent: '制度合规匹配智能体',
  SupplierAgent: '供应商工商风控智能体',
  BehaviorAgent: '时空与行为反欺诈智能体',
  AnomalyAgent: '时空与行为反欺诈智能体',
  ReviewerReflector: '证据质检与仲裁智能体',
  ReviewerAgent: '证据质检与仲裁智能体',
  ReportAgent: '报告生成与综合评分智能体',
  Supervisor: '主控调度中枢智能体'
}

/**
 * 格式化决策来源
 */
export function formatDecisionSource(source) {
  if (!source) return '系统内置'
  const key = String(source).trim()
  return decisionSourceMap[key] || `未知 (${key})`
}

/**
 * 格式化执行状态
 */
export function formatExecutionStatus(status) {
  if (!status) return '规划中'
  const key = String(status).trim().toUpperCase()
  return executionStatusMap[key] || `未知 (${status})`
}

/**
 * 格式化风险等级
 */
export function formatRiskLevel(level) {
  if (!level) return '低风险'
  const key = String(level).trim()
  return riskLevelMap[key] || riskLevelMap[key.toLowerCase()] || `未知 (${level})`
}

/**
 * 格式化审核完整度
 */
export function formatAuditCompleteness(completeness) {
  if (!completeness) return '完整审核'
  const key = String(completeness).trim().toUpperCase()
  return auditCompletenessMap[key] || `未知 (${completeness})`
}

/**
 * 格式化审批决策建议
 */
export function formatApprovalDecision(decision) {
  if (!decision) return '待审批流决断'
  const key = String(decision).trim().toUpperCase()
  return approvalDecisionMap[key] || `未知 (${decision})`
}

/**
 * 格式化智能体角色业务名称 (禁止暴露 0, 1 或裸英文)
 */
export function formatAgentRole(role) {
  if (role == null || role === '') return '智能审计节点'
  const key = String(role).trim()
  if (agentRoleNameMap[key]) {
    return agentRoleNameMap[key]
  }
  // 避免数字索引如 "0", "1", "2"
  if (/^\d+$/.test(key)) {
    return '智能审计核验节点'
  }
  return `未知角色 (${key})`
}

/**
 * 统一归一化解析并格式化 Agent Execution Plan
 * 同时完美兼容后端以 Array 交付或 Object 交付的场景
 */
export function normalizeAgentExecutions(fullReportPayload) {
  if (!fullReportPayload) return []

  const results = fullReportPayload.agent_execution_results || fullReportPayload.agent_results
  const plan = fullReportPayload.execution_plan || {}
  const plannedAgents = Array.isArray(plan.planned_agents) ? plan.planned_agents : []

  const normalized = []

  // 情况 A: agent_execution_results 是数组形态 (最常见且规范的 DTO 形态)
  if (Array.isArray(results)) {
    for (let i = 0; i < results.length; i++) {
      const item = results[i] || {}
      const rawRole = item.role || item.agent_role || item.name || plannedAgents[i] || `agent_${i}`
      const status = item.status || 'SUCCESS'
      const source = item.source || 'DETERMINISTIC_RULE'
      const durationMs = item.duration_ms ?? item.elapsed_ms

      normalized.push({
        id: `agent_exec_${i}`,
        raw_role: String(rawRole),
        role_cn: formatAgentRole(rawRole),
        status: String(status).toUpperCase(),
        status_cn: formatExecutionStatus(status),
        duration: durationMs != null ? `${durationMs} ms` : '-',
        source: String(source),
        source_cn: formatDecisionSource(source),
        reason: item.reason || item.detail || '核验通过，未触发阻断性异常',
        is_degraded: Boolean(item.is_degraded || status === 'DEGRADED')
      })
    }
    return normalized
  }

  // 情况 B: agent_execution_results 是字典/对象形态 (key -> item)
  if (results && typeof results === 'object') {
    const keys = Object.keys(results)
    // 过滤纯数字索引键以防二次污染
    const roleKeys = keys.filter(k => !/^\d+$/.test(k))
    const targetKeys = roleKeys.length > 0 ? roleKeys : plannedAgents

    for (let i = 0; i < targetKeys.length; i++) {
      const k = targetKeys[i]
      const item = results[k] || {}
      const rawRole = item.role || item.name || k
      const status = item.status || 'SUCCESS'
      const source = item.source || 'DETERMINISTIC_RULE'
      const durationMs = item.duration_ms ?? item.elapsed_ms

      normalized.push({
        id: `agent_exec_${i}`,
        raw_role: String(rawRole),
        role_cn: formatAgentRole(rawRole),
        status: String(status).toUpperCase(),
        status_cn: formatExecutionStatus(status),
        duration: durationMs != null ? `${durationMs} ms` : '-',
        source: String(source),
        source_cn: formatDecisionSource(source),
        reason: item.reason || item.detail || '核验通过，未触发阻断性异常',
        is_degraded: Boolean(item.is_degraded || status === 'DEGRADED')
      })
    }
    return normalized
  }

  // 情况 C: 仅有 planned_agents 规划快照
  if (plannedAgents.length > 0) {
    return plannedAgents.map((role, idx) => ({
      id: `agent_plan_${idx}`,
      raw_role: String(role),
      role_cn: formatAgentRole(role),
      status: 'PLANNED',
      status_cn: '规划中',
      duration: '-',
      source: 'SYSTEM',
      source_cn: '系统内置',
      reason: '已列入规划流水线，等待调度执行',
      is_degraded: false
    }))
  }

  return []
}

/**
 * 标签颜色映射助手
 */
export function getRiskLevelTagType(level) {
  const lvl = String(level).toLowerCase()
  if (lvl === 'high') return 'danger'
  if (lvl === 'medium') return 'warning'
  return 'success'
}

export function getCompletenessTagType(completeness) {
  const c = String(completeness).toUpperCase()
  if (c === 'COMPLETE') return 'success'
  if (c === 'DEGRADED') return 'warning'
  return 'danger'
}

export function getDecisionTagType(action) {
  const a = String(action).toUpperCase()
  if (a === 'AUTO_APPROVE') return 'success'
  if (a === 'MANUAL_REVIEW' || a === 'NEED_SUPPLEMENT') return 'warning'
  if (a === 'REJECT') return 'danger'
  return 'info'
}

export function getAgentStatusTagType(status) {
  const s = String(status).toUpperCase()
  if (s === 'SUCCESS') return 'success'
  if (s === 'DEGRADED') return 'warning'
  if (s === 'FAILED' || s === 'TIMEOUT') return 'danger'
  return 'info'
}
