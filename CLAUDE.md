# SILVER RESEARCH — 论文研读 AI Agent 框架

## 项目概述
Silver Research (silver_research_bot) 是基于 Python 异步架构的 AI Agent 框架。核心场景为**论文研读**：上传 PDF/文本自动完成翻译、四维系统分析、公式解读、Mermaid 可视化与质量审计。同时提供文献 RAG 检索。

## 技术栈
| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+, FastAPI + Uvicorn, asyncio, PyMuPDF/pypdf |
| 前端 | Vue 3.5 + Vite 6, 纯 JS (单文件 SPA), 深色科技风设计系统 |
| AI | LLM Provider 抽象层, 30+ 提供商 |
| 可视化 | MathJax 3 (LaTeX 公式), Mermaid 10.x (程序化生成流程图), Jinja2 (提示词模板) |
| 存储 | 文件系统: JSON 索引 + Markdown/HTML 产物, `~/.silver_research_bot/workspace/` |

## 启动方式
```bash
# 后端 (端口 8000)
uvicorn silver_research_bot.research_app:app --reload --port 8000

# 前端 (端口 5173+, 代理 /api → 127.0.0.1:8000)
cd web && npm install && npm run dev
```

---

## 完整目录结构

```
silver_research_bot/                  ← Python 包根目录
├── __init__.py / __main__.py         ← 包入口 + CLI
├── silver_research_bot.py            ← SilverAgent 高层 API
│
├── research_app.py                   ← FastAPI 主应用 (论文+RAG+Agent API)
├── research_core.py                  ← ResearchCore: 自主研究引擎
├── research_rag.py                   ← ResearchRAG: TF-IDF 余弦相似度文献检索
│
├── paper_analyzer/                   ← 论文研读核心模块
│   ├── orchestrator.py               ← PaperOrchestrator: 6阶段 Pipeline 编排
│   ├── extractor.py                  ← Stage 0: PDF 解析 (PyMuPDF), Unicode→LaTeX 映射
│   ├── translator.py                 ← Stage 1a: 英文分块翻译, 公式保留 $$
│   ├── analyzer.py                   ← Stage 1b: 4维并行 LLM 分析 (asyncio.gather)
│   ├── formula_explainer.py          ← Stage 2: 公式逐个解释, 批量8个/组
│   ├── visualizer.py                 ← Stage 3: 程序化生成 4层概述+Mermaid+实验表格
│   ├── auditor.py                    ← Stage 4: 结构检查 + LLM 深度质量审计
│   └── manager.py                    ← PaperManager: CRUD + JSON 索引 + 跨论文对比
│
├── agent/                            ← ReAct Agent 运行时 (loop/runner/memory/tools/...)
├── providers/                        ← LLM Provider 抽象层 (OpenAI兼容/Anthropic/Azure)
├── channels/                         ← 多渠道通信 (Telegram/飞书/钉钉/微信/...15种)
├── config/                           ← 配置系统 (JSON + ${VAR} 环境变量)
├── templates/paper/                  ← 9 个 Jinja2 提示词模板
│   ├── translator_system.md / analyzer_system_model.md / analyzer_problem.md
│   ├── analyzer_algorithm.md / analyzer_experiment.md / formula_explainer.md
│   ├── visualizer.md / comparison.md / auditor.md
└── utils/                            ← 工具函数

web/                                  ← Vue 3 前端 (Vite 6)
├── index.html                        ← MathJax CDN + Google Fonts
├── vite.config.js                    ← 端口 5173, /api → 127.0.0.1:8000
└── src/
    ├── main.js                       ← createApp, mount #app
    ├── App.vue                       ← 完整 SPA (单文件, 无 Vue Router/Pinia)
    └── style.css                     ← 深色科技风设计系统
```

---

## 前端架构 (App.vue)

