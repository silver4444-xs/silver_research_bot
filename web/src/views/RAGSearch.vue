<template>
  <section class="grid gr">
    <div v-if="ragError" class="rerr" @click="ragError = ''">
      <span>{{ ragError }}</span>
      <button class="rerr-x" type="button" aria-label="关闭">×</button>
    </div>

    <datalist id="rag-taglist">
      <option v-for="t in allTags" :key="t" :value="t"></option>
    </datalist>
    <datalist id="rag-authorlist">
      <option v-for="a in allAuthors" :key="a" :value="a"></option>
    </datalist>

    <div class="card">
      <div class="ch"><h3>文献检索</h3></div>
      <label for="rq">查询</label>
      <input id="rq" v-model="ragQ" />
      <div class="row">
        <div><label for="rk">Top K</label><input id="rk" v-model.number="ragK" type="number" min="1" max="20" /></div>
        <div><label for="rt">Tag</label><input id="rt" v-model="ragTag" list="rag-taglist" /></div>
        <div>
          <label for="rm">模态</label>
          <select id="rm" v-model="ragModality">
            <option value="">全部</option>
            <option value="text">文本</option>
            <option value="analysis">分析</option>
          </select>
        </div>
      </div>
      <div class="row" style="margin-top: var(--s-2)">
        <div><label for="rau">作者</label><input id="rau" v-model="ragAuthor" list="rag-authorlist" /></div>
        <div><label for="ryr">年份</label><input id="ryr" v-model.number="ragYear" type="number" /></div>
        <div><label for="rvn">出处</label><input id="rvn" v-model="ragVenue" /></div>
      </div>
      <div class="row" style="margin-top: var(--s-2)">
        <button class="btn bp bsm" :disabled="searching" @click="doRagSearch">{{ searching ? '检索中…' : '检索' }}</button>
        <button class="btn bs bsm" :disabled="searching" @click="doRagCtx">上下文</button>
        <button class="btn bs bsm" :disabled="searching" @click="doRagSuggest">建议</button>
        <label class="rrk"><input type="checkbox" v-model="ragRerank" /> LLM 重排</label>
        <span v-if="searched" class="chip cs" style="font-size: 11px">{{ ragResults.length }} 条</span>
      </div>
      <div v-if="ragResults.length" class="rlist" style="margin-top: var(--s-3)">
        <article v-for="r in ragResults" :key="r.chunk_id" class="ri">
          <div style="min-width: 0; flex: 1">
            <strong>{{ r.title }}</strong>
            <span class="chip cs" style="font-size: 10px; margin-left: 6px">{{ r.chunk_type }}</span>
            <p v-if="r.metadata" style="font-size: 11px; color: var(--c-text-secondary); margin: 2px 0">{{ fmtMeta(r.metadata) }}</p>
            <p style="font-size: 12px; color: var(--c-text-secondary)">{{ r.text && r.text.slice(0, 200) }}...</p>
          </div>
          <div style="text-align: right; min-width: 60px">
            <span style="font-size: 13px; color: var(--c-accent)">{{ (r.score * 100).toFixed(0) }}%</span>
          </div>
        </article>
      </div>
      <p v-if="!ragResults.length && searched" class="empty">未找到匹配文献。</p>
      <pre v-if="ragCtx" class="ctx">{{ ragCtx }}</pre>
      <pre v-if="ragSuggest" class="ctx">{{ ragSuggest }}</pre>
    </div>

    <div class="card">
      <div class="ch"><h3>文献入库</h3></div>

      <div class="utabs">
        <button type="button" :class="['ut', ingestMode === 'upload' && 'act']" @click="ingestMode = 'upload'">
          <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          上传文献
        </button>
        <button type="button" :class="['ut', ingestMode === 'manual' && 'act']" @click="ingestMode = 'manual'">
          <svg viewBox="0 0 24 24"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg>
          填写内容
        </button>
      </div>

      <div v-if="ingestMode === 'upload'">
        <div class="upload-card" :class="{ drag }" @dragover.prevent="drag = true" @dragleave="drag = false" @drop.prevent="onDrop">
          <div class="drop-zone">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 12 15 15"/></svg>
            <p>拖拽文献文件到此处，或点击下方按钮选择</p>
            <input ref="fileInput" type="file" accept=".pdf,.txt,.md,.docx,.xlsx,.pptx" hidden @change="onPickFile" />
            <button type="button" class="btn ba" @click="pickFile">选择文献文件</button>
            <div v-if="selectedFile" class="file-card">
              <div class="file-card-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></div>
              <div class="file-card-info">
                <div class="file-card-name">{{ selectedFile.name }}</div>
                <div class="file-card-meta">{{ fmtSize(selectedFile.size) }} · PDF / TXT / MD / DOCX / XLSX / PPTX</div>
              </div>
              <button type="button" class="file-card-remove" title="移除文件" @click="clearFile"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
            </div>
          </div>
        </div>

        <div class="row" style="margin-top: var(--s-3)">
          <div><label for="pau2">作者</label><input id="pau2" v-model="pfAuthors" list="rag-authorlist" /></div>
          <div><label for="pyr2">年份</label><input id="pyr2" v-model.number="pf.year" type="number" /></div>
          <div><label for="pvn2">出处</label><input id="pvn2" v-model="pf.venue" /></div>
        </div>
        <label for="pz2" style="margin-top: var(--s-2)">标签</label>
        <input id="pz2" v-model="pfTags" list="rag-taglist" />
        <button class="btn bp bf" :disabled="!selectedFile || uploading" @click="doUpload">{{ uploading ? '上传入库中…' : '上传入库' }}</button>
      </div>

      <div v-else>
        <label for="pt">标题</label>
        <input id="pt" v-model="pf.title" />
        <label for="pa">摘要</label>
        <textarea id="pa" v-model="pf.abstract" rows="2"></textarea>
        <label for="pc2">内容</label>
        <textarea id="pc2" v-model="pf.content" rows="4"></textarea>
        <div class="row">
          <div><label for="pau">作者</label><input id="pau" v-model="pfAuthors" list="rag-authorlist" /></div>
          <div><label for="pyr">年份</label><input id="pyr" v-model.number="pf.year" type="number" /></div>
          <div><label for="pvn">出处</label><input id="pvn" v-model="pf.venue" /></div>
        </div>
        <label for="pz">标签</label>
        <input id="pz" v-model="pfTags" list="rag-taglist" />
        <button class="btn bp bf" :disabled="ingesting" @click="doIngest">{{ ingesting ? '入库中…' : '入库' }}</button>
      </div>
    </div>

    <div class="card sp2">
      <div class="ch">
        <h3>文献列表</h3>
        <span class="chip cs" v-if="snap" style="font-size: 11px">共 {{ snap.paper_count }} 篇 / {{ snap.chunk_count }} 块{{ snap.updated_at ? ' · ' + snap.updated_at.slice(0, 10) : '' }}</span>
        <button class="btn bs bsm" :disabled="reindexing" @click="doReindex">{{ reindexing ? '重建中…' : '重建索引' }}</button>
        <button class="btn bs bsm" @click="loadRagPapers">刷新</button>
      </div>
      <div class="rlist">
        <article v-for="p in ragPapers" :key="p.paper_id" class="ri">
          <div style="min-width: 0; flex: 1">
            <strong>{{ p.title }}</strong>
            <p v-if="fmtMeta(p)" style="font-size: 11px; color: var(--c-text-secondary); margin: 2px 0">{{ fmtMeta(p) }}</p>
            <p style="font-size: 11px; color: var(--c-text-secondary)">{{ p.paper_id }} · {{ p.chunk_count || 0 }} 块</p>
          </div>
          <div style="text-align: right; font-size: 12px; flex-shrink: 0">
            <span v-if="(p.tags || []).length" class="chip cp">{{ (p.tags || []).join(',') }}</span>
            <div style="color: var(--c-text-secondary)">{{ (p.created_at || '').slice(0, 10) }}</div>
            <button class="btn bg bsm" style="color: var(--c-danger); margin-top: 4px" :disabled="deletingId === p.paper_id" @click="doDeletePaper(p.paper_id)">{{ deletingId === p.paper_id ? '删除中…' : '删除' }}</button>
          </div>
        </article>
        <p v-if="!ragPapers.length" class="empty">暂无文献。</p>
      </div>
    </div>
  </section>
