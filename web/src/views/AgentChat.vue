<template>
  <div class="agent-layout">
    <!-- Mobile Drawer Backdrop -->
    <div v-if="mobileDrawerOpen" class="drawer-backdrop" @click="closeMobileDrawer"></div>

    <!-- Session Panel -->
    <aside class="session-panel" :class="{ collapsed: panelCollapsed, 'drawer-open': mobileDrawerOpen }">
      <div class="panel-header">
        <span>对话记录</span>
        <div class="panel-header-actions">
          <button v-if="mobileDrawerOpen" class="btn bg bsm drawer-close-btn" @click="closeMobileDrawer" title="关闭">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
          <button class="btn bg bsm" @click="panelCollapsed = !panelCollapsed" title="收起面板">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="15 18 9 12 15 6"/></svg>
          </button>
        </div>
      </div>
      <button class="btn ba bf panel-new-btn" @click="newSession">+ 新对话</button>
      <input
        v-if="sessions.length > 0"
        class="session-search"
        v-model="searchQuery"
        type="text"
        placeholder="搜索会话..."
      />
      <div class="session-list">
        <div v-if="sessionsLoading" class="session-item muted">加载中...</div>
        <div v-else-if="!filteredSessions.length" class="session-item muted">{{ searchQuery ? '无匹配结果' : '暂无对话记录' }}</div>
        <div
          v-for="s in filteredSessions"
          :key="s.id"
          :class="['session-item', { active: s.id === sessionId }]"
          @click="switchSession(s.id); closeMobileDrawer()"
        >
          <div v-if="editingSessionId === s.id" class="session-title-edit">
            <input
              v-model="editingTitle"
              @keydown.enter="saveRename(s)"
              @keydown.escape="cancelRename"
              @blur="cancelRename"
              ref="renameInput"
              class="rename-input"
            />
          </div>
          <div v-else class="session-title" @dblclick.stop="startRename(s)">{{ s.title || '新对话' }}</div>
          <div class="session-preview">{{ s.preview || '暂无消息' }}</div>
          <div class="session-meta">
            <span>{{ formatTime(s.updated_at) }}</span>
            <button class="btn bg bsm session-del" @click.stop="deleteSession(s.id)" title="删除">&times;</button>
          </div>
        </div>
      </div>
    </aside>

    <!-- Expand button when panel collapsed -->
    <button v-if="panelCollapsed" class="panel-expand-btn" @click="panelCollapsed = false" title="展开面板">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="9 18 15 12 9 6"/></svg>
    </button>

    <!-- Chat Container -->
    <section class="chat-container">
      <div class="chat-header">
        <button class="btn bg bsm hamburger-btn" @click="openMobileDrawer" title="会话列表">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
        </button>
        <span class="chat-session-label">{{ sessionTitle || '会话: ' + sessionId }}</span>
        <div class="chat-header-actions">
          <button v-if="chatLoading" class="btn bg bsm stop-btn" @click="stopChat" title="停止生成">■ 停止</button>
          <button class="btn bg bsm" @click="newSession">新对话</button>
          <button class="btn bg bsm" @click="exportChat" title="导出对话" v-if="chatMsgs.length > 1">导出</button>
        </div>
      </div>
      <div class="thread" ref="threadEl">
        <div v-for="(m, i) in chatMsgs" :key="i" class="msg-row" :class="m.role">
          <!-- Structured report: when analysis data is present -->
          <div v-if="m.role === 'agent' && m.analysis" class="ar-wrapper">
            <strong>Agent</strong>
            <details v-if="m.reasoning || m.thinking" class="think-fold">
              <summary>💭 思考过程</summary>
              <div class="think-fold-body">
                <div v-if="m.thinking" class="think-sec">
                  <div class="think-sec-label">推理叙述</div>
                  <div v-html="md.renderFormula(m.thinking)"></div>
                </div>
                <div v-if="m.reasoning" class="think-sec">
                  <div class="think-sec-label">内部思考</div>
                  <div v-html="md.renderFormula(m.reasoning)"></div>
                </div>
              </div>
            </details>
            <AnalysisReport
              :analysis="m.analysis"
              :content="m.content"
              :streaming="i === chatMsgs.length - 1 && chatLoading"
              @export="exportChat"
              @ask-follow-up="focusInput('关于这篇论文，')"
            />
          </div>
          <!-- Regular message bubble -->
          <div v-else class="cbubble">
            <strong>{{ m.role === 'user' ? '你' : 'Agent' }}</strong>
            <details v-if="m.role === 'agent' && (m.reasoning || m.thinking)" class="think-fold">
              <summary>💭 思考过程</summary>
              <div class="think-fold-body">
                <div v-if="m.thinking" class="think-sec">
                  <div class="think-sec-label">推理叙述</div>
                  <div v-html="md.renderFormula(m.thinking)"></div>
                </div>
                <div v-if="m.reasoning" class="think-sec">
                  <div class="think-sec-label">内部思考</div>
                  <div v-html="md.renderFormula(m.reasoning)"></div>
                </div>
              </div>
            </details>
            <div v-if="m.role === 'agent'" v-html="md.renderFormula(m.content)"></div>
            <p v-else>{{ m.content }}</p>
          </div>
          <div v-if="m.role === 'agent' && i === chatMsgs.length - 1 && !chatLoading && m.content && !m.content.includes('连接中断')" class="bubble-actions">
            <button class="btn bg bsm regen-btn" @click="regenerate(i)" title="重新生成">⟳ 重新生成</button>
          </div>
          <button v-if="m.role === 'agent' && m.content.includes('连接中断') && i === chatMsgs.length - 1" class="btn bg bsm retry-btn" @click="retryMessage(m, i)">⟳ 重新发送</button>
        </div>
        <div v-if="chatLoading" class="cbubble agent thinking">
          <span class="dot-pulse">...</span>
          <div v-if="toolStatuses.length" class="tool-cards">
            <div v-for="ts in toolStatuses" :key="ts.key" :class="['tool-card', ts.status]">
              <span class="tool-icon">{{ ts.status === 'running' ? '⟳' : ts.ok ? '✓' : '✗' }}</span>
              <span class="tool-name">{{ ts.name }}</span>
              <span v-if="ts.stage" class="tool-stage">{{ stageLabel(ts.stage) }}</span>
              <span v-if="ts.summary" class="tool-summary">{{ ts.summary.slice(0, 60) }}</span>
              <div v-if="ts.name === 'analyze_paper' && ts.status === 'running' && ts.stages" class="stage-pipeline">
                <div class="pipe-track">
                  <template v-for="(s, si) in ANALYZE_STAGES" :key="s">
                    <span :class="['pipe-node', { done: ts.stages.includes(s), active: ts.stage === s }]"></span>
                    <span v-if="si < ANALYZE_STAGES.length - 1" :class="['pipe-line', { done: ts.stages.includes(s) }]"></span>
                  </template>
                </div>
                <div class="pipe-labels">
                  <span v-for="s in ANALYZE_STAGES" :key="s" :class="['pipe-label', { active: ts.stage === s }]">{{ STAGE_LABELS[s] }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class="chat-input-box">
        <button class="chat-attach-btn" @click="$refs.chatFileInput.click()" title="上传文件" :disabled="chatLoading">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
        </button>
        <input ref="chatFileInput" type="file" accept=".pdf,.txt,.md" hidden @change="onChatFile" />
        <textarea ref="chatTextarea" v-model="chatIn" rows="1" placeholder="输入消息...（Enter 发送，Shift+Enter 换行）" @keydown="onChatKey" @input="autoResize" :disabled="chatLoading"></textarea>
        <button class="chat-send-btn" :class="{ active: (chatIn.trim() || pendingPaper) && !chatLoading }" @click="doChat()" :disabled="(!chatIn.trim() && !pendingPaper) || chatLoading" title="发送">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
        </button>
      </div>
      <div v-if="chatAttach" class="chat-attach-tag">
        <span class="chip cb2">{{ pendingPaper ? pendingPaper.title : chatAttach.name }}</span>
        <button class="btn bg bsm" @click="clearAttach" style="color: var(--c-text-muted)">&times;</button>
      </div>
    </section>
  </div>
</template>

<script setup>
import { inject, ref, computed, onMounted, nextTick } from 'vue'
import AnalysisReport from '../components/AnalysisReport.vue'

const { api } = inject('api')
const md = inject('markdown')

const chatMsgs = ref([])
const chatIn = ref('')
const chatLoading = ref(false)
const chatAttach = ref(null)
const chatTextarea = ref(null)
const threadEl = ref(null)
const sessionId = ref('default')
const toolStatuses = ref([])
const pendingPaper = ref(null)  // { paper_id, title, pdf_path, filename } — uploaded but not yet sent
const abortController = ref(null)
const lastSentMessage = ref('')
const retryCount = ref(0)
const searchQuery = ref('')
const mobileDrawerOpen = ref(false)
const editingSessionId = ref(null)
const editingTitle = ref('')
const renameInput = ref(null)

const STAGE_LABELS = {
  parse: '解析', translate: '翻译', analyze: '四维分析',
  formula_explain: '公式解读', visualize: '可视化',
  citation: '引用图谱', review: '三视角审稿',
  audit: '审计', compare: '横向对比',
}

function stageLabel(stage) { return STAGE_LABELS[stage] || stage }

const ANALYZE_STAGES = ['parse', 'translate', 'analyze', 'formula_explain', 'visualize', 'citation', 'review']

// Session management
const sessions = ref([])
const sessionsLoading = ref(false)
const panelCollapsed = ref(false)
const isSwitching = ref(false)

const sessionTitle = computed(() => {
  const s = sessions.value.find(s => s.id === sessionId.value)
  return s?.title || null
})

const filteredSessions = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return sessions.value
  return sessions.value.filter(s =>
    (s.title || '').toLowerCase().includes(q) ||
    (s.preview || '').toLowerCase().includes(q)
  )
})

