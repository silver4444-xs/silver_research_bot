# 前端重构设计 — 2026-07-17

## 现状

- `App.vue` — 单文件 ~430 行（template 198 + script 232），包含所有 UI 和逻辑
- `style.css` — ~809 行，全部样式集中管理
- `main.js` — 4 行入口
- 无子组件、无 Vue Router、无状态管理库 — 纯 reactive refs 驱动

## 目标

将单文件 SPA 拆分为页面级组件，引入 Vue Router + provide/inject 数据流，CSS 从全局迁移到 scoped + 保留 design system 基础层。

## 技术选型

| 决策 | 选择 | 理由 |
|------|------|------|
| 路由 | Vue Router | URL 可定位，浏览器前进/后退正常 |
| 状态管理 | provide/inject + composables | 零依赖，Vue 3 内置，适合中等复杂度 |
| CSS | scoped + 全局 token 层 | 组件隔离，token 共享 |

## 路由设计

```
/                    → Dashboard      Hero + 状态概览
/papers              → PaperList      论文网格 + 批量操作
/papers/upload       → PaperUpload    文件上传 + Markdown 编辑器
/papers/:id          → PaperDetail    10 个详情 tab
/compare             → Compare        横向对比全流程（选择→仪表板→维度矩阵→图表→差异分析→综合分析）
/agent               → AgentChat      对话界面
/rag                 → RAGSearch      检索 + 入库 + 列表
```

Sidebar 用 `<router-link>` 替代 `tab` ref 手动切换。

## 文件结构

```
web/src/
├── main.js
├── App.vue                    ← 壳: Sidebar + <router-view> (~30行)
├── style.css                  ← Token + Reset + Design System (~400行)
├── router.js                  ← createRouter + createWebHistory
├── composables/
│   ├── useApi.js              ← fetch 封装 + 自动 JSON headers + 错误处理
│   ├── usePapers.js           ← papers[] + pdet + upload + CRUD + 轮询 + 批量选择
│   ├── useCompare.js          ← 对比状态 + D3 图表渲染 + 导出
│   ├── useMarkdown.js         ← renderMd / renderFormula / sanitizeLatex
│   └── useI18n.js             ← LOCALE + t() + lang ref
├── components/
│   ├── Sidebar.vue            ← brand + nav (router-link) + mini-card
│   ├── HeroHeader.vue         ← hero 统计条 (API 状态/论文数/公式数/图表数)
│   ├── ProgressTracker.vue    ← 8 阶段进度条 (上传页+详情页复用)
│   └── ArtifactCard.vue       ← 详情 tab 通用卡片 (标题行+下载按钮+Markdown 渲染区)
└── views/
    ├── Dashboard.vue          ← / 默认首页
    ├── PaperList.vue          ← /papers 统计栏 + 工具栏 + 论文卡片网格 + 选择/批量操作
    ├── PaperUpload.vue        ← /papers/upload 文件拖拽 + Markdown 编辑器 + 进度追踪
    ├── PaperDetail.vue        ← /papers/:id 元信息 + 进度条 + dtabs 切换 + 内容渲染
    ├── Compare.vue            ← /compare 论文选取 → 仪表板 → 维度矩阵 → D3图表 → 差异分析 → 综合分析 + 导出
    ├── AgentChat.vue          ← /agent 消息列表 + 输入框 + 文件上传
    └── RAGSearch.vue          ← /rag 检索表单 + 结果列表 + 入库表单 + 文献列表
```

## 数据流

```
useApi()           ← 底层 fetch 封装
    ↓
usePapers()        ← 论文核心状态 + CRUD + 轮询
useCompare()       ← 对比状态 + 渲染 + 导出
    ↓
App.vue provide()  ← 顶层注入
    ↓
各 view inject()   ← 按需消费
```

### useApi

```js
// 替代当前 api() 函数
export function useApi() {
  async function api(url, opts = {}) {
    const headers = opts.body instanceof FormData
      ? opts.headers || {}
      : { 'Content-Type': 'application/json', ...(opts.headers || {}) }
    const res = await fetch(url, { ...opts, headers })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  }
  return { api }
}
```

### usePapers

```js
// 导出: papers, pdet, selectedPapers, curPaperId, uploading, upStatus, progressMsg, pStages
// 导出: totalFormulas, totalFigures, hasAnyResult, cmpReadyPapers (computed)
// 方法: loadPapers, loadAll, doUpload, pollProgress, openPaper, delPaper, batchDelete,
//       toggleSelectPaper, selectAllPapers, clearPoll, doExport, exportArtifact
```

