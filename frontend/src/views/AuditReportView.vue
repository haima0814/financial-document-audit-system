<template>
  <div class="audit-report-page" v-loading="loading">
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
            <el-tag :type="getRiskTagType(report.overall_risk_level)" size="small" effect="dark">
              {{ (report.overall_risk_level || 'LOW').toUpperCase() }} 风险等级
            </el-tag>
          </div>
        </div>

        <div class="summary-card">
          <div class="summary-text">{{ report.summary || '经多智能体联合核查，该单据各项数据与发票匹配一致，未发现高危红线风险。' }}</div>
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

      <!-- 第二行：检出违规原因直观清单 (解决只有分数条数无原因痛点) -->
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
            ✅ 规则引擎与大模型联合核查：全单据及发票各项指标均已合规放行，未命中任何违规项
          </el-tag>
        </div>
      </div>
    </div>

    <!-- 三栏核心诊断视窗：左票据原件锚点 + 中风控证据详情 + 右智能问答 Copilot (支持收起/展开) -->
    <div class="report-workbench-layout">
      <!-- 左栏：票据与 BBox 视觉锚点 -->
      <div class="workbench-col canvas-col">
        <InvoiceCanvasViewer
          :invoice-data="invoiceData"
          :selected-finding="selectedFinding"
        />
      </div>

      <!-- 中栏：风险发现项列表与证据链 -->
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
              <el-tag size="small" type="info" effect="plain">{{ finding.agent_role }}</el-tag>
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

      <!-- 右栏：AI 问答交互 Copilot (支持收起/展开防挤压) -->
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
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Aim, ChatLineRound, DArrowRight } from '@element-plus/icons-vue'
import api from '@/api'
import InvoiceCanvasViewer from '@/components/InvoiceCanvasViewer.vue'
import AuditChatCopilot from '@/components/AuditChatCopilot.vue'

const route = useRoute()
const router = useRouter()
const documentId = computed(() => route.params.documentId)

const report = ref({})
const loading = ref(false)
const selectedFinding = ref(null)
const initialChatQuestion = ref('')
const isChatCollapsed = ref(false)

const availableDocs = ref([])
const currentSelectedDocId = ref('')

const invoiceData = ref({
  invoice_code: '011002000111',
  invoice_number: '23456789',
  issue_date: '2026-03-12',
  total_amount: '1,500.00',
  untaxed_amount: '1,415.09',
  tax_amount: '84.91',
  seller_name: '汉庭商务酒店（上海某某店）',
  seller_tax_id: '91310115MA1XXXXXX'
})

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
  currentSelectedDocId.value = String(documentId.value)
  localStorage.setItem('last_viewed_audit_id', String(documentId.value))

  try {
    const res = await api.get(`/audits/reports/${documentId.value}`)
    report.value = res
    if (res.findings?.length) {
      selectedFinding.value = res.findings[0]
    } else {
      selectedFinding.value = null
    }
  } catch (err) {
    console.warn('获取体检报告失败:', err)
    report.value = {}
  } finally {
    loading.value = false
  }

  // 同步获取单据关联发票原件与 OCR 视觉坐标
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
      invoiceData.value.file_path = docRes.attachments[0].file_path
    }
  } catch (docErr) {
    console.warn('获取单据原件与发票详情失败:', docErr)
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
  const match = (report.value.findings || []).find(f => f.finding_id === citation.finding_id)
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
  height: calc(100vh - 100px);
  min-width: 1120px;
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
  min-width: 220px;
}

.score-number {
  font-size: 38px;
  font-weight: 900;
  font-family: monospace;
}

.score-green { color: #16a34a; }
.score-yellow { color: #d97706; }
.score-red { color: #dc2626; }

.score-title {
  font-size: 13px;
  color: #64748b;
  margin-bottom: 4px;
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
  font-weight: 600;
}

.doc-opt-title {
  font-size: 12px;
  color: #64748b;
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hit-reasons-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 8px;
  border-top: 1px dashed #e2e8f0;
  flex-wrap: wrap;
}

.hit-reasons-label {
  font-size: 12px;
  font-weight: 700;
  color: #dc2626;
  white-space: nowrap;
}

.hit-reasons-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.hit-reason-tag {
  cursor: pointer;
  transition: all 0.2s;
  padding: 4px 8px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.hit-reason-tag:hover,
.hit-reason-tag.is-active-tag {
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(220, 38, 38, 0.2);
  border-color: #dc2626;
}

.tag-code {
  font-family: monospace;
}

.tag-title {
  font-weight: 600;
}

.tag-amount {
  font-weight: 700;
  color: #b91c1c;
}

.tag-aim-icon {
  font-size: 12px;
  color: #3b82f6;
  margin-left: 2px;
}

.report-workbench-layout {
  flex: 1;
  display: flex;
  gap: 16px;
  min-height: 0;
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

.collapsed-chat-bar:hover {
  background: #eff6ff;
}

.vertical-text {
  writing-mode: vertical-lr;
  letter-spacing: 2px;
  font-size: 12px;
  font-weight: 600;
}

.chat-wrapper {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.chat-col-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  border-radius: 8px 8px 0 0;
}

.chat-col-title {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
}

.col-header {
  padding: 10px 14px;
  border-bottom: 1px solid #e2e8f0;
  background: #f8fafc;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.col-title {
  font-weight: 600;
  color: #0f172a;
  font-size: 13px;
}

.col-sub {
  font-size: 11px;
  color: #64748b;
  margin-left: 8px;
}

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

.finding-card:hover {
  border-color: #3b82f6;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
}

.finding-card.active {
  border-color: #ef4444;
  background: #fffafa;
  box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.2);
}

.finding-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.finding-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.rule-code-badge {
  font-family: monospace;
  font-size: 11px;
  background: #f1f5f9;
  padding: 2px 6px;
  border-radius: 4px;
}

.finding-title {
  font-weight: 600;
  color: #1e293b;
  font-size: 13px;
}

.finding-desc {
  font-size: 12px;
  color: #475569;
  line-height: 1.5;
  margin-bottom: 8px;
}

.discrepancy-row {
  font-size: 12px;
  margin-bottom: 6px;
}

.suggestion-row {
  font-size: 12px;
  background: #f8fafc;
  padding: 6px 10px;
  border-radius: 4px;
  margin-bottom: 8px;
}

.sugg-label {
  font-weight: 600;
  color: #334155;
}

.card-footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>