### 标签页 (tab 响应式变量, 3个)
| tab | 说明 |
|---|---|
| `agent` | DeepSeek 风格对话: 用户右/Agent左气泡, Enter发送, 回形针文件上传, 圆形上箭头按钮 |
| `papers` | 论文研读, 4个子视图 (ps: upload/list/detail/compare) |
| `rag` | 文献 RAG: 检索/上下文/入库/列表 |

> runs/detail 标签页已移除 (2026-06-08)。

### 论文研读子视图
- **upload**: 双模式 (PDF拖拽 / Markdown编辑器+实时预览), 文件卡片 (图标+名称+大小+类型+移除), 6阶段进度, 全宽布局
- **list**: 论文卡片列表, 点击→detail, 可删除
- **detail**: 8个分析标签页 (翻译/4维分析/公式/可视化iframe/审计), 含"导出全部"按钮
- **compare**: 多选下拉, LLM 横向对比

### Agent 对话
- DeepSeek 风格圆角输入框 (`.chat-input-box`): 左回形针 → 输入区 → 右圆形上箭头发送按钮
- 用户消息靠右 (蓝紫边框), Agent 消息靠左 (紫色边框), max-width 78%
- Enter 发送, Shift+Enter 换行; 输入框自动伸缩
- 文件上传直接调用 `/api/paper/upload`, Agent 回复分析状态

### 前端设计
深色科技风 — `#0A0B1F` 基底, 网格纹理, 玻璃态卡片, 紫蓝渐变按钮, 霓虹发光, 赛博侧边栏

---

## API 端点

### 论文研读 (research_app.py)
```
POST   /api/paper/upload              ← multipart file, 返回 paper_id, 后台 asyncio.create_task 跑分析
GET    /api/paper/list                 ← 索引 + 文件系统扫描回退
GET    /api/paper/{paper_id}           ← 论文完整数据 + 所有产物内容
GET    /api/paper/{paper_id}/export    ← ZIP 打包下载 (文件名 = 论文标题)
GET    /api/paper/{paper_id}/progress  ← progress.json, 前端 2s 轮询
GET    /api/paper/{paper_id}/audit     ← 审计报告 JSON
GET    /api/paper/{paper_id}/figures/{filename}  ← 提取的论文图片 (PNG)
GET    /api/paper/{paper_id}/{type}    ← 单个产物 (兜底路由, 必须定义在最后)
POST   /api/paper/compare             ← 横向对比
DELETE /api/paper/{paper_id}           ← 删除
```

> **路由顺序至关重要**: `/export`, `/progress`, `/audit` 必须在 `/{artifact_type}` 之前, 否则 FastAPI 会把 `/progress` 误匹配到 `/{artifact_type}` (artifact_type="progress")

### 文献 RAG
```
GET    /api/rag/papers                 POST   /api/rag/papers
POST   /api/rag/search                POST   /api/rag/context
POST   /api/rag/suggest               GET    /api/rag/snapshot
```

### 其他
```
GET    /api/health                     POST   /api/agent/chat
```

---

## 6 阶段 Pipeline

```
Stage 0: EXTRACT → PyMuPDF 解析 → Unicode→LaTeX → 语言检测 → extracted.json
Stage 1a: TRANSLATE (仅英文) → 分块 LLM 翻译 → translation.md
Stage 1b: ANALYZE → 4维并行 (asyncio.gather) → 4个 analysis_*.md
Stage 2: FORMULA → 公式批量解释 → formula_explanations.md
Stage 3: VISUALIZE → 程序化生成 4层概述 + Mermaid + 实验表格 → analysis_visualization.html
Stage 4: AUDIT → 结构检查 + LLM 审计 → audit_report.json

工作区: ~/.silver_research_bot/workspace/papers/{paper_id}/
进度: progress.json {stage, status, message, updated_at}
```

### 后台任务容错
- `orchestrator.analyze_paper()` 入口: `paper_dir.mkdir(parents=True, exist_ok=True)` 确保目录存在
- `_run()` 异常处理: 写入 `{stage:"error", status:"failed", message:"..."}` 到 progress.json

