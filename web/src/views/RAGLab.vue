<template>
  <section>
    <div v-if="error" class="rerr" @click="clearError">
      <span>{{ error }}</span>
      <button class="rerr-x" type="button" aria-label="关闭">×</button>
    </div>
    <div v-if="saveError" class="rerr warn" @click="clearSaveError">
      <span>{{ saveError }}</span>
      <button class="rerr-x" type="button" aria-label="关闭">×</button>
    </div>

    <datalist id="rag-taglist">
      <option v-for="t in allTags" :key="t" :value="t"></option>
    </datalist>
    <datalist id="rag-authorlist">
      <option v-for="a in allAuthors" :key="a" :value="a"></option>
    </datalist>

    <RunBar :loading="loading" :current-run="currentRun" @search="onSearch" @ask="onAsk"
      @compare="onCompareClick" @export="onExportClick" />

    <div class="lab-grid">
      <ParamPanel :params="params" :config="config" :retrieval="retrieval" @change="setParams" />

      <div class="lab-main">
        <StageWaterfall :stages="retrieval?.stages || []" :timings="retrieval?.timings_ms || {}" />
        <ResultTable :results="retrieval?.results || []" :annotations="annotations"
          :selected-id="selectedId" @select="onSelect" />
        <SideTabs :retrieval="retrieval" :generation="generation" :selected-id="selectedId"
          @select="onSelect" @jump-source="onJumpSource" />
      </div>

      <AnnotationPanel :item="selectedItem" :annotations="annotations" :metrics="metrics"
        :can-annotate="!!currentRun" @annotate="onAnnotate" @clear="onClearAnn" />
    </div>

    <RunCompare v-if="compareRuns.length" :runs="compareRuns" @close="compareRuns = []" />

    <RunHistory :runs="runs" :current-run-id="currentRunId" @load="onLoadRun" @rename="onRename"
      @delete="onDelete" @compare="onCompare" @export="onExport" @refresh="loadRuns" />

    <LibraryManager />
  </section>
</template>

<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { useRagLab } from '../composables/useRagLab.js'
import RunBar from '../components/rag/RunBar.vue'
import ParamPanel from '../components/rag/ParamPanel.vue'
import StageWaterfall from '../components/rag/StageWaterfall.vue'
import ResultTable from '../components/rag/ResultTable.vue'
import SideTabs from '../components/rag/SideTabs.vue'
import AnnotationPanel from '../components/rag/AnnotationPanel.vue'
import RunHistory from '../components/rag/RunHistory.vue'
import RunCompare from '../components/rag/RunCompare.vue'
import LibraryManager from '../components/rag/LibraryManager.vue'

const { api } = inject('api')
const compare = inject('compare', null)

const {
  params, setParams, retrieval, generation, currentRun, runs, config,
  loading, error, clearError, saveError, clearSaveError, doSearch, doAsk,
  annotate, clearAnnotation, loadRun, loadRuns, loadConfig, renameRun, deleteRun,
  fetchRuns,
} = useRagLab(api)

const selectedId = ref(null)
const compareRuns = ref([])
const allTags = ref([])
const allAuthors = ref([])

const annotations = computed(() => (currentRun.value && currentRun.value.annotations) || {})
const metrics = computed(() => (currentRun.value && currentRun.value.metrics) || null)
const currentRunId = computed(() => (currentRun.value && currentRun.value.run_id) || null)

// 选中项可能在 results / dropped / rerank_before 任意一处
const selectedItem = computed(() => {
  const id = selectedId.value
  if (!id || !retrieval.value) return null
  return (retrieval.value.results || []).find((r) => r.chunk_id === id)
    || (retrieval.value.dropped || []).find((d) => d.chunk_id === id)
    || (retrieval.value.rerank_before || []).find((e) => e.chunk_id === id)
    || null
})

function onSearch(q) { doSearch(q) }
function onAsk(q) { doAsk(q) }
function onSelect(item) { if (item) selectedId.value = item.chunk_id }
function onAnnotate(chunkId, relevance, note) { annotate(chunkId, relevance, note) }
function onClearAnn(chunkId) { clearAnnotation(chunkId) }
function onLoadRun(id) { loadRun(id); selectedId.value = null }
function onRename(id, label, tags) { renameRun(id, label, tags) }
function onDelete(id) { deleteRun(id) }
function onJumpSource(s) {
  if (s && s.chunk_id) selectedId.value = s.chunk_id
}

async function onCompare(ids) {
  if (!ids || ids.length < 2) return
  try {
    compareRuns.value = await fetchRuns(ids)
  } catch (e) {
    error.value = (e && e.message) || '对比加载失败'
  }
}
function onCompareClick() {
  if (!compareRuns.value.length) alert('请在「运行历史」中勾选 2-4 个 run 后点击对比。')
}
function onExportClick() {
  onExport(currentRunId.value ? [currentRunId.value] : [])
}

async function onExport(ids) {
  try {
    const list = (ids && ids.length ? ids : runs.value.slice(0, 1).map((r) => r.run_id)).filter(Boolean)
    if (!list.length) return alert('无可导出的 run。')
    const rs = await fetchRuns(list)
    const blob = (compare && compare.downloadBlob) || defaultDownload
    const csv = buildCsv(rs)
    blob(JSON.stringify(rs, null, 2), 'rag_runs.json', 'application/json')
    blob(csv, 'rag_runs.csv', 'text/csv')
  } catch (e) {
    error.value = (e && e.message) || '导出失败'
  }
}

function buildCsv(rs) {
  const keys = ['run_id', 'label', 'query', 'p_at_k', 'hit_rate', 'rerank_gain', 'filter_false_drop']
  const lines = [keys.join(',')]
  for (const r of rs) {
    const m = r.metrics || {}
    lines.push([
      r.run_id, '"' + String(r.label || '') + '"', '"' + String(r.query || '') + '"',
      m.p_at_k ?? '', m.hit_rate ?? '', m.rerank_gain ?? '', m.filter_false_drop ?? '',
    ].join(','))
  }
  return lines.join('\n')
}

function defaultDownload(content, name, mime) {
  const b = new Blob([content], { type: mime })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(b)
  a.download = name
  a.click()
  URL.revokeObjectURL(a.href)
}

async function loadMeta() {
  try {
    const [tags, authors] = await Promise.all([api('/api/rag/tags'), api('/api/rag/authors')])
    allTags.value = tags || []
    allAuthors.value = authors || []
  } catch (e) {
    // 自动补全非关键，静默
  }
}

onMounted(function () {
  loadRuns()
  loadConfig()
  loadMeta()
})
</script>

<style scoped>
.rerr { display: flex; justify-content: space-between; align-items: center; padding: var(--s-3) var(--s-4); border: 1px solid var(--c-danger); border-radius: var(--r-md); background: rgba(244, 63, 94, 0.08); color: var(--c-danger); font-size: 13px; cursor: pointer; margin-bottom: var(--s-4) }
.rerr-x { background: none; border: none; color: inherit; font-size: 18px; cursor: pointer; line-height: 1 }
.rerr.warn { border-color: var(--c-warn); background: rgba(245,158,11,0.08); color: var(--c-warn) }
.lab-grid { display: grid; grid-template-columns: 280px 1fr 320px; gap: var(--s-5); align-items: start; margin-top: var(--s-4) }
.lab-main { display: flex; flex-direction: column; gap: var(--s-4); min-width: 0 }
@media (max-width: 1100px) { .lab-grid { grid-template-columns: 1fr } }
</style>
