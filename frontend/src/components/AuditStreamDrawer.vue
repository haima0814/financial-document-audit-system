<template>
  <el-drawer
    v-model="visible"
    title="⚡ 多智能体审查流水线实时轨迹 (SSE)"
    size="520px"
    :before-close="handleClose"
    direction="rtl"
  >
    <div class="stream-container">
      <!-- 顶部状态与进度条 -->
      <div class="stream-header">
        <div class="status-badge">
          <el-tag :type="statusTagType" effect="dark">
            {{ statusText }}
          </el-tag>
          <span class="task-id-text">Task: {{ taskId || '-' }}</span>
        </div>
        <el-progress :percentage="progress" :status="isCompleted ? 'success' : (isFailed ? 'exception' : '')" :stroke-width="8" />
      </div>

      <!-- 实时事件时间线 (Element Plus el-timeline) -->
      <div class="timeline-container" ref="timelineRef">
        <el-timeline v-if="eventTimeline.length">
          <el-timeline-item
            v-for="(item, idx) in eventTimeline"
            :key="idx"
            :type="item.type"
            :color="item.color"
            :timestamp="item.timestamp"
            placement="top"
          >
            <div class="timeline-card">
              <div class="timeline-title">
                <el-tag v-if="item.tag" :type="item.type" size="small" effect="plain" class="event-tag">
                  {{ item.tag }}
                </el-tag>
                <strong>{{ item.title }}</strong>
              </div>
              <div class="timeline-content">{{ item.content }}</div>
            </div>
          </el-timeline-item>
        </el-timeline>

        <div v-if="!isCompleted && !isFailed" class="stream-waiting">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>多智能体协同分析中，实时事件推送中...</span>
        </div>

        <el-empty
          v-if="!eventTimeline.length && (isCompleted || isFailed)"
          description="暂无审查事件记录"
          :image-size="60"
        />
      </div>

      <!-- 审查完成汇总与跳转 -->
      <div v-if="isCompleted" class="stream-footer">
        <div class="score-summary">
          <span>风控评分：</span>
          <strong :class="getScoreClass(summary.risk_score)">{{ summary.risk_score ?? 100 }} 分</strong>
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
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Loading } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: Boolean,
  taskId: String,
  documentId: [Number, String]
})

const emit = defineEmits(['update:modelValue', 'completed'])
const router = useRouter()

const visible = ref(props.modelValue)
const progress = ref(5)
const isCompleted = ref(false)
const isFailed = ref(false)
const eventTimeline = ref([])
const summary = ref({})
const timelineRef = ref(null)

let abortController = null

const statusText = computed(() => {
  if (isCompleted.value) return '审查完成'
  if (isFailed.value) return '审查异常中断'
  return '多智能体实时审查中 (SSE)'
})

const statusTagType = computed(() => {
  if (isCompleted.value) return 'success'
  if (isFailed.value) return 'danger'
  return 'primary'
})

watch(() => props.modelValue, (val) => {
  visible.value = val
  if (val && props.taskId) {
    initPipeline(props.taskId)
  } else {
    cleanup()
  }
})

const cleanup = () => {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
}

const handleClose = () => {
  cleanup()
  emit('update:modelValue', false)
}

onUnmounted(() => {
  cleanup()
})

const initPipeline = async (tid) => {
  cleanup()
  eventTimeline.value = []
  progress.value = 10
  isCompleted.value = false
  isFailed.value = false
  summary.value = {}

  if (!tid) return

  abortController = new AbortController()
  const token = localStorage.getItem('token')

  try {
    const response = await fetch(`/api/v1/audits/events/${tid}`, {
      headers: {
        ...(token ? { 'Authorization': `Bearer ${token}` } : {})
      },
      signal: abortController.signal
    })

    if (!response.ok) {
      if (response.status === 401) {
        isFailed.value = true
        eventTimeline.value.push({
          timestamp: new Date().toLocaleTimeString(),
          tag: 'AUTH_FAILED',
          title: '认证鉴权失败',
          content: '登录态失效或无权查看该任务的审核事件流',
          type: 'danger'
        })
        return
      }
      throw new Error(`SSE 连接失败 (HTTP ${response.status})`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      let currentEvent = 'message'
      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed || trimmed.startsWith(':')) {
          // 保活心跳 ping
          continue
        }
        if (trimmed.startsWith('event:')) {
          currentEvent = trimmed.substring(6).trim()
        } else if (trimmed.startsWith('data:')) {
          const dataStr = trimmed.substring(5).trim()
          handleEventMessage(currentEvent, dataStr)
        }
      }
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      return
    }
    console.warn('[SSE] Event stream interrupted:', err)
    if (!isCompleted.value) {
      isFailed.value = true
      eventTimeline.value.push({
        timestamp: new Date().toLocaleTimeString(),
        tag: 'DISCONNECT',
        title: '事件通道中断',
        content: err.message || '网络连接异常中断',
        type: 'warning'
      })
    }
  }
}

