# 前端重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Silver Research Bot 前端从单文件 SPA (~1500行 App.vue) 重构为 Vue Router + 7 view + 4 component + 5 composable + scoped CSS 架构

**Architecture:** Vue Router 驱动路由，provide/inject 共享状态（useApi -> usePapers/useCompare），CSS 三层（token + design system 保留全局，组件样式移入 scoped）。功能零变更，纯组织重构。

**Tech Stack:** Vue 3.5, Vite 6, vue-router@4, D3.js v7, MathJax 3, PDF.js 3.11, Mermaid 10

---

### Task 1: Install vue-router

**Files:**
- Modify: `web/package.json`

- [ ] **Step 1: Install vue-router@4**

```bash
cd web && npm install vue-router@4
```

- [ ] **Step 2: Verify installation**

Run: `npm ls vue-router`
Expected: `vue-router@4.x.x`

- [ ] **Step 3: Commit**

```bash
git add web/package.json web/package-lock.json
git commit -m "chore: add vue-router@4 dependency"
```

---

### Task 2: Create router.js

**Files:**
- Create: `web/src/router.js`

- [ ] **Step 1: Create router.js with all 7 route definitions (lazy-loaded views)**

```js
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('./views/Dashboard.vue') },
  { path: '/papers', name: 'paper-list', component: () => import('./views/PaperList.vue') },
  { path: '/papers/upload', name: 'paper-upload', component: () => import('./views/PaperUpload.vue') },
  { path: '/papers/:id', name: 'paper-detail', component: () => import('./views/PaperDetail.vue'), props: true },
  { path: '/compare', name: 'compare', component: () => import('./views/Compare.vue') },
  { path: '/agent', name: 'agent', component: () => import('./views/AgentChat.vue') },
  { path: '/rag', name: 'rag', component: () => import('./views/RAGSearch.vue') },
]

export default createRouter({ history: createWebHistory(), routes })
```

- [ ] **Step 2: Commit**

```bash
git add web/src/router.js
git commit -m "feat: add Vue Router config with 7 lazy-loaded routes"
```

---

### Task 3: Create useApi.js composable

**Files:**
- Create: `web/src/composables/useApi.js`

- [ ] **Step 1: Extract the `api()` function from App.vue into useApi.js**

```js
export function useApi() {
  async function api(url, opts = {}) {
    const res = await fetch(url, {
      ...opts,
      ...(opts.body instanceof FormData
        ? {}
        : { headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) } }),
    })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  }
  return { api }
}
```

- [ ] **Step 2: Commit**

```bash
git add web/src/composables/useApi.js
git commit -m "feat: add useApi composable"
```

---

### Task 4: Create useI18n.js composable

**Files:**
- Create: `web/src/composables/useI18n.js`

- [ ] **Step 1: Extract LOCALE, lang ref, and t() from App.vue into useI18n.js**

```js
import { ref } from 'vue'

const LOCALE = {
  zh: {
    eyebrow: '论文分析平台', title: '研究论文深度分析工作台',
    subtitle: '上传 PDF 即可自动翻译、四维分析、公式解读与可视化。',
    api: 'API', analyzed: '已分析', refresh: '刷新',
  },
  en: {
    eyebrow: 'Paper Analysis Platform', title: 'Research Paper Analysis Workbench',
    subtitle: 'Upload PDF for auto translation, 4D analysis, formula explanation & visualization.',
    api: 'API', analyzed: 'Analyzed', refresh: 'Refresh',
  },
}

export function useI18n() {
  const lang = ref('zh')
  function t(k) { return (LOCALE[lang.value] || LOCALE.zh)[k] || k }
  return { lang, t, LOCALE }
}
```

- [ ] **Step 2: Commit**

```bash
git add web/src/composables/useI18n.js
git commit -m "feat: add useI18n composable"
```

---

### Task 5: Create useMarkdown.js composable

**Files:**
- Create: `web/src/composables/useMarkdown.js`

- [ ] **Step 1: Extract renderMd, renderFormula, renderAll, sanitizeLatex, insMd, syncScroll, fmtSize, retypeset, retypesetDeferred from App.vue**

The composable exports all markdown rendering and editor helper functions. Full implementation matches the existing code in App.vue lines 269-413 exactly, wrapped in `export function useMarkdown()`. See the design spec or original App.vue for the complete function bodies. Key exports:

