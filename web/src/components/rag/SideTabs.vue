<template>
  <div class="card">
    <div class="ctabs">
      <button type="button" class="ctab" :class="{ on: tab === 'filtered' }" @click="tab = 'filtered'">
        被过滤 {{ (retrieval?.dropped || []).length }}
      </button>
      <button type="button" class="ctab" :class="{ on: tab === 'diff' }" @click="tab = 'diff'">重排 Diff</button>
      <button type="button" class="ctab" :class="{ on: tab === 'gen' }" @click="tab = 'gen'">生成与引用</button>
    </div>

    <!-- ①被过滤 -->
    <div v-if="tab === 'filtered'">
      <p v-if="!dropped.length" class="empty">无被过滤条目。</p>
      <div v-for="(items, reason) in byReason" :key="reason" class="dgroup">
        <div class="dgroup-h">{{ reason }} <span class="chip cs">{{ items.length }}</span></div>
        <div v-for="d in items" :key="d.chunk_id" class="drow" :class="{ sel: d.chunk_id === selectedId }"
          @click="emit('select', d)">
          <span class="mono">{{ d.chunk_id }}</span>
          <span class="dtitle">{{ d.title }}</span>
          <span class="mono dim">RRF {{ fmtScore(d.rrf_raw) }}</span>
        </div>
      </div>
      <p v-if="retrieval?.dropped_truncated" class="hint">被过滤条目超过 200 条上限，仅显示前 200。</p>
      <p class="hint">若目标 chunk 不在此列，说明未进粗排，尝试调大 coarse_top_k。</p>
    </div>

    <!-- ②重排 Diff -->
    <div v-else-if="tab === 'diff'">
      <div v-if="rerankSkipped" class="skipbox">
        <div class="skipbox-t">重排已跳过</div>
        <div class="skipbox-r">{{ rerankReason }}</div>
      </div>
      <div v-else class="difflist">
        <div v-for="d in diffRows" :key="d.chunk_id" class="drow"
          :class="{ sel: d.chunk_id === selectedId }" @click="emit('select', d)">
          <span class="mono">{{ d.before == null ? '—' : d.before }} → {{ d.after == null ? '—' : d.after }}</span>
          <span class="delta" :class="deltaClass(d)">{{ fmtDelta(d.before, d.after) }}</span>
          <span class="dtitle">{{ d.title }}</span>
          <span class="mono dim">RRF {{ fmtScore(d.rrf_raw) }}</span>
          <span class="chip cs">{{ d.state }}</span>
        </div>
        <p v-if="!diffRows.length" class="empty">无重排数据。</p>
      </div>
    </div>

    <!-- ③生成与引用 -->
    <div v-else>
      <div v-if="!generation" class="empty">尚未问答。点 RunBar 的「问答」生成答案。</div>
      <div v-else>
        <div class="gen-status" :class="generation.generation_status">
          {{ statusText(generation) }}
        </div>
        <div class="answer" v-html="renderAnswer(generation.answer || '')"></div>

        <div class="stats">
          <div v-for="(label, k) in statPairs" :key="k" class="stat">
            <span class="stat-k">{{ label }}</span>
            <span class="stat-v">{{ fmtStat(generation.citation_stats[k]) }}</span>
          </div>
        </div>

        <details v-if="generation.sources && generation.sources.length">
          <summary>来源 {{ generation.sources.length }}</summary>
          <div v-for="s in generation.sources" :key="s.idx" class="src" @click="jumpSource(s)">
            [{{ s.idx }}] {{ s.title }} <span class="mono dim">{{ s.chunk_id }}</span>
          </div>
        </details>

        <details v-if="generation.uncited_sentences && generation.uncited_sentences.length">
          <summary>无引用句 {{ generation.uncited_sentences.length }}</summary>
          <p v-for="(s, i) in generation.uncited_sentences" :key="i" class="uncited">{{ s }}</p>
        </details>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { fmtDelta, fmtScore } from '../../composables/useRagLab.js'

const props = defineProps({
  retrieval: { type: Object, default: null },
  generation: { type: Object, default: null },
  selectedId: { type: String, default: null },
})
const emit = defineEmits(['select', 'jump-source'])

const tab = ref('filtered')

const dropped = computed(() => (props.retrieval && props.retrieval.dropped) || [])

const byReason = computed(() => {
  const out = {}
  for (const d of dropped.value) {
    const r = d.reason || 'unknown'
    ;(out[r] = out[r] || []).push(d)
  }
  return out
})

const rerankStage = computed(() => {
  const stages = (props.retrieval && props.retrieval.stages) || []
  return stages.find((s) => s.name === 'rerank') || null
})
const rerankSkipped = computed(() => rerankStage.value && rerankStage.value.status !== 'ok')
const rerankReason = computed(() => {
  const s = rerankStage.value
  return (s && s.reason) || (rerankSkipped.value ? '候选数 ≤ top_k，重排未执行' : '')
})

