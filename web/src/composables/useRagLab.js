import { reactive, ref } from 'vue'

// RAG 验收台（RAG Lab）的状态与 API 编排。
// 契约见 docs/superpowers/specs/2026-08-25-rag-lab-page-design.md §5。
//
// 设计要点：
// - params 是整棵配置对象，更新一律 replace（不可变），绝不就地 mutate——贴合全局
//   coding-style 规则，也让「实际生效值回显」只需读 params_effective 一处。
// - 标注（annotations）落在 currentRun 里：一次 PUT 同时回写标注与重算后的 metrics，
//   单一响应即单一事实来源，前端不重复实现 §5.4 的七项指标公式。

// 相关性三档，与后端 RELEVANCE_GAIN 对齐
export const RELEVANCE_LABELS = { 2: '相关', 1: '部分相关', 0: '不相关' }
export const RELEVANCE_ORDER = [2, 1, 0]

// ── 默认参数（页面上可覆盖的字段）──────────────────────────────
export function defaultParams() {
  return {
    top_k: 5,
    coarse_top_k: 20,
    min_score: 0.0,
    rerank: true,
    tag: '',
    modality: '',
    author: '',
    year: null,
    venue: '',
    bypass_expansion_cache: false,
    // 生成（ask）专属
    temperature: 0.0,
    max_answer_chars: 2000,
  }
}

// 表单参数 → 请求体。空串/空值一律折成 null，避免把空串当过滤条件传给后端。
function toRequestPayload(params) {
  const p = params || defaultParams()
  const nullable = (v) => (v === '' || v === undefined ? null : v)
  return {
    top_k: p.top_k,
    coarse_top_k: p.coarse_top_k,
    min_score: p.min_score,
    rerank: p.rerank,
    tag: nullable(p.tag),
    modality: nullable(p.modality),
    author: nullable(p.author),
    year: p.year == null || p.year === '' ? null : Number(p.year),
    venue: nullable(p.venue),
    bypass_expansion_cache: !!p.bypass_expansion_cache,
    temperature: p.temperature,
    max_answer_chars: p.max_answer_chars,
  }
}

// 从 RunStore 落盘的 run 还原成 search(debug=true) 的响应形状。
// run 里 results/dropped/rerank_before 的 text 被截断，其余字段是完整的。
function runToRetrieval(run) {
  return {
    run_id: run.run_id,
    query: run.query,
    params_effective: run.params_effective || {},
    expanded_queries: run.expanded_queries || [],
    expansion_source: run.expansion_source || null,
    stages: run.stages || [],
    timings_ms: run.timings_ms || {},
    results: run.results || [],
    dropped: run.dropped || [],
    dropped_truncated: !!run.dropped_truncated,
    rerank_before: run.rerank_before || [],
  }
}

