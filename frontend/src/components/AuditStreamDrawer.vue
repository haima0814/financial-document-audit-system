<template>
  <el-drawer
    v-model="visible"
    title="⚡ 多智能体实时风控审查流水线"
    size="520px"
    :before-close="handleClose"
    direction="rtl"
  >
    <div class="stream-container">
      <!-- 顶部状态与进度条 -->
      <div class="stream-header">
        <div class="status-badge">
          <el-tag :type="isCompleted ? 'success' : 'primary'" effect="dark">
            {{ isCompleted ? '审查完成' : '多智能体并行推理中...' }}
          </el-tag>
          <span class="task-id-text">Task: {{ taskId }}</span>
        </div>
        <el-progress :percentage="progress" :status="isCompleted ? 'success' : ''" :stroke-width="10" />
      </div>

      <!-- 四阶段流水线状态指示卡片 -->
      <div class="stages-grid">
        <div :class="['stage-card', getStageStatus('STAGE_1')]">
          <div class="stage-num">1</div>
          <div class="stage-name">票据事实摄入</div>
        </div>
        <div :class="['stage-card', getStageStatus('STAGE_2')]">
          <div class="stage-num">2</div>
          <div class="stage-name">4大Agent并行审查</div>
        </div>
        <div :class="['stage-card', getStageStatus('STAGE_3')]">
          <div class="stage-num">3</div>
          <div class="stage-name">终审消歧门禁</div>
        </div>
        <div :class="['stage-card', getStageStatus('STAGE_4')]">
          <div class="stage-num">4</div>
          <div class="stage-name">综合体检报告</div>
        </div>
      </div>

      <!-- 实时流式事件日志输出 -->
      <div class="logs-wrapper" ref="logsRef">
        <div v-for="(log, idx) in eventLogs" :key="idx" class="log-item">
          <span class="log-time">{{ log.time }}</span>
          <el-tag size="small" :type="getTagType(log.event)">{{ log.event }}</el-tag>
          <div class="log-content">{{ log.message }}</div>
        </div>
      </div>

      <!-- 审查完成汇总与跳转 -->
      <div v-if="isCompleted" class="stream-footer">
        <div class="score-summary">
          <span>综合风控评分：</span>
          <strong :class="getScoreClass(summary.risk_score)">{{ summary.risk_score || 100 }} 分</strong>
          <el-tag :type="summary.overall_risk_level === 'high' ? 'danger' : (summary.overall_risk_level === 'medium' ? 'warning' : 'success')">
            {{ (summary.overall_risk_level || 'low').toUpperCase() }} 风险
          </el-tag>
        </div>
        <el-button type="primary" class="w-full" @click="goToReport">
          查看完整风控体检报告
        </el-button>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref, watch, nextTick, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api'

const props = defineProps({
  modelValue: Boolean,
  taskId: String,
  documentId: [Number, String]
})

const emit = defineEmits(['update:modelValue', 'completed'])
const router = useRouter()

const visible = ref(props.modelValue)
const progress = ref(10)
const isCompleted = ref(false)
const eventLogs = ref([])
const summary = ref({})
const currentStage = ref('STAGE_1')
const logsRef = ref(null)
let ws = null
let pollTimer = null

watch(() => props.modelValue, (val) => {
  visible.value = val
  if (val && (props.taskId || props.documentId)) {
    initPipeline(props.taskId)
  } else {
    cleanup()
  }
})

const initPipeline = (tid) => {
  eventLogs.value = []
  progress.value = 15
  isCompleted.value = false
  currentStage.value = 'STAGE_1'

  // 1. 尝试连接 WebSocket
  if (tid) {
    initWebSocket(tid)
  }

  // 2. 启动智能容灾轮询 (即使 WebSocket 出现网络抖动/重连，也能毫秒级捕获体检报告)
  startPolling()
}

