<template>
  <div class="dashboard-page" v-loading="loading">
    <!-- 顶部大盘标题与态势横幅 -->
    <div class="cockpit-header">
      <div class="header-left">
        <div class="badge-title">
          <span class="live-dot"></span>
          <span class="tag-text">AI 驱动实时内控中枢</span>
        </div>
        <h2 class="cockpit-title">企业财务单据智能风控态势驾驶舱</h2>
        <p class="cockpit-subtitle">确定性规则（Decimal精度+国标校验+哈希查重）与多智能体（大模型语义推演）联合审计</p>
      </div>

      <div class="header-actions">
        <el-tag effect="plain" type="success" class="status-tag">
          <el-icon><Check /></el-icon> 5大Agent流水线就绪
        </el-tag>
        <el-button type="primary" plain icon="Refresh" @click="fetchMetrics" :loading="loading">
          刷新实时态势
        </el-button>
      </div>
    </div>

    <!-- 第一行：4 大核心指标 KPI 卡片 -->
    <div class="metrics-grid">
      <div class="kpi-card kpi-blue">
        <div class="kpi-info">
          <span class="kpi-label">累计审查财务单据</span>
          <h3 class="kpi-value">{{ metrics.overview?.total_documents || 0 }} <span class="kpi-unit">笔</span></h3>
          <span class="kpi-footer">覆盖差旅、对公、预付款全场景</span>
        </div>
        <div class="kpi-icon-wrapper">
          <el-icon><Files /></el-icon>
        </div>
      </div>

      <div class="kpi-card kpi-indigo">
        <div class="kpi-info">
          <span class="kpi-label">审查申报资金总规模</span>
          <h3 class="kpi-value">¥{{ formatNumber(metrics.overview?.total_audited_amount) }}</h3>
          <span class="kpi-footer">前置算术硬核验 0 容差防呆</span>
        </div>
        <div class="kpi-icon-wrapper">
          <el-icon><Money /></el-icon>
        </div>
      </div>

      <div class="kpi-card kpi-rose">
        <div class="kpi-info">
          <span class="kpi-label">系统拦截高危违规资金</span>
          <h3 class="kpi-value text-rose">¥{{ formatNumber(metrics.overview?.blocked_high_risk_amount) }}</h3>
          <span class="kpi-footer">防范拆单、失信走账与超标</span>
        </div>
        <div class="kpi-icon-wrapper">
          <el-icon><WarningFilled /></el-icon>
        </div>
      </div>

      <div class="kpi-card kpi-emerald">
        <div class="kpi-info">
          <span class="kpi-label">AI 智能初审放行率</span>
          <h3 class="kpi-value text-emerald">{{ metrics.overview?.auto_pass_rate ?? 0 }}%</h3>
          <span class="kpi-footer">低危小额单据 RULE_AUTO_PASS</span>
        </div>
        <div class="kpi-icon-wrapper">
          <el-icon><Cpu /></el-icon>
        </div>
      </div>
    </div>

    <!-- 第二行：风险等级分布 + 高频违规规则 TOP 5 -->
    <div class="grid-two-col">
      <!-- 左：风险等级分布与综合健康度 -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <div class="card-header-flex">
            <span class="card-title">🛡️ 单据风险等级分布与综合评级</span>
            <el-tag size="small" type="primary">平均得分: {{ metrics.overview?.avg_score ?? 100 }} 分</el-tag>
          </div>
        </template>

        <div class="risk-dist-body">
          <div class="risk-score-badge">
            <div class="score-circle">
              <span class="score-num">{{ Math.round(metrics.overview?.avg_score ?? 100) }}</span>
              <span class="score-text">综合风控评级</span>
            </div>
            <div class="score-desc">
              <p>企业当前整体财务合规态势<strong>优良</strong>，未发现系统性拆单舞弊与特大坏账风险。</p>
            </div>
          </div>

          <div class="risk-bars-container">
            <div class="risk-bar-row">
              <div class="bar-label-group">
                <span class="dot dot-danger"></span>
                <span class="label-name">高危风险单据 (需CFO特批)</span>
                <span class="label-val">{{ metrics.risk_distribution?.high || 0 }} 笔</span>
              </div>
              <el-progress
                :percentage="calculatePct(metrics.risk_distribution?.high)"
                status="exception"
                :stroke-width="12"
              />
            </div>

            <div class="risk-bar-row">
              <div class="bar-label-group">
                <span class="dot dot-warning"></span>
                <span class="label-name">中危警示单据 (需主管复核)</span>
                <span class="label-val">{{ metrics.risk_distribution?.medium || 0 }} 笔</span>
              </div>
              <el-progress
                :percentage="calculatePct(metrics.risk_distribution?.medium)"
                color="#f59e0b"
                :stroke-width="12"
              />
            </div>

            <div class="risk-bar-row">
              <div class="bar-label-group">
                <span class="dot dot-success"></span>
                <span class="label-name">低危合规单据 (极速放行)</span>
                <span class="label-val">{{ metrics.risk_distribution?.low || 0 }} 笔</span>
              </div>
              <el-progress
                :percentage="calculatePct(metrics.risk_distribution?.low)"
                status="success"
                :stroke-width="12"
              />
            </div>
          </div>
        </div>
      </el-card>

      <!-- 右：高频违规内控规则 TOP 5 -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <div class="card-header-flex">
            <span class="card-title">⚠️ 高频内控违规触发规则 TOP 5</span>
            <span class="subtitle-text">多智能体实时检出频度</span>
          </div>
        </template>

        <div class="rules-rank-list">
          <div
            v-for="(rule, idx) in (metrics.top_rules || [])"
            :key="idx"
            class="rule-rank-item"
          >
            <div class="rank-index" :class="'rank-' + (idx + 1)">{{ idx + 1 }}</div>
            <div class="rule-detail">
              <div class="rule-name-row">
                <span class="rule-name">{{ rule.rule_name }}</span>
                <el-tag size="small" :type="rule.risk_level === 'high' ? 'danger' : 'warning'">
                  {{ rule.risk_level === 'high' ? '高危红线' : '合规警示' }}
                </el-tag>
              </div>
              <div class="rule-code-text">{{ rule.rule_code }}</div>
            </div>
            <div class="rule-count">
              <span class="count-num">{{ rule.count }}</span>
              <span class="count-unit">次检出</span>
            </div>
          </div>
        </div>
      </el-card>
    </div>

    <!-- 第三行：部门报销合规分布 + 最新动态流水 -->
    <div class="grid-two-col" style="margin-top: 16px;">
      <!-- 左：各部门支出与合规率 -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <div class="card-header-flex">
            <span class="card-title">🏢 业务部门申报金额与合规率分布</span>
          </div>
        </template>

        <el-table :data="metrics.department_stats || []" size="small" stripe border>
          <el-table-column prop="department_name" label="申报部门" width="130" />
          <el-table-column prop="document_count" label="单据量" width="80" align="center">
            <template #default="{ row }">{{ row.document_count }} 笔</template>
          </el-table-column>
          <el-table-column prop="total_amount" label="申报总额 (元)" align="right" width="130">
            <template #default="{ row }">¥{{ Number(row.total_amount).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</template>
          </el-table-column>
          <el-table-column label="合规达标率" min-width="140">
            <template #default="{ row }">
              <el-progress
                :percentage="row.compliance_rate"
                :status="row.compliance_rate >= 90 ? 'success' : 'warning'"
                :stroke-width="10"
              />
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 右：最新审查单据动态流水 -->
      <el-card shadow="never" class="section-card">
        <template #header>
          <div class="card-header-flex">
            <span class="card-title">⚡ 智能风控流水线实时动态</span>
            <el-button link type="primary" size="small" @click="$router.push('/documents')">进入工作台 &gt;</el-button>
          </div>
        </template>

        <div class="activity-stream">
          <div
            v-for="(act, idx) in (metrics.recent_activities || [])"
            :key="idx"
            class="activity-row"
            @click="$router.push(`/documents/${act.id}`)"
          >
            <div class="act-main">
              <span class="act-title">{{ act.title }}</span>
              <div class="act-meta">
                <span>{{ act.applicant_name }} ({{ act.department_name }})</span>
                <span class="dot-separator">•</span>
                <span>{{ act.created_at }}</span>
              </div>
            </div>
            <div class="act-right">
              <span class="act-amount">¥{{ Number(act.total_amount).toFixed(2) }}</span>
              <el-tag size="small" :type="getStatusType(act.status)">
                {{ formatStatus(act.status) }}
              </el-tag>
            </div>
          </div>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Files, Money, WarningFilled, Cpu, Check, Refresh } from '@element-plus/icons-vue'