```js
export function useMarkdown() {
  let _mjRetries = 0
  function retypeset() { /* same as App.vue:269-271 */ }
  document.addEventListener('MathJax:ready', () => { _mjRetries = 0; retypeset() })
  function retypesetDeferred() { /* same as App.vue:273 */ }
  function renderAll(t) { let h = renderMd(t); retypesetDeferred(); return h }
  function sanitizeLatex(s) { /* same as App.vue:275 */ }
  function renderFormula(t) { /* same as App.vue:276-281 */ }
  function renderMd(t) { /* same as App.vue:381-409 */ }
  function insMd(before, after) { /* same as App.vue:410 */ }
  function syncScroll(fromPreview) { /* same as App.vue:411 */ }
  function fmtSize(bytes) { /* same as App.vue:412 */ }
  return { renderMd, renderFormula, renderAll, sanitizeLatex, insMd, syncScroll, fmtSize, retypeset, retypesetDeferred }
}
```

- [ ] **Step 2: Commit**

```bash
git add web/src/composables/useMarkdown.js
git commit -m "feat: add useMarkdown composable"
```

---

### Task 6: Create usePapers.js composable

**Files:**
- Create: `web/src/composables/usePapers.js`

- [ ] **Step 1: Extract all paper state + methods from App.vue into usePapers.js**

This composable depends on useApi. It exports all paper-related reactive state, computed properties, and methods. Full implementation mirrors App.vue lines 215-297. Key structure:

```js
import { ref, computed } from 'vue'
import { useApi } from './useApi.js'

const STAGES = [
  { id: 'parse', label: '文档解析' }, { id: 'translate', label: '全文翻译' },
  { id: 'analyze', label: '四维分析' }, { id: 'formula_explain', label: '公式解读' },
  { id: 'visualize', label: '可视化' }, { id: 'citation', label: '引用图谱' },
  { id: 'review', label: '多视角审稿' },
]

export function usePapers() {
  const { api } = useApi()
  // State: papers, pdet, selectedPapers, curPaperId, uploading, upStatus, progressMsg,
  //        pStages, upFile, upText, upLang, upMode, drag, apiOk, apiStatus
  // Computed: totalFormulas, totalFigures, canSubmit, hasAnyResult, cmpReadyPapers
  // Methods: loadPapers, loadAll, doUpload, pollProgress, clearPoll, openPaper,
  //          pollDetailProgress, doExport, exportArtifact, delPaper, toggleSelectPaper,
  //          selectAllPapers, deleteSelected, deleteAllPapers, onDrop, onFile
  // (exact code from design spec/App.vue)
}
```

- [ ] **Step 2: Commit**

```bash
git add web/src/composables/usePapers.js
git commit -m "feat: add usePapers composable"
```

---

### Task 7: Create useCompare.js composable

**Files:**
- Create: `web/src/composables/useCompare.js`

- [ ] **Step 1: Extract all comparison logic + D3 rendering from App.vue into useCompare.js**

Depends on useApi. Accepts `papers` ref from usePapers. Exports all compare state, computed, chart renderers, and export functions. Key structure:

```js
import { ref, computed, nextTick } from 'vue'
import { useApi } from './useApi.js'

const PAPER_COLORS = ['#a78bfa','#60a5fa','#34d399','#fbbf24','#f472b6','#fb923c','#94a3b8','#f87171','#4ade80','#c084fc','#38bdf8','#a3e635']
const DIM_COLORS = ['#a78bfa','#60a5fa','#34d399','#fbbf24','#f472b6','#fb923c','#94a3b8','#f87171']

export function useCompare(papers) {
  const { api } = useApi()
  // State: cmpIds, cmpResult, cmpStructured, cmpTab, cmpPapers, comparing, cmpError,
  //        cmpHistory, showCmpHistory, cmpDimFilter, cmpDimNames, cmpExpandedDim,
  //        cmpRadarCv, cmpHeatCv, cmpBarsCv, cmpStackCv
  // Computed: filteredCmpDims, cmpPairwiseGaps
  // Methods: toggleCmpPaper, cmpAvgScore, cmpPaperTitle, switchCmpTab, cmpMiniHeatmap,
  //          renderAllCmpCharts, renderCmpRadar, renderCmpHeat, renderCmpBars, renderCmpStack,
  //          doCompare, loadCompareHistory, loadCompareResult, deleteCompareResult,
  //          exportCompareMD, exportCompareCSV, exportCompareHTML, downloadBlob
  // (exact code from design spec/App.vue)
}
```

- [ ] **Step 2: Commit**

```bash
git add web/src/composables/useCompare.js
git commit -m "feat: add useCompare composable"
```

---

### Task 8: Create shared components

**Files:**
- Create: `web/src/components/Sidebar.vue`
- Create: `web/src/components/HeroHeader.vue`
- Create: `web/src/components/ProgressTracker.vue`
- Create: `web/src/components/ArtifactCard.vue`

- [ ] **Step 1: Create ArtifactCard.vue** — reusable card shell with title + download button + slot