const handleEventMessage = (eventName, dataStr) => {
  try {
    const envelope = JSON.parse(dataStr)
    const timeStr = envelope.timestamp
      ? new Date(envelope.timestamp).toLocaleTimeString()
      : new Date().toLocaleTimeString()
    const payload = envelope.data || envelope

    const ev = (eventName || envelope.event || '').toLowerCase()

    if (ev === 'task_started') {
      progress.value = 25
      eventTimeline.value.push({
        timestamp: timeStr,
        tag: 'STAGE_1',
        title: '单据事实与凭证摄入完成',
        content: `摄入明细 ${payload.items_count || 0} 项，关联发票 ${payload.invoices_count || 0} 张`,
        type: 'primary'
      })
    } else if (ev === 'node_status') {
      const agent = payload.agent_name || payload.role || 'Agent'
      const status = payload.status || 'SUCCESS'
      const elapsed = payload.elapsed_ms ?? payload.duration_ms ?? 0
      const isSuccess = status === 'SUCCESS'
      const isDegraded = status === 'DEGRADED'

      progress.value = Math.min(progress.value + 10, 75)
      eventTimeline.value.push({
        timestamp: timeStr,
        tag: 'NODE',
        title: `${agent} 核验节点`,
        content: `状态: ${status} (${elapsed}ms) | 来源: ${payload.source || 'DETERMINISTIC'}${payload.reason ? ' - ' + payload.reason : ''}`,
        type: isSuccess ? 'success' : (isDegraded ? 'warning' : 'danger')
      })
    } else if (ev === 'task_progress') {
      const stage = payload.stage || payload.current_stage || ''
      const count = payload.findings_count ?? payload.findings_found ?? 0
      progress.value = Math.max(progress.value, 70)

      eventTimeline.value.push({
        timestamp: timeStr,
        tag: 'PROGRESS',
        title: 'Stage 2: 多智能体并行核查完成',
        content: `并行核查完成，发现候选风险项 ${count} 条`,
        type: 'primary'
      })
    } else if (ev === 'review_reflect') {
      progress.value = 85
      const applied = payload.reflection_applied
      const verified = payload.verified_count ?? 0

      eventTimeline.value.push({
        timestamp: timeStr,
        tag: 'STAGE_3',
        title: 'Stage 3: 终审门禁反思消歧 (ReviewerReflector)',
        content: applied
          ? `已执行反思消歧，最终确认有效风险项 ${verified} 条`
          : '终审门禁复核通过，未触发消歧',
        type: applied ? 'warning' : 'success'
      })
    } else if (ev === 'task_completed') {
      progress.value = 100
      isCompleted.value = true
      summary.value = payload

      eventTimeline.value.push({
        timestamp: timeStr,
        tag: 'STAGE_4',
        title: 'Stage 4: 风控体检报告生成归档',
        content: `审核完毕！综合评分: ${payload.risk_score ?? payload.final_score ?? 100} 分，风险等级: ${(payload.overall_risk_level || 'LOW').toUpperCase()}`,
        type: 'success'
      })

      emit('completed', payload)
    } else if (ev === 'task_failed') {
      isFailed.value = true
      eventTimeline.value.push({
        timestamp: timeStr,
        tag: 'ERROR',
        title: '审核流水线执行失败',
        content: payload.error_message || '任务异常中止',
        type: 'danger'
      })
    }

    nextTick(() => {
      if (timelineRef.value) {
        timelineRef.value.scrollTop = timelineRef.value.scrollHeight
      }
    })
  } catch (e) {
    console.error('[SSE] Failed to parse event envelope:', e, dataStr)
  }
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

.timeline-container {
  flex: 1;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 16px;
  overflow-y: auto;
}

.timeline-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 8px 12px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}

.timeline-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #1e293b;
  margin-bottom: 4px;
}

.event-tag {
  font-size: 11px;
}

.timeline-content {
  font-size: 12px;
  color: #64748b;
  line-height: 1.5;
  word-break: break-all;
}

.stream-waiting {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #3b82f6;
  font-size: 12px;
  padding: 12px 0;
  justify-content: center;
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
