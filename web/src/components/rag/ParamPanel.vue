<template>
  <div class="card">
    <div class="ch"><h3>参数面板</h3></div>

    <details open>
      <summary>检索</summary>
      <div class="grid2">
        <div>
          <label for="rlp-topk">top_k</label>
          <input id="rlp-topk" type="number" min="1" max="20"
            :value="params.top_k" @input="upd('top_k', $event.target.value)" />
        </div>
        <div>
          <label for="rlp-coarse">coarse_top_k *</label>
          <input id="rlp-coarse" type="number" min="1" max="100"
            :value="params.coarse_top_k" @input="upd('coarse_top_k', $event.target.value)" />
        </div>
      </div>
      <label for="rlp-min">min_score</label>
      <input id="rlp-min" type="number" min="0" max="1" step="0.01"
        :value="params.min_score" @input="upd('min_score', $event.target.value)" />
      <p class="hint">阈值比较的是归一化后的相对分，top-1 恒为 1.0，永远过滤不掉 top-1。</p>

      <div class="cb-row">
        <label class="cb"><input type="checkbox" :checked="params.rerank"
          @change="upd('rerank', $event.target.checked)" /> LLM 重排</label>
        <label class="cb"><input type="checkbox" :checked="params.bypass_expansion_cache"
          @change="upd('bypass_expansion_cache', $event.target.checked)" /> 绕过扩展缓存</label>
      </div>
      <p v-if="retrieval && retrieval.expansion_source === 'cached'" class="hint warn">
        本次扩展命中缓存（expansion_source=cached），A/B 对比时请勾选「绕过扩展缓存」消除混淆。
      </p>
    </details>

    <details open>
      <summary>过滤</summary>
      <div class="grid2">
        <div>
          <label for="rlp-tag">tag</label>
          <input id="rlp-tag" :value="params.tag" list="rag-taglist" @input="upd('tag', $event.target.value)" />
        </div>
        <div>
          <label for="rlp-mod">modality</label>
          <select id="rlp-mod" :value="params.modality" @change="upd('modality', $event.target.value)">
            <option value="">全部</option>
            <option value="text">文本</option>
            <option value="analysis">分析</option>
          </select>
        </div>
        <div>
          <label for="rlp-author">author</label>
          <input id="rlp-author" :value="params.author" list="rag-authorlist" @input="upd('author', $event.target.value)" />
        </div>
        <div>
          <label for="rlp-year">year</label>
          <input id="rlp-year" type="number" :value="params.year" @input="upd('year', $event.target.value)" />
        </div>
        <div>
          <label for="rlp-venue">venue</label>
          <input id="rlp-venue" :value="params.venue" @input="upd('venue', $event.target.value)" />
        </div>
      </div>
    </details>

    <details open>
      <summary>生成</summary>
      <div class="grid2">
        <div>
          <label for="rlp-temp">temperature</label>
          <input id="rlp-temp" type="number" min="0" max="2" step="0.1"
            :value="params.temperature" @input="upd('temperature', $event.target.value)" />
        </div>
        <div>
          <label for="rlp-maxch">max_answer_chars</label>
          <input id="rlp-maxch" type="number" min="200" max="20000" step="100"
            :value="params.max_answer_chars" @input="upd('max_answer_chars', $event.target.value)" />
        </div>
      </div>
    </details>

    <details open>
      <summary>库信息（只读）</summary>
      <dl v-if="config" class="ro">
        <template v-for="(v, k) in config.config" :key="k">
          <dt>{{ k }} <span class="mut" :class="mutScope(k)">{{ mutLabel(k) }}</span></dt>
          <dd>{{ fmtVal(v) }}</dd>
        </template>
      </dl>
      <p v-else class="hint">未加载到 /api/rag/config。</p>
    </details>

    <details v-if="retrieval && retrieval.params_effective" open>
      <summary>实际生效值</summary>
      <dl class="ro">
        <template v-for="(v, k) in retrieval.params_effective" :key="k">
          <dt>{{ k }}</dt>
          <dd>{{ fmtVal(v) }}</dd>
        </template>
      </dl>
    </details>
  </div>
</template>

<script setup>
const props = defineProps({
  params: { type: Object, required: true },
  config: { type: Object, default: null },
  retrieval: { type: Object, default: null },
})
const emit = defineEmits(['change'])

// 不可变更新：构造新对象并整体上交，绝不就地改 props.params
function upd(key, value) {
  const next = { ...props.params, [key]: value }
  emit('change', next)
}

const SCOPE_LABEL = { request: '请求可覆盖', restart: '需重启', reindex: '需 reindex' }
function mutLabel(k) {
  const m = props.config && props.config.mutability && props.config.mutability[k]
  return SCOPE_LABEL[(m && m.scope) || 'restart']
}
function mutScope(k) {
  const m = props.config && props.config.mutability && props.config.mutability[k]
  return (m && m.scope) || 'restart'
}
function fmtVal(v) {
  if (v == null) return '—'
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
</script>

<style scoped>
details { border: 1px solid var(--c-border); border-radius: var(--r-md); margin-bottom: var(--s-3); overflow: hidden }
summary { padding: var(--s-2) var(--s-3); cursor: pointer; font-size: 12px; font-weight: 600; color: var(--c-text-secondary); background: rgba(99,102,241,0.04); user-select: none }
summary:hover { background: rgba(99,102,241,0.08) }
details > label, details > .grid2, details > .cb-row, details > .hint, details > .ro { margin: var(--s-2) var(--s-3) }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: var(--s-2) }
.cb-row { display: flex; gap: var(--s-4); flex-wrap: wrap }
.cb { margin: 0 }
.hint { font-size: 11px; color: var(--c-text-muted); line-height: 1.5; margin: var(--s-1) var(--s-3) }
.hint.warn { color: var(--c-warn) }
.ro { display: grid; grid-template-columns: minmax(140px, auto) 1fr; gap: 2px 12px; font-size: 12px; margin: var(--s-2) var(--s-3) var(--s-3) }
.ro dt { color: var(--c-text-secondary); font-family: var(--ff-mono) }
.ro dd { margin: 0; color: var(--c-text); word-break: break-all }
.mut { display: inline-block; font-size: 9px; padding: 1px 6px; border-radius: 10px; margin-left: 6px; vertical-align: middle }
.mut.request { background: rgba(34,197,94,0.12); color: var(--c-success) }
.mut.restart { background: rgba(245,158,11,0.12); color: var(--c-warn) }
.mut.reindex { background: rgba(239,68,68,0.12); color: var(--c-danger) }
@media (max-width: 768px) { .grid2 { grid-template-columns: 1fr } }
</style>
