# SILVER RESEARCH BOT — 论文研读 AI Agent 框架

> 最后更新: 2026-06-15 | 含 6 轮大规模升级 (RAG / 记忆 / 论文搜索 / Pipeline / Agent / 前端)

## 项目概述
基于 Python 异步架构的 AI Agent 框架。核心场景**论文研读**：上传 PDF 自动完成 8 阶段分析（翻译→四维分析→公式解读→可视化→引用图谱→A/B审稿→审计），同时提供混合 RAG 检索、多 Agent 协作、30+ LLM Provider。

## 技术栈
| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+, FastAPI + Uvicorn, asyncio, PyMuPDF, httpx |
| 前端 | Vue 3.5 + Vite 6, 单文件 SPA, 深色科技风, SW 离线, i18n (zh/en) |
| AI | LLM Provider 30+, Embedding (OpenAI text-embedding-3), D3.js v7 |
| 可视化 | MathJax 3 / Mermaid 10 / D3.js 力导向图 / Jinja2 模板 |
| 存储 | 文件系统 (JSON+MD+pickle+numpy), `~/.silver_research_bot/workspace/` |

## 启动
```bash
uvicorn silver_research_bot.research_app:app --reload --port 8000
cd web && npm install && npm run dev
```

---

## 目录结构 (关键文件)

```
silver_research_bot/
├── research_app.py          ← FastAPI 主应用 (论文+RAG+Agent+WebSocket)
├── research_rag.py          ← 混合检索 RAG (BM25+向量+重排序+多模态)
├── research_rag 辅助:
│   ├── research_bm25.py         ← Okapi BM25
│   ├── research_vector_store.py ← numpy 向量存储 + tombstone
│   ├── research_embedder.py     ← Embedding 引擎 (provider+TF-IDF 回退)
│   ├── research_reranker.py     ← LLM Cross-Encoder 重排序
│   └── research_multimodal.py   ← 多模态索引 (公式/图表/表格)
├── paper_analyzer/
│   ├── orchestrator.py      ← 8 阶段 Pipeline 编排
│   ├── extractor.py         ← Stage 0: PDF 解析, 80+ Unicode→LaTeX
│   ├── translator.py        ← Stage 1: 分块翻译, 公式保护
│   ├── analyzer.py          ← Stage 2: 4维并行 asyncio.gather
│   ├── formula_explainer.py ← Stage 3: 公式 HTML 卡片解释
│   ├── visualizer.py        ← Stage 4: 程序化 Mermaid+实验表格
│   ├── citation_graph.py    ← Stage 5: D3.js 交互力导向图 ★NEW
│   ├── reviewer.py          ← Stage 6: 三视角 A/B 审稿 ★NEW
│   ├── auditor.py           ← Stage 7: 质量审计
│   ├── reproducer.py        ← 可选: 算法→代码→执行 ★NEW
│   └── manager.py           ← CRUD+索引+增强对比
├── agent/
│   ├── loop.py              ← Agent 主循环 (含 PaperSearchTool 注册)
│   ├── memory.py            ← MemoryStore + MemoryEntry 元数据
│   ├── autocompact.py       ← 重要性评分压缩
│   ├── context.py           ← 三层记忆注入 (Active/Project/Long-term)
│   ├── paper_team.py        ← 多 Agent 协作团队 ★NEW
│   ├── role_factory.py      ← 5角色工厂 + SOUL.md ★NEW
│   ├── memory_scorer.py     ← LLM 重要性评分 ★NEW
│   ├── memory_conflict.py   ← 语义冲突检测 ★NEW
│   ├── memory_retrieval.py  ← 主动记忆检索 ★NEW
│   ├── memory_forgetting.py ← Ebbinghaus 遗忘曲线 ★NEW
│   └── tools/
│       ├── paper_search.py  ← arXiv/SemanticScholar/PubMed/DBLP ★NEW
│       ├── web.py / filesystem.py / shell.py / mcp.py / ...
├── providers/base.py        ← LLMProvider ABC (含 embed/embed_batch)
├── config/schema.py         ← 配置模型 (含 MemoryConfig/RAGConfig/PaperSearchConfig)
├── templates/
│   ├── paper/               ← 9 个分析模板
│   └── roles/               ← 3 个角色模板 ★NEW
└── tests/                   ← Eval 框架 (8 测试类) ★NEW

web/
├── sw.js                    ← Service Worker 离线缓存 ★NEW
└── src/App.vue              ← 单文件 SPA (Dashboard+i18n+11 详情标签)
```