export function useRagLab(api) {
  const params = ref(defaultParams())

  // 当前这一次检索 / 生成的完整响应
  const retrieval = ref(null)
  const generation = ref(null)

  // run 留存：currentRun 是「正在看」的 run（含 annotations + metrics），
  // runs 是历史列表（索引摘要）。currentRun 为 null 表示本次检索尚未落盘。
  const currentRun = ref(null)
  const runs = ref([])
  const config = ref(null)

  const loading = reactive({ search: false, ask: false, save: false })
  const error = ref('')
  // run 留存失败单独一条：主错误条留给检索/问答，留存失败不该盖掉已展示的结果
  const saveError = ref('')

  function fail(e) {
    error.value = (e && e.message) || '请求失败'
  }
  function clearError() {
    error.value = ''
  }
  function clearSaveError() {
    saveError.value = ''
  }

  async function doSearch(query) {
    if (!query || !query.trim()) return
    loading.search = true
    clearError()
    try {
      const r = await api('/api/rag/search', {
        method: 'POST',
        body: JSON.stringify({ ...toRequestPayload(params.value), query: query.trim(), debug: true }),
      })
      retrieval.value = r
      generation.value = null
      currentRun.value = null
      await saveRun({ retrieval: r, generation: null })
    } catch (e) {
      fail(e)
    } finally {
      loading.search = false
    }
  }

  async function doAsk(query) {
    if (!query || !query.trim()) return
    loading.ask = true
    clearError()
    try {
      const g = await api('/api/rag/ask', {
        method: 'POST',
        body: JSON.stringify({ ...toRequestPayload(params.value), question: query.trim() }),
      })
      generation.value = g
      // 5.2：ask 响应自带整份检索结构，检索结果与引用编号天然对齐
      retrieval.value = g.retrieval || null
      currentRun.value = null
      await saveRun({ retrieval: retrieval.value, generation: g })
    } catch (e) {
      fail(e)
    } finally {
      loading.ask = false
    }
  }

  // 检索后立即留存。留存失败不回滚已展示的结果——那才是用户真正要看的，
  // 但要让用户知道「这组结果没存下来、不能标注/对比」。
  async function saveRun({ retrieval, generation }) {
    if (!retrieval) return
    loading.save = true
    saveError.value = ''
    try {
      const created = await api('/api/rag/runs', {
        method: 'POST',
        body: JSON.stringify({ retrieval, generation }),
      })
      currentRun.value = created
      await loadRuns()
    } catch (e) {
      saveError.value = '本次结果未能留存（无法标注/对比）：' + ((e && e.message) || '请求失败')
    } finally {
      loading.save = false
    }
  }

  // 标注一条 chunk。PUT 合并语义在后端，前端只传这一条。
  async function annotate(chunkId, relevance, note) {
    if (!currentRun.value) return
    try {
      const updated = await api('/api/rag/runs/' + currentRun.value.run_id, {
        method: 'PUT',
        body: JSON.stringify({
          annotations: { [chunkId]: { relevance, note: note || '' } },
        }),
      })
      currentRun.value = updated
      await loadRuns()
    } catch (e) {
      fail(e)
    }
  }

  // 清除一条标注 = 把它从 annotations 里抹掉。后端只有「合并」和「整体替换」，
  // 无逐条删除端点，因此这里带上「去掉该条后的完整 annotations + replace_annotations」。
  async function clearAnnotation(chunkId) {
    if (!currentRun.value) return
    const next = { ...(currentRun.value.annotations || {}) }
    delete next[chunkId]
    try {
      const updated = await api('/api/rag/runs/' + currentRun.value.run_id, {
        method: 'PUT',
        body: JSON.stringify({ annotations: next, replace_annotations: true }),
      })
      currentRun.value = updated
      await loadRuns()
    } catch (e) {
      fail(e)
    }
  }

  async function loadRun(runId) {
    clearError()
    try {
      const run = await api('/api/rag/runs/' + runId)
      currentRun.value = run
      retrieval.value = runToRetrieval(run)
      generation.value = run.generation || null
    } catch (e) {
      fail(e)
    }
  }

  async function loadRuns() {
    try {
      const r = await api('/api/rag/runs?limit=100&offset=0')
      runs.value = r.runs || []
    } catch (e) {
      // 历史列表非关键，静默
    }
  }

  async function loadConfig() {
    try {
      config.value = await api('/api/rag/config')
    } catch (e) {
      // 只读库信息非关键，静默
    }
  }

  async function renameRun(runId, label, tags, labeler) {
    try {
      const updated = await api('/api/rag/runs/' + runId, {
        method: 'PUT',
        body: JSON.stringify({ label, tags, labeler }),
      })
      if (currentRun.value && currentRun.value.run_id === runId) currentRun.value = updated
      await loadRuns()
    } catch (e) {
      fail(e)
    }
  }

  async function deleteRun(runId) {
    try {
      await api('/api/rag/runs/' + runId, { method: 'DELETE' })
      if (currentRun.value && currentRun.value.run_id === runId) currentRun.value = null
      await loadRuns()
    } catch (e) {
      fail(e)
    }
  }

  // 多组对比：按 id 取回完整 run。Promise.all 并行取。
  function fetchRuns(runIds) {
    return Promise.all(runIds.map((id) => api('/api/rag/runs/' + id)))
  }

  return {
    params,
    setParams: (next) => { params.value = next },
    retrieval,
    generation,
    currentRun,
    runs,
    config,
    loading,
    error,
    clearError,
    saveError,
    clearSaveError,
    doSearch,
    doAsk,
    annotate,
    clearAnnotation,
    loadRun,
    loadRuns,
    loadConfig,
    renameRun,
    deleteRun,
    fetchRuns,
  }
}

// ── 展示层纯函数（组件间共享，避免各自手写格式化漂移）──────────

export function fmtScore(v) {
  if (v == null) return '—'
  return Number(v).toFixed(4)
}

export function fmtPct(v, digits = 1) {
  if (v == null) return '—'
  return (v * 100).toFixed(digits) + '%'
}

export function fmtMs(ms) {
  if (ms == null) return '—'
  if (ms < 1000) return ms + 'ms'
  return (ms / 1000).toFixed(2) + 's'
}

export function fmtDelta(before, after) {
  if (before == null || after == null) return '='
  const d = before - after
  if (d > 0) return '▲' + d
  if (d < 0) return '▼' + (-d)
  return '='
}
