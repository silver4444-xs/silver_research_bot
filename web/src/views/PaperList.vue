<template>
  <div>
    <div class="pstats-bar">
      <div class="pstat"><span class="pstat-num">{{ papers.length }}</span><span class="pstat-label">论文</span></div>
      <div class="pstat"><span class="pstat-num cp">{{ papers.filter(p => p.status === 'completed').length }}</span><span class="pstat-label">已完成</span></div>
      <div class="pstat"><span class="pstat-num ca">{{ papers.filter(p => p.status === 'processing').length }}</span><span class="pstat-label">分析中</span></div>
    </div>
    <div class="ptoolbar">
      <label class="row" style="gap:6px;font-size:13px;cursor:pointer;user-select:none" @click.stop>
        <div :class="['sel-circle', selectedPapers.size === papers.length && papers.length > 0 ? 'on' : '']" @click="selectAllPapers"></div>
        <span style="color:var(--c-text-secondary)">全选</span>
      </label>
      <div class="ptoolbar-spacer"></div>
      <button v-if="selectedPapers.size" class="btn btn-del-sel bsm" @click="deleteSelected">删除选中 ({{ selectedPapers.size }})</button>
      <button class="btn btn-del-all bsm" @click="deleteAllPapers">全部删除</button>
      <button class="btn bs bsm" @click="loadPapers">刷新</button>
      <button class="btn ba bsm" @click="router.push('/papers/upload')">上传新论文</button>
    </div>
    <div class="pgrid" v-if="papers.length">
      <article v-for="p in papers" :key="p.paper_id" :class="['pcard', { sel: selectedPapers.has(p.paper_id) }]" @click="openPaper(p.paper_id)">
        <div class="pcard-top">
          <div class="pcard-sel" @click.stop>
            <div :class="['sel-circle', selectedPapers.has(p.paper_id) ? 'on' : '']" @click="toggleSelectPaper(p.paper_id)"></div>
          </div>
          <button class="pcard-del" @click.stop="delPaper(p.paper_id)" title="删除">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          </button>
        </div>
        <div class="pcard-body">
          <h4 class="pcard-title">{{ p.title }}</h4>
          <div class="pcard-meta">
            <span class="chip" :class="p.status === 'completed' ? 'cp' : p.status === 'processing' ? 'ca' : 'cg'">{{ p.status === 'completed' ? '已完成' : p.status === 'processing' ? '分析中' : '未知' }}</span>
            <span class="chip" :class="p.language === 'en' ? 'cb2' : 'cg'">{{ p.language === 'en' ? 'EN' : 'ZH' }}</span>
            <span>{{ p.page_count || '?' }} 页</span>
            <span>{{ p.formula_count || '?' }} 公式</span>
            <span class="pcard-date">{{ (p.uploaded_at || '').slice(0, 10) }}</span>
          </div>
        </div>
      </article>
    </div>
    <div v-if="!papers.length" class="pempty">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" width="56" height="56" style="opacity:0.15;margin-bottom:var(--s-4)"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
      <p style="color:var(--c-text-muted);font-size:14px">暂无已分析的论文</p>
      <button class="btn ba bsm" @click="router.push('/papers/upload')" style="margin-top:var(--s-3)">上传第一篇论文</button>
    </div>
  </div>
</template>

<script setup>
import { inject } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const papersApi = inject('papers')

const {
  papers, selectedPapers,
  loadPapers, delPaper, deleteSelected, deleteAllPapers,
  toggleSelectPaper, selectAllPapers,
} = papersApi

function openPaper(pid) {
  router.push(`/papers/${pid}`)
}
</script>