function openMobileDrawer() { mobileDrawerOpen.value = true }
function closeMobileDrawer() { mobileDrawerOpen.value = false }

function startRename(s) {
  editingSessionId.value = s.id
  editingTitle.value = s.title || ''
  nextTick(() => {
    if (renameInput.value) {
      if (Array.isArray(renameInput.value)) renameInput.value[0]?.focus()
      else renameInput.value.focus?.()
    }
  })
}

async function saveRename(s) {
  const newTitle = editingTitle.value.trim()
  editingSessionId.value = null
  if (!newTitle) return
  s.title = newTitle
  try {
    await fetch('/api/agent/sessions/' + s.id, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: newTitle }),
    })
  } catch (e) {
    console.warn('Failed to rename session:', e)
  }
}

function cancelRename() { editingSessionId.value = null }

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
  if (!f) return
  if (pendingPaper.value) { pendingPaper.value = null }
  chatAttach.value = f
  uploadAndStorePaper()
}

async function uploadAndStorePaper() {
  const f = chatAttach.value
  if (!f) return
  chatLoading.value = true
  try {
    const fd = new FormData()
    fd.append('file', f)
    const r = await api('/api/paper/upload?mode=extract_only', { method: 'POST', body: fd })
    const pdfPath = r.workspace_pdf_path || ''

    // ——— Dedup: paper was already uploaded and analyzed ———
    if (r.duplicate && r.status === 'already_analyzed') {
      chatAttach.value = null
      // Preserve paper context so follow-up questions include paper info
      pendingPaper.value = {
        paper_id: r.paper_id,
        title: r.title,
        pdf_path: r.workspace_pdf_path || '',
        filename: f.name,
        already_analyzed: true,
      }
      // Fetch structured summary so AnalysisReport renders the card
      try {
        const summary = await api('/api/paper/' + r.paper_id + '/summary')
        if (summary && summary.paper_id) {
          chatMsgs.value.push({
            role: 'agent',
            content: '📄 **此论文已分析完成！**\n\n'
              + '论文: **' + r.title + '**\n'
              + '语言: ' + (r.language === 'en' ? '英文' : '中文')
              + ' | ID: `' + r.paper_id + '`\n\n'
              + '无需重新分析，可直接查看已有产物。请告诉我您想了解什么。',
            analysis: summary,
          })
          chatLoading.value = false
          return
        }
      } catch (_) { /* fall through to plain-text fallback */ }
      chatMsgs.value.push({
        role: 'agent',
        content: '📄 **此论文已分析完成！**\n\n'
          + '论文: **' + r.title + '**\n'
          + '语言: ' + (r.language === 'en' ? '英文' : '中文')
          + ' | ID: `' + r.paper_id + '`\n\n'
          + '无需重新分析，可直接查看已有产物。请告诉我您想了解什么。',
      })
      chatLoading.value = false
      return
    }
    // ——— Dedup: paper extracted before but not yet analyzed ———
    if (r.duplicate && r.status === 'extracted') {
      chatMsgs.value.push({
        role: 'agent',
        content: '📄 **此论文已上传过**（未分析）\n\n'
          + '论文: **' + r.title + '** | ID: `' + r.paper_id + '`\n\n'
          + '将复用已有提取数据，无需重新上传。请告诉我您想如何处理。',
      })
    }

    pendingPaper.value = {
      paper_id: r.paper_id,
      title: r.title,
      pdf_path: pdfPath,
      filename: f.name,
    }
    chatMsgs.value.push({
      role: 'agent',
      content: '已上传论文: **' + r.title + '**\n\n'
        + '语言: ' + (r.language === 'en' ? '英文' : '中文')
        + ' | ID: `' + r.paper_id + '`\n\n'
        + '请告诉我您想如何处理。例如:\n'
        + '- "分析这篇文章" → 完整四维分析\n'
        + '- "这篇文章的优化算法是什么" → 分析后回答具体问题\n'
        + '- "搜索相关论文" → 搜索相似研究方向',
    })
  } catch (e) {
    chatMsgs.value.push({ role: 'agent', content: '文件上传失败: ' + e.message })
    pendingPaper.value = null
    chatAttach.value = null
  } finally {
    chatLoading.value = false
  }
}

