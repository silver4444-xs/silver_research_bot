<template>
  <div class="rtbl-wrap">
    <table v-if="results.length" class="rtbl">
      <thead>
        <tr>
          <th @click="toggle('rank')">#</th>
          <th @click="toggle('delta')">Δ</th>
          <th @click="toggle('rrf_raw')">RRF ▾</th>
          <th @click="toggle('score')" :title="SCORE_TOOLTIP">score *</th>
          <th>BM25</th>
          <th>VEC</th>
          <th>标题</th>
          <th>类型</th>
          <th>标注</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in sorted" :key="r.chunk_id" :class="{ sel: r.chunk_id === selectedId }"
          @click="emit('select', r)">
          <td class="mono">{{ r.rank }}</td>
          <td class="delta" :class="deltaClass(r)">{{ fmtDelta(r.rank_before_rerank, r.rank) }}</td>
          <td class="mono">{{ fmtScore(r.rrf_raw) }}</td>
          <td class="mono dim">{{ fmtScore(r.score) }}</td>
          <td class="mono">{{ channelCell(r, 'bm25') }}</td>
          <td class="mono">{{ channelCell(r, 'vector') }}</td>
          <td class="title">{{ r.title }}</td>
          <td><span class="chip cs">{{ r.chunk_type }}</span></td>
          <td class="mono">{{ annLabel(r.chunk_id) }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty">尚无结果。</p>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { RELEVANCE_LABELS, fmtDelta, fmtScore } from '../../composables/useRagLab.js'

const SCORE_TOOLTIP = '相对分：归一化后 top-1 恒为 1.0，不可跨 query 比较。跨 query 请用 RRF 列。'

const props = defineProps({
  results: { type: Array, default: () => [] },
  annotations: { type: Object, default: () => ({}) },
  selectedId: { type: String, default: null },
})
const emit = defineEmits(['select'])

const sortKey = ref('rank')
const sortDir = ref(1)

function toggle(key) {
  if (sortKey.value === key) sortDir.value = -sortDir.value
  else { sortKey.value = key; sortDir.value = 1 }
}

function val(r, key) {
  if (key === 'delta') return (r.rank_before_rerank == null ? r.rank : r.rank_before_rerank) - r.rank
  return r[key]
}

const sorted = computed(() => {
  const arr = props.results.slice()
  const k = sortKey.value
  arr.sort((a, b) => {
    const av = val(a, k)
    const bv = val(b, k)
    if (av == null) return 1
    if (bv == null) return -1
    return (av < bv ? -1 : av > bv ? 1 : 0) * sortDir.value
  })
  return arr
})

function channelCell(r, kind) {
  const ch = (r.channels && r.channels[kind]) || []
  if (!ch.length) return '—'
  return ch.map((c) => `q${c.q_idx}#${c.rank}`).join(' ')
}

function deltaClass(r) {
  if (r.rank_before_rerank == null) return 'flat'
  const d = r.rank_before_rerank - r.rank
  return d > 0 ? 'up' : d < 0 ? 'down' : 'flat'
}

function annLabel(cid) {
  const a = props.annotations[cid]
  if (!a) return '—'
  return RELEVANCE_LABELS[a.relevance] || String(a.relevance)
}
</script>

<style scoped>
.rtbl-wrap { overflow-x: auto }
.rtbl { width: 100%; border-collapse: collapse; font-size: 12px }
.rtbl th { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--c-border); color: var(--c-text-secondary); font-weight: 500; font-size: 11px; cursor: pointer; white-space: nowrap; user-select: none }
.rtbl th:hover { color: var(--c-text) }
.rtbl td { padding: 6px 8px; border-bottom: 1px solid rgba(99,102,241,0.06); vertical-align: top }
.rtbl tbody tr { cursor: pointer }
.rtbl tbody tr:hover td { background: rgba(99,102,241,0.05) }
.rtbl tr.sel td { background: rgba(99,102,241,0.1) }
.mono { font-family: var(--ff-mono); font-size: 11px; white-space: nowrap }
.dim { color: var(--c-text-muted) }
.title { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap }
.delta { white-space: nowrap }
.delta.up { color: var(--c-success) }
.delta.down { color: var(--c-danger) }
.delta.flat { color: var(--c-text-muted) }
.empty { color: var(--c-text-muted); font-style: italic; text-align: center; padding: var(--s-6) }
</style>
