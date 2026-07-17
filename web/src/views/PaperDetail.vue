<template>
  <div v-if="pdet" class="pd" style="margin-top:var(--s-5)">
    <div class="card">
      <div class="ch">
        <h3>{{ pdet.title }}</h3>
        <div class="row rt">
          <button class="btn bg bsm" @click="router.push('/papers'); clearPoll()">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="16" height="16"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
            返回
          </button>
          <button class="btn ba bsm" @click="doExport">导出全部</button>
        </div>
      </div>
      <div class="pdm">
        <span class="chip" :class="pdet.language === 'en' ? 'cb2' : 'cg'">{{ pdet.language === 'en' ? '英文' : '中文' }}</span>
        <span style="font-size:13px;color:var(--c-text-secondary)">{{ pdet.page_count || '?' }}页 &middot; {{ pdet.formula_count || '?' }}公式</span>
        <span v-if="pdet.status === 'processing'" class="chip ca">分析中</span>
        <span v-if="pdet.has_translation" class="chip cb2">已翻译</span>
        <span v-if="pdet.status === 'completed'" class="chip cp">已审计</span>
      </div>
    </div>

    <div v-if="pdet.status === 'processing' && !hasAnyResult" class="card">
      <div class="pt-head"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="16" height="16" class="spin"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg> 分析进行中…</div>
      <div class="pt-stages"><template v-if="pStages.length"><div v-for="s in pStages" :key="s.id" :class="['pts', s.s]"><div class="ptd"><div v-if="s.s === 'active'" class="pts-spin"></div></div><span>{{ s.label }}</span></div></template></div>
    </div>

    <div class="dtabs"><button v-for="t in dtabs" :key="t.id" :class="['st', dt === t.id && 'act']" @click="dt = t.id">{{ t.label }}</button></div>

    <div class="card" v-if="dt === 'translation' && pdet.translation">
      <div class="ch"><h3>全文翻译</h3><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id, 'translation', '全文翻译')" title="下载全文翻译"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div>
      <div class="md" v-html="renderAll(pdet.translation)"></div>
    </div>
    <div v-if="dt === 'translation' && !pdet.translation" class="card"><p class="empty">该论文暂无翻译结果（可能为中文论文或翻译尚未完成）</p></div>

    <template v-for="d in dims" :key="d.id">
      <div class="card" v-if="dt === d.id && pdet[d.id]">
        <div class="ch"><h3>{{ d.label }}</h3><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id, d.id, d.label)" :title="'下载' + d.label"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div>
        <div class="md" v-html="renderAll(pdet[d.id])"></div>
      </div>
    </template>

    <div class="card" v-if="dt === 'formulas' && pdet.formula_explanations">
      <div class="ch"><h3>公式解读</h3><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id, 'formulas', '公式解读')" title="下载公式解读"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div>
      <div class="md" v-html="renderFormula(pdet.formula_explanations)"></div>
    </div>

    <div class="card" v-if="dt === 'visualization' && pdet.visualization_html">
      <div class="ch"><h3>可视化分析</h3><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id, 'visualization', '可视化分析')" title="下载可视化分析"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div>
      <iframe :srcdoc="pdet.visualization_html" class="vis-frame" sandbox="allow-scripts allow-same-origin" title="可视化分析"></iframe>
    </div>

    <div class="card" v-if="dt === 'citation_graph' && pdet.citation_graph_html">
      <div class="ch"><h3>引用图谱</h3><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id, 'citation_graph', '引用图谱')" title="下载引用图谱"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div>
      <iframe :srcdoc="pdet.citation_graph_html" class="vis-frame" sandbox="allow-scripts"></iframe>
    </div>

    <div class="card" v-if="dt === 'review'">
      <div class="ch"><h3>审稿意见</h3></div>
      <div v-if="pdet.review_theory" style="margin-bottom:16px">
        <div style="display:flex;align-items:center;gap:8px"><h4 style="margin:0;font-size:14px;color:var(--c-text-secondary)">理论视角</h4><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id, 'review_theory', '理论审稿意见')" title="下载理论审稿意见"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div>
        <div class="md" v-html="renderAll(pdet.review_theory)"></div>
      </div>
      <div v-if="pdet.review_engineering" style="margin-bottom:16px">
        <div style="display:flex;align-items:center;gap:8px"><h4 style="margin:0;font-size:14px;color:var(--c-text-secondary)">工程视角</h4><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id, 'review_engineering', '工程审稿意见')" title="下载工程审稿意见"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div>
        <div class="md" v-html="renderAll(pdet.review_engineering)"></div>
      </div>
      <div v-if="pdet.review_domain" style="margin-bottom:16px">
        <div style="display:flex;align-items:center;gap:8px"><h4 style="margin:0;font-size:14px;color:var(--c-text-secondary)">领域视角</h4><button class="btn bg bsm" @click="exportArtifact(pdet.paper_id, 'review_domain', '领域审稿意见')" title="下载领域审稿意见"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></div>
        <div class="md" v-html="renderAll(pdet.review_domain)"></div>
      </div>
      <p v-if="!pdet.review_theory && !pdet.review_engineering && !pdet.review_domain" class="empty">审稿意见尚未生成。</p>
    </div>

    <div class="card" v-if="dt === 'ask'">
      <div class="ch"><h3>提问</h3></div>
      <div class="row" style="gap:8px">
        <input v-model="askQ" placeholder="基于分析结果提问…" style="flex:1" @keydown.enter="doAsk" />
        <button class="btn bp bsm" @click="doAsk" :disabled="!askQ.trim()">提问</button>
      </div>
      <div v-if="askA" class="md" style="margin-top:12px" v-html="renderAll(askA)"></div>
    </div>
  </div>