### useCompare

```js
// 导出: cmpIds, cmpResult, cmpStructured, cmpTab, cmpPapers, comparing, cmpError
// 方法: toggleCmpPaper, doCompare, loadCompareHistory, loadCompareResult, deleteCompareResult
//       switchCmpTab, renderAllCmpCharts, exportMD/CSV/HTML, downloadBlob
// 派生: cmpAvgScore, cmpPaperTitle, cmpPairwiseGaps, filteredCmpDims
```

## CSS 三层架构

### 第一层: Token + Reset (`style.css`, ~60 行)
- `:root` CSS 变量（40+ token）
- 盒模型重置 `*, *::before, *::after`
- body 背景/网格线/字体
- h1-h4 基础样式
- 滚动条样式

### 第二层: Design System (`style.css`, ~350 行)
通用 class（不改名称）:
- `.btn` `.btn.bp` `.btn.ba` `.btn.bs` `.btn.bg` `.btn.bd` 等变体
- `.card` `.ch` `.chip` `.chip.cd/cg/cb2/ca/cp`
- `.st` `.st.act` (sub-nav/detail tabs)
- `.navi` `.navi.act` (侧边栏导航)
- `.ut` `.ut.act` (上传模式切换)
- `.dtabs` `.subnav` (标签容器)
- `.ptrack` `.pt-stages` `.pts` (进度条)
- `.sel-circle` `.sel-circle.on`
- `.md` (Markdown 渲染区)
- `.cmp-*` (横向对比公共样式)
- `.rlist` `.ri`
- `.ibox` `.empty` `.spin`

### 第三层: Component Scoped (各组件 `<style scoped>`, 30-80 行/文件)
| 组件 | 特有样式内容 |
|------|------------|
| Sidebar.vue | `.sidebar` `.brand` `.logo` `.mini-card` `.nav` |
| HeroHeader.vue | `.hero` `.eyebrow` `.sbar` `.si` `.dot` |
| PaperList.vue | `.pgrid` `.pcard` `.pcard-*` `.pstats-bar` `.ptoolbar` `.pempty` |
| PaperUpload.vue | `.editor-area` `.ep/.pp` `.tb` `.tb-btn` `.upload-card` `.drop-zone` `.file-card` |
| PaperDetail.vue | `.pd` `.pdm` `.vis-frame` `.dual-reader` `.frow/.fnum/.fbody` |
| Compare.vue | `.cmp-pick-grid` `.cmp-matrix` `.cmp-charts` `.cmp-gap` `.cmp-dash` `.cmp-mini-heat` `.ctabs` |
| AgentChat.vue | `.chat-container` `.thread` `.cbubble` `.chat-input-box` `.chat-*` |
| RAGSearch.vue | 少量特有样式 |
| ArtifactCard.vue | 无需 scoped |

## 实施要点

1. **不改 CSS class 名称** — 遵从 CLAUDE.md 约束
2. **保持 CDN 依赖不变** — MathJax 3 / Mermaid 10 / PDF.js 3.11 / D3.js v7
3. **provide/inject 用 Symbol key** — 避免命名冲突
4. **Vue Router** — `npm install vue-router@4`
5. **迁移顺序** — router.js → 各 composable → Sidebar/Hero → PaperList → PaperUpload → PaperDetail → Compare → AgentChat → RAG → App.vue 收尾
6. **存量功能零变化** — 重构仅改变组织方式，不新增/删除功能，不改变 API 调用

## 预计规模

| 文件 | 预计行数 |
|------|---------|
| router.js | ~25 |
| useApi.js | ~20 |
| usePapers.js | ~120 |
| useCompare.js | ~180 |
| useMarkdown.js | ~80 |
| useI18n.js | ~20 |
| Sidebar.vue | ~50 |
| HeroHeader.vue | ~30 |
| ProgressTracker.vue | ~50 |
| ArtifactCard.vue | ~25 |
| Dashboard.vue | ~40 |
| PaperList.vue | ~90 |
| PaperUpload.vue | ~140 |
| PaperDetail.vue | ~130 |
| Compare.vue | ~220 |
| AgentChat.vue | ~70 |
| RAGSearch.vue | ~100 |
| App.vue | ~30 |
| **总计** | **~1420 行** |

## 不包含

- 功能变更或新增
- CSS 变量或 class 名称修改
- 引入 Pinia / Vuex / TypeScript
- API 契约变更
