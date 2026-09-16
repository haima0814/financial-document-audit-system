<template>
  <div class="approval-center-page">
    <el-card shadow="never" class="main-card">
      <template #header>
        <div class="card-header">
          <div class="header-title">
            <el-icon><Stamp /></el-icon>
            <span>我的待办审批工作台</span>
          </div>
          <el-button icon="Refresh" size="small" @click="fetchPendingTasks" :loading="loading">
            刷新待办
          </el-button>
        </div>
      </template>

      <!-- 待办任务列表 -->
      <el-table :data="tasks" v-loading="loading" stripe style="width: 100%">
        <el-table-column prop="task_id" label="任务ID" width="90" align="center" />
        <el-table-column prop="document_no" label="单据编号" width="180">
          <template #default="{ row }">
            <el-link type="primary" @click="$router.push(`/documents/${row.document_id}`)">
              {{ row.document_no }}
            </el-link>
          </template>
        </el-table-column>

        <el-table-column prop="node_name" label="当前审批节点" width="160">
          <template #default="{ row }">
            <el-tag effect="plain">{{ row.node_name }}</el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="title" label="单据事由" min-width="200" show-overflow-tooltip />

        <el-table-column prop="total_amount" label="申报总金额" width="140" align="right">
          <template #default="{ row }">
            <span class="amount-text">¥{{ Number(row.total_amount).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="created_at" label="任务派发时间" width="180">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>

        <el-table-column label="操作" width="220" fixed="right" align="center">
          <template #default="{ row }">
            <el-button size="small" type="success" plain @click="$router.push(`/audits/${row.document_id}`)">
              查看风控报告
            </el-button>
            <el-button size="small" type="primary" @click="openActionModal(row)">
              办理审批
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!tasks.length && !loading" description="暂无待办审批任务，工作轻松愉快！" :image-size="80" />
    </el-card>

    <!-- 审批办理弹窗 -->
    <el-dialog
      v-model="actionModalVisible"
      :title="`办理单据审批：${currentTask?.document_no || ''}`"
      width="560px"
      destroy-on-close
    >
      <el-form :model="actionForm" label-width="110px">
        <el-form-item label="审批动作" required>
          <el-radio-group v-model="actionForm.action">
            <el-radio-button label="APPROVE">同意通过</el-radio-button>
            <el-radio-button label="REJECT">驳回打回</el-radio-button>
            <el-radio-button label="TRANSFER">转交他人</el-radio-button>
            <el-radio-button label="ADD_SIGN">征询加签</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <!-- 转交人设置 -->
        <el-form-item v-if="actionForm.action === 'TRANSFER'" label="目标转交人" required>
          <el-select v-model="actionForm.target_user_id" placeholder="请选择转交人" style="width: 100%">
            <el-option label="张经理 (用户ID: 2)" :value="2" />
            <el-option label="李财务 (用户ID: 3)" :value="3" />
            <el-option label="王总监 (用户ID: 4)" :value="4" />
          </el-select>
        </el-form-item>

        <!-- 加签类型与加签人 -->
        <template v-if="actionForm.action === 'ADD_SIGN'">
          <el-form-item label="加签模式" required>
            <el-radio-group v-model="actionForm.add_sign_type">
              <el-radio label="BEFORE">前置加签 (对方先审，再回给我)</el-radio>
              <el-radio label="AFTER">后置加签 (我先通过，再送对方审)</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-form-item label="目标加签人" required>
            <el-select v-model="actionForm.target_user_id" placeholder="请选择加签人" style="width: 100%">
              <el-option label="李财务 (用户ID: 3)" :value="3" />
              <el-option label="王总监 (用户ID: 4)" :value="4" />
            </el-select>
          </el-form-item>
        </template>

        <!-- 审批意见 -->
        <el-form-item label="审批批注意见" required>
          <el-input
            v-model="actionForm.comment"
            type="textarea"
            :rows="3"
            placeholder="请填写详细审批意见或说明"
          />
        </el-form-item>

        <!-- 高危红线放行特批理由 -->
        <el-alert
          v-if="actionForm.action === 'APPROVE'"
          title="⚠️ 高危风控强行特批提示"
          type="warning"
          description="若该单据存在多 Agent 检出的高危红线，系统强制要求填写特批理由 (override_reason) 留痕入库！"
          show-icon
          :closable="false"
          style="margin-bottom: 16px;"
        />

        <el-form-item
          v-if="actionForm.action === 'APPROVE'"
          label="特批放行理由"
        >
          <el-input
            v-model="actionForm.override_reason"
            type="textarea"
            :rows="2"
            placeholder="如有高危风险项，请务必具名填写特批放行说明"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <span class="dialog-footer">
          <el-button @click="actionModalVisible = false">取消</el-button>
          <el-button type="primary" :loading="submitting" @click="submitAction">
            确认提交处理
          </el-button>
        </span>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Stamp, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '@/api'

const tasks = ref([])
const loading = ref(false)
const submitting = ref(false)
const actionModalVisible = ref(false)
const currentTask = ref(null)

const actionForm = reactive({
  action: 'APPROVE',
  comment: '同意通过，核验无误',
  target_user_id: null,
  add_sign_type: 'BEFORE',
  override_reason: ''
})

const fetchPendingTasks = async () => {
  loading.value = true
  try {
    const res = await api.get('/approvals/tasks/pending')
    tasks.value = res.tasks || []
  } catch (err) {
    // 错误处理
  } finally {
    loading.value = false
  }
}

const openActionModal = (task) => {
  currentTask.value = task
  actionForm.action = 'APPROVE'
  actionForm.comment = '同意通过，核验无误'
  actionForm.target_user_id = null
  actionForm.override_reason = ''
  actionModalVisible.value = true
}

const submitAction = async () => {
  if (!actionForm.comment) {
    ElMessage.warning('请填写审批意见')
    return
  }

  submitting.value = true
  try {
    await api.post(`/approvals/tasks/${currentTask.value.task_id}/action`, {
      action: actionForm.action,
      comment: actionForm.comment,
      target_user_id: actionForm.target_user_id,
      add_sign_type: actionForm.action === 'ADD_SIGN' ? actionForm.add_sign_type : null,
      override_reason: actionForm.override_reason || null
    })

    ElMessage.success('审批处理成功！')
    actionModalVisible.value = false
    fetchPendingTasks()
  } catch (err) {
    // 错误处理
  } finally {
    submitting.value = false
  }
}

const formatTime = (ts) => {
  if (!ts) return '-'
  return new Date(ts).toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  fetchPendingTasks()
})
</script>

<style scoped>
.approval-center-page {
  display: flex;
  flex-direction: column;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
}

.amount-text {
  font-weight: 700;
  color: #0f172a;
  font-family: monospace;
}
</style>