async function doChat(overrideMessage) {
  const msg = typeof overrideMessage === 'string' ? overrideMessage : chatIn.value
  const rawMessage = (msg || '').trim()
  const hasPending = !!pendingPaper.value
  if ((!rawMessage && !hasPending) || chatLoading.value) return
  if (!overrideMessage) { chatIn.value = '' }

  lastSentMessage.value = rawMessage
  retryCount.value = 0

  // If a paper is pending, prepend context to the backend message
  let sendMessage = rawMessage
  if (hasPending) {
    const pp = pendingPaper.value
    if (pp.already_analyzed) {
      // Paper already analyzed — use reference marker (not upload marker)
      sendMessage = '[已分析论文: ' + pp.title + ', paper_id=' + pp.paper_id + ']'
    } else {
      sendMessage = '[已上传论文: ' + pp.title + ', paper_id=' + pp.paper_id + ', pdf_path=' + pp.pdf_path + ']'
    }
    if (rawMessage) sendMessage += '\n\n' + rawMessage
    pendingPaper.value = null
    chatAttach.value = null
  }

  const userMsgCount = chatMsgs.value.filter(m => m.role === 'user').length
  chatMsgs.value.push({ role: 'user', content: rawMessage || '分析这篇论文' })
  chatIn.value = ''
  const t = chatTextarea.value
  if (t) { t.style.height = 'auto' }
  scrollToBottom()

  const agentIdx = chatMsgs.value.length
  chatMsgs.value.push({ role: 'agent', content: '', reasoning: '', thinking: '' })
  chatLoading.value = true
  toolStatuses.value = []

  abortController.value = new AbortController()
  try {
    const res = await fetch('/api/agent/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: sendMessage, run_id: sessionId.value }),
      signal: abortController.value.signal,
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
            switch (parsed.type) {
              case 'token':
                chatMsgs.value[agentIdx].content += parsed.text
                break
              case 'tool_start': {
                const key = parsed.name + '_' + Date.now()
                toolStatuses.value.push({ key, name: parsed.name, args: parsed.args, status: 'running', ok: true, summary: '' })
                break
              }
              case 'tool_end': {
                const idx = toolStatuses.value.findIndex(t => t.name === parsed.name && t.status === 'running')
                if (idx >= 0) {
                  toolStatuses.value[idx].status = parsed.ok ? 'done' : 'error'
                  toolStatuses.value[idx].ok = parsed.ok
                  toolStatuses.value[idx].summary = parsed.summary
                  setTimeout(() => { toolStatuses.value = toolStatuses.value.filter(t => t.status === 'running') }, 2000)
                }
                break
              }
              case 'tool_progress': {
                const idx = toolStatuses.value.findIndex(t => t.name === parsed.name && t.status === 'running')
                if (idx >= 0) {
                  toolStatuses.value[idx].summary = parsed.message || ''
                  if (parsed.stage) {
                    toolStatuses.value[idx].stage = parsed.stage
                    if (!toolStatuses.value[idx].stages) toolStatuses.value[idx].stages = []
                    if (parsed.status === 'completed' && !toolStatuses.value[idx].stages.includes(parsed.stage)) {
                      toolStatuses.value[idx].stages.push(parsed.stage)
                    }
                  }
                }
                break
              }
              case 'analysis_complete':
                chatMsgs.value[agentIdx].analysis = parsed.summary
                break
              case 'reasoning': {
                const msg = chatMsgs.value[agentIdx]
                if (!msg.reasoning) msg.reasoning = ''
                msg.reasoning += parsed.text
                break
              }
              case 'segment_end': {
                // resuming=true → more tool calls follow, so the content emitted so far is
                // intermediate "thinking-out-loud" narration, not the final answer.
                const msg = chatMsgs.value[agentIdx]
                if (parsed.resuming && msg.content) {
                  if (!msg.thinking) msg.thinking = ''
                  msg.thinking += (msg.thinking ? '\n\n' : '') + msg.content
                  msg.content = ''
                }
                break
              }
              case 'governance':
              case 'injection':
                break
              case 'error':
                chatMsgs.value[agentIdx].content += '\n⚠️ ' + parsed.detail
                break
            }
          } catch (e) {
            console.warn('[SSE] Failed to parse line:', line.slice(0, 200), e)
          }
        }
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') return
    const partialContent = chatMsgs.value[agentIdx].content.trim()
    if (!partialContent && retryCount.value < 1) {
      retryCount.value++
      chatMsgs.value.pop()
      chatMsgs.value.push({ role: 'agent', content: '_(连接中断，正在重试...)_' })
      scrollToBottom()
      chatLoading.value = false
      await new Promise(r => setTimeout(r, 2000))
      doChat(lastSentMessage.value)
      return
    }
    if (partialContent) {
      chatMsgs.value[agentIdx].content = partialContent + '\n\n---\n⚠️ **连接中断，响应未完整。**'
    } else {
      chatMsgs.value[agentIdx].content = '错误: ' + (e.message || '未知错误')
    }
  } finally {
    chatLoading.value = false
    abortController.value = null
    scrollToBottom()
    if (userMsgCount === 0) {
      generateTitle()
      fetchSessions()
    }
  }
}