---

## 提示词模板规范

**所有 9 个模板位于 `templates/paper/`，通过 `render_template()` 加载为系统提示。**

### 三条铁律
1. **禁止角色扮演**: 不得包含 "你是xxx领域专家" → 改为任务描述
2. **强制直接输出**: 每个模板末尾必须含 "直接输出内容，禁止包含问候语、角色介绍、思考过程或元评论"
3. **双约束**: 系统提示 + `analyzer.py` 用户消息都追加直接输出指令

---

## 可视化架构 (2026-06-11 重构)

**核心原则**: 可视化由 Python 程序化生成，不再依赖 LLM 输出 Mermaid（LLM 生成的 Mermaid 极不可靠）。

### visualizer.py 程序化生成组件
| 组件 | 生成方式 | 数据来源 |
|------|---------|---------|
| 4层系统概述 | `_build_overview()` — 从 markdown 提取 `###` 小节标题，CSS 卡片布局 | 4维分析文本 |
| Mermaid流程图 | `_build_mermaid_from_text()` — 提取标题作为节点，自动连成 flowchart TD | 分析文本 |
| 公式依赖图 | `_build_formula_mermaid()` — 用结构化公式数据按行排列+链接 | formulas[{index,latex}] |
| 实验对比表 | `_llm_experiment_table()` — 唯一用 LLM 的组件（需理解数值），含 CSS 回退 | 实验分析文本 |
| HTML 包装 | `_wrap_html()` — 注入 OVERVIEW_CSS + Mermaid CDN + error handler | — |

### 配色体系 (全项目统一)
- 系统模型: 蓝 `#E6F1FB` bg / `#185FA5` border
- 问题表述: 紫 `#EEEDFE` bg / `#534AB7` border
- 优化算法: 琥珀 `#FAEEDA` bg / `#854F0B` border
- 实验设计: 绿 `#EAF3DE` bg / `#3B6D11` border
- 公式卡片: 6色标签 (sys/mdp/alg/rwd/gat/obs)

### 前端渲染
- 公式解读: `renderFormula()` 检测 HTML 直出（跳过 renderMd 的 HTML 转义）
- 可视化: iframe 内嵌独立 HTML 文档（自带 Mermaid v10 CDN + error handler）
- Mermaid 必须用 `<pre class="mermaid">`，禁止 `<div>`

---

## Extractor 输出数据结构 (2026-06-10 更新)

`extract_pdf_text()` 返回的 dict 包含 11 个字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `pages` | list[dict] | 每页的文本块 (block.text, has_formula, section) |
| `sections` | list[dict] | 检测到的章节标题 |
| `formulas` | list[dict] | 结构化公式 {index, latex, context, page} |
| `figures` | list[dict] | 图片 {index, page, bbox, width, height, caption, placeholder} |
| `tables` | list[dict] | 表格 {index, page, bbox, rows, cols, markdown} |
| `full_text` | str | 完整文本 (含 `[图N]` `[表N]` 占位符) |
| `page_count` | int | 总页数 |
| `formula_count` | int | 公式数量 |
| `figure_count` | int | 图片数量 |
| `table_count` | int | 表格数量 |

> **公式检测**: 字体(`_is_math_font`) + 启发式(`_looks_like_formula`)双信号，跨span连续数学片段自动合并，最小 3 字符。
> **图片检测**: `type==1` 图像块 + 启发式图题检测 (垂直距离 < 70pt 短文本匹配 `Fig|Figure|图`)。
> **表格检测**: `page.find_tables()` API → `_cells_to_markdown_table()` → Markdown 表格，try/except 容错。

## Extractor 关键逻辑

