<template>
  <div class="doc-list-page">
    <!-- 顶部工作台权限范围提示条 -->
    <div class="role-scope-banner">
      <el-alert
        v-if="authStore.isAdmin"
        title="系统管理员视角：全局运维监控视图，呈现全系统全量单据。"
        type="success"
        :closable="false"
        show-icon
      />
      <el-alert
        v-else-if="authStore.isCFO"
        title="财务总监视角：已启用风控特批过滤，仅呈现申报金额 ≥ ¥10,000 元或判定为高危红线待特批的单据。"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-alert
        v-else-if="authStore.isFinance"
        title="财务复核视角：呈现全公司所有已提交流转中的业务单据（他人私人草稿箱已自动隔离）。"
        type="info"
        :closable="false"
        show-icon
      />
      <el-alert
        v-else-if="authStore.isManager"
        title="部门主管视角：呈现本部门员工申报的在审单据及您本人经办的单据。"
        type="info"
        :closable="false"
        show-icon
      />
      <el-alert
        v-else
        title="经办员工视角：个人数据隐私保护生效中，当前仅显示您本人创建与提交的单据。"
        type="info"
        :closable="false"
        show-icon
      />
    </div>

    <div class="action-bar">
      <div class="filter-group">
        <el-radio-group v-model="selectedStatus" @change="fetchDocuments" size="default">
          <el-radio-button label="">全部单据</el-radio-button>
          <el-radio-button label="DRAFT">草稿箱</el-radio-button>
          <el-radio-button label="PENDING_APPROVAL">待人工审批</el-radio-button>
          <el-radio-button label="APPROVED">已办结通过</el-radio-button>
          <el-radio-button label="REJECTED">已打回修改</el-radio-button>
          <el-radio-button label="CANCELLED">已撤回</el-radio-button>
        </el-radio-group>
      </div>

      <div class="btn-group">
        <el-button type="primary" icon="Plus" @click="$router.push('/documents/create')">
          新建报销/付款单
        </el-button>
        <el-button icon="Refresh" @click="fetchDocuments" :loading="loading">
          刷新
        </el-button>
      </div>
    </div>

    <!-- 单据数据表格 -->
    <el-card shadow="never" class="table-card">
      <el-table :data="documents" v-loading="loading" stripe style="width: 100%">
        <el-table-column prop="document_no" label="单据编号" width="180">
          <template #default="{ row }">
            <el-link type="primary" @click="viewDetail(row.id)">{{ row.document_no }}</el-link>
          </template>
        </el-table-column>

        <el-table-column label="经办人 / 部门" width="160">
          <template #default="{ row }">
            <div class="applicant-cell">
              <span class="applicant-name">{{ row.applicant_name || '经办人' }}</span>
              <el-tag size="small" type="info" effect="plain">{{ row.department_name || '未填' }}</el-tag>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="document_type" label="单据类型" width="140">
          <template #default="{ row }">
            <el-tag size="small">{{ formatDocType(row.document_type) }}</el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="title" label="申报事由 / 标题" min-width="200" show-overflow-tooltip />


        <el-table-column prop="total_amount" label="申报总金额" width="140" align="right">
          <template #default="{ row }">
            <span class="amount-text">¥{{ Number(row.total_amount).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="status" label="宏观流转状态" width="140" align="center">
          <template #default="{ row }">
            <el-tag :type="getStatusTagType(row.status)" effect="light">
              {{ formatStatus(row.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>

        <el-table-column label="操作" width="260" fixed="right" align="center">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="viewDetail(row.id)">详情</el-button>

            <!-- 1. 草稿状态：提交审查 -->
            <el-button
              v-if="row.status === 'DRAFT'"
              size="small"
              type="primary"
              :loading="submittingId === row.id"
              :disabled="submittingId !== null"
              @click="handleSubmit(row)"
            >
              提交审查
            </el-button>

            <!-- 2. 已提交审查 / 审核中状态：查看审查进度 (打开 SSE 抽屉，禁止调用 submit) -->
            <el-button
              v-else-if="row.status === 'SUBMITTED' || row.status === 'IN_REVIEW'"
              size="small"
              type="primary"
              plain
              :loading="progressLoadingId === row.id"
              @click="viewAuditProgress(row)"
            >
              查看审查进度
            </el-button>

            <!-- 3. 已完成阶段状态：查看体检报告 -->
            <template v-else-if="['PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'NEED_SUPPLEMENT'].includes(row.status)">
              <el-button
                size="small"
                :type="row.status === 'REJECTED' ? 'danger' : 'success'"
                plain
                @click="viewAuditReport(row.id)"
              >
                {{ row.status === 'REJECTED' ? '体检报告(已驳回)' : (row.status === 'NEED_SUPPLEMENT' ? '体检报告(待补充)' : '查看体检报告') }}
              </el-button>
              <el-button
                v-if="row.status === 'REJECTED' || row.status === 'NEED_SUPPLEMENT'"
                size="small"
                type="warning"
                plain
                :loading="submittingId === row.id"
                :disabled="submittingId !== null"
                @click="handleSubmit(row)"
              >
                重新提交
              </el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-bar">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="fetchDocuments"
        />
      </div>
    </el-card>

    <!-- 实时审查流式抽屉 -->
    <AuditStreamDrawer
      v-model="drawerVisible"
      :task-id="activeTaskId"
      :document-id="activeDocId"
      @completed="handleAuditCompleted"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '@/api'
import { useAuthStore } from '@/stores/auth'
import AuditStreamDrawer from '@/components/AuditStreamDrawer.vue'

const router = useRouter()
const authStore = useAuthStore()

const documents = ref([])

const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(10)
const selectedStatus = ref('')
const loading = ref(false)

// 审查抽屉
const drawerVisible = ref(false)
const activeTaskId = ref('')
const activeDocId = ref(null)
const submittingId = ref(null)
const progressLoadingId = ref(null)

const fetchDocuments = async () => {
  loading.value = true
  try {
    const res = await api.get('/documents', {
      params: {
        status: selectedStatus.value || undefined,
        page: currentPage.value,
        page_size: pageSize.value
      }
    })
    documents.value = res.items
    total.value = res.total
  } catch (err) {
    // 错误处理
  } finally {
    loading.value = false
  }
}

const handleSubmit = async (row) => {
  if (submittingId.value !== null) return
  submittingId.value = row.id
  try {
    const res = await api.post(`/documents/${row.id}/submit`)
    ElMessage.success('提交成功，多智能体风控流水线已启动！')
    activeTaskId.value = res.task_id
    activeDocId.value = row.id
    console.log(`[DocumentList] 提交审查: document_id=${row.id}, audit_version=${res.audit_version}, task_id=${res.task_id}, reused=${res.reused}`)
    drawerVisible.value = true
    fetchDocuments()
  } catch (err) {
    console.error('[DocumentList] 提交审查失败:', err)
    ElMessage.error(err.response?.data?.detail || err.message || '提交审查失败')
  } finally {
    submittingId.value = null
  }
}

const viewAuditProgress = async (row) => {
  if (progressLoadingId.value !== null) return
  progressLoadingId.value = row.id
  try {
    const res = await api.get(`/audits/tasks/by-document/${row.id}/latest`)
    if (res && res.task_id) {
      activeTaskId.value = res.task_id
      activeDocId.value = row.id
      drawerVisible.value = true
    } else {
      ElMessage.warning('未查询到当前版本的有效审查任务')
    }
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message || '获取审核任务进度失败')
  } finally {
    progressLoadingId.value = null
  }
}

const viewDetail = (docId) => {
  router.push(`/documents/${docId}`)
}

const viewAuditReport = (docId) => {
  router.push(`/audits/${docId}`)
}

const handleAuditCompleted = () => {
  fetchDocuments()
}

const formatDocType = (t) => {
  const map = {
    TRAVEL_REIMBURSEMENT: '差旅报销单',
    EXPENSE_REIMBURSEMENT: '费用报销单',
    CORP_PAYMENT: '对公付款单',
    ADVANCE_PAYMENT: '预付款单',
    BATCH_PAYMENT: '批量付款单'
  }
  return map[t] || t
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
  return map[s] || s
}

const getStatusTagType = (s) => {
  const map = {
    DRAFT: 'info',
    SUBMITTED: 'primary',
    IN_REVIEW: 'primary',
    PENDING_APPROVAL: 'warning',
    APPROVED: 'success',
    REJECTED: 'danger',
    CANCELLED: 'info'
  }
  return map[s] || ''
}

const formatTime = (ts) => {
  if (!ts) return '-'
  return new Date(ts).toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  fetchDocuments()
})
</script>

<style scoped>
.doc-list-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.role-scope-banner {
  margin-bottom: 4px;
}

.applicant-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.applicant-name {
  font-weight: 600;
  color: #1e293b;
  font-size: 13px;
}

.action-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #ffffff;
  padding: 16px 20px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
}


.btn-group {
  display: flex;
  gap: 10px;
}

.amount-text {
  font-weight: 700;
  color: #0f172a;
  font-family: monospace;
  font-size: 14px;
}

.pagination-bar {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
