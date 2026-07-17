<template>
  <section class="chat-container">
    <div class="thread">
      <div v-for="(m, i) in chatMsgs" :key="i" :class="['cbubble', m.role]">
        <strong>{{ m.role === 'user' ? '你' : 'Agent' }}</strong>
        <p>{{ m.content }}</p>
      </div>
    </div>
    <div class="chat-input-box">
      <button class="chat-attach-btn" @click="$refs.chatFileInput.click()" title="上传文件">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
      </button>
      <input ref="chatFileInput" type="file" accept=".pdf,.txt,.md" hidden @change="onChatFile" />
      <textarea ref="chatTextarea" v-model="chatIn" rows="1" placeholder="输入消息...（Enter 发送，Shift+Enter 换行）" @keydown="onChatKey" @input="autoResize"></textarea>
      <button class="chat-send-btn" :class="{ active: chatIn.trim() }" @click="doChat" :disabled="!chatIn.trim()" title="发送">
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
import { inject, ref } from 'vue'

const { api } = inject('api')

const chatMsgs = ref([{ role: 'agent', content: '你好！上传 PDF 或粘贴论文文本即可自动完成翻译、四维分析、公式解读。' }])
const chatIn = ref('')
const chatReply = ref(null)
const chatAttach = ref(null)
const chatTextarea = ref(null)

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
    chatAttach.value = null
  } catch (e) {
    chatMsgs.value.push({ role: 'agent', content: '文件上传失败: ' + e.message })
    chatAttach.value = null
  }
}

async function doChat() {
  const m = chatIn.value.trim()
  if (!m) return
  chatMsgs.value.push({ role: 'user', content: m })
  chatReply.value = await api('/api/agent/chat', { method: 'POST', body: JSON.stringify({ message: m }) })
  chatMsgs.value.push({ role: 'agent', content: chatReply.value.reply })
  chatIn.value = ''
  const t = chatTextarea.value
  if (t) { t.style.height = 'auto' }
}
</script>

<style scoped>
.chat-container { display: flex; flex-direction: column; height: calc(100vh - 180px); max-width: 860px; margin: 0 auto; width: 100% }
.thread { flex: 1; overflow-y: auto; padding: 0 var(--s-2) var(--s-4); display: flex; flex-direction: column; gap: var(--s-4) }
.cbubble { font-size: 14px; padding: var(--s-3) var(--s-4); border-radius: var(--r-lg); max-width: 78%; line-height: 1.7 }
.cbubble.user { background: rgba(99, 102, 241, 0.15); border: 1px solid rgba(99, 102, 241, 0.22); align-self: flex-end }
.cbubble.agent { background: rgba(168, 85, 247, 0.08); border: 1px solid rgba(168, 85, 247, 0.15); align-self: flex-start }
.cbubble strong { display: block; margin-bottom: 2px; font-size: 10px; text-transform: uppercase; letter-spacing: .08em }
.cbubble p { margin: 0; white-space: pre-wrap }
.cbubble.user strong { color: var(--c-info) }
.cbubble.agent strong { color: #C084FC }
.chat-input-box { display: flex; align-items: flex-end; gap: var(--s-2); background: var(--c-bg-card); border: 1px solid var(--c-border); border-radius: var(--r-xl); padding: var(--s-2) var(--s-3); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); transition: border-color var(--t-fast) }
.chat-input-box:focus-within { border-color: var(--c-border-active); box-shadow: var(--glow-sm) }
.chat-input-box textarea { flex: 1; border: none !important; background: transparent; color: var(--c-text); font-size: 14px; padding: var(--s-2) 0; resize: none; outline: none; min-height: 24px; max-height: 160px; line-height: 1.5; font-family: var(--ff-body); box-shadow: none !important }
.chat-attach-btn { width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; border: none; background: transparent; color: var(--c-text-muted); cursor: pointer; border-radius: var(--r-sm); transition: all var(--t-fast); flex-shrink: 0 }
.chat-attach-btn:hover { color: var(--c-text); background: rgba(99, 102, 241, 0.1) }
.chat-attach-btn svg { width: 20px; height: 20px }
.chat-send-btn { width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; border: none; background: rgba(99, 102, 241, 0.12); color: var(--c-text-muted); cursor: pointer; border-radius: 50%; transition: all var(--t-fast); flex-shrink: 0 }
.chat-send-btn.active { background: linear-gradient(135deg, var(--c-accent), var(--c-accent-2)); color: #fff; box-shadow: 0 0 14px var(--c-accent-glow) }
.chat-send-btn.active:hover { transform: scale(1.06); box-shadow: 0 0 20px rgba(139, 92, 246, 0.5) }
.chat-send-btn:disabled { cursor: default }
.chat-send-btn svg { width: 16px; height: 16px }
.chat-attach-tag { display: flex; align-items: center; gap: var(--s-2); padding: var(--s-2) 0 0 }
</style>
