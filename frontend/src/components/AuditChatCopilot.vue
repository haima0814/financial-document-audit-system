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
    <div class="chat-messages" ref="msgListRef" @scroll="handleScroll">
      <div v-for="(msg, idx) in messages" :key="idx" :class="['message-row', msg.role]">
        <div class="avatar-col">
          <el-avatar :size="32" :icon="msg.role === 'assistant' ? Service : User" :class="msg.role" />
        </div>
        <div class="content-col">
          <div class="bubble">
            <!-- 正在分析等待首 token 占位 -->
            <div v-if="!msg.content && msg.isGenerating" class="generating-placeholder">
              <el-icon class="is-loading"><Loading /></el-icon>
              <span>正在分析单据事实与制度条款...</span>
            </div>

            <!-- Markdown 富文本安全渲染 -->
            <div
              v-else
              class="bubble-text markdown-body"
              v-html="renderMarkdown(msg.content)"
            ></div>

            <!-- 流式生成光标指示器 -->
            <span v-if="msg.content && msg.isGenerating" class="typing-cursor"></span>

            <!-- 引用依据卡片 (流结束后稳定展示) -->
            <div v-if="msg.citations && msg.citations.length" class="citations-box">
              <div class="citation-title">📌 关联依据与视觉锚点：</div>
              <div
                v-for="(c, cIdx) in msg.citations"
                :key="cIdx"
                class="citation-pill"
                @click="emitCitationClick(c)"
                :title="`点击定位原件 [${c.rule_code}] 视觉区域`"
              >
                <span>[{{ c.rule_code }}] {{ c.title }}</span>
                <el-icon><Aim /></el-icon>
              </div>
            </div>
          </div>
          <div class="msg-time">{{ msg.time }}</div>
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
        :effect="loading ? 'plain' : 'light'"
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
          <el-button type="primary" @click="handleSend" :loading="loading" :disabled="!inputQuery.trim()">
            {{ loading ? '生成中' : '发送' }}
          </el-button>
        </template>
      </el-input>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { ChatDotRound, Aim, Loading, Service, User } from '@element-plus/icons-vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

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
    citations: [],
    isGenerating: false
  }
])

const inputQuery = ref('')
const loading = ref(false)
const sessionId = ref('')
const msgListRef = ref(null)
const userScrolledUp = ref(false)
let currentAbortController = null

const quickQuestions = [
  '为什么判定差旅住宿费超标？',
  '该发票是否存在跨单重复报销？',
  '供应商是否存在高危失信记录？',
  '如何进行合规特批放行？'
]

// 安全 HTML 转义与 DOMPurify 净化 (彻底杜绝 XSS fail-open)
const escapeHtml = (str) => {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

const renderMarkdown = (text) => {
  if (!text) return ''
  try {
    const rawHtml = marked.parse(text, { breaks: true, gfm: true })
    return DOMPurify.sanitize(rawHtml)
  } catch (err) {
    console.warn('Markdown 渲染失败 (已启动安全转义与净化防线):', err)
    return DOMPurify.sanitize(escapeHtml(text))
  }
}

// 检查用户是否在消息列表底部附近
const isNearBottom = () => {
  if (!msgListRef.value) return true
  const { scrollTop, scrollHeight, clientHeight } = msgListRef.value
  return scrollHeight - scrollTop - clientHeight <= 70
}

const handleScroll = () => {
  if (!msgListRef.value) return
  // 如果用户主动向上滚动查阅历史，标记 userScrolledUp
  userScrolledUp.value = !isNearBottom()
}

const scrollToBottom = () => {
  nextTick(() => {
    if (msgListRef.value) {
      msgListRef.value.scrollTop = msgListRef.value.scrollHeight
    }
  })
}

const sendQuick = (q) => {
  if (loading.value) return
  inputQuery.value = q
  handleSend()
}

const handleSend = async () => {
  const query = inputQuery.value.trim()
  if (!query || loading.value) return

  // 中止上一个仍在执行中的流式请求
  if (currentAbortController) {
    currentAbortController.abort()
  }
  currentAbortController = new AbortController()

  // 1. 立即记录用户消息
  messages.value.push({
    role: 'user',
    content: query,
    time: new Date().toLocaleTimeString(),
    isGenerating: false
  })
  inputQuery.value = ''
  loading.value = true
  userScrolledUp.value = false
  scrollToBottom()

  // 2. 立即创建 Assistant 消息占位符 (首 token 前显示“正在分析”)
  const assistantMsg = reactive({
    role: 'assistant',
    content: '',
    time: new Date().toLocaleTimeString(),
    citations: [],
    isGenerating: true
  })
  messages.value.push(assistantMsg)
  scrollToBottom()

  try {
    const token = localStorage.getItem('token')
    const response = await fetch('/api/v1/audits/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {})
      },
      body: JSON.stringify({
        document_id: Number(props.documentId),
        message: query,
        session_id: sessionId.value || null
      }),
      signal: currentAbortController.signal
    })

    if (!response.ok) {
      const errText = await response.text()
      assistantMsg.content = `服务响应异常 (${response.status}): ${errText || '无法连接对话服务'}`
      assistantMsg.isGenerating = false
      return
    }

    // 3. 读取 SSE 流式数据
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    let currentEvent = 'message'

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) {
          currentEvent = 'message'
          continue
        }

        if (trimmed.startsWith('event:')) {
          currentEvent = trimmed.slice(6).trim()
        } else if (trimmed.startsWith('data:')) {
          const dataStr = trimmed.slice(5).trim()
          try {
            const payload = JSON.parse(dataStr)

            if (currentEvent === 'meta') {
              if (payload.session_id) {
                sessionId.value = payload.session_id
              }
            } else if (currentEvent === 'delta') {
              if (payload.delta) {
                assistantMsg.content += payload.delta
                // 用户在底部附近时才跟随滚动
                if (!userScrolledUp.value) {
                  scrollToBottom()
                }
              }
            } else if (currentEvent === 'citations') {
              if (payload.citations) {
                assistantMsg.citations = payload.citations
              }
            } else if (currentEvent === 'done') {
              if (payload.session_id) sessionId.value = payload.session_id
              if (payload.citations && payload.citations.length) {
                assistantMsg.citations = payload.citations
              }
            } else if (currentEvent === 'error') {
              assistantMsg.content += `\n\n> ⚠️ [AI 对话异常]: ${payload.error || '生成中断'}`
            }
          } catch (err) {
            console.warn('解析 SSE 帧失败:', err, dataStr)
          }
        }
      }
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      console.log('流式问答已主动中止')
      return
    }
    console.error('流式问答通信异常:', err)
    if (!assistantMsg.content) {
      assistantMsg.content = '抱歉，网络连接异常或审查对话服务不可用，请稍后重试。'
    } else {
      assistantMsg.content += '\n\n> ⚠️ [网络连接中断]'
    }
  } finally {
    assistantMsg.isGenerating = false
    loading.value = false
    currentAbortController = null
    if (!userScrolledUp.value) {
      scrollToBottom()
    }
  }
}

