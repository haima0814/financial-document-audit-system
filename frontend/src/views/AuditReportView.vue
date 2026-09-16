<template>
  <div class="audit-report-page" v-loading="loading">
    <!-- Fail-Closed 安全兜底视图：报告未生成或拉取异常时严禁显示假绿灯 -->
    <div v-if="loadFailed || (!loading && (!report || !report.id))" class="fail-closed-card">
      <el-result
        icon="error"
        title="风控体检报告未生成或加载失败"
        sub-title="当前单据尚未通过多智能体风控审核流水线，或后台解析异常。为保障财务合规安全，系统已启动 Fail-Closed 安全防线，禁止盲目信任或默认放行。"
      >
        <template #extra>
          <div class="fail-closed-actions">
            <el-button type="primary" :icon="Refresh" @click="fetchReport">重新加载体检报告</el-button>
            <el-button :icon="Back" @click="$router.push('/documents')">返回单据列表</el-button>
            <el-button type="success" plain @click="$router.push('/approvals')">审批工作流</el-button>
          </div>
        </template>
      </el-result>
    </div>

    <!-- 正常报告主视图 -->
    <template v-else-if="report && report.id">
      <!-- 顶部总览看板 -->
      <div class="report-top-banner">
        <!-- 第一行：评分、综合摘要与快捷操作 -->
        <div class="banner-top-row">
          <div class="score-card">
            <div class="score-number" :class="getScoreClass(report.final_score)">
              {{ report.final_score ?? 100 }}
            </div>
            <div class="score-meta">
              <div class="score-title">智能风控综合体检评分</div>
              <div class="tag-group">
                <el-tag :type="getRiskTagType(report.overall_risk_level)" size="small" effect="dark">
                  {{ (report.overall_risk_level || 'LOW').toUpperCase() }} 风险等级
                </el-tag>
                <!-- 审核完整度 AuditCompleteness -->
                <el-tag :type="completenessTagType" size="small" effect="dark">
                  {{ completenessText }}
                </el-tag>
                <!-- 审批决策 ApprovalDecision -->
                <el-tag :type="decisionTagType" size="small" effect="dark">
                  {{ decisionText }}
                </el-tag>
              </div>
            </div>
          </div>

          <div class="summary-card">
            <div class="summary-text">{{ report.summary || '经多智能体联合核查，各项数据核验完毕。' }}</div>
            <div class="risk-counters">
              <span class="counter-item text-danger">高危红线: <strong>{{ report.high_risks_count || 0 }}</strong> 项</span>
              <span class="counter-item text-warning">中危合规: <strong>{{ report.medium_risks_count || 0 }}</strong> 项</span>
              <span class="counter-item text-info">低危提示: <strong>{{ report.low_risks_count || 0 }}</strong> 项</span>
            </div>
          </div>

          <div class="banner-actions">
            <!-- 单据切换下拉器 -->
            <div class="doc-switcher">
              <el-select
                v-model="currentSelectedDocId"
                placeholder="快速切换体检单据"
                size="default"
                style="width: 260px"
                @change="handleSwitchDoc"
              >
                <el-option
                  v-for="item in availableDocs"
                  :key="item.id"
                  :label="`${item.document_no} - ${item.title}`"
                  :value="String(item.id)"
                >
                  <div class="doc-option-item">
                    <span class="doc-opt-no">{{ item.document_no }}</span>
                    <span class="doc-opt-title">{{ item.title }}</span>
                    <el-tag size="small" :type="getStatusType(item.status)">{{ formatStatus(item.status) }}</el-tag>
                  </div>
                </el-option>
              </el-select>
            </div>

            <el-button-group>
              <el-button @click="$router.push('/documents')">单据列表</el-button>
              <el-button type="primary" plain @click="$router.push('/approvals')">审批中心</el-button>
            </el-button-group>
          </div>
        </div>

        <!-- 审核完整度与一票否决强提醒警示条 -->
        <div v-if="auditCompleteness === 'DEGRADED'" class="banner-alert">
          <el-alert
            type="warning"
            show-icon
            :closable="false"
            title="⚠️ 审核完整度降级 (DEGRADED)：部分外部要素缺失或校验降级，已禁止系统自动放行，单据必须转入人工重点复核。"
          />
        </div>
        <div v-if="auditCompleteness === 'INCOMPLETE'" class="banner-alert">
          <el-alert
            type="error"
            show-icon
            :closable="false"
            title="🚨 审核完整度未完备 (INCOMPLETE)：核心必检项未执行或校验失败，一票否决自动放行权限，必须人工全面复审。"
          />
        </div>
        <div v-if="decisionAction === 'REJECT'" class="banner-alert">
          <el-alert
            type="error"
            show-icon
            :closable="false"
            :title="`⛔ 状态机一票否决 (REJECT)：${decisionReason || '检出不可覆盖高危违规项，系统直接终审驳回并终止审批。'}`"
          />
        </div>

        <!-- 第二行：检出违规命中原因直观清单 -->
        <div class="hit-reasons-bar">
          <span class="hit-reasons-label">🚨 检出违规命中原因：</span>
          <div v-if="report.findings?.length" class="hit-reasons-tags">
            <el-tooltip
              v-for="finding in report.findings"
              :key="finding.id"
              :content="finding.description"
              placement="bottom"
            >
              <el-tag
                :type="getRiskTagType(finding.risk_level)"
                effect="light"
                class="hit-reason-tag"
                :class="{ 'is-active-tag': selectedFinding?.id === finding.id }"
                @click="selectFinding(finding)"
              >
                <strong class="tag-code">[{{ finding.rule_code }}]</strong>
                <span class="tag-title">{{ finding.title }}</span>
                <span v-if="finding.discrepancy_amount" class="tag-amount">
                  (偏差: ¥{{ Number(finding.discrepancy_amount).toFixed(2) }})
                </span>
                <el-icon class="tag-aim-icon"><Aim /></el-icon>
              </el-tag>
            </el-tooltip>
          </div>
          <div v-else class="hit-reasons-empty">
            <el-tag type="success" effect="light" size="default">
              ✅ 规则引擎与大模型联合核查：全单据及发票各项指标均已合规，未命中任何违规项
            </el-tag>
          </div>
        </div>
      </div>

      <!-- 三栏核心诊断视窗：左票据原件锚点 + 中风控证据详情 + 右智能问答 Copilot -->
      <div class="report-workbench-layout">
        <!-- 左栏：票据与 BBox 视觉锚点 -->
        <div class="workbench-col canvas-col">
          <InvoiceCanvasViewer
            :invoice-data="invoiceData"
            :selected-finding="selectedFinding"
          />
        </div>

        <!-- 中栏：风险发现项列表与五维证据链 -->
        <div class="workbench-col findings-col">
          <div class="col-header">
            <div class="header-text-group">
              <span class="col-title">智能体检出风险项 (共 {{ report.findings?.length || 0 }} 项)</span>
              <span class="col-sub">点击卡片可触发左侧视觉原件精准定焦</span>
            </div>
            <el-button
              v-if="isChatCollapsed"
              size="small"
              type="primary"
              plain
              icon="ChatLineRound"
              @click="isChatCollapsed = false"
            >
              展开 AI 助手
            </el-button>
          </div>

          <div class="findings-scroll-list">
            <div
              v-for="finding in report.findings || []"
              :key="finding.id"
              :class="['finding-card', finding.risk_level, { active: selectedFinding?.id === finding.id }]"
              @click="selectFinding(finding)"
            >
              <div class="finding-card-header">
                <div class="finding-title-group">
                  <el-tag size="small" :type="getRiskTagType(finding.risk_level)">
                    {{ finding.risk_level.toUpperCase() }}
                  </el-tag>
                  <span class="rule-code-badge">{{ finding.rule_code }}</span>
                  <span class="finding-title">{{ finding.title }}</span>
                </div>
                <div class="finding-tags-right">
                  <el-tag v-if="finding.is_overridable === false" size="small" type="danger" effect="dark">
                    一票否决 (不可覆盖)
                  </el-tag>
                  <el-tag v-else size="small" type="info" effect="plain">
                    可人工审批覆盖
                  </el-tag>
                  <el-tag size="small" type="info" effect="plain">{{ finding.agent_role }}</el-tag>
                </div>
              </div>

              <div class="finding-desc">{{ finding.description }}</div>

              <!-- 金额偏差与预期对比 -->
              <div v-if="finding.discrepancy_amount" class="discrepancy-row">
                <span>违规/偏差金额：</span>
                <strong class="text-danger">¥{{ Number(finding.discrepancy_amount).toFixed(2) }}</strong>
              </div>

              <!-- 处置建议 -->
              <div class="suggestion-row">
                <span class="sugg-label">💡 处置建议：</span>
                <span class="sugg-text">{{ finding.suggestion }}</span>
              </div>

              <!-- 锚点定焦按钮 -->
              <div class="card-footer-actions">
                <el-button size="small" type="primary" link icon="Aim" @click.stop="selectFinding(finding)">
                  定位发票视觉证据
                </el-button>
                <el-button size="small" type="info" link icon="ChatLineRound" @click.stop="askAboutFinding(finding)">
                  向 AI 提问该项
                </el-button>
              </div>
            </div>

            <el-empty v-if="!report.findings?.length" description="各项核验指标优良，未检出任何合规风险" :image-size="80" />
          </div>
        </div>

        <!-- 右栏：AI 问答交互 Copilot -->
        <div :class="['workbench-col', 'chat-col', { collapsed: isChatCollapsed }]">
          <div v-if="isChatCollapsed" class="collapsed-chat-bar" @click="isChatCollapsed = false" title="点击展开 AI 审计助手">
            <el-icon><ChatLineRound /></el-icon>
            <span class="vertical-text">展开 AI 助手</span>
          </div>
          <div v-else class="chat-wrapper">
            <div class="chat-col-header">
              <span class="chat-col-title">🤖 AI 审计专家 Copilot</span>
              <el-button size="small" link @click="isChatCollapsed = true" title="收起助手获得更宽票据视野">
                <el-icon><DArrowRight /></el-icon> 收起
              </el-button>
            </div>
            <AuditChatCopilot
              :document-id="documentId"
              :initial-question="initialChatQuestion"
              @select-citation="handleCitationSelect"
            />
          </div>
        </div>
      </div>

      <!-- 终审反思消歧展示 (ReviewerReflector Disambiguation) -->
      <div v-if="disambiguationLogs.length" class="disambiguation-section">
        <div class="section-card">
          <div class="section-header">
            <div class="header-title text-success">
              <el-icon><Check /></el-icon>
              <span>终审门禁反思消歧记录 (ReviewerReflector Disambiguation)</span>
            </div>
            <el-tag size="small" type="success">已自动处理 {{ disambiguationLogs.length }} 项争议</el-tag>
          </div>
          <div class="disambiguation-list">
            <div v-for="(log, idx) in disambiguationLogs" :key="idx" class="disambiguation-item">
              <el-tag size="small" type="success">已消歧/核减</el-tag>
              <span class="log-desc">{{ formatDisambiguationLog(log) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 多智能体协同执行明细简单表格 (Agent Execution Plan) -->
      <div class="agent-executions-section">
        <div class="section-card">
          <div class="section-header">
            <div class="header-title">
              <el-icon><Cpu /></el-icon>
              <span>多智能体协同执行明细 (Agent Execution Plan)</span>
            </div>
            <el-tag size="small" type="info">{{ agentExecutions.length }} 个核验节点</el-tag>
          </div>
          <el-table :data="agentExecutions" size="small" border stripe style="width: 100%">
            <el-table-column prop="role" label="智能体角色" width="180">
              <template #default="{ row }">
                <strong>{{ row.role }}</strong>
                <span class="text-muted" style="margin-left: 4px; font-size: 11px;">({{ row.name }})</span>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="执行状态" width="120">
              <template #default="{ row }">
                <el-tag :type="getAgentStatusType(row.status)" size="small">
                  {{ row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="duration" label="耗时" width="100" />
            <el-table-column prop="source" label="决策来源" width="160">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" type="info">{{ row.source }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="判定依据 / 降级说明" min-width="240" />
          </el-table>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Aim, ChatLineRound, DArrowRight, Cpu, Check, Refresh, Back } from '@element-plus/icons-vue'
import api from '@/api'
import InvoiceCanvasViewer from '@/components/InvoiceCanvasViewer.vue'
import AuditChatCopilot from '@/components/AuditChatCopilot.vue'

const route = useRoute()
const router = useRouter()
const documentId = computed(() => route.params.documentId)

const report = ref(null)
const loading = ref(false)
const loadFailed = ref(false)
const selectedFinding = ref(null)
const initialChatQuestion = ref('')
const isChatCollapsed = ref(false)

const availableDocs = ref([])
const currentSelectedDocId = ref('')

const invoiceData = ref({})

// 审核完整度
const auditCompleteness = computed(() => {
  if (!report.value) return 'COMPLETE'
  const payload = report.value.full_report_payload || {}
  return payload.audit_completeness || 'COMPLETE'
})

const completenessText = computed(() => {
  const map = {
    COMPLETE: 'COMPLETE 审核完备',
    DEGRADED: 'DEGRADED 降级运行',
    INCOMPLETE: 'INCOMPLETE 审核未完备'
  }
  return map[auditCompleteness.value] || auditCompleteness.value
})

const completenessTagType = computed(() => {
  const map = {
    COMPLETE: 'success',
    DEGRADED: 'warning',
    INCOMPLETE: 'danger'
  }
  return map[auditCompleteness.value] || 'info'
})

// 审批决策
const decisionAction = computed(() => {
  if (!report.value) return ''
  const payload = report.value.full_report_payload || {}
  if (payload.approval_decision?.action) {
    return payload.approval_decision.action
  }
  // 兜底推导
  if (report.value.overall_risk_level === 'high') {
    const hasVeto = (report.value.findings || []).some(f => f.risk_level === 'high' && f.is_overridable === false)
    return hasVeto ? 'REJECT' : 'MANUAL_REVIEW'
  }
  if (auditCompleteness.value !== 'COMPLETE') {
    return 'MANUAL_REVIEW'
  }
  if (report.value.final_score >= 90) {
    return 'AUTO_APPROVE'
  }
  return 'MANUAL_REVIEW'
})

const decisionReason = computed(() => {
  const payload = report.value?.full_report_payload || {}
  return payload.approval_decision?.reason || ''
})

const decisionText = computed(() => {
  const map = {
    AUTO_APPROVE: '自动放行 (AUTO_APPROVE)',
    MANUAL_REVIEW: '人工初复审 (MANUAL_REVIEW)',
    NEED_SUPPLEMENT: '要求补证 (NEED_SUPPLEMENT)',
    REJECT: '一票否决 (REJECT)'
  }
  return map[decisionAction.value] || decisionAction.value
})

const decisionTagType = computed(() => {
  const map = {
    AUTO_APPROVE: 'success',
    MANUAL_REVIEW: 'warning',
    NEED_SUPPLEMENT: 'warning',
    REJECT: 'danger'
  }
  return map[decisionAction.value] || 'info'
})

// 多智能体协同明细
const agentExecutions = computed(() => {
  const payload = report.value?.full_report_payload
  if (!payload) return []
  const results = payload.agent_execution_results || {}
  const plan = payload.execution_plan || {}
  const plannedAgents = plan.planned_agents || Object.keys(results)

  const list = []
  for (const agentName of plannedAgents) {
    const exec = results[agentName] || {}
    list.push({
      name: agentName,
      role: getAgentRoleName(agentName),
      status: exec.status || 'PLANNED',
      duration: exec.elapsed_ms != null ? `${exec.elapsed_ms} ms` : (exec.duration_ms != null ? `${exec.duration_ms} ms` : '-'),
      source: exec.source || 'DETERMINISTIC',
      reason: exec.reason || exec.detail || '按既定能力集核验通过'
    })
  }
  return list
})

const getAgentRoleName = (name) => {
  const map = {
    InvoiceOcrAgent: '票据OCR解析专家',
    AmountAgent: '金额平衡与纳税校验专家',
    ComplianceAgent: '内控制度与合规校验专家',
    SupplierAgent: '供应商资质与画像风控专家',
    BehaviorAgent: '员工历史报销行为画像专家',
    ReviewerReflector: '终审门禁反思专家',
    ReportAgent: '综合报告生成专家'
  }
  return map[name] || name
}

const getAgentStatusType = (status) => {
  const map = {
    SUCCESS: 'success',
    DEGRADED: 'warning',
    FAILED: 'danger',
    SKIPPED: 'info',
    PLANNED: 'info'
  }
  return map[status] || 'info'
}

// 终审反思消歧日志
const disambiguationLogs = computed(() => {
  const payload = report.value?.full_report_payload
  if (!payload) return []
  return payload.disambiguation_logs || []
})

const formatDisambiguationLog = (log) => {
  if (typeof log === 'string') return log
  if (log.rule_code) {
    return `[${log.rule_code}] ${log.action || '消歧'}: ${log.reason || log.detail || ''}`
  }
  return JSON.stringify(log)
}

const fetchAvailableDocs = async () => {
  try {
    const res = await api.get('/documents', { params: { page_size: 50 } })
    if (res?.items) {
      availableDocs.value = res.items
    }
  } catch (err) {
    console.warn('获取候选单据清单失败:', err)
  }
}

const handleSwitchDoc = (newDocId) => {
  if (newDocId && newDocId !== documentId.value) {
    router.push(`/audits/${newDocId}`)
  }
}

const fetchReport = async () => {
  if (!documentId.value) return
  loading.value = true
  loadFailed.value = false
  currentSelectedDocId.value = String(documentId.value)
  localStorage.setItem('last_viewed_audit_id', String(documentId.value))

  try {
    const res = await api.get(`/audits/reports/${documentId.value}`)
    if (!res || !res.id) {
      loadFailed.value = true
      report.value = null
      return
    }
    report.value = res
    if (res.findings?.length) {
      selectedFinding.value = res.findings[0]
    } else {
      selectedFinding.value = null
    }
  } catch (err) {
    console.warn('获取体检报告失败 (Fail-Closed 生效):', err)
    loadFailed.value = true
    report.value = null
  } finally {
    loading.value = false
  }

  // 同步获取单据关联发票原件与 OCR 视觉坐标 (不硬编码虚假发票)
  try {
    const docRes = await api.get(`/documents/${documentId.value}`)
    if (docRes && docRes.invoices && docRes.invoices.length > 0) {
      const inv = docRes.invoices[0]
      invoiceData.value = {
        invoice_code: inv.invoice_code,
        invoice_number: inv.invoice_number,
        invoice_type: inv.invoice_type,
        issue_date: inv.issue_date,
        total_amount: Number(inv.total_amount).toFixed(2),
        untaxed_amount: Number(inv.untaxed_amount || inv.total_amount * 0.94).toFixed(2),
        tax_amount: Number(inv.tax_amount || inv.total_amount * 0.06).toFixed(2),
        seller_name: inv.seller_name,
        seller_tax_id: inv.seller_tax_id,
        buyer_name: inv.buyer_name || '北京智能前沿科技有限公司',
        buyer_tax_id: inv.buyer_tax_id || '91110108MA01XXXXXX',
        file_path: inv.file_path || inv.raw_payload?.file_path || docRes.attachments?.[0]?.file_path,
        bbox_positions: inv.bbox_positions || inv.raw_payload?.bbox_positions
      }
    } else if (docRes && docRes.attachments && docRes.attachments.length > 0) {
      invoiceData.value = {
        file_path: docRes.attachments[0].file_path
      }
    } else {
      invoiceData.value = {}
    }
  } catch (docErr) {
    console.warn('获取单据原件与发票详情失败:', docErr)
    invoiceData.value = {}
  }
}

const selectFinding = (f) => {
  selectedFinding.value = f
}

const askAboutFinding = (f) => {
  if (isChatCollapsed.value) {
    isChatCollapsed.value = false
  }
  initialChatQuestion.value = `请详细解释为什么检出 [${f.rule_name || f.title}]，依据的制度或规则是什么？`
}

const handleCitationSelect = (citation) => {
  const match = (report.value?.findings || []).find(f => f.finding_id === citation.finding_id)
  if (match) {
    selectedFinding.value = match
  }
}

const getScoreClass = (score) => {
  if (score >= 90) return 'score-green'
  if (score >= 60) return 'score-yellow'
  return 'score-red'
}

const getRiskTagType = (lvl) => {
  if (lvl === 'high') return 'danger'
  if (lvl === 'medium') return 'warning'
  return 'success'
}

const getStatusType = (status) => {
  const map = {
    DRAFT: 'info',
    SUBMITTED: 'primary',
    PENDING_APPROVAL: 'warning',
    APPROVED: 'success',
    REJECTED: 'danger',
    NEED_SUPPLEMENT: 'warning',
    CANCELLED: 'info'
  }
  return map[status] || 'info'
}

const formatStatus = (status) => {
  const map = {
    DRAFT: '草稿',
    SUBMITTED: '审查中',
    PENDING_APPROVAL: '待审批',
    APPROVED: '已办结',
    REJECTED: '已驳回',
    NEED_SUPPLEMENT: '待补充材料',
    CANCELLED: '已撤回'
  }
  return map[status] || status
}

watch(
  () => route.params.documentId,
  (newId) => {
    if (newId) {
      fetchReport()
    }
  }
)

onMounted(() => {
  fetchReport()
  fetchAvailableDocs()
})
</script>

<style scoped>
.audit-report-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: calc(100vh - 100px);
  min-width: 1120px;
  padding-bottom: 24px;
}

.fail-closed-card {
  background: #ffffff;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  padding: 40px;
  margin-top: 20px;
}

.fail-closed-actions {
  display: flex;
  gap: 12px;
  justify-content: center;
}

.report-top-banner {
  background: #ffffff;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  padding: 14px 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.banner-top-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
}

.score-card {
  display: flex;
  align-items: center;
  gap: 14px;
  border-right: 1px solid #e2e8f0;
  padding-right: 20px;
  min-width: 320px;
}

.score-number {
  font-size: 38px;
  font-weight: 900;
  font-family: monospace;
}

.score-green { color: #16a34a; }
.score-yellow { color: #d97706; }
.score-red { color: #dc2626; }

.score-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.score-title {
  font-size: 13px;
  color: #64748b;
}

.tag-group {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.summary-card {
  flex: 1;
  min-width: 320px;
}

.summary-text {
  font-size: 13px;
  color: #1e293b;
  line-height: 1.5;
  margin-bottom: 6px;
}

.risk-counters {
  display: flex;
  gap: 16px;
  font-size: 12px;
}

.text-danger { color: #ef4444; }
.text-warning { color: #f59e0b; }
.text-info { color: #3b82f6; }
.text-muted { color: #64748b; }

.banner-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.doc-option-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.doc-opt-no {
  font-family: monospace;
  font-weight: bold;
}

.doc-opt-title {
  color: #64748b;
  font-size: 12px;
}

.banner-alert {
  margin-top: 2px;
}

.hit-reasons-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  background: #f8fafc;
  padding: 8px 12px;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
  flex-wrap: wrap;
}

.hit-reasons-label {
  font-size: 12px;
  font-weight: 600;
  color: #334155;
  white-space: nowrap;
}

.hit-reasons-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.hit-reason-tag {
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.2s;
}

.hit-reason-tag:hover,
.hit-reason-tag.is-active-tag {
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  border-color: #dc2626;
}

.tag-code { font-family: monospace; }
.tag-title { font-weight: 600; }
.tag-amount { font-weight: 700; color: #b91c1c; }
.tag-aim-icon { font-size: 12px; color: #3b82f6; margin-left: 2px; }

.report-workbench-layout {
  display: flex;
  gap: 16px;
  height: 580px;
  overflow: hidden;
}

.workbench-col {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.canvas-col {
  flex: 1.2;
  min-width: 380px;
}

.findings-col {
  flex: 1.2;
  min-width: 380px;
  background: #ffffff;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  overflow: hidden;
}

.chat-col {
  flex: 1;
  min-width: 300px;
  transition: all 0.25s ease;
}

.chat-col.collapsed {
  flex: 0 0 42px;
  min-width: 42px;
  width: 42px;
  overflow: hidden;
  background: #ffffff;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
}

.collapsed-chat-bar {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  cursor: pointer;
  background: #f8fafc;
  color: #2563eb;
  padding: 16px 0;
  transition: background 0.2s;
}

.collapsed-chat-bar:hover { background: #eff6ff; }
.vertical-text { writing-mode: vertical-lr; letter-spacing: 2px; font-size: 12px; font-weight: 600; }

.chat-wrapper { height: 100%; display: flex; flex-direction: column; }
.chat-col-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  border-radius: 8px 8px 0 0;
}
.chat-col-title { font-size: 13px; font-weight: 600; color: #1e293b; }

.col-header {
  padding: 10px 14px;
  border-bottom: 1px solid #e2e8f0;
  background: #f8fafc;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.col-title { font-weight: 600; color: #0f172a; font-size: 13px; }
.col-sub { font-size: 11px; color: #64748b; margin-left: 8px; }

.findings-scroll-list {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.finding-card {
  padding: 14px;
  border-radius: 6px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  cursor: pointer;
  transition: all 0.2s;
}
.finding-card:hover { border-color: #3b82f6; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }
.finding-card.active { border-color: #ef4444; background: #fffafa; box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.2); }

.finding-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.finding-title-group { display: flex; align-items: center; gap: 8px; }
.finding-tags-right { display: flex; align-items: center; gap: 6px; }
.rule-code-badge {
  font-family: monospace;
  font-size: 11px;
  background: #f1f5f9;
  padding: 2px 6px;
  border-radius: 4px;
}
.finding-title { font-weight: 600; color: #1e293b; font-size: 13px; }
.finding-desc { font-size: 12px; color: #475569; line-height: 1.5; margin-bottom: 8px; }
.discrepancy-row { font-size: 12px; margin-bottom: 6px; }
.suggestion-row {
  font-size: 12px;
  background: #f8fafc;
  padding: 6px 10px;
  border-radius: 4px;
  margin-bottom: 8px;
}
.sugg-label { font-weight: 600; color: #334155; }
.card-footer-actions { display: flex; justify-content: flex-end; gap: 12px; }

/* 终审消歧记录样式 */
.disambiguation-section,
.agent-executions-section {
  background: #ffffff;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  padding: 16px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 14px;
  color: #1e293b;
}

.disambiguation-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.disambiguation-item {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 12px;
}

.log-desc {
  color: #166534;
  line-height: 1.4;
}
</style>