- [ ] **Step 2: Create ProgressTracker.vue** — reusable 8-stage progress display (used in both upload and detail pages)

- [ ] **Step 3: Create Sidebar.vue** — brand + `<router-link>` nav + mini-card. Uses scoped styles for .sidebar, .brand, .logo, .nav, .mini-card

- [ ] **Step 4: Create HeroHeader.vue** — hero stats bar with props for all display values. Uses scoped styles for .hero, .eyebrow, .sbar, .si, .dot

- [ ] **Step 5: Verify build compiles**

```bash
cd web && npx vite build 2>&1 | Select-Object -First 30
```

Expected: No errors about missing imports for the new components.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/
git commit -m "feat: add shared components (Sidebar, HeroHeader, ProgressTracker, ArtifactCard)"
```

---

### Task 9: Create view components (PaperList + PaperUpload + PaperDetail + Compare + AgentChat + RAGSearch + Dashboard)

**Files:**
- Create: `web/src/views/Dashboard.vue`
- Create: `web/src/views/PaperList.vue`
- Create: `web/src/views/PaperUpload.vue`
- Create: `web/src/views/PaperDetail.vue`
- Create: `web/src/views/Compare.vue`
- Create: `web/src/views/AgentChat.vue`
- Create: `web/src/views/RAGSearch.vue`

Each view injects composables via `inject()` and contains only the relevant template section from the original App.vue. Component-specific styles moved into `<style scoped>`.

**View → Original App.vue lines mapping:**
- Dashboard.vue: uses HeroHeader + stats
- PaperList.vue: lines 82-126 (paper grid + toolbar + empty state)
- PaperUpload.vue: lines 16-78 (file upload + markdown editor + progress)
- PaperDetail.vue: lines 128-143 (detail tabs + content rendering)
- Compare.vue: lines 146-171 (compare workflow + D3 charts)
- AgentChat.vue: lines 178-191 (chat interface)
- RAGSearch.vue: line 195 (search + ingest + list)

Each view is a self-contained SFC with `<template>`, `<script setup>`, and `<style scoped>`.

> **Note:** Due to the verbatim extraction requirement, each view's full template code is identical to its corresponding section in the current App.vue. The plan references the original line numbers rather than duplicating all templates inline.

- [ ] **Step 1: Create Dashboard.vue**

Template: `<HeroHeader>` with injected props + language toggle + refresh handler. ~30 lines.

- [ ] **Step 2: Create AgentChat.vue**

Template from App.vue lines 178-191. Script: chatMsgs, chatIn, chatAttach, onChatKey, autoResize, onChatFile, doUploadChatFile, doChat. Scoped styles for .chat-container, .thread, .cbubble, .chat-input-box.

- [ ] **Step 3: Create RAGSearch.vue**

Template from App.vue line 195 RAG section. Script: ragQ, ragK, ragTag, ragModality, ragResults, pf, pfTags, ragPapers, doRagSearch, doRagCtx, doRagSuggest, doIngest, doDeletePaper.

- [ ] **Step 4: Create PaperUpload.vue**

Template from App.vue lines 16-78. Script: upFile, upText, upLang, upMode, drag, canSubmit, uploading, progressMsg, pStages, onDrop, onFile, insMd, syncScroll, renderMd, doUpload. Scoped styles for .editor-area, .ep/.pp, .tb, .tb-btn, .upload-card, .drop-zone, .file-card.

- [ ] **Step 5: Create PaperList.vue**

Template from App.vue lines 82-126. Script: papers, psub, ps, selectedPapers, loadPapers, delPaper, deleteSelected, deleteAllPapers, toggleSelectPaper, selectAllPapers, openPaper.

- [ ] **Step 6: Create PaperDetail.vue**

Template from App.vue lines 128-143. Script: pdet, dt, dtabs, dims, askQ, askA, pStages, doExport, exportArtifact, doAsk, renderAll, renderFormula, pollDetailProgress. Loads paper by `$route.params.id` on mount.

- [ ] **Step 7: Create Compare.vue**

Template from App.vue lines 146-171. Script: all compare state + D3 methods from useCompare. Scoped styles for .cmp-pick-grid, .cmp-matrix, .cmp-charts, .cmp-gap, .cmp-dash, .cmp-mini-heat, .ctabs.

- [ ] **Step 8: Verify build compiles all views**

```bash
cd web && npx vite build 2>&1
```

Expected: Successful build with no errors.

- [ ] **Step 9: Commit**

```bash
git add web/src/views/
git commit -m "feat: add all view components (7 views, router-driven)"
```

---

### Task 10: Refactor App.vue to shell + update main.js

**Files:**
- Modify: `web/src/App.vue`
- Modify: `web/src/main.js`

- [ ] **Step 1: Replace App.vue with minimal shell**

```vue
<template>
  <div class="app">
    <Sidebar />
    <main class="main">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { provide } from 'vue'