const initWebSocket = (tid) => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${window.location.host}/ws/audit/${tid}`

  try {
    ws = new WebSocket(wsUrl)
    ws.onopen = () => {
      console.log('[WS Connected] 审计事件总线已连接:', wsUrl)
    }
    ws.onmessage = (evt) => {
      try {
        const envelope = JSON.parse(evt.data)
        if (envelope.event === 'PING') return

        const timeStr = new Date().toLocaleTimeString()
        const payload = envelope.data || {}
        let msg = JSON.stringify(payload)

        if (envelope.event === 'TASK_STARTED') {
          msg = `已加载单据明细 ${payload.items_count || 0} 项，关联发票 ${payload.invoices_count || 0} 张`
          currentStage.value = 'STAGE_2'
          if (progress.value < 35) progress.value = 35
        } else if (envelope.event === 'TASK_PROGRESS') {
          if (payload.stage === 'STAGE_2_PARALLEL_DONE') {
            msg = `多智能体并行分析完毕，初步检出风险项 ${payload.findings_count} 条`
            currentStage.value = 'STAGE_3'
            if (progress.value < 75) progress.value = 75
          } else if (payload.stage === 'STAGE_3_REVIEW_DONE') {
            msg = `风控门禁质检消歧完成，确认有效证据链 ${payload.verified_count} 条`
            currentStage.value = 'STAGE_4'
            if (progress.value < 90) progress.value = 90
          }
        } else if (envelope.event === 'TASK_COMPLETED') {
          msg = `体检报告生成完成！综合风险等级: ${(payload.overall_risk_level || 'LOW').toUpperCase()}，评分: ${payload.risk_score} 分`
          progress.value = 100
          isCompleted.value = true
          summary.value = payload
          stopPolling()
          emit('completed', payload)
        }

        eventLogs.value.push({ time: timeStr, event: envelope.event, message: msg })
        nextTick(() => {
          if (logsRef.value) logsRef.value.scrollTop = logsRef.value.scrollHeight
        })
      } catch (e) {
        console.error('WS parse error:', e)
      }
    }

    ws.onerror = (err) => {
      console.warn('WebSocket connect error, fallback polling active:', err)
    }

    ws.onclose = () => {
      console.log('WebSocket closed.')
    }
  } catch (err) {
    console.warn('WebSocket connect failed, using fallback polling:', err)
  }
}

const startPolling = () => {
  stopPolling()
  let pollCount = 0

  pollTimer = setInterval(async () => {
    if (isCompleted.value) {
      stopPolling()
      return
    }

    pollCount++
    // 进度条平滑过渡提升体验
    if (progress.value < 35) {
      progress.value = 35
      currentStage.value = 'STAGE_2'
    } else if (progress.value < 75 && pollCount >= 2) {
      progress.value = 75
      currentStage.value = 'STAGE_3'
    } else if (progress.value < 90 && pollCount >= 3) {
      progress.value = 90
      currentStage.value = 'STAGE_4'
    }

    // 检查是否已有完成的风控体检报告
    if (props.documentId) {
      try {
        const rep = await api.get(`/audits/reports/${props.documentId}`)
        if (rep && rep.id && !isCompleted.value) {
          isCompleted.value = true
          progress.value = 100
          currentStage.value = 'STAGE_4'
          summary.value = {
            report_id: rep.id,
            overall_risk_level: rep.overall_risk_level,
            risk_score: rep.final_score,
            high_count: rep.high_risks_count,
            medium_count: rep.medium_risks_count,
            low_count: rep.low_risks_count
          }
          const timeStr = new Date().toLocaleTimeString()
          eventLogs.value.push({
            time: timeStr,
            event: 'TASK_COMPLETED',
            message: `智能风控体检完成！综合风险等级: ${(rep.overall_risk_level || 'LOW').toUpperCase()}，评分: ${rep.final_score} 分`
          })
          stopPolling()
          emit('completed', summary.value)
        }
      } catch (e) {
        // 报告仍在生成中，属于正常等待
      }
    }

    // 最多轮询 20 次 (约 16 秒) 防止僵尸定时器
    if (pollCount > 20) {
      stopPolling()
    }
  }, 800)
}

const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const cleanup = () => {
  if (ws) {
    ws.close()
    ws = null
  }
  stopPolling()
}

const handleClose = () => {
  cleanup()
  emit('update:modelValue', false)
}

onUnmounted(() => {
  cleanup()
})


const getStageStatus = (stage) => {
  const order = ['STAGE_1', 'STAGE_2', 'STAGE_3', 'STAGE_4']
  const curIdx = order.indexOf(currentStage.value)
  const targetIdx = order.indexOf(stage)
  if (isCompleted.value || curIdx > targetIdx) return 'done'
  if (curIdx === targetIdx) return 'active'
  return 'pending'
}

const getTagType = (ev) => {
  if (ev === 'TASK_COMPLETED') return 'success'
  if (ev === 'TASK_STARTED') return 'primary'
  return 'info'
}

const getScoreClass = (score) => {
  if (score >= 90) return 'text-green'
  if (score >= 60) return 'text-yellow'
  return 'text-red'
}

const goToReport = () => {
  handleClose()
  router.push(`/audits/${props.documentId}`)
}
</script>

<style scoped>
.stream-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 16px;
}

.stream-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.status-badge {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.task-id-text {
  font-size: 12px;
  color: #64748b;
  font-family: monospace;
}

.stages-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}

.stage-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 4px;
  border-radius: 6px;
  background: #f1f5f9;
  font-size: 11px;
  text-align: center;
  border: 1px solid #e2e8f0;
  transition: all 0.3s;
}

.stage-card.active {
  background: #e0f2fe;
  border-color: #38bdf8;
  color: #0369a1;
  font-weight: bold;
}

.stage-card.done {
  background: #f0fdf4;
  border-color: #86efac;
  color: #15803d;
}

.stage-num {
  font-size: 14px;
  font-weight: 800;
  margin-bottom: 2px;
}

.logs-wrapper {
  flex: 1;
  background: #0f172a;
  border-radius: 8px;
  padding: 12px;
  overflow-y: auto;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}

.log-item {
  margin-bottom: 8px;
  line-height: 1.5;
}

.log-time {
  color: #64748b;
  margin-right: 8px;
}

.log-content {
  color: #cbd5e1;
  margin-top: 2px;
  word-break: break-all;
}

.stream-footer {
  padding-top: 12px;
  border-top: 1px solid #e2e8f0;
}

.score-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  font-size: 14px;
}

.text-green { color: #16a34a; font-size: 20px; }
.text-yellow { color: #d97706; font-size: 20px; }
.text-red { color: #dc2626; font-size: 20px; }
.w-full { width: 100%; }
</style>