async function stopChat() {
  if (abortController.value) {
    abortController.value.abort()
    abortController.value = null
  }
  try {
    await fetch('/api/agent/chat/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: sessionId.value }),
    })
  } catch (_) { /* best-effort */ }
  chatLoading.value = false
  const last = chatMsgs.value[chatMsgs.value.length - 1]
  if (last && last.role === 'agent' && !last.content.trim()) {
    last.content = '_(已停止)_'
  }
  scrollToBottom()
}

async function regenerate(agentIdx) {
  const userIdx = agentIdx - 1
  if (userIdx < 0 || chatMsgs.value[userIdx].role !== 'user') return
  const userMessage = chatMsgs.value[userIdx].content
  chatMsgs.value.splice(agentIdx, 1)
  lastSentMessage.value = userMessage
  retryCount.value = 0
  await doChat(userMessage)
}

function retryMessage(agentMsg, idx) {
  chatMsgs.value.splice(idx, 1)
  const userMsg = lastSentMessage.value
  if (userMsg) doChat(userMsg)
}

function exportChat() {
  // 定位最终分析报告：取最后一条有实质内容的 agent 消息作为报告正文，
  // 并从任意携带结构化 analysis 的消息中提取论文名（二者可能不在同一条消息上）。
  let reportContent = ''
  let paperTitle = ''
  for (const m of chatMsgs.value) {
    if (m.role !== 'agent') continue
    const c = (m.content || '').trim()
    if (c) reportContent = c
    if (!paperTitle && m.analysis && m.analysis.title) paperTitle = m.analysis.title.trim()
  }
  if (!reportContent) return

  const date = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
  const lines = []
  if (paperTitle) lines.push('# ' + paperTitle, '')
  lines.push('> 导出日期: ' + date, '', reportContent, '')

  const text = lines.join('\n')
  const base = (paperTitle || sessionTitle.value || '论文分析报告').replace(/[\\/:*?"<>|]/g, '_').trim().slice(0, 80)
  const blob = new Blob(['﻿' + text], { type: 'text/markdown;charset=utf-8' })
  const aEl = document.createElement('a')
  aEl.href = URL.createObjectURL(blob)
  aEl.download = base + '_分析结果.md'
  aEl.click()
  URL.revokeObjectURL(aEl.href)
}

function clearAttach() {
  chatAttach.value = null
  pendingPaper.value = null
}

function focusInput(prefill) {
  chatIn.value = prefill
  nextTick(() => {
    const t = chatTextarea.value
    if (t) { t.focus(); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 160) + 'px' }
  })
}

function newSession() {
  const id = Date.now().toString(36)
  sessions.value.unshift({
    id,
    key: 'web:' + id,
    title: null,
    preview: '',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    message_count: 0,
  })
  sessionId.value = id
  chatMsgs.value = [{ role: 'agent', content: '新对话已开始。请告诉我你的研究问题。' }]
  toolStatuses.value = []
  scrollToBottom()
}

async function fetchSessions() {
  sessionsLoading.value = true
  try {
    const data = await api('/api/agent/sessions')
    sessions.value = (data || [])
      .filter(s => s.key && s.key.startsWith('web:'))
      .map(s => ({
        ...s,
        id: s.key.replace('web:', ''),
        title: s.title || null,
        preview: s.preview || '',
      }))
  } catch (e) {
    console.error('Failed to fetch sessions:', e)
  } finally {
    sessionsLoading.value = false
  }
}

async function switchSession(id) {
  if (id === sessionId.value || isSwitching.value) return
  isSwitching.value = true
  sessionId.value = id
  chatMsgs.value = []
  toolStatuses.value = []
  chatIn.value = ''
  chatAttach.value = null
  pendingPaper.value = null
  await loadSessionHistory()
  scrollToBottom()
  isSwitching.value = false
}

async function generateTitle() {
  try {
    const res = await api('/api/agent/sessions/' + sessionId.value + '/title', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: false }),
    })
    if (res.title) {
      const idx = sessions.value.findIndex(s => s.id === sessionId.value)
      if (idx >= 0) sessions.value[idx].title = res.title
    }
  } catch (e) {
    console.warn('Title generation failed:', e)
  }
}