</template>

<script setup>
import { inject, ref, onMounted } from 'vue'

const { api } = inject('api')

const ragQ = ref('retrieval augmented')
const ragK = ref(5)
const ragTag = ref('')
const ragModality = ref('')
const ragAuthor = ref('')
const ragYear = ref(null)
const ragVenue = ref('')
const ragRerank = ref(true)
const ragResults = ref([])
const ragCtx = ref('')
const ragSuggest = ref('')
const searched = ref(false)
const searching = ref(false)
const pfTags = ref('rag,llm')
const pfAuthors = ref('')
const pf = ref({ title: '', abstract: '', content: '', year: null, venue: '' })
const ragPapers = ref([])
const snap = ref(null)
const allTags = ref([])
const allAuthors = ref([])
const ingesting = ref(false)
const uploading = ref(false)
const reindexing = ref(false)
const deletingId = ref('')
const selectedFile = ref(null)
const fileInput = ref(null)
const ingestMode = ref('upload')
const drag = ref(false)
const ragError = ref('')

let errTimer = null
function showError(e) {
  ragError.value = (e && e.message) || '请求失败'
  if (errTimer) clearTimeout(errTimer)
  errTimer = setTimeout(function () { ragError.value = '' }, 5000)
}

function fmtMeta(m) {
  const parts = []
  if (m.authors && m.authors.length) parts.push(m.authors.join(', '))
  if (m.year) parts.push(String(m.year))
  if (m.venue) parts.push(m.venue)
  return parts.join(' · ')
}