### 公式检测流程 (线 130-153)
```
span → _is_math_font 或 _looks_like_formula → True
  → _convert_formula_text (Unicode→LaTeX)
  → line_has_math = True
  → block_has_formula = True  (2026-06-10 修复)
  → 若 prev_span_was_math: 合并到 block_formulas[-1]; 否则新增
```
### 图片处理 (线 122-150)
```
if block["type"] == 1: → 提取 bbox → 向后4块查找图题 → 注入 [图N：描述]
```
### 表格处理 (每页块循环后)
```
try: page.find_tables() → _cells_to_markdown_table() → 注入 [表N]\n|...|
except: pass
```

---

## 前端渲染管线 (2026-06-11 更新)

```
论文分析产物 → API 加载到 pdet → 根据内容类型选择渲染路径:
  翻译/分析/公式(旧) → renderAll() → renderMd() → HTML转义 → v-html
  公式解读(HTML)    → renderFormula() → 检测 frow/style → 直出 v-html → retypeset()
  可视化            → iframe srcdoc → 独立 HTML 文档 (自带 CSS+Mermaid+MathJax)
```

- `web/index.html`: Mermaid v10 CDN + `mermaid.initialize({startOnLoad:false})` + MathJax 3 CDN
- `web/src/App.vue`: 新增 `renderFormula()` 检测 HTML 直出；`renderMd()` 处理 Markdown → Mermaid
- 可视化 iframe 自带完整 HTML 文档 (独立 Mermaid CDN + error handler)

---

## 已知修复记录 (按时间倒序)

### 2026-06-13
- **文件卡片**: 上传区域选择/拖拽 PDF 后显示文件卡片 (`.file-card`): 左侧紫色发光竖条 + 文档图标 + 文件名(省略号截断) + 格式化文件大小 + × 移除按钮, 替换原来的纯文本 `.fname`
- **fmtSize 工具函数**: 新增 `fmtSize(bytes)` 将字节数转为 `1.5 MB` 人类可读格式 (B/KB/MB/GB 自适应)
- **文件卡片 CSS**: 新增 7 个样式类 (`.file-card`, `.file-card-icon`, `.file-card-info`, `.file-card-name`, `.file-card-meta`, `.file-card-remove`), 玻璃态边框 + 移除按钮 hover 变红

### 2026-06-11
- **可视化全面重构**: ①`visualizer.py` 完全重写为程序化生成 — `_build_overview()` 从分析文本提取小节生成CSS卡片, `_build_mermaid_from_text()` 提取标题自动构建 flowchart, `_build_formula_mermaid()` 从结构化数据生成公式依赖图 ②仅实验表格用 LLM (`_llm_experiment_table()`), 其余全部程序化 ③4个 analyzer 模板的"可视化图示(Mermaid)"节改为"结构化总结(要点列表)" ④`orchestrator.py` 新增 `formulas=` 参数传递
- **formula_explainer HTML 卡片格式**: ①模板改为 HTML (`.frow` 网格 + `.ftag` 6色分类标签 + `.sec-title` 分组) ②`formula_explainer.py` 新增 `FORMULA_CSS` 包装 + `_wrap_html()` 后处理 ③前端新增 `renderFormula()` 检测 HTML 直出
- **元数据过滤**: `extractor.py` 新增 `_filter_metadata_lines()` 过滤 DOI/arXiv/期刊名/日期/URL/机构行
- **公式检测增强**: `SYMBOL_TO_LATEX` 28→80+ 条目 (含大写希腊/变体/运算符/箭头/Blackboard), `MATH_FONT_PATTERNS` 4→9 模式 (新增 XITS/STIX/Asana/Libertinus/MTPro), 检测阈值 2→1, FORMULA_MARKERS 新增 LaTeX 样式命令检测
- **翻译公式保护**: `translator.py` 新增 `_validate_formulas()` (修复 `\boldsymbol`→`\mathbf` 篡改, 检测未闭合 `$$`) + `_embed_figures_tables()` (`[图N]`→`![图N](figures/figure_N.png)`), 模板强调禁止修改 LaTeX
- **论文图片嵌入**: `extract_pdf_text()` 新增 `output_dir` 参数 → `page.get_pixmap(clip=bbox, dpi=150)` 导出 PNG 到 `figures/` 目录, `research_app.py` 新增 `GET /api/paper/{id}/figures/{filename}` 端点
- **统一配色体系**: 全项目 (formula_explainer + visualizer) 统一 4+6 色配色方案

