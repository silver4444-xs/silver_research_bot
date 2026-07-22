<template>
  <section class="chat-container">
    <div class="chat-header">
      <span class="chat-session-label">会话: {{ sessionId }}</span>
      <button class="btn bg bsm" @click="newSession">新对话</button>
    </div>
    <div class="thread" ref="threadEl">
      <div v-for="(m, i) in chatMsgs" :key="i" :class="['cbubble', m.role]">
        <strong>{{ m.role === 'user' ? '你' : 'Agent' }}</strong>
        <div v-if="m.role === 'agent'" v-html="md.renderFormula(m.content)"></div>
        <p v-else>{{ m.content }}</p>
      </div>
      <div v-if="chatLoading" class="cbubble agent thinking">
        <span class="dot-pulse">...</span>
      </div>
    </div>
    <div class="chat-input-box">
      <button class="chat-attach-btn" @click="$refs.chatFileInput.click()" title="上传文件" :disabled="chatLoading">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
      </button>
      <input ref="chatFileInput" type="file" accept=".pdf,.txt,.md" hidden @change="onChatFile" />
      <textarea ref="chatTextarea" v-model="chatIn" rows="1" placeholder="输入消息...（Enter 发送，Shift+Enter 换行）" @keydown="onChatKey" @input="autoResize" :disabled="chatLoading"></textarea>
      <button class="chat-send-btn" :class="{ active: chatIn.trim() && !chatLoading }" @click="doChat" :disabled="!chatIn.trim() || chatLoading" title="发送">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
      </button>
    </div>
    <div v-if="chatAttach" class="chat-attach-tag">
      <span class="chip cb2">{{ chatAttach.name }}</span>
      <button class="btn bg bsm" @click="chatAttach = null" style="color: var(--c-text-muted)">&times;</button>
    </div>
  </section>
</template>

<script setup>
import { inject, ref, onMounted, nextTick } from 'vue'

const { api } = inject('api')
const md = inject('markdown')

const chatMsgs = ref([])
const chatIn = ref('')
const chatLoading = ref(false)
const chatAttach = ref(null)
const chatTextarea = ref(null)
const threadEl = ref(null)
const sessionId = ref('default')

function scrollToBottom() {
  nextTick(() => {
    const el = threadEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function onChatKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doChat() }
}

function autoResize() {
  const t = chatTextarea.value
  if (!t) return
  t.style.height = 'auto'
  t.style.height = Math.min(t.scrollHeight, 160) + 'px'
}

function onChatFile(e) {
  const f = e.target && e.target.files && e.target.files[0]
  if (f) {
    chatAttach.value = f
    chatMsgs.value.push({ role: 'user', content: '[上传文件: ' + f.name + ']' })
    doUploadChatFile()
  }
}

async function doUploadChatFile() {
  const f = chatAttach.value
  if (!f) return
  try {
    const fd = new FormData()
    fd.append('file', f)
    const r = await api('/api/paper/upload', { method: 'POST', body: fd })
    chatMsgs.value.push({ role: 'agent', content: '已开始分析论文: **' + r.title + '**\n\n语言: ' + (r.language === 'en' ? '英文' : '中文') + '\n状态: ' + r.status + '\n\n分析完成后可在「论文研读」中查看。' })
    chatIn.value = '请分析我刚刚上传的论文: ' + r.title
    chatAttach.value = null
  } catch (e) {
    chatMsgs.value.push({ role: 'agent', content: '文件上传失败: ' + e.message })
    chatAttach.value = null
  }
}

async function doChat() {
  const m = chatIn.value.trim()
  if (!m || chatLoading.value) return
  chatMsgs.value.push({ role: 'user', content: m })
  chatIn.value = ''
  const t = chatTextarea.value
  if (t) { t.style.height = 'auto' }
  scrollToBottom()

  const agentIdx = chatMsgs.value.length
  chatMsgs.value.push({ role: 'agent', content: '' })
  chatLoading.value = true

  try {
    const res = await fetch('/api/agent/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: m, run_id: sessionId.value }),
    })
    if (!res.ok) {
      const errText = await res.text()
      throw new Error(errText || 'HTTP ' + res.status)
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const payload = line.slice(6)
          if (payload === '[DONE]') continue
          try {
            const parsed = JSON.parse(payload)
            chatMsgs.value[agentIdx].content += parsed.token
          } catch (_) {
            // skip malformed JSON lines
          }
        }
      }
    }
  } catch (e) {
    chatMsgs.value[agentIdx].content = '错误: ' + (e.message || '未知错误')
  } finally {
    chatLoading.value = false
    scrollToBottom()
  }
}

function newSession() {
  sessionId.value = Date.now().toString(36)
  chatMsgs.value = [{ role: 'agent', content: '新对话已开始。请告诉我你的研究问题。' }]
  scrollToBottom()
}

async function loadSessionHistory() {
  try {
    const data = await api('/api/agent/sessions/' + sessionId.value)
    if (data.messages && data.messages.length) {
      const msgs = []
      for (const m of data.messages) {
        if (m.role === 'user' || m.role === 'assistant') {
          let content = ''
          if (typeof m.content === 'string') {
            content = m.content
          } else if (Array.isArray(m.content)) {
            content = m.content.map(c => c.text || '').join('')
          }
          if (content) {
            msgs.push({ role: m.role === 'assistant' ? 'agent' : 'user', content })
          }
        }
      }
      if (msgs.length) {
        chatMsgs.value = msgs
        scrollToBottom()
        return
      }
    }
  } catch (_) {
    // session doesn't exist yet
  }
  chatMsgs.value = [{ role: 'agent', content: '你好！我是研究助手。上传 PDF 或直接提问即可开始。' }]
}