async function doRagSearch() {
  searching.value = true
  ragError.value = ''
  try {
    const r = await api('/api/rag/search', {
      method: 'POST',
      body: JSON.stringify({
        query: ragQ.value,
        top_k: ragK.value,
        tag: ragTag.value || null,
        modality: ragModality.value || null,
        author: ragAuthor.value || null,
        year: ragYear.value || null,
        venue: ragVenue.value || null,
        rerank: ragRerank.value,
      }),
    })
    ragResults.value = r.results || []
    ragCtx.value = ''
    ragSuggest.value = ''
    searched.value = true
  } catch (e) {
    showError(e)
  } finally {
    searching.value = false
  }
}

async function doRagCtx() {
  searching.value = true
  ragError.value = ''
  try {
    const r = await api('/api/rag/context', {
      method: 'POST',
      body: JSON.stringify({
        query: ragQ.value,
        top_k: ragK.value,
        tag: ragTag.value || null,
        modality: ragModality.value || null,
        author: ragAuthor.value || null,
        year: ragYear.value || null,
        venue: ragVenue.value || null,
      }),
    })
    ragCtx.value = r.context
    ragResults.value = r.results || []
    ragSuggest.value = ''
    searched.value = true
  } catch (e) {
    showError(e)
  } finally {
    searching.value = false
  }
}

async function doRagSuggest() {
  searching.value = true
  ragError.value = ''
  try {
    ragSuggest.value = JSON.stringify(
      await api('/api/rag/suggest', {
        method: 'POST',
        body: JSON.stringify({
          query: ragQ.value,
          top_k: ragK.value,
          tag: ragTag.value || null,
          modality: ragModality.value || null,
          author: ragAuthor.value || null,
          year: ragYear.value || null,
          venue: ragVenue.value || null,
        }),
      }),
      null,
      2,
    )
  } catch (e) {
    showError(e)
  } finally {
    searching.value = false
  }
}

async function doIngest() {
  const tags = pfTags.value.split(',').map(function (s) { return s.trim() }).filter(Boolean)
  const authors = pfAuthors.value.split(',').map(function (s) { return s.trim() }).filter(Boolean)
  ingesting.value = true
  ragError.value = ''
  try {
    await api('/api/rag/papers', {
      method: 'POST',
      body: JSON.stringify({
        ...pf.value,
        tags: tags,
        authors: authors,
        year: pf.value.year || null,
        venue: pf.value.venue || null,
      }),
    })
    await loadRagPapers()
    await loadSnapshot()
    await loadMeta()
  } catch (e) {
    showError(e)
  } finally {
    ingesting.value = false
  }
}

function onPickFile(e) {
  selectedFile.value = e.target.files[0] || null
}

function onDrop(e) {
  drag.value = false
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]
  if (f) selectedFile.value = f
}

function pickFile() {
  if (fileInput.value) fileInput.value.click()
}

function clearFile() {
  selectedFile.value = null
  if (fileInput.value) fileInput.value.value = ''
}

function fmtSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return (bytes / Math.pow(1024, i)).toFixed(i ? 1 : 0) + ' ' + units[i]
}

async function doUpload() {
  const file = selectedFile.value
  if (!file) return
  uploading.value = true
  ragError.value = ''
  try {
    const params = new URLSearchParams()
    if (pfAuthors.value.trim()) params.set('authors', pfAuthors.value)
    if (pfTags.value.trim()) params.set('tags', pfTags.value)
    if (pf.value.year) params.set('year', String(pf.value.year))
    if (pf.value.venue.trim()) params.set('venue', pf.value.venue)
    const qs = params.toString()
    const fd = new FormData()
    fd.append('file', file)
    const rec = await api('/api/rag/upload' + (qs ? '?' + qs : ''), { method: 'POST', body: fd })
    pf.value.title = rec.title || ''
    selectedFile.value = null
    if (fileInput.value) fileInput.value.value = ''
    await loadRagPapers()
    await loadSnapshot()
    await loadMeta()
  } catch (e) {
    showError(e)
  } finally {
    uploading.value = false
  }
}

async function doDeletePaper(pid) {
  deletingId.value = pid
  ragError.value = ''
  try {
    await api('/api/rag/papers/' + pid, { method: 'DELETE' })
    await loadRagPapers()
    await loadSnapshot()
  } catch (e) {
    showError(e)
  } finally {
    deletingId.value = ''
  }
}

async function doReindex() {
  reindexing.value = true
  ragError.value = ''
  try {
    await api('/api/rag/reindex', { method: 'POST' })
    await loadRagPapers()
    await loadSnapshot()
  } catch (e) {
    showError(e)
  } finally {
    reindexing.value = false
  }
}

async function loadRagPapers() {
  try {
    ragPapers.value = await api('/api/rag/papers')
  } catch (e) {
    showError(e)
  }
}

async function loadSnapshot() {
  try {
    snap.value = await api('/api/rag/snapshot')
  } catch (e) {
    // 快照非关键，静默
  }
}

async function loadMeta() {
  try {
    const [tags, authors] = await Promise.all([
      api('/api/rag/tags'),
      api('/api/rag/authors'),
    ])
    allTags.value = tags || []
    allAuthors.value = authors || []
  } catch (e) {
    // 自动补全非关键，静默
  }
}

onMounted(function () {
  loadRagPapers()
  loadSnapshot()
  loadMeta()
})
</script>

<style scoped>
.grid { display: grid; gap: var(--s-5) }
.gr { grid-template-columns: 1fr 1fr }
.sp2 { grid-column: span 2 }
.rerr {
  grid-column: span 2; display: flex; justify-content: space-between; align-items: center;
  padding: var(--s-3) var(--s-4); border: 1px solid var(--c-danger);
  border-radius: var(--r-md); background: rgba(244, 63, 94, 0.08);
  color: var(--c-danger); font-size: 13px; cursor: pointer;
}
.rerr-x { background: none; border: none; color: inherit; font-size: 18px; cursor: pointer; line-height: 1 }
.rlist { display: flex; flex-direction: column; gap: 2px; margin-top: var(--s-3) }
.ri {
  display: flex; justify-content: space-between; align-items: center;
  padding: var(--s-3) var(--s-4); border: 1px solid var(--c-border);
  border-radius: var(--r-md); cursor: pointer; transition: all var(--t-fast);
  background: var(--c-bg-card); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
}
.ri:hover { background: rgba(99, 102, 241, 0.08); border-color: var(--c-border-active); box-shadow: var(--glow-sm); transform: translateX(2px) }
.ri strong { font-family: var(--ff-heading); font-size: 14px; display: block; color: #fff }
.row { display: flex; gap: var(--s-3); align-items: flex-end; flex-wrap: wrap }
.row > div { flex: 1 }
.rrk { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-text-secondary); cursor: pointer; white-space: nowrap }
.ctx {
  white-space: pre-wrap; font-size: 12px; line-height: 1.5;
  color: var(--c-text-secondary); background: rgba(0, 0, 0, 0.25);
  padding: var(--s-3); border-radius: var(--r-md); border: 1px solid var(--c-border);
  margin-top: var(--s-3); max-height: 300px; overflow: auto;
}
@media (max-width: 768px) {
  .gr { grid-template-columns: 1fr }
  .sp2 { grid-column: span 1 }
}
</style>