const emitCitationClick = (citation) => {
  emit('selectCitation', citation)
}

watch(
  () => props.initialQuestion,
  (newQ) => {
    if (newQ) {
      sendQuick(newQ)
    }
  }
)

onMounted(() => {
  if (props.initialQuestion) {
    sendQuick(props.initialQuestion)
  }
})

onUnmounted(() => {
  if (currentAbortController) {
    currentAbortController.abort()
    currentAbortController = null
  }
})
</script>

<style scoped>
.audit-chat-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  background: #ffffff;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  overflow: hidden;
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.ai-icon {
  font-size: 18px;
  color: #3b82f6;
  flex-shrink: 0;
}

.header-title {
  font-weight: 600;
  color: #0f172a;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-messages {
  flex: 1;
  padding: 14px;
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
  gap: 14px;
  background: #fafafa;
  min-width: 0;
}

.message-row {
  display: flex;
  gap: 10px;
  max-width: 92%;
  min-width: 0;
}

.message-row.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message-row.assistant {
  align-self: flex-start;
}

.avatar-col {
  flex-shrink: 0;
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
  min-width: 0;
  flex: 1;
}

.bubble {
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.6;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
  word-break: break-word;
  overflow-wrap: anywhere;
  white-space: normal;
  min-width: 0;
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

.generating-placeholder {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #64748b;
  font-size: 12px;
}

/* Markdown 富文本样式 */
.markdown-body {
  font-size: 13px;
  line-height: 1.65;
  color: #1e293b;
}

.markdown-body :deep(p) {
  margin: 0 0 8px 0;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 4px 0 8px 18px;
  padding: 0;
}

.markdown-body :deep(li) {
  margin-bottom: 4px;
}

.markdown-body :deep(strong) {
  font-weight: 600;
  color: #0f172a;
}

.markdown-body :deep(code) {
  background: #f1f5f9;
  padding: 2px 4px;
  border-radius: 4px;
  font-family: monospace;
  font-size: 12px;
}

.markdown-body :deep(blockquote) {
  margin: 6px 0;
  padding: 4px 10px;
  border-left: 3px solid #3b82f6;
  background: #f8fafc;
  color: #475569;
  border-radius: 0 4px 4px 0;
}

/* 打字机闪烁光标 */
.typing-cursor {
  display: inline-block;
  width: 6px;
  height: 13px;
  background: #3b82f6;
  margin-left: 4px;
  vertical-align: middle;
  animation: blink 0.9s infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
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
  word-break: break-all;
}

.citation-pill:hover {
  background: #dbeafe;
  border-color: #93c5fd;
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
  padding: 8px 14px;
  background: #f1f5f9;
  overflow-x: auto;
  flex-shrink: 0;
}

.prompt-label {
  font-size: 11px;
  color: #64748b;
  white-space: nowrap;
  flex-shrink: 0;
}

.prompt-tag {
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
}

.chat-input-area {
  padding: 10px 14px;
  background: #ffffff;
  border-top: 1px solid #e2e8f0;
  flex-shrink: 0;
}
</style>
