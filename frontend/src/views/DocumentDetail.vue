<template>
  <div class="doc-detail-page" v-loading="loading">
    <div class="detail-header-card">
      <div class="header-main">
        <div class="doc-title-row">
          <span class="doc-no-badge">{{ document.document_no }}</span>
          <h2 class="doc-title">{{ document.title }}</h2>
          <el-tag :type="getStatusType(document.status)">{{ formatStatus(document.status) }}</el-tag>
        </div>
        <div class="doc-meta-row">
          <span>单据类型：<strong>{{ formatDocType(document.document_type) }}</strong></span>
          <span>申报部门：<strong>{{ document.department_name || '-' }}</strong></span>
          <span>申报总额：<strong class="text-danger">¥{{ Number(document.total_amount || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</strong></span>
          <span>当前版本：<el-tag size="small" type="primary" effect="plain" style="cursor: pointer;" @click="openVersionModal">V{{ document.current_version }} (历史快照)</el-tag></span>
        </div>
      </div>

      <div class="header-actions">
        <el-button @click="$router.push('/documents')">返回列表</el-button>
        <el-button
          v-if="['SUBMITTED', 'PENDING_APPROVAL', 'IN_REVIEW'].includes(document.status)"
          type="warning"
          plain
          @click="handleCancel"
        >
          撤回单据
        </el-button>
        <el-button
          v-if="document.status !== 'DRAFT' && document.status !== 'CANCELLED'"
          :type="document.status === 'REJECTED' ? 'danger' : 'success'"
          plain
          @click="$router.push(`/audits/${document.id}`)"
        >
          {{ document.status === 'REJECTED' ? '查看驳回体检报告' : '查看风控体检报告' }}
        </el-button>
        <el-button
          v-if="document.status === 'DRAFT' || document.status === 'REJECTED'"
          type="primary"
          :loading="submitting"
          :disabled="submitting"
          @click="handleSubmit"
        >
          {{ document.status === 'REJECTED' ? '重新提交审查 (升级V' + (document.current_version + 1) + ')' : '提交智能风控审查' }}
        </el-button>
      </div>
    </div>

    <!-- 异常状态横幅提示 -->
    <el-alert
      v-if="document.status === 'REJECTED'"
      title="该单据已被审批驳回。您可直接点击【重新提交审查】，系统将自动生成 V2+ 新版本快照并触发多智能体复审流水线！"
      type="error"
      show-icon
      :closable="false"
      style="margin-bottom: 4px;"
    />
    <el-alert
      v-else-if="document.status === 'CANCELLED'"
      title="该单据已被经办人主动撤回，审批流程已废止。"
      type="info"
      show-icon
      :closable="false"
      style="margin-bottom: 4px;"
    />

    <!-- 左右分栏：左侧明细与附件，右侧票据 Canvas 原件 -->
    <div class="detail-split-layout">
      <!-- 左侧：明细与附件 -->
      <div class="split-left">
        <el-card shadow="never" class="section-card">
          <template #header>
            <div class="section-header">
              <span>费用明细项 (共 {{ document.line_items?.length || 0 }} 项)</span>
            </div>
          </template>

          <el-table :data="document.line_items || []" border stripe size="small">
            <el-table-column prop="line_no" label="行号" width="60" align="center" />
            <el-table-column prop="expense_type" label="费用类别" width="100" />
            <el-table-column prop="item_desc" label="费用描述" min-width="140" show-overflow-tooltip />
            <el-table-column prop="city_name" label="城市" width="80" />
            <el-table-column prop="amount" label="金额 (元)" width="110" align="right">
              <template #default="{ row }">
                ¥{{ Number(row.amount).toFixed(2) }}
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never" class="section-card" style="margin-top: 16px;">
          <template #header>
            <div class="section-header">
              <span>关联发票原件与 OCR 状态 (共 {{ document.invoices?.length || document.attachments?.length || 0 }} 张)</span>
            </div>
          </template>

          <div v-if="document.invoices?.length" class="attachments-list">
            <div
              v-for="(inv, idx) in document.invoices"
              :key="idx"
              :class="['attachment-row', selectedInvoiceIndex === idx ? 'active-inv' : '']"
              @click="selectedInvoiceIndex = idx"
              style="cursor: pointer;"
            >
              <div class="att-info">
                <el-icon><Document /></el-icon>
                <span class="inv-name-text">{{ inv.invoice_type }} [{{ inv.invoice_number }}] - ¥{{ Number(inv.total_amount).toFixed(2) }}</span>
                <el-tag size="small" type="success">OCR 提取成功</el-tag>
              </div>
              <el-button size="small" :type="selectedInvoiceIndex === idx ? 'primary' : 'default'">
                {{ selectedInvoiceIndex === idx ? '当前预览' : '切换发票' }}
              </el-button>
            </div>
          </div>
          <div v-else-if="document.attachments?.length" class="attachments-list">
            <div
              v-for="att in document.attachments"
              :key="att.id"
              class="attachment-row"
            >
              <div class="att-info">
                <el-icon><Document /></el-icon>
                <span>{{ att.file_name }}</span>
                <el-tag size="small" type="success">OCR 已就绪</el-tag>
              </div>
              <el-button size="small" link type="primary">原件预览</el-button>
            </div>
          </div>
          <el-empty v-else description="暂无电子票据附件" :image-size="60" />
        </el-card>
      </div>

      <!-- 右侧：发票视觉原件与 BBox 证据锚点 -->
      <div class="split-right">
        <InvoiceCanvasViewer
          :invoice-data="activeInvoice"
        />
      </div>
    </div>

    <!-- 实时审查流式抽屉 -->
    <AuditStreamDrawer
      v-model="drawerVisible"
      :task-id="activeTaskId"
      :document-id="document.id"
      @completed="fetchDetail"
    />

    <!-- 历史不可变版本全量快照弹窗 -->
    <el-dialog v-model="versionDialogVisible" title="单据历史版本全量快照溯源" width="620px">
      <div v-loading="versionLoading">
        <el-timeline v-if="versionList.length">
          <el-timeline-item
            v-for="ver in versionList"
            :key="ver.id"
            :timestamp="new Date(ver.created_at).toLocaleString()"
            placement="top"
            :type="ver.version_no === document.current_version ? 'primary' : 'info'"
          >
            <el-card shadow="never" style="border-radius: 6px; border: 1px solid #e2e8f0;">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <span style="font-size: 14px; font-weight: 700; color: #1e293b;">版本 V{{ ver.version_no }} ({{ ver.trigger_action }})</span>
                <el-tag size="small" :type="ver.version_no === document.current_version ? 'success' : 'info'">
                  {{ ver.version_no === document.current_version ? '当前生效版' : '历史快照' }}
                </el-tag>
              </div>
              <div style="font-size: 13px; color: #475569; margin-bottom: 6px;">{{ ver.change_summary || '版本快照已固化' }}</div>
              <div style="font-size: 12px; color: #64748b; background: #f8fafc; padding: 6px 10px; border-radius: 4px;">
                申报总额：<strong>¥{{ Number(ver.snapshot_payload?.total_amount || 0).toFixed(2) }}</strong> |
                包含明细项：<strong>{{ ver.snapshot_payload?.line_items?.length || 0 }}</strong> 条
              </div>
            </el-card>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无历史版本记录" :image-size="60" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document } from '@element-plus/icons-vue'
import api from '@/api'
import InvoiceCanvasViewer from '@/components/InvoiceCanvasViewer.vue'
import AuditStreamDrawer from '@/components/AuditStreamDrawer.vue'

const route = useRoute()
const router = useRouter()

const document = ref({})
const loading = ref(false)
const submitting = ref(false)
const drawerVisible = ref(false)
const activeTaskId = ref('')
const selectedInvoiceIndex = ref(0)
const versionDialogVisible = ref(false)
const versionLoading = ref(false)
const versionList = ref([])

const activeInvoice = computed(() => {
  if (document.value.invoices && document.value.invoices.length > selectedInvoiceIndex.value) {
    const inv = document.value.invoices[selectedInvoiceIndex.value]
    return {
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
      file_path: inv.file_path || inv.raw_payload?.file_path || (document.value.attachments?.[selectedInvoiceIndex.value]?.file_path),
      bbox_positions: inv.bbox_positions || inv.raw_payload?.bbox_positions
    }
  }
  return {
    invoice_code: '011002000111',
    invoice_number: '23456789',
    issue_date: '2026-03-12',
    total_amount: document.value.total_amount || '1,200.00',
    untaxed_amount: document.value.total_amount ? (Number(document.value.total_amount) * 0.94).toFixed(2) : '1,128.00',
    tax_amount: document.value.total_amount ? (Number(document.value.total_amount) * 0.06).toFixed(2) : '72.00',
    seller_name: '北京神州数码技术有限公司',
    seller_tax_id: '91110108551385082Q',
    file_path: document.value.attachments?.[0]?.file_path,
    bbox_positions: null
  }
})


const fetchDetail = async () => {
  loading.value = true
  try {
    const docId = route.params.id
    const res = await api.get(`/documents/${docId}`)
    document.value = res
  } catch (err) {
    // 错误处理
  } finally {
    loading.value = false
  }
}

const handleSubmit = async () => {
  if (submitting.value) return
  submitting.value = true
  try {
    const res = await api.post(`/documents/${document.value.id}/submit`)
    ElMessage.success('提交成功，多智能体风控流水线已启动！')
    activeTaskId.value = res.task_id
    console.log(`[DocumentDetail] 提交审查: document_id=${document.value.id}, audit_version=${res.audit_version}, task_id=${res.task_id}, reused=${res.reused}`)
    drawerVisible.value = true
  } catch (err) {
    console.error('[DocumentDetail] 提交审查失败:', err)
    ElMessage.error(err.response?.data?.detail || err.message || '提交审查失败')
  } finally {
    submitting.value = false
  }
}

const handleCancel = async () => {
  try {
    const { value: reason } = await ElMessageBox.prompt(
      '单据撤回后将终止流转，关联审批待办将自动失效。请输入撤回原因：',
      '撤回单据确认',
      {
        confirmButtonText: '确认撤回',
        cancelButtonText: '取消',
        inputPlaceholder: '例如：发票信息填报有误，需核实后重新录入',
        inputValue: '经办人主动撤回调整'
      }
    )
    await api.post(`/documents/${document.value.id}/cancel`, { reason: reason || '经办人主动撤回调整' })
    ElMessage.success('单据已成功撤回！')
    fetchDetail()
  } catch (action) {
    // 用户取消操作
  }
}

const openVersionModal = async () => {
  versionDialogVisible.value = true
  versionLoading.value = true
  try {
    const res = await api.get(`/documents/${document.value.id}/versions`)
    versionList.value = res || []
  } catch (err) {
    ElMessage.error('获取历史版本快照失败')
  } finally {
    versionLoading.value = false
  }
}

const formatDocType = (t) => {
  const map = {
    TRAVEL_REIMBURSEMENT: '差旅报销单',
    EXPENSE_REIMBURSEMENT: '费用报销单',
    CORP_PAYMENT: '对公付款单',
    ADVANCE_PAYMENT: '预付款单',
    BATCH_PAYMENT: '批量付款单'
  }
  return map[t] || t || '-'
}

const formatStatus = (s) => {
  const map = {
    DRAFT: '草稿中',
    SUBMITTED: '已提交审查',
    IN_REVIEW: 'AI审查中',
    PENDING_APPROVAL: '审批流转中',
    APPROVED: '终审通过',
    REJECTED: '已被驳回',
    CANCELLED: '经办人撤回'
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
    REJECTED: 'danger'
  }
  return map[s] || ''
}

onMounted(() => {
  fetchDetail()
})
</script>

<style scoped>
.doc-detail-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.detail-header-card {
  background: #ffffff;
  padding: 18px 24px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.doc-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.doc-no-badge {
  font-family: monospace;
  font-size: 13px;
  background: #f1f5f9;
  padding: 2px 8px;
  border-radius: 4px;
  color: #475569;
}

.doc-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: #0f172a;
}

.doc-meta-row {
  display: flex;
  gap: 20px;
  font-size: 13px;
  color: #64748b;
}

.text-danger {
  color: #ef4444;
}

.detail-split-layout {
  display: flex;
  gap: 16px;
  height: calc(100vh - 200px);
}

.split-left {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.split-right {
  flex: 1.1;
  height: 100%;
}

.section-header {
  font-weight: 600;
  font-size: 14px;
}

.attachments-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.attachment-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: #f8fafc;
  border-radius: 6px;
  border: 1px solid #e2e8f0;
  transition: all 0.2s ease;
}

.attachment-row.active-inv {
  background: #eff6ff;
  border-color: #3b82f6;
  box-shadow: 0 1px 3px rgba(59, 130, 246, 0.15);
}

.inv-name-text {
  font-weight: 500;
  color: #1e293b;
}

.att-info {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
</style>