const diffRows = computed(() => {
  const before = (props.retrieval && props.retrieval.rerank_before) || []
  const after = (props.retrieval && props.retrieval.results) || []
  const beforeMap = {}
  before.forEach((e) => { beforeMap[e.chunk_id] = e })

  const rows = []
  for (const r of after) {
    const b = beforeMap[r.chunk_id]
    const bRank = b ? b.rank : null
    rows.push({
      chunk_id: r.chunk_id,
      title: r.title,
      rrf_raw: r.rrf_raw,
      before: bRank,
      after: r.rank,
      state: bRank == null ? '新进' : bRank === r.rank ? '不变' : bRank > r.rank ? '上升' : '下降',
      item: r,
    })
  }
  const afterIds = new Set(after.map((r) => r.chunk_id))
  for (const b of before) {
    if (!afterIds.has(b.chunk_id)) {
      rows.push({
        chunk_id: b.chunk_id,
        title: b.title,
        rrf_raw: b.rrf_raw,
        before: b.rank,
        after: null,
        state: '被挤出',
        item: b,
      })
    }
  }
  return rows
})

function deltaClass(d) {
  if (d.before == null || d.after == null) return d.after == null ? 'down' : 'up'
  const delta = d.before - d.after
  return delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat'
}

function statusText(g) {
  const s = g.generation_status
  if (s === 'ok') return '生成成功'
  if (s === 'no_results') return '无检索结果'
  if (s === 'no_provider') return '无可用 provider'
  if (s === 'llm_failed') return 'LLM 调用失败'
  return s || '未知'
}

const statPairs = {
  markers_total: '标记总数',
  markers_valid: '有效标记',
  markers_dangling: '悬空标记',
  sources_total: '来源总数',
  sources_cited: '已引用来源',
  sources_unused: '未用来源',
  sentences_total: '句子总数',
  sentences_uncited: '无引用句',
}

function fmtStat(v) {
  if (v == null) return '不适用'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(3)
  return String(v)
}

// 将 [N] 渲染为徽章，越界编号标红；输入先整体转义，杜绝 innerHTML 注入。
function renderAnswer(text) {
  const esc = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const stats = (props.generation && props.generation.citation_stats) || {}
  const total = stats.sources_total || 0
  return esc.replace(/\[(\d+)\]/g, (m, n) => {
    const num = Number(n)
    const valid = num >= 1 && num <= total
    return `<span class="cite ${valid ? '' : 'dangle'}">${m}</span>`
  })
}

function jumpSource(s) {
  emit('jump-source', s)
}
</script>

<style scoped>
.ctabs { margin-bottom: var(--s-3) }
.dgroup { margin-bottom: var(--s-3) }
.dgroup-h { font-size: 11px; font-weight: 600; color: var(--c-text-secondary); margin-bottom: var(--s-1); display: flex; align-items: center; gap: 6px }
.drow { display: flex; gap: var(--s-2); align-items: center; padding: var(--s-2) var(--s-3); border: 1px solid var(--c-border); border-radius: var(--r-md); margin-bottom: 4px; cursor: pointer; font-size: 12px }
.drow:hover { background: rgba(99,102,241,0.06) }
.drow.sel { background: rgba(99,102,241,0.1); border-color: var(--c-border-active) }
.dtitle { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-text) }
.mono { font-family: var(--ff-mono); font-size: 11px; white-space: nowrap }
.dim { color: var(--c-text-muted) }
.delta { white-space: nowrap; font-family: var(--ff-mono); font-size: 11px }
.delta.up { color: var(--c-success) }
.delta.down { color: var(--c-danger) }
.delta.flat { color: var(--c-text-muted) }
.hint { font-size: 11px; color: var(--c-text-muted); line-height: 1.5 }
.skipbox { border: 1px dashed var(--c-border); border-radius: var(--r-md); padding: var(--s-4); text-align: center; color: var(--c-text-muted) }
.skipbox-t { font-weight: 600; color: var(--c-warn); margin-bottom: var(--s-1) }
.skipbox-r { font-size: 12px }
.gen-status { font-size: 12px; font-weight: 600; margin-bottom: var(--s-2) }
.gen-status.ok { color: var(--c-success) }
.gen-status:not(.ok) { color: var(--c-warn) }
.answer { font-size: 13px; line-height: 1.7; white-space: pre-wrap; margin-bottom: var(--s-3) }
.answer :deep(.cite) { color: var(--c-accent); font-weight: 600 }
.answer :deep(.cite.dangle) { color: var(--c-danger); text-decoration: underline wavy rgba(239,68,68,0.6) }
.stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: var(--s-2); margin-bottom: var(--s-3) }
.stat { border: 1px solid var(--c-border); border-radius: var(--r-sm); padding: var(--s-2); display: flex; justify-content: space-between; font-size: 11px }
.stat-k { color: var(--c-text-secondary) }
.stat-v { color: var(--c-text); font-family: var(--ff-mono) }
.src { padding: var(--s-1) var(--s-2); cursor: pointer; border-radius: var(--r-sm); font-size: 12px }
.src:hover { background: rgba(99,102,241,0.06) }
.uncited { font-size: 12px; color: var(--c-text-secondary); text-decoration: underline wavy rgba(245,158,11,0.6); margin: var(--s-1) 0 }
.empty { color: var(--c-text-muted); font-style: italic; text-align: center; padding: var(--s-6) }
</style>