### 2026-06-10
- **extractor 全面增强**: ①公式 Bug 修复(`block_has_formula` 正确置 True) ②跨 span 公式合并 ③最小公式长度 5→3 ④图片捕获 (type==1 块 + 启发式图题) ⑤表格检测 (`find_tables()` + `_cells_to_markdown_table()`) ⑥输出新增 figures/tables/figure_count/table_count ⑦所有路径一致输出 11 字段
- **formula_explainer 加固**: `_explain_from_text()` 单次 12K 截断 → 分块处理 (12K/块, 500 重叠, try/except)
- **模板可视化指令**: 5 个模板新增"可视化图示"节，要求 LLM 输出 ```mermaid 代码块
- **前端 Mermaid 支持**: `index.html` 加载 CDN, `renderMd()` 提取/恢复 mermaid 块, `retypeset()` 调用 `mermaid.run()`
- **visualizer 增强**: 上下文截断 2000→4000/1500→3000, 逐维度图表生成指令
- **orchestrator/manager**: 提取 figures/tables, 索引新增 figure_count/table_count

### 2026-06-08
- 提示词模板: 删除所有角色扮演话术, 添加"直接输出"指令, analyzer.py 用户消息同步追加
- 前端: 移除 runs/detail 标签页, Agent 对话改为 DeepSeek 风格布局+文件上传
- Vue 3 bug: `v-if` + `v-for` 同元素冲突 → `<template v-for>` 包裹 (v-if 优先级高于 v-for)
- 导出: 新增 `/api/paper/{id}/export`, ZIP 文件名使用论文标题
- Mermaid: `<div>`→`<pre>`, CDN 固定 v10, 渲染失败黄色警告
- 路由顺序: `/progress` `/export` `/audit` 移到 `/{artifact_type}` 之前
- progress 端点: 直接用 `papers_dir/paper_id` 拼接路径, 不依赖索引 workspace_dir
- 后台崩溃: 写 error progress.json 供前端展示
- 前端: pollProgress 不再静默吞错

### 2026-06-07
- 前端全面重设计: 深色科技风, CSS 变量修正, SVG Material 风格, openPaper 错误提示

### 更早
- 项目重命名 nanobot→silver_research_bot, 导入路径修正, uvicorn reload 容错, 异步上传+轮询

---

## 修改前端注意

1. **Vue 3 `v-if` + `v-for` 不能同元素** — v-if 优先级高于 v-for, v-for 别名未定义导致 `.id` undefined 报错。必须 `<template v-for>` 包裹
2. **不改 class 名称** — CSS/JS 依赖 `.card` `.navi` `.btn` `.ptrack` `.dtabs` `.file-card` `.file-card-icon` `.file-card-info` `.file-card-name` `.file-card-meta` `.file-card-remove` 等
3. **CSS 变量在 `:root`** — `--c-*` 颜色/间距/圆角, `--c-secondary` 已删除, 用 `--c-text-secondary`
4. **无 Vue Router / Pinia** — 全通过 reactive refs
5. **SVG 必须 `viewBox="0 0 24 24"`** — 否则不显示

## 修改后端注意

1. **路由顺序**: 具体路由在通配路由之前
2. **路径一致性**: 直接用 `_paper_manager.papers_dir / paper_id` 拼接, 不依赖索引入口 workspace_dir
3. **提示词模板**: 无角色扮演, 含直接输出指令, 系统+用户双约束
4. **后台任务**: `asyncio.create_task()` 启动, 异常写 error progress.json
