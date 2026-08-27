<template>
  <CollapsibleCard title="文献入库 & 库管理" icon="🗂" :expanded="false">
    <div v-if="err" class="err" @click="err = ''">{{ err }}</div>
    <div class="lib-grid">
      <div class="card">
        <div class="ch"><h3>文献入库</h3></div>

        <div class="utabs">
          <button type="button" :class="['ut', ingestMode === 'upload' && 'act']" @click="ingestMode = 'upload'">上传文献</button>
          <button type="button" :class="['ut', ingestMode === 'manual' && 'act']" @click="ingestMode = 'manual'">填写内容</button>
        </div>

        <div v-if="ingestMode === 'upload'">
          <div class="upload-card" :class="{ drag }" @dragover.prevent="drag = true" @dragleave="drag = false" @drop.prevent="onDrop">
            <div class="drop-zone">
              <p>拖拽文献文件到此处，或点击下方按钮选择</p>
              <input ref="fileInput" type="file" accept=".pdf,.txt,.md,.docx,.xlsx,.pptx" hidden @change="onPickFile" />
              <button type="button" class="btn ba" @click="pickFile">选择文献文件</button>
              <div v-if="selectedFile" class="file-card">
                <div class="file-card-name">{{ selectedFile.name }}</div>
                <button type="button" class="file-card-remove" title="移除文件" @click="clearFile">×</button>
              </div>
            </div>
          </div>

          <div class="row" style="margin-top: var(--s-3)">
            <div><label for="lmu-au">作者</label><input id="lmu-au" v-model="pfAuthors" list="rag-authorlist" /></div>
            <div><label for="lmu-yr">年份</label><input id="lmu-yr" v-model.number="pf.year" type="number" /></div>
            <div><label for="lmu-vn">出处</label><input id="lmu-vn" v-model="pf.venue" /></div>
          </div>
          <label for="lmu-tg" style="margin-top: var(--s-2)">标签</label>
          <input id="lmu-tg" v-model="pfTags" list="rag-taglist" />
          <button class="btn bp bf" :disabled="!selectedFile || uploading" @click="doUpload">{{ uploading ? '上传入库中…' : '上传入库' }}</button>
        </div>

        <div v-else>
          <label for="lmt-ti">标题</label><input id="lmt-ti" v-model="pf.title" />
          <label for="lmt-ab">摘要</label><textarea id="lmt-ab" v-model="pf.abstract" rows="2"></textarea>
          <label for="lmt-co">内容</label><textarea id="lmt-co" v-model="pf.content" rows="4"></textarea>
          <div class="row">
            <div><label for="lmt-au">作者</label><input id="lmt-au" v-model="pfAuthors" list="rag-authorlist" /></div>
            <div><label for="lmt-yr">年份</label><input id="lmt-yr" v-model.number="pf.year" type="number" /></div>
            <div><label for="lmt-vn">出处</label><input id="lmt-vn" v-model="pf.venue" /></div>
          </div>
          <label for="lmt-tg">标签</label><input id="lmt-tg" v-model="pfTags" list="rag-taglist" />
          <button class="btn bp bf" :disabled="ingesting" @click="doIngest">{{ ingesting ? '入库中…' : '入库' }}</button>
        </div>
      </div>

      <div class="card">
        <div class="ch">
          <h3>文献列表</h3>
          <span class="chip cs" v-if="snap" style="font-size: 11px">共 {{ snap.paper_count }} 篇 / {{ snap.chunk_count }} 块</span>
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
              <button class="btn bg bsm" style="color: var(--c-danger); margin-top: 4px" :disabled="deletingId === p.paper_id" @click="doDeletePaper(p.paper_id)">{{ deletingId === p.paper_id ? '删除中…' : '删除' }}</button>
            </div>
          </article>
          <p v-if="!ragPapers.length" class="empty">暂无文献。</p>
        </div>
      </div>
    </div>
  </CollapsibleCard>
</template>

<script setup>
import { inject, onMounted, ref } from 'vue'
import CollapsibleCard from '../CollapsibleCard.vue'

const { api } = inject('api')

const err = ref('')
let errTimer = null
const pfTags = ref('rag,llm')
const pfAuthors = ref('')
const pf = ref({ title: '', abstract: '', content: '', year: null, venue: '' })
const ragPapers = ref([])
const snap = ref(null)
const ingesting = ref(false)
const uploading = ref(false)
const reindexing = ref(false)
const deletingId = ref('')
const selectedFile = ref(null)
const fileInput = ref(null)
const ingestMode = ref('upload')
const drag = ref(false)

function showError(e) {
  err.value = (e && e.message) || '库操作失败'
  if (errTimer) clearTimeout(errTimer)
  errTimer = setTimeout(function () { err.value = '' }, 5000)
}