async function deleteSession(id) {
  try {
    await api('/api/agent/sessions/' + id, { method: 'DELETE' })
    sessions.value = sessions.value.filter(s => s.id !== id)
    if (sessionId.value === id) {
      if (sessions.value.length > 0) {
        await switchSession(sessions.value[0].id)
      } else {
        newSession()
      }
    }
  } catch (e) {
    console.error('Failed to delete session:', e)
  }
}

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now - d
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return diffMin + '分钟前'
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return diffHr + '小时前'
  const diffDay = Math.floor(diffHr / 24)
  if (diffDay < 7) return diffDay + '天前'
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

// 剥离用户消息中的论文上传/引用控制标记，返回用户真实输入
function stripPaperMarker(content) {
  if (typeof content !== 'string') return content
  return content.replace(/^\s*\[(?:已上传论文|已分析论文):[^\n]*\]\s*/, '').trim()
}

async function loadSessionHistory() {
  try {
    const data = await api('/api/agent/sessions/' + sessionId.value)
    if (data.messages && data.messages.length) {
      const allMsgs = data.messages

      // ── Pass 1: find paper_ids from tool messages ──
      // Map: assistant-message index in allMsgs → paper_id
      const analysisMap = new Map()
      for (let i = 0; i < allMsgs.length; i++) {
        const m = allMsgs[i]
        if (m.role !== 'tool') continue
        let tc = m.content
        if (typeof tc !== 'string') continue
        // Tool results may be truncated; still try to parse paper_id
        let paperId = null
        try {
          const parsed = JSON.parse(tc)
          if (parsed.paper_id && (parsed.status === 'completed' || parsed.status === 'already_analyzed')) {
            paperId = parsed.paper_id
          }
        } catch (_) {
          // Fallback: extract paper_id via regex for truncated JSON
          const m2 = tc.match(/"paper_id"\s*:\s*"([^"]+)"/)
          if (m2) paperId = m2[1]
        }
        if (!paperId) continue
        // The next assistant message with content is the analysis report
        for (let j = i + 1; j < allMsgs.length; j++) {
          if (allMsgs[j].role === 'assistant' && allMsgs[j].content) {
            analysisMap.set(j, paperId)
            break
          }
        }
      }

      // ── Pass 2: build display messages ──
      const msgs = []
      for (let i = 0; i < allMsgs.length; i++) {
        const m = allMsgs[i]
        if (m.role !== 'user' && m.role !== 'assistant') continue
        let content = ''
        if (typeof m.content === 'string') {
          content = m.content
        } else if (Array.isArray(m.content)) {
          content = m.content.map(c => c.text || '').join('')
        }
        if (!content) continue
        // 隐藏内部控制消息（系统提醒 + 论文上传/引用标记），不应出现在聊天记录中
        if (m.role === 'user') {
          if (content.trim().startsWith('[系统提醒]')) continue
          content = stripPaperMarker(content) || '分析这篇论文'
        }
        const entry = { role: m.role === 'assistant' ? 'agent' : 'user', content }
        if (m.role === 'assistant') {
          // Collapse internal chain-of-thought (reasoning_content) into the fold
          if (m.reasoning_content) entry.reasoning = m.reasoning_content
          // Intermediate turns (that called tools) are "thinking-out-loud" narration,
          // not the final answer — collapse them instead of showing as a normal bubble.
          if (Array.isArray(m.tool_calls) && m.tool_calls.length) {
            entry.thinking = content
            entry.content = ''
          }
        }
        // Attach structured analysis if this message follows a paper analysis tool
        const paperId = analysisMap.get(i)
        if (paperId) {
          try {
            const summary = await api('/api/paper/' + paperId + '/summary')
            if (summary && summary.paper_id) {
              entry.analysis = summary
            }
          } catch (e) {
            console.warn('Failed to restore analysis for paper:', paperId, e)
          }
        }
        msgs.push(entry)
      }

      if (msgs.length) {
        chatMsgs.value = msgs
        scrollToBottom()
        const hasTitle = data.metadata && data.metadata.title
        if (!hasTitle) generateTitle()
        return
      }
    }
  } catch (_) {
    // session doesn't exist yet
  }
  chatMsgs.value = [{ role: 'agent', content: '你好！我是研究助手。上传 PDF 或直接提问即可开始。' }]
}