import Sidebar from './components/Sidebar.vue'
import { useApi } from './composables/useApi.js'
import { useI18n } from './composables/useI18n.js'
import { useMarkdown } from './composables/useMarkdown.js'
import { usePapers } from './composables/usePapers.js'
import { useCompare } from './composables/useCompare.js'

const { api } = useApi()
const i18n = useI18n()
const md = useMarkdown()
const papers = usePapers()
const compare = useCompare(papers.papers)

provide('api', { api })
provide('i18n', i18n)
provide('markdown', md)
provide('papers', papers)
provide('compare', compare)
</script>
```

- [ ] **Step 2: Update main.js to register router**

```js
import { createApp } from 'vue'
import App from './App.vue'
import router from './router.js'
import './style.css'

const app = createApp(App)
app.use(router)
app.mount('#app')
```

- [ ] **Step 3: Verify build**

```bash
cd web && npx vite build 2>&1
```

Expected: Successful build with no errors or warnings.

- [ ] **Step 4: Commit**

```bash
git add web/src/App.vue web/src/main.js
git commit -m "refactor: replace monolithic App.vue with router-driven shell (~30 lines)"
```

---

### Task 11: Split style.css — remove components' scoped styles

**Files:**
- Modify: `web/src/style.css`

- [ ] **Step 1: Remove component-specific style blocks from style.css**

Delete these CSS blocks (now in scoped styles):
- `.sidebar`, `.brand`, `.logo`, `.mini-card` — 20 lines → Sidebar.vue
- `.hero`, `.eyebrow`, `.sbar`, `.si`, `.dot` — 20 lines → HeroHeader.vue
- `.editor-area`, `.ep`, `.pp`, `.eh`, `.ph`, `.tb`, `.tb-btn`, `.upload-card`, `.drop-zone`, `.file-card`, `.file-card-*` — 55 lines → PaperUpload.vue
- `.pgrid`, `.pcard`, `.pcard-*`, `.pstats-bar`, `.ptoolbar`, `.pempty`, `.sel-circle` — 50 lines → PaperList.vue
- `.pd`, `.pdm`, `.vis-frame`, `.dual-reader`, `.frow`, `.fnum`, `.fbody`, `.audit-*` — 60 lines → PaperDetail.vue
- `.cmp-*`, `.ctabs`, `.ctab` — 75 lines → Compare.vue
- `.chat-container`, `.thread`, `.cbubble`, `.chat-input-box`, `.chat-*` — 35 lines → AgentChat.vue
- `.fml-*` — 10 lines → PaperDetail.vue

**Retain:** `:root` tokens, reset, body, h1-h4, `.app`, `.main`, `.card`, `.btn`, `.chip`, `.st`, `.navi`, `.ut`, `.dtabs`, `.subnav`, `.ptrack`, `.md`, `.row`, `.rlist`, `.ri`, `.ibox`, `.empty`, `.spin`, `.grid`, form elements, code/pre, responsive breakpoint.

- [ ] **Step 2: Verify build passes after CSS split**

```bash
cd web && npx vite build 2>&1
```

Expected: Build success, no missing styles (visual check via dev server).

- [ ] **Step 3: Commit**

```bash
git add web/src/style.css
git commit -m "refactor: remove component-specific styles from global CSS (~400 lines remain)"
```

---

### Task 12: Integration verification

**Files:** (none, verification only)

- [ ] **Step 1: Start backend + frontend dev servers**

```bash
# Terminal 1
uvicorn silver_research_bot.research_app:app --port 8765

# Terminal 2
cd web && npm run dev
```

- [ ] **Step 2: Verify each route renders correctly**

| URL | Check |
|-----|-------|
| `http://localhost:8766/` | Hero header + stats visible |
| `http://localhost:8766/papers` | Paper list loads from API |
| `http://localhost:8766/papers/upload` | Upload form renders |
| `http://localhost:8766/papers/<id>` | Detail page with content |
| `http://localhost:8766/compare` | Compare workflow works |
| `http://localhost:8766/agent` | Chat interface renders |
| `http://localhost:8766/rag` | RAG search renders |

- [ ] **Step 3: Verify sidebar navigation**

Click each nav item. URL changes correctly. Active highlight follows. Browser back/forward works.

- [ ] **Step 4: Verify no console errors**

Open DevTools console — no errors on any page load.

- [ ] **Step 5: Commit any integration fixes**

```bash
git add -A && git commit -m "fix: integration adjustments after refactor"
```