function fmtMeta(m) {
  const parts = []
  if (m.authors && m.authors.length) parts.push(m.authors.join(', '))
  if (m.year) parts.push(String(m.year))
  if (m.venue) parts.push(m.venue)
  return parts.join(' · ')
}

async function doIngest() {
  const tags = pfTags.value.split(',').map((s) => s.trim()).filter(Boolean)
  const authors = pfAuthors.value.split(',').map((s) => s.trim()).filter(Boolean)
  ingesting.value = true
  try {
    await api('/api/rag/papers', {
      method: 'POST',
      body: JSON.stringify({ ...pf.value, tags, authors, year: pf.value.year || null, venue: pf.value.venue || null }),
    })
    await Promise.all([loadRagPapers(), loadSnapshot()])
  } catch (e) { showError(e) } finally { ingesting.value = false }
}

function onPickFile(e) { selectedFile.value = e.target.files[0] || null }
function onDrop(e) {
  drag.value = false
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]
  if (f) selectedFile.value = f
}
function pickFile() { if (fileInput.value) fileInput.value.click() }
function clearFile() { selectedFile.value = null; if (fileInput.value) fileInput.value.value = '' }

async function doUpload() {
  const file = selectedFile.value
  if (!file) return
  uploading.value = true
  try {
    const params = new URLSearchParams()
    if (pfAuthors.value.trim()) params.set('authors', pfAuthors.value)
    if (pfTags.value.trim()) params.set('tags', pfTags.value)
    if (pf.value.year) params.set('year', String(pf.value.year))
    if (pf.value.venue.trim()) params.set('venue', pf.value.venue)
    const qs = params.toString()
    const fd = new FormData()
    fd.append('file', file)
    await api('/api/rag/upload' + (qs ? '?' + qs : ''), { method: 'POST', body: fd })
    selectedFile.value = null
    if (fileInput.value) fileInput.value.value = ''
    await Promise.all([loadRagPapers(), loadSnapshot()])
  } catch (e) { showError(e) } finally { uploading.value = false }
}

async function doDeletePaper(pid) {
  deletingId.value = pid
  try {
    await api('/api/rag/papers/' + pid, { method: 'DELETE' })
    await Promise.all([loadRagPapers(), loadSnapshot()])
  } catch (e) { showError(e) } finally { deletingId.value = '' }
}

async function doReindex() {
  reindexing.value = true
  try {
    await api('/api/rag/reindex', { method: 'POST' })
    await Promise.all([loadRagPapers(), loadSnapshot()])
  } catch (e) { showError(e) } finally { reindexing.value = false }
}

async function loadRagPapers() {
  try { ragPapers.value = await api('/api/rag/papers') } catch (e) { showError(e) }
}
async function loadSnapshot() {
  try { snap.value = await api('/api/rag/snapshot') } catch (e) { /* 非关键 */ }
}

onMounted(function () {
  loadRagPapers()
  loadSnapshot()
})
</script>

<style scoped>
.err { grid-column: 1 / -1; padding: var(--s-2) var(--s-3); border: 1px solid var(--c-danger); border-radius: var(--r-md); background: rgba(244,63,94,0.08); color: var(--c-danger); font-size: 12px; cursor: pointer; margin-bottom: var(--s-3) }
.lib-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-4) }
.row { display: flex; gap: var(--s-3); align-items: flex-end; flex-wrap: wrap }
.row > div { flex: 1 }
.upload-card { border: 1px dashed var(--c-border-active); border-radius: var(--r-lg); padding: var(--s-5); text-align: center; transition: all var(--t-fast) }
.upload-card.drag { background: rgba(99,102,241,0.08); border-color: var(--c-accent) }
.drop-zone p { color: var(--c-text-muted); font-size: 12px; margin-bottom: var(--s-3) }
.file-card { margin-top: var(--s-3); display: flex; justify-content: space-between; align-items: center; background: var(--c-bg-input); border: 1px solid var(--c-border); border-radius: var(--r-md); padding: var(--s-2) var(--s-3) }
.file-card-name { font-size: 12px; color: var(--c-text) }
.file-card-remove { background: none; border: none; color: var(--c-danger); cursor: pointer; font-size: 16px }
.rlist { display: flex; flex-direction: column; gap: 2px; margin-top: var(--s-3); max-height: 360px; overflow: auto }
.ri { display: flex; justify-content: space-between; align-items: center; padding: var(--s-3) var(--s-4); border: 1px solid var(--c-border); border-radius: var(--r-md); transition: all var(--t-fast); background: var(--c-bg-card) }
.ri:hover { background: rgba(99,102,241,0.08); border-color: var(--c-border-active) }
.ri strong { font-family: var(--ff-heading); font-size: 14px; display: block; color: #fff }
.empty { color: var(--c-text-muted); font-style: italic; text-align: center; padding: var(--s-6) }
@media (max-width: 768px) { .lib-grid { grid-template-columns: 1fr } }
</style>