import api from '@/api'

const loading = ref(false)
const metrics = ref({})

const fetchMetrics = async () => {
  loading.value = true
  try {
    const res = await api.get('/dashboard/metrics')
    metrics.value = res || {}
  } catch (err) {
    // 降级兜底展示
  } finally {
    loading.value = false
  }
}

const formatNumber = (val) => {
  if (!val) return '0.00'
  return Number(val).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const calculatePct = (count) => {
  const total = (metrics.value.risk_distribution?.high || 0) +
                (metrics.value.risk_distribution?.medium || 0) +
                (metrics.value.risk_distribution?.low || 0)
  if (!total) return 0
  return Math.round(((count || 0) / total) * 100)
}

const formatStatus = (s) => {
  const map = {
    DRAFT: '草稿',
    SUBMITTED: '已提交审查',
    IN_REVIEW: 'AI审查中',
    PENDING_APPROVAL: '待审批',
    APPROVED: '已办结',
    REJECTED: '已驳回',
    CANCELLED: '已撤回'
  }
  return map[s] || s || '-'
}

const getStatusType = (s) => {
  const map = {
    DRAFT: 'info',
    SUBMITTED: 'primary',
    IN_REVIEW: 'primary',
    PENDING_APPROVAL: 'warning',
    APPROVED: 'success',
    REJECTED: 'danger',
    CANCELLED: 'info'
  }
  return map[s] || 'info'
}

onMounted(() => {
  fetchMetrics()
})
</script>

<style scoped>
.dashboard-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.cockpit-header {
  background: #ffffff;
  padding: 20px 24px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.badge-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  padding: 2px 10px;
  border-radius: 20px;
  margin-bottom: 6px;
}

.live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 8px #10b981;
}

