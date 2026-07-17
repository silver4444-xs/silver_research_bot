<template>
  <section class="grid gr">
    <div class="card">
      <div class="ch"><h3>文献检索</h3></div>
      <label for="rq">查询</label>
      <input id="rq" v-model="ragQ" />
      <div class="row">
        <div><label for="rk">Top K</label><input id="rk" v-model.number="ragK" type="number" min="1" /></div>
        <div><label for="rt">Tag</label><input id="rt" v-model="ragTag" /></div>
        <div>
          <label for="rm">模态</label>
          <select id="rm" v-model="ragModality">
            <option value="">全部</option>
            <option value="text">文本</option>
            <option value="formula">公式</option>
            <option value="figure">图表</option>
            <option value="table">表格</option>
          </select>
        </div>
      </div>
      <div class="row" style="margin-top: var(--s-2)">
        <button class="btn bp bsm" @click="doRagSearch">检索</button>
        <button class="btn bs bsm" @click="doRagCtx">上下文</button>
        <button class="btn bs bsm" @click="doRagSuggest">建议</button>
      </div>
      <div v-if="ragResults.length" class="rlist" style="margin-top: var(--s-3)">
        <article v-for="r in ragResults" :key="r.chunk_id" class="ri">
          <div>
            <strong>{{ r.title }}</strong>
            <span class="chip cs" style="font-size: 10px; margin-left: 6px">{{ r.chunk_type }}</span>
            <p style="font-size: 12px; color: var(--c-text-secondary)">{{ r.text && r.text.slice(0, 200) }}...</p>
          </div>
          <div style="text-align: right; min-width: 60px">
            <span style="font-size: 13px; color: var(--c-accent)">{{ (r.score * 100).toFixed(0) }}%</span>
          </div>
        </article>
      </div>
    </div>

    <div class="card">
      <div class="ch"><h3>文献入库</h3></div>
      <label for="pt">标题</label>
      <input id="pt" v-model="pf.title" />
      <label for="pa">摘要</label>
      <textarea id="pa" v-model="pf.abstract" rows="2"></textarea>
      <label for="pc2">内容</label>
      <textarea id="pc2" v-model="pf.content" rows="4"></textarea>
      <label for="pz">标签</label>
      <input id="pz" v-model="pfTags" />
      <button class="btn bp bf" @click="doIngest">入库</button>
    </div>

    <div class="card sp2">
      <div class="ch">
        <h3>文献列表</h3>
        <button class="btn bs bsm" @click="loadRagPapers">刷新</button>
      </div>
      <div class="rlist">
        <article v-for="p in ragPapers" :key="p.paper_id" class="ri">
          <div>
            <strong>{{ p.title }}</strong>
            <p>{{ p.paper_id }}</p>
          </div>
          <div style="text-align: right; font-size: 12px">
            <span class="chip cp">{{ (p.tags || []).join(',') }}</span>
            <div style="color: var(--c-text-secondary)">{{ p.created_at }}</div>
            <button class="btn bg bsm" style="color: var(--c-danger); margin-top: 4px" @click="doDeletePaper(p.paper_id)">删除</button>
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
const ragResults = ref([])
const ragCtx = ref('')
const ragSuggest = ref('')
const pfTags = ref('rag,llm')
const pf = ref({ title: '', abstract: '', content: '' })
const ragPapers = ref([])

async function doRagSearch() {
  const r = await api('/api/rag/search', {
    method: 'POST',
    body: JSON.stringify({
      query: ragQ.value,
      top_k: ragK.value,
      tag: ragTag.value || null,
      modality: ragModality.value || null,
      rerank: true,
    }),
  })
  ragResults.value = r.results || []
}

async function doRagCtx() {
  const r = await api('/api/rag/context', {
    method: 'POST',
    body: JSON.stringify({ query: ragQ.value, top_k: ragK.value }),
  })
  ragCtx.value = r.context
  ragResults.value = r.results || []
}

async function doRagSuggest() {
  ragSuggest.value = JSON.stringify(
    await api('/api/rag/suggest', {
      method: 'POST',
      body: JSON.stringify({ query: ragQ.value, top_k: ragK.value }),
    }),
    null,
    2,
  )
}

async function doIngest() {
  const tags = pfTags.value.split(',').map(function (s) { return s.trim() }).filter(Boolean)
  await api('/api/rag/papers', {
    method: 'POST',
    body: JSON.stringify({ ...pf.value, tags: tags }),
  })
  await loadRagPapers()
}

async function doDeletePaper(pid) {
  await api('/api/rag/papers/' + pid, { method: 'DELETE' })
  await loadRagPapers()
}

async function loadRagPapers() {
  ragPapers.value = await api('/api/rag/papers')
}

onMounted(loadRagPapers)
</script>

<style scoped>
.grid { display: grid; gap: var(--s-5) }
.gr { grid-template-columns: 1fr 1fr }
.sp2 { grid-column: span 2 }
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
@media (max-width: 768px) {
  .gr { grid-template-columns: 1fr }
  .sp2 { grid-column: span 1 }
}
</style>
