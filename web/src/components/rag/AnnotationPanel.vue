<template>
  <div class="card">
    <div class="ch"><h3>标注面板</h3></div>

    <div v-if="!item" class="empty">点结果表 / 被过滤 / Diff 里的任一条目查看详情并标注。</div>
    <div v-else>
      <div class="detail">
        <div class="detail-h">
          <strong>{{ item.title }}</strong>
          <span class="chip cs">{{ item.chunk_type || '—' }}</span>
        </div>
        <div class="meta mono">
          <span>{{ item.chunk_id }}</span>
          <span v-if="item.section"> · {{ item.section }}</span>
          <span v-if="item.rrf_raw != null"> · RRF {{ fmtScore(item.rrf_raw) }}</span>
        </div>
        <div class="text" v-html="highlighted"></div>
      </div>

      <div v-if="canAnnotate" class="ann">
        <div class="tiers">
          <button v-for="t in RELEVANCE_ORDER" :key="t" type="button" class="tier"
            :class="[`t${t}`, { on: curRel === t }]" @click="setRel(t)">
            {{ RELEVANCE_LABELS[t] }}
          </button>
        </div>
        <textarea v-model="note" rows="2" placeholder="备注（可选）"></textarea>
        <div class="ann-actions">
          <button class="btn bsm bp" @click="saveNote">保存备注</button>
          <button class="btn bsm bs" @click="clearAnn">清除标注</button>
        </div>
      </div>
      <p v-else class="hint">本次检索尚未留档成功，无法标注。重新检索后再试。</p>
    </div>

    <div class="metrics">
      <div class="metrics-h">本次指标</div>
      <div class="mgrid">
        <div v-for="m in metricList" :key="m.k" class="mcell">
          <div class="mcell-k">{{ m.label }}</div>
          <div class="mcell-v">{{ m.value }}</div>
          <div v-if="m.sub" class="mcell-sub">{{ m.sub }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { RELEVANCE_LABELS, RELEVANCE_ORDER, fmtPct, fmtScore } from '../../composables/useRagLab.js'

const props = defineProps({
  item: { type: Object, default: null },
  annotations: { type: Object, default: () => ({}) },
  metrics: { type: Object, default: null },
  canAnnotate: { type: Boolean, default: false },
})
const emit = defineEmits(['annotate', 'clear'])

const note = ref('')

// 切换选中条目时，把备注草稿同步到该条目的现有标注
watch(
  () => [props.item && props.item.chunk_id],
  () => {
    const a = props.item && props.annotations[props.item.chunk_id]
    note.value = (a && a.note) || ''
  },
  { immediate: true },
)

const curRel = computed(() => {
  const a = props.item && props.annotations[props.item.chunk_id]
  return a ? a.relevance : null
})

function setRel(t) {
  emit('annotate', props.item.chunk_id, t, note.value)
}
function saveNote() {
  if (props.item) emit('annotate', props.item.chunk_id, curRel.value, note.value)
}
function clearAnn() {
  note.value = ''
  if (props.item) emit('clear', props.item.chunk_id)
}

const highlighted = computed(() => {
  const text = (props.item && props.item.text) || ''
  const esc = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const terms = ((props.item && props.item.highlight_terms) || []).filter(Boolean)
  if (!terms.length) return esc.replace(/\n/g, '<br>')
  const re = new RegExp('(' + terms.map(escRe).join('|') + ')', 'gi')
  return esc.replace(re, '<mark>$1</mark>').replace(/\n/g, '<br>')
})
function escRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

const metricList = computed(() => {
  const m = props.metrics || {}
  const cc = m.channel_contribution || {}
  const both = cc.both != null ? fmtPct(cc.both, 0) : '—'
  return [
    { k: 'p_at_k', label: 'P@K', value: m.p_at_k == null ? '—' : m.p_at_k.toFixed(3), sub: `已标注 ${m.p_at_k_annotated ?? '—'}` },
    { k: 'hit_rate', label: '命中率', value: m.hit_rate == null ? '—' : fmtPct(m.hit_rate, 0) },
    { k: 'rerank_gain', label: '重排增益', value: m.rerank_gain == null ? '不适用' : (m.rerank_gain >= 0 ? '+' : '') + m.rerank_gain.toFixed(3) },
    { k: 'channel', label: '双路重合', value: both, sub: `both ${cc.counts?.both ?? 0} / ${m.k ?? 0}` },
    { k: 'filter_false_drop', label: '过滤误伤', value: m.filter_false_drop ?? '—', sub: `已标注 ${m.filter_false_drop_annotated ?? '—'}` },
    { k: 'citation_precision', label: '引用精度', value: m.citation_precision == null ? '不适用' : fmtPct(m.citation_precision, 1) },
    { k: 'groundedness', label: '引用支撑', value: m.answer_groundedness == null ? '不适用' : fmtPct(m.answer_groundedness, 1) },
  ]
})
</script>

<style scoped>
.detail { border: 1px solid var(--c-border); border-radius: var(--r-md); padding: var(--s-3); margin-bottom: var(--s-3) }
.detail-h { display: flex; gap: var(--s-2); align-items: center; margin-bottom: var(--s-1) }
.detail-h strong { color: #fff; font-size: 13px }
.meta { font-size: 11px; color: var(--c-text-secondary); margin-bottom: var(--s-2); word-break: break-all }
.text { font-size: 12px; line-height: 1.6; color: var(--c-text-secondary); max-height: 240px; overflow: auto }
.text :deep(mark) { background: rgba(168, 85, 247, 0.35); color: #fff; padding: 0 2px; border-radius: 2px }
.tiers { display: flex; gap: var(--s-2); margin-bottom: var(--s-2) }
.tier { flex: 1; padding: var(--s-2); border: 1px solid var(--c-border); border-radius: var(--r-md); background: none; color: var(--c-text-secondary); cursor: pointer; font-size: 12px; transition: all var(--t-fast) }
.tier:hover { border-color: var(--c-border-active) }
.tier.on { font-weight: 600; color: #fff }
.tier.t2.on { background: rgba(34,197,94,0.15); border-color: var(--c-success); color: var(--c-success) }
.tier.t1.on { background: rgba(245,158,11,0.15); border-color: var(--c-warn); color: var(--c-warn) }
.tier.t0.on { background: rgba(239,68,68,0.15); border-color: var(--c-danger); color: var(--c-danger) }
.ann textarea { margin-bottom: var(--s-2) }
.ann-actions { display: flex; gap: var(--s-2) }
.hint { font-size: 11px; color: var(--c-text-muted) }
.metrics { margin-top: var(--s-3) }
.metrics-h { font-size: 12px; font-weight: 600; color: var(--c-text-secondary); margin-bottom: var(--s-2) }
.mgrid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-2) }
.mcell { border: 1px solid var(--c-border); border-radius: var(--r-sm); padding: var(--s-2) }
.mcell-k { font-size: 10px; color: var(--c-text-muted) }
.mcell-v { font-size: 16px; color: var(--c-text); font-family: var(--ff-mono); margin-top: 2px }
.mcell-sub { font-size: 10px; color: var(--c-text-muted); margin-top: 2px }
.empty { color: var(--c-text-muted); font-style: italic; padding: var(--s-6); text-align: center }
</style>