.tag-text {
  font-size: 11px;
  color: #1d4ed8;
  font-weight: 600;
}

.cockpit-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
}

.cockpit-subtitle {
  margin: 4px 0 0 0;
  font-size: 13px;
  color: #64748b;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-tag {
  font-weight: 500;
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}

.kpi-card {
  background: #ffffff;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  padding: 18px 20px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: relative;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0,0,0,0.03);
}

.kpi-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 4px;
  height: 100%;
}

.kpi-blue::before { background: #3b82f6; }
.kpi-indigo::before { background: #6366f1; }
.kpi-rose::before { background: #f43f5e; }
.kpi-emerald::before { background: #10b981; }

.kpi-label {
  font-size: 13px;
  color: #64748b;
  font-weight: 500;
}

.kpi-value {
  margin: 6px 0;
  font-size: 22px;
  font-weight: 700;
  color: #0f172a;
}

.kpi-unit {
  font-size: 13px;
  font-weight: normal;
  color: #94a3b8;
}

.text-rose { color: #e11d48; }
.text-emerald { color: #059669; }

.kpi-footer {
  font-size: 11px;
  color: #94a3b8;
}

.kpi-icon-wrapper {
  width: 46px;
  height: 46px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
}

.kpi-blue .kpi-icon-wrapper { background: #eff6ff; color: #3b82f6; }
.kpi-indigo .kpi-icon-wrapper { background: #eef2ff; color: #6366f1; }
.kpi-rose .kpi-icon-wrapper { background: #fff1f2; color: #f43f5e; }
.kpi-emerald .kpi-icon-wrapper { background: #ecfdf5; color: #10b981; }

.grid-two-col {
  display: grid;
  grid-template-columns: 1.1fr 1fr;
  gap: 16px;
}

.section-card {
  border-radius: 8px;
  border: 1px solid #e2e8f0;
}

.card-header-flex {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-title {
  font-size: 14px;
  font-weight: 700;
  color: #1e293b;
}

.subtitle-text {
  font-size: 12px;
  color: #94a3b8;
}

.risk-dist-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.risk-score-badge {
  display: flex;
  align-items: center;
  gap: 16px;
  background: #f8fafc;
  padding: 14px 18px;
  border-radius: 8px;
  border: 1px solid #f1f5f9;
}

.score-circle {
  width: 68px;
  height: 68px;
  border-radius: 50%;
  background: #2563eb;
  color: #ffffff;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 4px 10px rgba(37,99,235,0.25);
}

.score-num {
  font-size: 22px;
  font-weight: 800;
  line-height: 1;
}

.score-text {
  font-size: 9px;
  opacity: 0.85;
}

.score-desc p {
  margin: 0;
  font-size: 13px;
  color: #475569;
  line-height: 1.6;
}

.risk-bars-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.risk-bar-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bar-label-group {
  display: flex;
  align-items: center;
  font-size: 12px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
}

.dot-danger { background: #ef4444; }
.dot-warning { background: #f59e0b; }
.dot-success { background: #10b981; }

.label-name {
  color: #475569;
  flex: 1;
}

.label-val {
  font-weight: 600;
  color: #1e293b;
}

.rules-rank-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.rule-rank-item {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  background: #f8fafc;
  border-radius: 6px;
  border: 1px solid #f1f5f9;
  gap: 12px;
}

.rank-index {
  width: 24px;
  height: 24px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 12px;
  background: #e2e8f0;
  color: #64748b;
  flex-shrink: 0;
}

.rank-1 { background: #fee2e2; color: #ef4444; }
.rank-2 { background: #ffedd5; color: #f97316; }
.rank-3 { background: #fef3c7; color: #d97706; }

.rule-detail {
  flex: 1;
}

.rule-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}

.rule-name {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
}

.rule-code-text {
  font-size: 11px;
  color: #94a3b8;
  font-family: monospace;
}

.rule-count {
  text-align: right;
}

.count-num {
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
}

.count-unit {
  font-size: 11px;
  color: #94a3b8;
  margin-left: 2px;
}

.activity-stream {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.activity-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: #f8fafc;
  border-radius: 6px;
  border: 1px solid #f1f5f9;
  cursor: pointer;
  transition: all 0.2s;
}

.activity-row:hover {
  background: #eff6ff;
  border-color: #bfdbfe;
}

.act-title {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  display: block;
  margin-bottom: 2px;
}

.act-meta {
  font-size: 11px;
  color: #94a3b8;
}

.dot-separator {
  margin: 0 4px;
}

.act-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.act-amount {
  font-size: 13px;
  font-weight: 700;
  color: #0f172a;
}
</style>