onMounted(async () => {
  await fetchSessions()
  if (sessions.value.length > 0) {
    sessionId.value = sessions.value[0].id
  }
  await loadSessionHistory()
})
</script>

<style scoped>
.agent-layout {
  display: flex;
  height: calc(100vh - 180px);
  max-width: 1100px;
  margin: 0 auto;
  width: 100%;
  gap: 0;
}

/* Session Panel */
.session-panel {
  width: 280px;
  min-width: 280px;
  background: var(--c-bg-card);
  border-right: 1px solid var(--c-border);
  display: flex;
  flex-direction: column;
  padding: var(--s-3);
  overflow-y: auto;
  transition: width var(--t-base, 0.2s), min-width var(--t-base, 0.2s), opacity var(--t-base, 0.2s), padding var(--t-base, 0.2s);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}

.session-panel.collapsed {
  width: 0;
  min-width: 0;
  padding: 0;
  opacity: 0;
  overflow: hidden;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 600;
  color: var(--c-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: var(--s-2);
}

.panel-new-btn {
  margin-bottom: var(--s-3);
  width: 100%;
  text-align: center;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.session-item {
  padding: var(--s-2) var(--s-3);
  border-radius: var(--r-md);
  cursor: pointer;
  transition: all var(--t-fast, 0.15s);
  border: 1px solid transparent;
}

.session-item:hover {
  background: rgba(99, 102, 241, 0.06);
  border-color: var(--c-border);
}

.session-item.active {
  background: rgba(99, 102, 241, 0.1);
  border-color: var(--c-border-active);
}

.session-item.muted {
  color: var(--c-text-muted);
  cursor: default;
  font-size: 13px;
  text-align: center;
  padding: var(--s-4) var(--s-3);
}

.session-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-preview {
  font-size: 11px;
  color: var(--c-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 2px;
}

.session-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
  font-size: 10px;
  color: var(--c-text-muted);
}

.session-del {
  opacity: 0;
  color: var(--c-danger, #ef4444);
  padding: 2px 6px;
  font-size: 14px;
  line-height: 1;
  transition: opacity var(--t-fast, 0.15s);
}

.session-item:hover .session-del {
  opacity: 1;
}

.panel-expand-btn {
  width: 28px;
  background: var(--c-bg-card);
  border: 1px solid var(--c-border);
  border-left: none;
  border-radius: 0 var(--r-md) var(--r-md) 0;
  color: var(--c-text-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all var(--t-fast, 0.15s);
  flex-shrink: 0;
}

.panel-expand-btn:hover {
  background: rgba(99, 102, 241, 0.08);
  color: var(--c-text);
}

/* Chat container (inside layout) */
.agent-layout .chat-container {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 180px);
  max-width: none;
  margin: 0;
  width: auto;
}

/* Existing styles */
.chat-header { display: flex; align-items: center; justify-content: space-between; padding: var(--s-2) var(--s-2) var(--s-3); border-bottom: 1px solid var(--c-border); margin-bottom: var(--s-2) }
.chat-header-actions { display: flex; align-items: center; gap: var(--s-2) }
.chat-session-label { font-size: 12px; color: var(--c-text-muted); font-family: var(--ff-mono, monospace) }
.thread { flex: 1; overflow-y: auto; padding: 0 var(--s-2) var(--s-4); display: flex; flex-direction: column; gap: var(--s-4) }
.msg-row { display: flex; flex-direction: column; width: 100%; margin-bottom: var(--s-1) }
.msg-row.user { align-items: flex-end }
.msg-row.agent { align-items: flex-start }
.cbubble { font-size: 14px; padding: var(--s-3) var(--s-4); border-radius: var(--r-lg); max-width: 78%; line-height: 1.7 }
.cbubble.user { background: rgba(99, 102, 241, 0.15); border: 1px solid rgba(99, 102, 241, 0.22) }
.cbubble.agent { background: rgba(168, 85, 247, 0.08); border: 1px solid rgba(168, 85, 247, 0.15) }
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
.thinking { display: flex; flex-direction: column; align-items: flex-start; min-height: 40px; gap: 6px }
.dot-pulse { animation: pulse 1.5s ease-in-out infinite; font-size: 20px; letter-spacing: 4px; color: #C084FC }
.tool-cards { display: flex; flex-wrap: wrap; gap: 6px; width: 100% }
.tool-card { display: flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 6px; font-size: 12px; background: rgba(99, 102, 241, 0.08); border: 1px solid var(--c-border); color: var(--c-text-muted) }
.tool-card.running { border-color: rgba(99, 102, 241, 0.3); color: var(--c-accent) }
.tool-card.done { border-color: rgba(34, 197, 94, 0.3); color: #22c55e }
.tool-card.error { border-color: rgba(239, 68, 68, 0.3); color: #ef4444 }
.tool-icon { flex-shrink: 0; font-size: 13px }
.tool-name { font-weight: 600; white-space: nowrap }
.tool-stage { color: var(--c-accent); font-size: 11px; text-transform: capitalize }
.tool-summary { color: var(--c-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px }
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

/* Stop button */
.stop-btn {
  color: #ef4444;
  border-color: rgba(239, 68, 68, 0.3);
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
}
.stop-btn:hover { background: rgba(239, 68, 68, 0.1); }

/* Bubble actions (regenerate) */
.bubble-actions {
  margin-top: 2px;
  padding-left: var(--s-4);
  opacity: 0;
  transition: opacity var(--t-fast, 0.15s);
}
.cbubble.agent:hover + .bubble-actions,
.bubble-actions:hover { opacity: 1; }
.regen-btn {
  font-size: 12px;
  color: var(--c-text-muted);
  padding: 2px 8px;
}
.regen-btn:hover { color: var(--c-accent); }

/* Retry button */
.retry-btn {
  margin-top: 4px;
  margin-left: var(--s-4);
  color: var(--c-accent);
  font-size: 13px;
}

/* Stage pipeline */
.stage-pipeline {
  width: 100%;
  padding: 6px 2px 2px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.pipe-track {
  display: flex;
  align-items: center;
  gap: 0;
}
.pipe-node {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--c-border);
  flex-shrink: 0;
  transition: background var(--t-fast, 0.15s);
}
.pipe-node.done {
  background: #22c55e;
  box-shadow: 0 0 4px rgba(34, 197, 94, 0.4);
}
.pipe-node.active {
  background: var(--c-accent);
  box-shadow: 0 0 6px rgba(99, 102, 241, 0.6);
  animation: pulse-node 1s ease-in-out infinite;
}
.pipe-line {
  flex: 1;
  min-width: 4px;
  height: 2px;
  background: var(--c-border);
  transition: background var(--t-fast, 0.15s);
}
.pipe-line.done { background: #22c55e; }
.pipe-labels {
  display: flex;
  justify-content: space-between;
}
.pipe-label {
  font-size: 8px;
  color: var(--c-text-muted);
  white-space: nowrap;
  text-align: center;
  flex: 1;
}
.pipe-label.active { color: var(--c-accent); font-weight: 600; }
@keyframes pulse-node {
  0%, 100% { box-shadow: 0 0 4px rgba(99, 102, 241, 0.4); }
  50% { box-shadow: 0 0 10px rgba(99, 102, 241, 0.8); }
}

.panel-header-actions { display: flex; align-items: center; gap: var(--s-1) }

/* Session search */
.session-search {
  width: 100%;
  padding: 6px 10px;
  margin-bottom: var(--s-2);
  background: rgba(0,0,0,0.15);
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  color: var(--c-text);
  font-size: 12px;
  outline: none;
  transition: border-color var(--t-fast, 0.15s);
}
.session-search:focus { border-color: var(--c-border-active); }
.session-search::placeholder { color: var(--c-text-muted); }

/* Inline rename */
.session-title-edit { margin-bottom: 2px }
.rename-input {
  width: 100%;
  padding: 2px 6px;
  background: rgba(0,0,0,0.2);
  border: 1px solid var(--c-border-active);
  border-radius: 4px;
  color: var(--c-text);
  font-size: 13px;
  font-weight: 600;
  outline: none;
}

/* Hamburger button */
.hamburger-btn {
  display: none;
  padding: 4px 6px;
  margin-right: var(--s-2);
  color: var(--c-text-muted);
}
.hamburger-btn:hover { color: var(--c-text); }

/* Mobile drawer */
.drawer-backdrop {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  z-index: 99;
  backdrop-filter: blur(2px);
}
.drawer-close-btn {
  padding: 4px 6px;
  color: var(--c-text-muted);
}

/* Responsive */
@media (max-width: 860px) {
  .session-panel {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    z-index: 100;
    width: 280px;
    transform: translateX(-100%);
    transition: transform 0.25s ease;
    border-right: 1px solid var(--c-border);
    box-shadow: none;
  }
  .session-panel.drawer-open {
    transform: translateX(0);
    box-shadow: 4px 0 20px rgba(0,0,0,0.4);
  }
  .session-panel.collapsed { display: none; }
  .drawer-backdrop { display: block; }
  .panel-expand-btn { display: none; }
  .hamburger-btn { display: flex; }
  .agent-layout .chat-container {
    padding-left: 0;
  }
}

.ar-wrapper { width: 100%; }
.ar-wrapper strong {
  display: block;
  margin-bottom: var(--s-2);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #C084FC;
}

/* Collapsible thinking fold (推理叙述 + 内部思考) */
.think-fold {
  margin: 0 0 var(--s-2);
  padding: var(--s-2) var(--s-3);
  background: rgba(99, 102, 241, 0.05);
  border: 1px solid var(--c-border);
  border-radius: var(--r-md);
  font-size: 12px;
}
.think-fold summary {
  cursor: pointer;
  user-select: none;
  color: var(--c-text-muted);
  font-weight: 600;
  list-style: none;
  display: flex;
  align-items: center;
  gap: 4px;
}
.think-fold summary::-webkit-details-marker { display: none; }
.think-fold summary::before {
  content: '▸';
  display: inline-block;
  transition: transform 0.15s ease;
  color: var(--c-text-muted);
}
.think-fold[open] summary::before { transform: rotate(90deg); }
.think-fold-body {
  margin-top: var(--s-2);
  padding-top: var(--s-2);
  border-top: 1px dashed var(--c-border);
  color: var(--c-text-secondary);
  max-height: 320px;
  overflow-y: auto;
}
.think-sec { margin-bottom: var(--s-2); }
.think-sec:last-child { margin-bottom: 0; }
.think-sec-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--c-text-muted);
  margin-bottom: 4px;
}
.think-sec p { margin: 0; line-height: 1.6; }
</style>