</template>

<script setup>
import { ref, inject, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useApi } from '../composables/useApi.js'

const route = useRoute()
const router = useRouter()
const { api } = useApi()

const papersApi = inject('papers')
const md = inject('markdown')

const {
  pdet, pStages, STAGES, hasAnyResult, curPaperId,
  clearPoll, doExport, exportArtifact,
  pollDetailProgress,
} = papersApi

const { renderAll, renderFormula, retypesetDeferred } = md

const dt = ref('translation')
const askQ = ref('')
const askA = ref('')

const dtabs = [
  { id: 'translation', label: '全文翻译' },
  { id: 'system_model', label: '系统模型' },
  { id: 'problem_formulation', label: '问题表述' },
  { id: 'optimization_algorithm', label: '优化算法' },
  { id: 'experiment_design', label: '实验设计' },
  { id: 'formulas', label: '公式解读' },
  { id: 'visualization', label: '可视化' },
  { id: 'citation_graph', label: '引用图谱' },
  { id: 'review', label: '审稿意见' },
  { id: 'ask', label: '提问' },
]
const dims = [
  { id: 'system_model', label: '系统模型分析' },
  { id: 'problem_formulation', label: '问题表述分析' },
  { id: 'optimization_algorithm', label: '优化算法分析' },
  { id: 'experiment_design', label: '实验设计分析' },
]

let _pollTimer = null

async function loadPaperDetails(pid) {
  papersApi.clearPoll()
  try {
    pdet.value = await api(`/api/paper/${pid}`)
    dt.value = pdet.value.translation ? 'translation' : 'system_model'

    if (pdet.value.status === 'processing') {
      curPaperId.value = pid
      pStages.value = STAGES.map(s => ({ ...s, s: 'pending' }))
      _pollTimer = setInterval(pollDetailProgress, 2000)
    } else {
      pStages.value = STAGES.map(s => ({ ...s, s: 'done' }))
      retypesetDeferred()
    }
  } catch (e) {
    console.error(e)
    alert('加载论文失败: ' + (e.message || '网络错误，请确认后端已启动'))
  }
}

async function doAsk() {
  const q = askQ.value.trim()
  if (!q || !pdet.value?.paper_id) return
  askA.value = '思考中…'
  try {
    const r = await api(`/api/paper/${pdet.value.paper_id}/ask`, { method: 'POST', body: JSON.stringify({ question: q }) })
    askA.value = r.answer || '无回答'
  } catch (e) {
    askA.value = '提问失败: ' + e.message
  }
}

onMounted(() => {
  const paperId = route.params.id
  if (paperId) {
    loadPaperDetails(paperId)
  }
})

watch(() => route.params.id, (newId) => {
  if (newId) {
    loadPaperDetails(newId)
  }
})

onUnmounted(() => {
  if (_pollTimer) {
    clearInterval(_pollTimer)
    _pollTimer = null
  }
})
</script>
