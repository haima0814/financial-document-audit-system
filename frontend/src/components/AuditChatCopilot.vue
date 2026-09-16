<template>
  <div class="audit-chat-container">
    <div class="chat-header">
      <div class="header-left">
        <el-icon class="ai-icon"><ChatDotRound /></el-icon>
        <span class="header-title">智能风控 AI 审查问答 Copilot</span>
      </div>
      <el-tag size="small" type="success" effect="plain">大模型上下文已对齐</el-tag>
    </div>

    <!-- 消息对话流 -->
    <div class="chat-messages" ref="msgListRef">
      <div v-for="(msg, idx) in messages" :key="idx" :class="['message-row', msg.role]">
        <div class="avatar-col">
          <el-avatar :size="32" :icon="msg.role === 'assistant' ? 'Service' : 'User'" :class="msg.role" />
        </div>
        <div class="content-col">
          <div class="bubble">
            <div class="bubble-text" style="white-space: pre-wrap;">{{ msg.content }}</div>
            <!-- 引用依据卡片 -->
            <div v-if="msg.citations && msg.citations.length" class="citations-box">
              <div class="citation-title">📌 关联依据与视觉锚点：</div>
              <div
                v-for="(c, cIdx) in msg.citations"
                :key="cIdx"
                class="citation-pill"
                @click="emitCitationClick(c)"
              >
                <span>[{{ c.rule_code }}] {{ c.title }}</span>
                <el-icon><Aim /></el-icon>
              </div>
            </div>
          </div>
          <div class="msg-time">{{ msg.time }}</div>
        </div>
      </div>

      <div v-if="loading" class="message-row assistant">
        <div class="avatar-col">
          <el-avatar :size="32" icon="Service" class="assistant" />
        </div>
        <div class="content-col">
          <div class="bubble loading-bubble">
            <el-icon class="is-loading"><Loading /></el-icon>
            <span>正在分析单据事实与制度条款...</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 快捷提问气泡 -->
    <div class="quick-prompts">
      <span class="prompt-label">常见疑问：</span>
      <el-tag
        v-for="(q, idx) in quickQuestions"
        :key="idx"
        size="small"
        class="prompt-tag"
        @click="sendQuick(q)"
      >
        {{ q }}
      </el-tag>
    </div>

    <!-- 输入框与发送 -->
    <div class="chat-input-area">
      <el-input
        v-model="inputQuery"
        placeholder="输入您对该单据风控结果的疑问（如：为什么判定第2笔住宿费超标？）"
        @keyup.enter="handleSend"
        :disabled="loading"
      >
        <template #append>
          <el-button type="primary" @click="handleSend" :loading="loading">
            发送
          </el-button>
        </template>
      </el-input>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { ChatDotRound, Aim, Loading } from '@element-plus/icons-vue'
import api from '@/api'

const props = defineProps({
  documentId: [Number, String],
  initialQuestion: String
})

const emit = defineEmits(['selectCitation'])

const messages = ref([
  {
    role: 'assistant',
    content: '您好！我是智能风控审查 Copilot。我已经完成了对该单据的五方金额核对、差旅制度匹配、发票防重及供应商穿透分析。请问有什么我可以帮您解答的？',
    time: new Date().toLocaleTimeString(),
    citations: []
  }
])

const inputQuery = ref('')
const loading = ref(false)
const sessionId = ref('')
const msgListRef = ref(null)

const quickQuestions = [
  '为什么判定差旅住宿费超标？',
  '该发票是否存在跨单重复报销？',
  '供应商是否存在高危失信记录？',
  '如何进行合规特批放行？'
]

const scrollToBottom = () => {
  nextTick(() => {
    if (msgListRef.value) {
      msgListRef.value.scrollTop = msgListRef.value.scrollHeight
    }
  })
}

const sendQuick = (q) => {
  inputQuery.value = q
  handleSend()
}

const handleSend = async () => {
  const query = inputQuery.value.trim()
  if (!query || loading.value) return

  messages.value.push({
    role: 'user',
    content: query,
    time: new Date().toLocaleTimeString()
  })
  inputQuery.value = ''
  loading.value = true
  scrollToBottom()

  try {
    const res = await api.post('/audits/chat', {
      document_id: Number(props.documentId),
      message: query,
      session_id: sessionId.value || null
    })

    sessionId.value = res.session_id
    messages.value.push({
      role: res.message.role,
      content: res.message.content,
      time: new Date().toLocaleTimeString(),
      citations: res.message.citations || []
    })
  } catch (err) {
    messages.value.push({
      role: 'assistant',
      content: '对话服务响应超时，请稍后重试。',
      time: new Date().toLocaleTimeString()
    })
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

const emitCitationClick = (citation) => {
  emit('selectCitation', citation)
}

onMounted(() => {
  if (props.initialQuestion) {
    sendQuick(props.initialQuestion)
  }
})
</script>

<style scoped>
.audit-chat-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #ffffff;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  overflow: hidden;
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ai-icon {
  font-size: 18px;
  color: #3b82f6;
}

.header-title {
  font-weight: 600;
  color: #0f172a;
  font-size: 14px;
}

.chat-messages {
  flex: 1;
  padding: 16px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
  background: #fafafa;
}

.message-row {
  display: flex;
  gap: 12px;
  max-width: 88%;
}

.message-row.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message-row.assistant {
  align-self: flex-start;
}

.avatar-col .assistant {
  background: #3b82f6;
}

.avatar-col .user {
  background: #10b981;
}

.content-col {
  display: flex;
  flex-direction: column;
}

.bubble {
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.6;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}

.message-row.user .bubble {
  background: #2563eb;
  color: #ffffff;
  border-bottom-right-radius: 2px;
}

.message-row.assistant .bubble {
  background: #ffffff;
  color: #1e293b;
  border: 1px solid #e2e8f0;
  border-bottom-left-radius: 2px;
}

.loading-bubble {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #64748b;
}

.citations-box {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed #cbd5e1;
}

.citation-title {
  font-size: 11px;
  color: #64748b;
  margin-bottom: 4px;
}

.citation-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  color: #1d4ed8;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
  margin-right: 6px;
  margin-bottom: 4px;
  transition: all 0.2s;
}

.citation-pill:hover {
  background: #dbeafe;
}

.msg-time {
  font-size: 10px;
  color: #94a3b8;
  margin-top: 4px;
}

.message-row.user .msg-time {
  text-align: right;
}

.quick-prompts {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: #f1f5f9;
  overflow-x: auto;
}

.prompt-label {
  font-size: 11px;
  color: #64748b;
  white-space: nowrap;
}

.prompt-tag {
  cursor: pointer;
  white-space: nowrap;
}

.chat-input-area {
  padding: 12px 16px;
  background: #ffffff;
  border-top: 1px solid #e2e8f0;
}
</style>