onMounted(() => {
  loadSessionHistory()
})
</script>

<style scoped>
.chat-container { display: flex; flex-direction: column; height: calc(100vh - 180px); max-width: 860px; margin: 0 auto; width: 100% }
.chat-header { display: flex; align-items: center; justify-content: space-between; padding: var(--s-2) var(--s-2) var(--s-3); border-bottom: 1px solid var(--c-border); margin-bottom: var(--s-2) }
.chat-session-label { font-size: 12px; color: var(--c-text-muted); font-family: var(--ff-mono, monospace) }
.thread { flex: 1; overflow-y: auto; padding: 0 var(--s-2) var(--s-4); display: flex; flex-direction: column; gap: var(--s-4) }
.cbubble { font-size: 14px; padding: var(--s-3) var(--s-4); border-radius: var(--r-lg); max-width: 78%; line-height: 1.7 }
.cbubble.user { background: rgba(99, 102, 241, 0.15); border: 1px solid rgba(99, 102, 241, 0.22); align-self: flex-end }
.cbubble.agent { background: rgba(168, 85, 247, 0.08); border: 1px solid rgba(168, 85, 247, 0.15); align-self: flex-start }
.cbubble strong { display: block; margin-bottom: 2px; font-size: 10px; text-transform: uppercase; letter-spacing: .08em }
.cbubble p { margin: 0; white-space: pre-wrap }
.cbubble.user strong { color: var(--c-info) }
.cbubble.agent strong { color: #C084FC }
.cbubble.agent :deep(h2), .cbubble.agent :deep(h3), .cbubble.agent :deep(h4), .cbubble.agent :deep(h5) { margin: 0.6em 0 0.3em; line-height: 1.3 }
.cbubble.agent :deep(pre) { background: rgba(0,0,0,0.25); padding: var(--s-3); border-radius: var(--r-md); overflow-x: auto; font-size: 13px; margin: var(--s-2) 0 }
.cbubble.agent :deep(code) { font-family: var(--ff-mono, monospace); font-size: 13px; background: rgba(0,0,0,0.2); padding: 2px 6px; border-radius: 4px }
.cbubble.agent :deep(pre code) { background: none; padding: 0 }
.cbubble.agent :deep(blockquote) { border-left: 3px solid var(--c-accent); padding-left: var(--s-3); margin: var(--s-2) 0; color: var(--c-text-muted) }
.cbubble.agent :deep(table) { border-collapse: collapse; margin: var(--s-2) 0; font-size: 13px }
.cbubble.agent :deep(th), .cbubble.agent :deep(td) { border: 1px solid var(--c-border); padding: 4px 10px; text-align: left }
.cbubble.agent :deep(a) { color: var(--c-accent); text-decoration: underline }
.thinking { display: flex; align-items: center; min-height: 40px }
.dot-pulse { animation: pulse 1.5s ease-in-out infinite; font-size: 20px; letter-spacing: 4px; color: #C084FC }
@keyframes pulse { 0%, 100% { opacity: 0.2 } 50% { opacity: 1 } }
.chat-input-box { display: flex; align-items: flex-end; gap: var(--s-2); background: var(--c-bg-card); border: 1px solid var(--c-border); border-radius: var(--r-xl); padding: var(--s-2) var(--s-3); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); transition: border-color var(--t-fast) }
.chat-input-box:focus-within { border-color: var(--c-border-active); box-shadow: var(--glow-sm) }
.chat-input-box textarea { flex: 1; border: none !important; background: transparent; color: var(--c-text); font-size: 14px; padding: var(--s-2) 0; resize: none; outline: none; min-height: 24px; max-height: 160px; line-height: 1.5; font-family: var(--ff-body); box-shadow: none !important }
.chat-input-box textarea:disabled { opacity: 0.5 }
.chat-attach-btn { width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; border: none; background: transparent; color: var(--c-text-muted); cursor: pointer; border-radius: var(--r-sm); transition: all var(--t-fast); flex-shrink: 0 }
.chat-attach-btn:hover { color: var(--c-text); background: rgba(99, 102, 241, 0.1) }
.chat-attach-btn:disabled { opacity: 0.4; cursor: default }
.chat-attach-btn svg { width: 20px; height: 20px }
.chat-send-btn { width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; border: none; background: rgba(99, 102, 241, 0.12); color: var(--c-text-muted); cursor: pointer; border-radius: 50%; transition: all var(--t-fast); flex-shrink: 0 }
.chat-send-btn.active { background: linear-gradient(135deg, var(--c-accent), var(--c-accent-2)); color: #fff; box-shadow: 0 0 14px var(--c-accent-glow) }
.chat-send-btn.active:hover { transform: scale(1.06); box-shadow: 0 0 20px rgba(139, 92, 246, 0.5) }
.chat-send-btn:disabled { cursor: default; opacity: 0.4 }
.chat-send-btn svg { width: 16px; height: 16px }
.chat-attach-tag { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) 0 0 }
</style>