---

## 8 阶段 Pipeline (工作区: workspace/papers/{paper_id}/)

```
0. EXTRACT   → PyMuPDF → extracted.json
1. TRANSLATE → 分块 LLM (仅en) → translation.md
2. ANALYZE   → 4维并行 → analysis_system_model/problem/algorithm/experiment.md
3. FORMULA   → 批量 HTML 卡片 → formula_explanations.md
4. VISUALIZE → 程序化 Mermaid → analysis_visualization.html
5. CITATION  → LLM 提取参考文献 → D3.js 力导向图 → citation_graph.html ★NEW
6. REVIEW    → 三视角并行 → review_theory/engineering/domain.md ★NEW
7. AUDIT     → 结构+LLM审计 → audit_report.json

进度: progress.json {stage, status, message, updated_at}
```

## RAG 检索流程

```
search(query) →
  1. BM25 关键词粗排 top-20
  2. Embedding 向量相似度 top-20 (并行)
  3. Min-max 归一化 + 加权融合 (0.3×BM25 + 0.7×Vector)
  4. LLM Cross-Encoder 重排序 → top-5
  5. 多模态过滤: modality=text|formula|figure|table

CRUD: add_paper / update_paper / delete_paper(tombstone) / reindex
```

## 记忆系统

- **元数据**: `<!-- uid:M1 imp:8 ts:2026-06-15T10:30:00 acc:2026-06-15T14:00:00 -->`
- **三层**: Active Memory (检索注入) → Project Memory (跨session) → Long-term
- **评分**: MemoryScorer LLM 1-10 评分
- **遗忘**: Ebbinghaus R=e^(-t/S), 半衰期 7 天
- **冲突**: 新写入 vs 已有 → 语义相似度 → contradiction/duplicate/update → 新信息优先

## Agent 角色工厂

5 预定义角色: paper_reviewer / code_reviewer / literature_review / translator / formula_expert
+ SOUL.md 自定义角色, 每个角色有专属 tools + temperature

---

## API 端点

### 论文研读
```
POST   /api/paper/upload               GET /api/paper/list
GET    /api/paper/{id}                  GET /api/paper/{id}/export
GET    /api/paper/{id}/progress         WS  /api/paper/{id}/stream ★NEW
POST   /api/paper/{id}/ask ★NEW         GET /api/paper/{id}/audit
GET    /api/paper/{id}/figures/{fname}  POST /api/paper/compare
DELETE /api/paper/{id}
```

### 文献 RAG
```
GET    /api/rag/papers          POST /api/rag/papers
POST   /api/rag/search          POST /api/rag/context
PUT    /api/rag/papers/{id} ★NEW DELETE /api/rag/papers/{id} ★NEW
POST   /api/rag/reindex ★NEW    POST /api/rag/suggest
GET    /api/rag/snapshot
```

> **路由顺序**: `/export` `/progress` `/audit` `/stream` `/ask` 必须在 `/{artifact_type}` 前

---

## 前端注意

1. Vue 3 `v-if`+`v-for` 不能同元素 → `<template v-for>` 包裹
2. 不改 class 名称 (`.card` `.navi` `.btn` `.ptrack` `.dtabs` `.file-card` 等)
3. CSS 变量在 `:root` (`--c-*`)
4. 无 Vue Router/Pinia — 全 reactive refs
5. SVG 必须 `viewBox="0 0 24 24"`
6. i18n: `lang` ref + `t(key)` + `LOCALE` 对象

## 后端注意

1. 路由顺序: 具体路由在通配路由前
2. 路径: 直接用 `_paper_manager.papers_dir / paper_id` 拼接
3. 提示词: 无角色扮演 + 直接输出指令
4. 后台任务: `asyncio.create_task()`, 异常写 error progress.json
5. RAG 端点现已 async → 用 `await _get_rag().xxx()`
6. Provider 懒加载: `_get_orchestrator()` / `_get_rag()`
