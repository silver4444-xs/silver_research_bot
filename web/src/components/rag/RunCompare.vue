<template>
  <div class="card">
    <div class="ch">
      <h3>运行对比（{{ runs.length }} 组）</h3>
      <div class="actions">
        <button class="btn bs bsm" @click="exportJSON">JSON</button>
        <button class="btn bs bsm" @click="exportCSV">CSV</button>
        <button class="btn bg bsm" @click="emit('close')">关闭</button>
      </div>
    </div>

    <div class="cmp-wrap">
      <table class="cmp">
        <thead>
          <tr>
            <th class="rowhead"></th>
            <th v-for="r in runs" :key="r.run_id">
              <div class="run-name">{{ r.label || r.run_id }}</div>
              <div class="run-id mono">{{ r.run_id }}</div>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in metricRows" :key="m.k">
            <th class="rowhead">{{ m.label }}</th>
            <td v-for="r in runs" :key="r.run_id" class="mono">{{ fmtMetric(m, r) }}</td>
          </tr>
          <tr v-for="p in paramRows" :key="p">
            <th class="rowhead">{{ p }}</th>
            <td v-for="r in runs" :key="r.run_id" :class="{ diff: isDiff(p) }">{{ paramVal(r, p) }}</td>
          </tr>
          <tr>
            <th class="rowhead">结果集</th>
            <td v-for="r in runs" :key="r.run_id" class="mono">
              {{ (r.results || []).length }} 条 · 交集 {{ sharedCount(r) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <details class="sets">
      <summary>结果集交并差</summary>
      <p class="sets-intersect mono">{{ intersectIds.join(' · ') || '无共同 chunk' }}</p>
      <div v-for="r in runs" :key="r.run_id" class="sets-row">
        <strong>{{ r.label || r.run_id }}</strong> 独有：
        <span class="mono">{{ diffIds(r).join(' · ') || '无' }}</span>
      </div>
    </details>
  </div>
</template>

<script setup>
import { computed, inject } from 'vue'

const props = defineProps({
  runs: { type: Array, default: () => [] },
})
const emit = defineEmits(['close'])

const compare = inject('compare', null)
const downloadBlob = (compare && compare.downloadBlob) || function (content, name, mime) {
  const blob = new Blob([content], { type: mime })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = name
  a.click()
  URL.revokeObjectURL(a.href)
}

const metricRows = [
  { k: 'p_at_k', label: 'P@K' },
  { k: 'hit_rate', label: '命中率' },
  { k: 'rerank_gain', label: '重排增益' },
  { k: 'filter_false_drop', label: '过滤误伤' },
  { k: 'citation_precision', label: '引用精度' },
  { k: 'answer_groundedness', label: '引用支撑' },
]

function fmtMetric(m, r) {
  const v = (r.metrics || {})[m.k]
  if (v == null) return '—'
  if (typeof v === 'number' && m.k !== 'filter_false_drop') return v.toFixed(3)
  return String(v)
}

const paramRows = computed(() => {
  const keys = new Set()
  for (const r of props.runs) Object.keys(r.params_effective || {}).forEach((k) => keys.add(k))
  return Array.from(keys)
})

function paramVal(r, k) {
  const v = (r.params_effective || {})[k]
  if (v == null) return '—'
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  return String(v)
}

function isDiff(k) {
  const vals = props.runs.map((r) => paramVal(r, k))
  return new Set(vals).size > 1
}

const idSets = computed(() => props.runs.map((r) => new Set((r.results || []).map((x) => x.chunk_id))))
const intersectIds = computed(() => {
  const first = idSets.value[0]
  if (!first) return []
  return Array.from(first).filter((id) => idSets.value.every((s) => s.has(id)))
})
function sharedCount(r) {
  const s = new Set((r.results || []).map((x) => x.chunk_id))
  return intersectIds.value.filter((id) => s.has(id)).length
}
function diffIds(r) {
  const s = new Set((r.results || []).map((x) => x.chunk_id))
  const others = idSets.value.filter((x) => x !== s)
  return Array.from(s).filter((id) => others.every((o) => !o.has(id)))
}

function exportJSON() {
  downloadBlob(JSON.stringify(props.runs, null, 2), 'rag_runs_compare.json', 'application/json')
}
function exportCSV() {
  const cols = ['run_id', ...paramRows.value, ...metricRows.map((m) => m.k)]
  const lines = [cols.join(',')]
  for (const r of props.runs) {
    const row = [r.run_id]
    for (const p of paramRows.value) row.push('"' + String((r.params_effective || {})[p] ?? '') + '"')
    for (const m of metricRows.value) row.push(String((r.metrics || {})[m.k] ?? ''))
    lines.push(row.join(','))
  }
  downloadBlob(lines.join('\n'), 'rag_runs_compare.csv', 'text/csv')
}
</script>

<style scoped>
.actions { display: flex; gap: var(--s-2) }
.cmp-wrap { overflow-x: auto }
.cmp { width: 100%; border-collapse: collapse; font-size: 12px }
.cmp th, .cmp td { border: 1px solid var(--c-border); padding: 6px 10px; text-align: left }
.cmp thead th { background: rgba(99,102,241,0.06); color: var(--c-text) }
.cmp .rowhead { color: var(--c-text-secondary); font-weight: 500; white-space: nowrap; width: 120px }
.run-name { font-size: 13px; color: #fff }
.run-id { font-size: 10px; color: var(--c-text-muted) }
.cmp td.diff { background: rgba(245,158,11,0.08); color: var(--c-warn) }
.mono { font-family: var(--ff-mono); font-size: 11px }
.sets { margin-top: var(--s-3) }
.sets-intersect { font-size: 11px; color: var(--c-text-secondary); word-break: break-all }
.sets-row { font-size: 12px; margin: var(--s-1) 0; color: var(--c-text-secondary) }
.sets-row strong { color: var(--c-text) }
</style>
