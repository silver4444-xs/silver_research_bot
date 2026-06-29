# SILVER RESEARCH BOT — 论文研读 AI Agent 框架

> 最后更新: 2026-06-26 | v0.6.1 | Python 3.11+ | 基于 nanoBot Agent 框架扩展

## 项目概述
基于 Python 异步架构的 AI Agent 框架，**核心场景为论文研读**。上传 PDF → 自动完成 8 阶段深度分析（翻译→四维分析→公式解读→可视化→引用图谱→A/B审稿→质量审计），同时内置混合 RAG 检索、多 Agent 协作团队、30+ LLM Provider、交互式 D3.js 可视化。底层复用 nanoBot 通用 Agent 基础设施（消息总线、会话管理、记忆系统、MCP 集成、多渠道接入）。

## 技术栈
| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+, FastAPI + Uvicorn, asyncio, PyMuPDF, httpx, loguru |
| 前端 | Vue 3.5 + Vite 6, 单文件 SPA (App.vue ~1500 行), 深色科技风 CSS, i18n (zh/en) |
| AI | LLM Provider 30+ (OpenAI/Anthropic/DeepSeek/智谱/通义/火山引擎/SiliconFlow/OpenRouter 等) |
| 可视化 | MathJax 3 / Mermaid 10 / D3.js v7 (力导向图+热力图+折线图) / PDF.js **v3.11** |
| 存储 | 纯文件系统: JSON + Markdown + pickle + numpy, `~/.silver_research_bot/workspace/` |

## 启动命令
```bash
# 后端 (先启动)
uvicorn silver_research_bot.research_app:app --reload --port 8000

# 前端 (后启动)
cd web && npm install && npm run dev
# 浏览器访问 http://localhost:5173
```

---

## 完整目录结构

```
silver_research_bot/               ← Python 包根目录
├── research_app.py                ← FastAPI 主应用 (80+ API端点, 含 WebSocket)
├── research_core.py               ← 通用科研实验引擎 (ResearchCore, 非论文专用)
├── research_cli.py                ← CLI 入口
├── research_service.py            ← 业务服务层
├── research_workflow.py           ← 工作流编排
│
├── paper_analyzer/                ← ★核心: 论文分析子系统
│   ├── orchestrator.py            ← 8 阶段 Pipeline 编排器 (PaperOrchestrator)
│   ├── extractor.py               ← Stage 0: PDF 解析 (PyMuPDF, doc.extract_image原图提取, 80+ Unicode→LaTeX, 公式5规则过滤)
│   ├── translator.py              ← Stage 1a: 分块翻译 (3000字/块, 前文摘要连贯, 公式占位保护)
│   ├── analyzer.py                ← Stage 1b: 四维并行 asyncio.gather (系统模型/问题表述/优化算法/实验设计)
│   ├── formula_explainer.py       ← Stage 2: 公式批量 LLM 解释 → HTML 卡片 (.frow)
│   ├── visualizer.py              ← Stage 3: 程序化 Mermaid 图表 + 实验表格
│   ├── citation_graph.py          ← Stage 4a: LLM 提取参考文献 → D3.js 力导向图 HTML
│   ├── reviewer.py                ← Stage 4b: 三视角 A/B 审稿 (理论家/工程派/领域专家)
│   ├── auditor.py                 ← Stage 5: 结构完整性检查 + LLM 深度审计
│   ├── reproducer.py              ← 可选: 算法伪代码→LLM 转 Python→subprocess 执行→对比原文指标
│   ├── manager.py                 ← PaperManager: CRUD + 索引 + 横向对比 (LLM增强)
│   ├── models.py                  ← AnalysisPlan / PaperAnalysis / CrossPaperComparison
│   └── tools.py                   ← 论文分析工具辅助
│
├── agent/                         ← ★nanoBot Agent 基础设施 (论文分析的上层调度框架)
│   ├── loop.py                    ← AgentLoop 核心 (~1500行): ReAct循环, 中轮注入, crash恢复, 流式, MCP
│   ├── runner.py                  ← AgentRunner: 封装 LLM 调用执行循环
│   ├── context.py                 ← ContextBuilder: 系统提示+历史+记忆+技能+引导文件
│   ├── memory.py                  ← MemoryStore + Consolidator + Dream (~1140行)
│   ├── autocompact.py             ← AutoCompact: 空闲会话自动压缩, TTL管理
│   ├── hook.py                    ← AgentHook: 钩子系统 (流式/工具进度/完成回调)
│   ├── skills.py                  ← SkillsLoader: 动态技能加载与管理
│   ├── subagent.py                ← SubagentManager: 子代理管理
│   ├── paper_team.py              ← PaperAnalysisTeam: 三Agent协作 (Translator+Analyzer+Auditor)
│   ├── role_factory.py            ← 5 预定义角色 + SOUL.md 自定义角色系统
│   ├── memory_scorer.py           ← LLM 1-10 重要性评分
│   ├── memory_conflict.py         ← 语义冲突检测 (contradiction/duplicate/update)
│   ├── memory_retrieval.py        ← 主动记忆检索注入
│   ├── memory_forgetting.py       ← Ebbinghaus R=e^(-t/S) 遗忘曲线, 半衰期7天
│   └── tools/                     ← 14个内置工具
│       ├── paper_search.py        ← arXiv/SemanticScholar/PubMed/DBLP 并行搜索
│       ├── base.py                ← Tool 基类 + @tool_parameters 装饰器
│       ├── schema.py              ← 参数 schema (StringSchema/IntegerSchema/...)
│       ├── web.py                 ← WebSearchTool + WebFetchTool
│       ├── filesystem.py          ← ReadFile/WriteFile/EditFile/ListDir
│       ├── shell.py               ← ExecTool (子进程执行)
│       ├── message.py             ← MessageTool (主动消息发送)
│       ├── spawn.py               ← SpawnTool (启动子代理)
│       ├── cron.py                ← CronTool (定时任务)
│       ├── self.py                ← MyTool (Agent自省/修改配置)
│       ├── mcp.py                 ← MCP 协议客户端
│       ├── search.py              ← GlobTool + GrepTool
│       ├── notebook.py            ← NotebookEditTool
│       ├── sandbox.py             ← 沙箱执行
│       ├── registry.py            ← ToolRegistry
│       ├── file_state.py          ← 文件状态追踪
│       └── schema.py              ← 参数类型定义
│
├── providers/                     ← LLM Provider 层
│   ├── base.py                    ← LLMProvider ABC (~980行): 统一接口+自动重试+角色交替修正+图片降级
│   ├── openai_compat_provider.py  ← OpenAI 兼容 Provider (30+提供商共用)
│   ├── anthropic_provider.py      ← Anthropic Provider
│   ├── openai_codex_provider.py   ← OpenAI Codex Provider
│   ├── github_copilot_provider.py ← GitHub Copilot Provider
│   ├── azure_openai_provider.py   ← Azure OpenAI Provider
│   ├── registry.py                ← Provider 注册与自动检测
│   ├── transcription.py           ← 语音转文字
│   └── openai_responses/          ← OpenAI Responses API 解析
│
├── config/                        ← 配置系统
│   ├── schema.py                  ← Pydantic 配置模型 (Agent/RAG/Memory/Provider/Dream/Channels/PaperSearch)
│   ├── loader.py                  ← 配置加载器 (JSON/YAML + 环境变量)
│   └── paths.py                   ← 工作区路径管理
│
├── bus/                           ← 消息总线
│   ├── events.py                  ← InboundMessage / OutboundMessage 定义
│   └── queue.py                   ← MessageBus 队列实现
│
├── session/                       ← 会话管理
│   ├── manager.py                 ← SessionManager: 会话持久化与恢复
│   └── __init__.py                ← Session 数据模型
│
├── channels/                      ← 多渠道接入
│   ├── weixin.py / wecom.py / dingtalk.py / feishu.py / telegram.py / discord.py / slack.py / whatsapp.py / qq.py / matrix.py / mochat.py
│   ├── websocket.py               ← WebSocket 通道
│   ├── email.py / msteams.py
│   ├── base.py                    ← 通道基类
│   ├── manager.py                 ← ChannelManager
│   └── registry.py                ← 通道注册
│
├── cron/                          ← 定时任务
│   ├── service.py                 ← CronService
│   └── types.py                   ← CronSchedule
│
├── heartbeat/                     ← 心跳监控
│   └── service.py
│
├── command/                       ← 斜杠命令系统
│   ├── router.py                  ← CommandRouter (/stop, /reset, /help 等)
│   ├── builtin.py                 ← 内置命令实现
│   └── __init__.py                ← CommandContext
│
├── skills/                        ← 内置技能
│   ├── skill-creator/             ← 技能创建工具
│   └── summarize/                 ← 摘要技能
│
├── utils/                         ← 工具函数
│   ├── helpers.py                 ← strip_think, image_placeholder, 时间格式化
│   ├── prompt_templates.py        ← render_template (Jinja2)
│   ├── document.py                ← extract_documents (媒体文件解析)
│   ├── gitstore.py                ← GitStore (SOUL.md/MEMORY.md 自动版本追踪)
│   ├── evaluator.py               ← Eval 评估框架
│   ├── runtime.py                 ← 运行时常量和环境检测
│   ├── restart.py                 ← 进程重启
│   ├── tool_hints.py              ← 工具调用人类可读提示
│   ├── searchusage.py             ← 搜索用量统计
│   └── path.py                    ← 路径工具
│
├── security/                      ← 安全 (网络访问控制等)
├── cli/                           ← CLI 交互 (stream, models, onboard, commands)
├── api/                           ← API 服务端
├── templates/                     ← LLM Prompt 模板
│   ├── paper/                     ← 9个论文分析模板
│   ├── roles/                     ← 3个角色模板 (paper_reviewer/code_reviewer/literature_review)
│   ├── agent/                     ← Agent系统模板 (identity/consolidator/skills_section/dream等)
│   └── memory/                    ← 记忆系统模板
├── __init__.py                    ← 包入口 (懒加载, 模块发现)
├── __main__.py                    ← `python -m silver_research_bot`
├── SilverAgent.py                 ← SilverAgent 快速入口
└── silver_research_bot.py         ← nanoBot 核心桥接

web/                               ← 前端 SPA
├── index.html                     ← HTML入口 (CDN: MathJax3/Mermaid10/PDF.js3.11/D3.v7)
├── sw.js                          ← Service Worker (★开发时需禁用, cacheFirst与HMR冲突)
├── package.json                   ← Vue 3.5 + Vite 6
├── vite.config.js                 ← Vite配置 (hmr显式绑定 + API代理到127.0.0.1:8000)
└── src/
    ├── main.js                    ← Vue 挂载入口
    ├── App.vue                    ← ★单文件SPA (~1500行): Dashboard+i18n+5nav+14detailTabs+历史+趋势
    └── style.css                  ← 深色科技风 (~800行): CSS变量 + grid布局 + glow特效
```

---

## 8 阶段 Pipeline (工作区: `workspace/papers/{paper_id}/`)

```
0. EXTRACT   → PyMuPDF 解析 → extracted.json            {title, language, pages, formulas[], figures[], tables[], full_text}
1. TRANSLATE → 分块LLM (仅en)→ translation.md            英语论文翻译为中文, 公式占位保护 <FORMULA_i>
2. ANALYZE   → 四维并行     → analysis_{system_model|problem|algorithm|experiment}.md
3. FORMULA   → 批量LLM卡片  → formula_explanations.md    HTML .frow 卡片 (序号+LaTeX+解释)
4. VISUALIZE → 程序化Mermaid→ analysis_visualization.html 架构图+流程+实验表格
5. CITATION  → LLM提取+D3   → citation_graph.html        力导向图 (paper/foundation/comparison/background)
6. REVIEW    → 三视角并行    → review_{theory|engineering|domain}.md  理论家/工程派/领域专家
7. AUDIT     → 结构+LLM审计 → audit_report.json          {passed, issues[{severity,dimension,detail,fix}]}

进度追踪: progress.json  {stage, status, message, updated_at}
最终摘要: analysis_summary.json
```

## RAG 混合检索流程

```
search(query) →
  1. BM25 Okapi 关键词粗排 top-20     (research_bm25.py)
  2. Embedding 向量相似度 top-20 并行  (research_embedder.py + research_vector_store.py)
  3. Min-max 归一化 + 加权融合 0.3*BM25 + 0.7*Vector
  4. LLM Cross-Encoder 重排序 → top-5  (research_reranker.py)
  5. 多模态过滤: text|formula|figure|table (research_multimodal.py)

存储: corpus.jsonl (JSONL append-only) + index.json + vectors/ (numpy)
CRUD: add_paper / update_paper / delete_paper (tombstone soft-delete) / reindex
```

## Agent 核心架构 (nanoBot 框架)

```
InboundMessage → MessageBus.consume_inbound()
  → AgentLoop.run() 主循环
    → _dispatch(msg) 按会话分发
      → _process_message(msg)
        → ContextBuilder.build_messages()      # 系统提示+记忆+历史+运行时上下文
        → Consolidator.maybe_consolidate()     # Token预算检查+压缩
        → AgentRunner.run()                    # ReAct循环: LLM↔Tool交替
          → provider.chat_with_retry()         # 自动重试+错误分类+图片降级
          → ToolRegistry.execute()             # 并行或串行执行工具调用
          → injection_callback()               # 中轮注入 (pending_queue)
          → checkpoint_callback()              # 运行时检查点 (crash恢复)
        → _save_turn()                         # 持久化对话历史
        → publish_outbound()                   # 发送响应到消息总线

并发控制: asyncio.Semaphore(3) 全局并发限制 + asyncio.Lock per-session 串行保证
```

## 记忆系统 (Memory)

- **MemoryStore**: 纯文件I/O — `memory/MEMORY.md` (结构化条目) + `memory/history.jsonl` (历史JSONL)
- **MemoryEntry**: 结构化记忆, 元数据标签 `<!-- uid:M1 imp:8 ts:2026-06-15T10:30:00 acc:2026-06-15T14:00:00 -->`
- **三层架构**: Active Memory (检索注入每轮对话) → Project Memory (跨session共享) → Long-term Memory (持久归档)
- **Consolidator**: Token预算驱动压缩 — 估算超限→选user消息边界→LLM摘要→更新 last_consolidated
- **Dream**: 两阶段后台整合 — Phase1 (LLM分析历史) → Phase2 (AgentRunner编辑文件, 创建技能)
- **子模块**: MemoryScorer (LLM评分1-10), MemoryConflict (语义冲突检测), MemoryRetrieval (主动检索), MemoryForgetting (Ebbinghaus R=e^(-t/S), 半衰期7天)
- **Git追踪**: GitStore 自动 git commit SOUL.md/USER.md/MEMORY.md 变更

## Agent 角色工厂

5 预定义角色 (paper_reviewer / code_reviewer / literature_review / translator / formula_expert)
+ 通过 SOUL.md 模板自定义角色, 每个角色绑定专属 tools + temperature
+ PaperAnalysisTeam: Translator + Analyzer + Auditor 三Agent协作, 通过 MessageBus 异步协同

## LLM Provider 系统

- **LLMProvider ABC**: chat() / embed() / embed_batch() / chat_stream()
- **智能重试**: 标准模式 (1/2/4s指数退避, 3次) + 持久模式 (上限60s, 10次相同错误退出)
- **429分类**: 区分限流可重试 (rate_limit_exceeded) vs 配额耗尽不重试 (insufficient_quota)
- **图片降级**: 非临时错误自动移除 base64 图片后重试, 成功后永久剥离
- **角色交替修正**: `_enforce_role_alternation()` 合并连续同角色 + 尾部清理 + 安全网
- **30+ Provider**: OpenAI/Anthropic/DeepSeek/Groq/Gemini/Mistral/智谱/通义/阶跃星辰/硅基流动/火山引擎/百度千帆/Moonshot/MiniMax/OpenRouter/Ollama/LM Studio/vLLM 等

---

## API 端点总览 (research_app.py)

### 论文研读 (核心)
```
POST   /api/paper/upload               # 上传PDF/TXT/MD, 返回paper_id, 后台启动8-stage Pipeline
GET    /api/paper/list                  # 所有论文列表 (含文件系统fallback扫描)
GET    /api/paper/{id}                 # 论文完整数据 (含formulas数组, 产物内容)
GET    /api/paper/{id}/export          # 一键导出ZIP (所有产物+原文)
GET    /api/paper/{id}/progress        # 分析进度 {stage, status, message}
GET    /api/paper/{id}/pdf             # 原始PDF文件 (FileResponse, PDF.js阅读器用)
GET    /api/paper/{id}/audit           # 审计报告 JSON
GET    /api/paper/{id}/figures/{fname} # 提取的图表图片
GET    /api/paper/{id}/{artifact_type} # 各产物内容 (translation/system_model/...)
POST   /api/paper/{id}/ask            # 交互式Q&A (基于分析结果+LLM)
DELETE /api/paper/{id}                # 删除论文及所有产物 (shutil.rmtree)
POST   /api/paper/compare             # 多论文横向对比 (LLM增强)
WS     /api/paper/{id}/stream         # WebSocket实时进度推送
```

### 阅读历史 & 趋势
```
POST   /api/paper/{id}/view           # 记录查看事件
GET    /api/history/events             # 阅读时间线 (倒序, 最多200条)
GET    /api/paper/{id}/bookmark       # 获取书签状态
POST   /api/paper/{id}/bookmark       # 切换书签
GET    /api/paper/{id}/notes          # 获取笔记
POST   /api/paper/{id}/notes          # 保存笔记
GET    /api/trends                     # 研究趋势 (10领域关键词匹配 + D3可视化数据)
```
存储: `workspace/reading_history.json` → `{events:[{paper_id,action,timestamp}], bookmarks:{paper_id:true}, notes:{paper_id:text}}`

### 文献 RAG
```
GET    /api/rag/papers                 # 文献列表
POST   /api/rag/papers                 # 入库 (自动BM25+向量+多模态索引)
PUT    /api/rag/papers/{id}           # 更新文献
DELETE /api/rag/papers/{id}           # 删除文献 (tombstone)
POST   /api/rag/search                 # 混合检索 {query, top_k, tag, modality, rerank}
POST   /api/rag/context                # 构建研究上下文 (搜索+组装)
POST   /api/rag/suggest                # 研究建议 (基于检索结果)
GET    /api/rag/snapshot               # 快照统计
POST   /api/rag/reindex                # 全量重建索引
```

### 研究实验
```
GET    /api/research/runs              # 所有实验运行
POST   /api/research/run               # 创建实验运行
POST   /api/research/run/{id}/execute  # 执行实验
POST   /api/research/batch             # 批量实验
GET    /api/research/runs/{id}         # 实验摘要
GET    /api/research/runs/{id}/audit   # 审计事件
GET    /api/research/runs/{id}/paper-outline  # 论文大纲
POST   /api/research/runs/{id}/notes   # 实验笔记
GET    /api/research/compare           # 实验对比
```

### Agent 对话
```
POST   /api/agent/chat                 # Agent对话 (关键词匹配→建议action)
GET    /api/health                     # 健康检查 → {"status":"ok"}
```

> **路由顺序**: `/export` `/progress` `/audit` `/stream` `/ask` 必须在通配路由 `/{artifact_type}` 前面

---

## 前端架构 (App.vue 单文件SPA)

### 5 个导航标签 (nav)
1. **Agent 对话** — 聊天界面, 支持文本+文件上传 (PDF→自动分析)
2. **论文研读** — 上传→列表→详情→对比, 核心交互
3. **文献 RAG** — 检索+入库+列表管理
4. **阅读历史** — 时间线 + 书签
5. **研究趋势** — D3.js 三图: 柱状+热力图+折线

### 14 个详情标签 (dtabs)
translation / system_model / problem_formulation / optimization_algorithm / experiment_design / formulas / visualization / citation_graph / review / audit / pdf_reader / ask

### 关键前端行为
- **上传**: 文件拖拽(FormData) / Markdown编辑器粘贴, 后台异步分析, 2秒轮询 progress
- **PDF阅读器**: PDF.js v3.11 逐页渲染canvas, 双栏同步滚动, 缩放±10%
- **公式交互**: MathJax渲染后扫描 `<mjx-container>` → 匹配 formulaMap → click弹出解释卡片
- **趋势图**: D3.js v7 renderTrendHeatmap (plasma色阶) + renderTrendLineChart (多系列), resize防抖300ms
- **i18n**: `lang` ref + `t(key)` + `LOCALE` 对象, zh/en 切换
- **SVG图标**: 全部内联 `<svg viewBox="0 0 24 24">` 通过 `v-html` 绑定
- **Markdown渲染**: 正则管线 (标题/列表/粗斜体/代码/表格/引用), Mermaid pre标签特殊处理

### 前端约束 (修改时务必遵守)
1. Vue 3 `v-if`+`v-for` 不能同元素 → 用 `<template v-for>` 包裹
2. **不改 CSS class 名称** (`.card` `.navi` `.btn` `.ptrack` `.dtabs` 等)
3. CSS 变量在 `:root` (`--c-*`), 深色科技风
4. 无 Vue Router / Pinia — 全部用 reactive refs 做状态管理
5. dtabs/nav 数组顺序即显示顺序, 不要改
6. 公式交互依赖 MathJax 时序: `retypeset()` → `.then(enhanceAllFormulas)`
7. PDF.js v3.11 通过 CDN 全局 `pdfjsLib` 访问, worker 同版本 CDN
8. D3.js 图表在 `nextTick()` 后渲染, resize 时防抖重绘

---

## ⚠️ 已知问题 & 注意事项

### PDF.js CDN 版本
- **必须使用 v3.11.174** (`pdfjs-dist@3.11.174/build/pdf.min.js`)
- v4.x 只有 ESM 构建 (`.mjs`), 没有 UMD 的 `pdf.min.js`, 无法通过 `<script>` 标签作为全局变量加载
- Worker 必须同版本: `pdfjs-dist@3.11.174/build/pdf.worker.min.js`

### Service Worker 开发注意事项
- `sw.js` 使用 `cacheFirst` 策略缓存所有非API请求（包括 index.html + Vite 客户端代码）
- **开发时必须禁用 SW** (已在 index.html 中注释掉注册代码)
- SW cacheFirst 与 Vite HMR WebSocket 热更新机制根本冲突
- 生产构建时可以恢复 SW 注册

### Python 代码字符编码
- 文件中有中文注释/文档字符串 — 这是正常的
- 但 **Python 语法关键字/操作符必须使用 ASCII 字符**:
  - 字符串引号: `"` (U+0022) 而非 `"` `"` (U+201C/U+201D)
  - 括号: `()` (U+0028/U+0029) 而非 `（）` (U+FF08/U+FF09)
  - 赋值/注释: `= 8  # 说明` 而非 `= 8（说明）`

### 关键导入关系
- `tool_parameters` 装饰器来自 `agent.tools.base`, **不是** `agent.tools.schema`
- `agent/__init__.py` 触发链: context → loop → autocompact → memory → paper_search → ...
- Provider/RAG/Orchestrator 全部使用懒加载 `_get_xxx()` 模式

### 后端开发注意事项
1. **路由顺序**: 具体路由必须在通配路由 `/{artifact_type}` 前面
2. **路径拼接**: 直接用 `_paper_manager.papers_dir / paper_id`, 不用字符串拼接
3. **后台任务**: `asyncio.create_task()`, 异常写入 `progress.json` (不抛向上层)
4. **RAG端点**: 全部 async, 使用 `await _get_rag().xxx()`
5. **配置**: 通过 `config/loader.py` 的 `load_config()` + `resolve_config_env_vars()` 加载

### 工作区结构 (`~/.silver_research_bot/workspace/`)
```
workspace/
├── papers/                  ← 论文分析产物
│   ├── index.json           ← PaperManager 索引
│   └── p_<uuid>/
│       ├── extracted.json   ← Stage 0 解析结果
│       ├── original.pdf     ← 原始文件
│       ├── progress.json    ← 实时进度
│       ├── analysis_plan.json
│       ├── analysis_summary.json
│       ├── translation.md
│       ├── analysis_{system_model|problem|algorithm|experiment}.md
│       ├── formula_explanations.md
│       ├── analysis_visualization.html
│       ├── citation_graph.html
│       ├── review_{theory|engineering|domain}.md
│       ├── audit_report.json
│       └── figures/         ← 提取的图表 PNG
├── research/                ← 通用实验运行
├── research_rag/            ← RAG 索引
│   ├── index.json
│   ├── corpus.jsonl
│   ├── logs.jsonl
│   └── vectors/             ← numpy 向量存储
├── reading_history.json     ← 阅读历史+书签+笔记
└── memory/                  ← Agent 记忆系统
    ├── MEMORY.md            ← 长期记忆条目
    ├── history.jsonl        ← 交互历史 (JSONL)
    ├── .cursor / .dream_cursor  ← 处理游标
    └── HISTORY.md.bak       ← 旧版历史备份
```

---

## v0.4.0 变更记录 (2026-06-22)

### 图片提取 (extractor.py) — 完全重写

**Strategy**: `_try_extract_figure()` — 先提取再编号，仅成功时分配 figure_idx + 占位符。

| 改动 | 说明 |
|------|------|
| `page.get_pixmap(clip=bbox)` → `doc.extract_image(xref)` | 原图提取, 非截图; xref 来自 block.number |
| `got_pixmap` fallback | xref 非图片时回退到截图 |
| **先提取后编号** | 提取失败不分配图号、不插入 `◈FIG_N◈` 占位符 |
| bbox 裁剪到 page.rect | 防止 "Invalid bandwriter header" 错误 |
| 静默跳过非图片块 | 矢量图形/蒙版不产生 "图片未导出" 噪音 |

**图片嵌入 (translator.py)**:

| 改动 | 说明 |
|------|------|
| URL 使用 `image_rel_path` 可变扩展名 | 不再硬编码 `.png` |
| `onerror` 从 `display:none` 改为可见提示 | 加载失败时显示橙色边框提示框 |
| "图片未导出" 样式化 | 左侧橙色边框卡片 |

**图片 API (research_app.py)**:
- `paper_figure` 端点 `media_type` 动态检测: 支持 `.png/.jpg/.jpeg/.gif/.webp/.bmp/.svg/.tiff`

### 公式检测 (extractor.py + formula_explainer.py) — 重写过滤逻辑

**数学公式特征**: 在学术论文中, 公式包含关系/二元运算符(= + ≤ ∈ ⊂)且周围有变量/数字, 或包含带参数的 LaTeX 命令, 或希腊字母伴随其他内容。单个符号(`φ`, `=`, `+`)不是公式。

| 改动 | 说明 |
|------|------|
| `/` 从所有数学运算符字符类移除 | 匹配 URL/DOI/path 的概率远超数学分数 |
| `^` `_` 加入 `FORMULA_MARKERS[3]` | 超/下标是内联数学的通用标记 |
| `_is_valid_formula()` 5 条规则重写 | 孤立 LaTeX 命令(如 `\phi`)→拒绝; 运算符必须伴随变量; 希腊字母必须有其他内容; 显式拒绝 URL/DOI |
| 公式装配: span → block 级合并 | 扫描 block_text 中 `$...$` 区域, 间距≤8 字符的合并; 不再有 ±10 字符填充(引入 English 噪音) |

### 前端 renderMd() (App.vue) — 保留安全 HTML 标签

- 新增 placeholder 保护模式: 在 `<>` 转义前提取 `<div>/<img>/<span>` 标签 → `◈HTML_N◈` → 转义后还原
- 修复翻译中 `<img>` 标签被转义为 `&lt;img&gt;` 的根因 bug

### 可视化 (visualizer.py)

| 改动 | 说明 |
|------|------|
| `_render_md_inline()` | 卡片中 `**text**`→`<strong>`, `*text*`→`<em>`, `` `code` ``→`<code>` |
| `_truncate_at_sentence()` | 在 `。！？.!?` 处截断, 不再硬切 100 字符 |
| `_is_table_row()` | 过滤 Markdown 表格行(含 `|`), 不显示为卡片条目 |
| 行内 `$...$` 保留 | MathJax 可渲染, 不再替换为 `[公式]` 文字 |
| 展示公式 `$$...$$` 静默移除 | 卡片空间太小, 不再显示 "公式" 占位 |
| 条目数 3→5, 字符 100→150 | 更丰富的内容展示 |
| `_llm_experiment_table` fallback | LLM 返回 Markdown 管道表格时转为 HTML `<table>` |

### 审计报告 (auditor.py + App.vue) — 可视化仪表板

- `renderAudit()`: JSON → HTML 仪表板
  - 顶部通过/未通过横幅(绿/红) + 严重/一般/建议数量统计
  - 问题按严重程度分组, 颜色编码左边框(红/橙/蓝)
  - LLM 审计内容通过 `renderMd()` 渲染 Markdown
  - 每个问题显示: 维度 → 详情 → 修复建议

### 原文阅读 (App.vue + style.css) — 全屏双栏 + 段落对齐

| 改动 | 说明 |
|------|------|
| 全屏模式 | position:fixed 覆盖视口, 去除 .app/.main 的 z-index 层叠上下文 |
| 三种模式 | **PDF 原文**(默认): 左 PDF.js + 右译文; **段落对照**: 左原文/右译文按 `\n\n` 段落索引对齐; **提取文本**: 左右连续文本 |
| 同步滚动 | 段落模式: 左右同步 scrollTop; PDF 模式: 比例同步 |
| 悬停高亮 | 段落对 hover 时双方同时高亮 |

### 修改文件清单 (v0.4.0)

```
paper_analyzer/extractor.py        — 图片提取先验后编号;公式检测重写;bbox裁剪
paper_analyzer/translator.py       — 图片可变扩展名;可见错误回退
paper_analyzer/formula_explainer.py — _is_valid_formula 5规则重写(与extractor一致)
paper_analyzer/visualizer.py       — Markdown渲染;智能截断;表格行过滤;LaTeX保留
research_app.py                    — 动态MIME type;无变更
web/src/App.vue                    — renderMd HTML保护;renderAudit仪表板;双栏阅读器重写
web/src/style.css                  — 审计仪表板CSS;双栏阅读器CSS;层叠上下文修复
CLAUDE.md                          — v0.4.0 更新
```

### 当前关键架构决策

1. **图片先提取后编号**：`_try_extract_figure()` 成功后 figure_idx 才递增 → 失败块不留占位符
2. **公式5规则过滤**：运算符+变量 / 带参LaTeX / 希腊+其他 / 数字+运算符 / 已知函数名 — 拒绝孤立符号和URL/DOI
3. **公式block级装配**：扫描 `$...$` 区域间距≤8合并且不加前后填充 → 避免 English 噪音
4. **renderMd HTML保护**：`◈HTML_N◈` 占位符在 `<>` 转义前后保护安全标签
5. **翻译块结构**：2000字符/块 + 动态max_tokens + 2级截断重试 + chunk重叠
6. **层叠上下文**：`.app` 和 `.main` 不能有 `z-index` → 否则 trapping 全屏 fixed 元素
7. **图表API dynamic MIME**：`paper_figure` 端点根据文件扩展名检测 Content-Type
8. **原文阅读默认PDF模式**：左PDF.js渲染 + 右译文, 可切换段落对照/提取文本

---

## v0.5.0 变更记录 (2026-06-24)

### 公式解读重构 — 从翻译提取完整公式

| 文件 | 变更 |
|------|------|
| `formula_explainer.py` | 新增 `extract_formulas_from_translation()` 从 translation.md 提取 `$$...$$` 完整公式; 新增 `_explain_translation_formulas()`; `explain_formulas()` 新增 `translation_text` 可选参数; 公式解读数据源优先级: 翻译公式 → PDF碎片 → 全文回退 |
| `orchestrator.py` | Stage 2: `lang=="en"` 时传入 `translation_text=analysis.translation` |
| `templates/paper/formula_explainer.md` | 新增四级解读层次(符号定义/数学含义/领域含义/关联关系) |

### 引用图谱重写 (6项修复)

| 文件 | 变更 |
|------|------|
| `citation_graph.py` | 完全重写: `_js_escape()` 防XSS; JSON直接嵌入替代Mermaid伪文本+正则; 提取范围 8000→16000字; `related_to` 字段实现引用间关联边; LLM降级韧性; P节点使用论文标题 |

### 公式截断修复

| 文件 | 变更 |
|------|------|
| `extractor.py` | 新增 `_expand_formula_boundaries()` 扩展公式边界; `_is_valid_formula()` 增强(占位符拒绝+英文散文拒绝+`len(non_op)>=2`+命令白名单); 页面级合并 gap 8→4 |
| `formula_explainer.py` | `_is_valid_formula()` 同步所有规则 |
| `web/src/App.vue` | `renderFormula()` 跳过非数学 $ 包裹; `sanitizeLatex()` 合并双下标4种模式+控制字符移除+未闭合括号补全 |

### 修改文件清单 (v0.5.0, uncommitted)

```
paper_analyzer/extractor.py        — _expand_formula_boundaries + _is_valid_formula 增强
paper_analyzer/formula_explainer.py — 翻译公式提取 + _is_valid_formula 同步
paper_analyzer/citation_graph.py   — 完全重写 (6项修复)
paper_analyzer/orchestrator.py     — Stage 2 翻译源切换
templates/paper/formula_explainer.md — 四级解读层次
web/src/App.vue                    — double sub合并 + renderFormula保护 + 控制字符移除
CLAUDE.md                          — v0.5.0 更新
```

---

## v0.6.0 变更记录 (2026-06-25)

### `_is_valid_formula` 去重 (消除双重维护)

| 文件 | 变更 |
|------|------|
| `formula_explainer.py` | 删除本地 `_is_valid_formula`(130行), 改为 `from extractor import _is_valid_formula`, 单一数据源消除同步风险 |
| `extractor.py` | `_is_valid_formula` 中重复词列表 `represents\|denotes\|...\|satisfies` 出现在两行 → 删除重复; `_FORMULA_EXPAND_STOP_RE` 新增 `re.IGNORECASE` |

### `_COMPLETE_FORMULA_RE` 正则转义 Bug 修复 (阻塞级)

**根因**: raw string `r"\\leq"` → Python 字串 `\leq` → 正则 `\l` 是未知转义(消费为 `l`), 匹配 `leq` 而非 `\leq`. 更严重: `\\frac`→`\f`=换页符, `\\neq`→`\n`=换行, `\\sum`→`\s`=空白类. 50+ LaTeX 命令匹配全部失效.

**修复**: 全部 `\\cmd` → `\\\\cmd` (raw string 4个`\`→Python`\\`→正则`\`=字面反斜杠). 覆盖 relational/arithmetic/structural/function/nabla 五类.

| 文件 | 变更 |
|------|------|
| `formula_explainer.py` | `_COMPLETE_FORMULA_RE` 50+ 模式修复: `\\leq`→`\\\\leq`, `\\frac`→`\\\\frac`, `\\sum`→`\\\\sum`, `\\sin\\b`→`\\\\sin\\b`, `\\mathbb\\{E\\}`→`\\\\mathbb\\{E\\}` 等 |

### 公式提取重构 (翻译路径从死代码到正常工作)

**根因**: `extract_formulas_from_translation` 只匹配 `$$...$$`, 但 PDF 提取器只产 `$...$`, 翻译从不含 `$$` → 翻译路径永远返回空 → 回退到 PDF 碎片.

**修复**: 新增 `_promote_display_math()` 预处理, 将独立成行的 `$...$` 升级为 `$$...$$` (判断: 独占行 / 多行 / 后跟编号 `(N)`).

| 文件 | 变更 |
|------|------|
| `formula_explainer.py` | 新增 `_promote_display_math()`; 重写 `extract_formulas_from_translation()` 为 `$$`/`\[` 专用; 新增 `_is_complete_formula()` 过滤纯符号定义 |
| `extractor.py` | 页面级合并 gap `4→8` |

### `.fmean` 行内 LaTeX 渲染修复

| 文件 | 变更 |
|------|------|
| `templates/paper/formula_explainer.md` | 规则4: 要求 `.fmean` 中所有 LaTeX 用 `$...$` 包裹; 规则3: 要求 `.fexpr` 逐字复制公式原文 |
| `web/src/App.vue` | `renderFormula()` 新增 `.fmean` 后处理: 保护已有 `$...$` → 正则匹配 `\cmd{args}` 类 LaTeX → 包裹 `$...$` → 还原保护块 |
| `formula_explainer.py` | 两个 user message 加 `⚠️必须完整复制到fexpr` 标记 |

### 公式解读页面完整数据流 (v0.6.0)

```
翻译产出 (translation.md, 含 $...$)
  │
  ▼
_promote_display_math()      ← 独立行 $...$ → $$...$$ 升级
  │
  ▼
extract_formulas_from_translation()
  │  匹配: $$...$$ + \[...\]
  │  提取: \tag{N} 编号
  │  过滤: _is_complete_formula (关系/算术/结构/函数运算符)
  │
  ▼
_explain_translation_formulas()
  │  分批 → LLM 四级解读 (符号定义/数学含义/领域含义/关联关系)
  │
  ▼
formula_explanations.md → 前端 renderFormula() → MathJax 渲染
```

### 关键架构决策 (v0.6.0)

1. **公式提取只从 `$$...$$`**: 显示公式块 = 论文公式; `$...$` 行内数学不提取
2. **`_promote_display_math` 桥接**: 翻译中只有 `$...$`, 但独立行/有编号者表现同 `$$`, 升级后提取
3. **`_is_complete_formula` 二次过滤**: 即使 `$$...$$` 中也可能有纯符号, 需关系/算术/结构运算符才算完整公式
4. **`_is_valid_formula` 单一源**: extractor.py 维护, formula_explainer.py 导入
5. **LLM LaTeX 逐字复制**: prompt + user message 双重强调, 防止 LLM 截断/改写

### 修改文件清单 (v0.6.0)

```
paper_analyzer/formula_explainer.py  — _COMPLETE_FORMULA_RE 转义修复 + _promote_display_math + extract重写 + _is_complete_formula + _is_valid_formula 去重导入
paper_analyzer/extractor.py          — _is_valid_formula 去重 + re.IGNORECASE + merge gap 8
templates/paper/formula_explainer.md — $...$ 包裹要求 + fexpr 逐字复制要求
web/src/App.vue                      — .fmean 行内 LaTeX 后处理
CLAUDE.md                            — v0.6.0 更新
```

---

## v0.6.1 变更记录 (2026-06-26)

### 公式提取全链路加固 (5 项修复, 3 文件)

**背景**: 公式解读页面产生 `+ It n`、`= D t nyt n,n` 等乱码碎片, 以及 MathJax `#`/`%` 特殊字符报错。

**根因链**: PDF编码损坏 → `_looks_like_formula` 误包裹 → `_expand_formula_boundaries` 跨空格吞噬英文词 → `.isalpha()` 词计数Bug(逗号否决)绕过4词阈值 → `_is_complete_formula` 裸 `=` 放行 → `sanitizeLatex` 不处理 `#` `%` → MathJax 报错

| # | 文件 | 函数/区域 | 修改 |
|---|------|----------|------|
| 1 | `extractor.py:177-215` | `_is_valid_formula` | `.isalpha()`→`re.findall(r'[a-zA-Z]{2,}')`; 阈值4→3+数学结构例外; 新增4+字母长词白名单检查 |
| 2 | `extractor.py:238-255` | `_is_valid_formula` | 新增多token英文检测: ≥1个2+字母纯英文token + 单操作符 + 无强数学→拒绝 |
| 3 | `extractor.py:397-461` | `_expand_formula_boundaries` | 扩展上限10→6; 连续字母上限5→3; **禁止跨空格扩展**; 移除逐个字符累积词检查(simplified) |
| 4 | `formula_explainer.py:72-95` | `_is_complete_formula` | 裸 `=` 为唯一信号 + ≥2个3+字母序列 → 拒绝 |
| 5 | `formula_explainer.py:220-252` | `_promote_display_math` | 移除 `re.DOTALL`; 新增5词安全阈值; 移除跨行promotion |
| 6 | `formula_explainer.py:289-299` | `extract_formulas_from_translation` | 词计数同步修复 |
| 7 | `formula_explainer.py:437-448` | `_explain_translation_formulas` | 新增 `_is_valid_formula` 二次过滤安全网 |
| 8 | `web/src/App.vue:256-265` | `sanitizeLatex` | `#`→`\#`, `%`→`\%`, `~`→`\textasciitilde{}`, Unicode引号→ASCII, 不可断空格规范化 |

### 修改文件清单 (v0.6.1, total uncommitted)

```
paper_analyzer/extractor.py          — _is_valid_formula 全面加固 (4项) + _expand_formula_boundaries 收紧
paper_analyzer/formula_explainer.py  — _is_complete_formula加固 + _promote_display_math安全 + 翻译路径二次过滤 + 词计数修复
web/src/App.vue                      — sanitizeLatex TeX特殊字符转义 (#, %, ~, Unicode引号)
CLAUDE.md                            — v0.6.1 更新
memory/project_state.md              — 项目状态快照 v0.6.1
(+ 16 个其他未提交文件 — 参见 v0.4-v0.6.0 变更)
```

### 公式解读完整数据流 (v0.6.1)

```
PDF → extract_pdf_text() → extracted.json
  │
  │  en papers (翻译路径):
  │  translation.md ($...$)
  │    → _promote_display_math()        ← 独立行 $→$$ (无DOTALL)
  │    → extract_formulas_from_translation() ← $$/\[ 提取 + _is_complete_formula
  │    → _explain_translation_formulas()     ← _is_valid_formula 二次过滤 ★
  │
  │  zh papers / 回退:
  │  extracted.json formulas[]
  │    → _is_valid_formula() + _is_complete_formula() 双重过滤
  │
  ▼ LLM四级解读 (符号定义/数学含义/领域含义/关联关系)
  ▼ formula_explanations.md
  ▼ 前端 renderFormula() → sanitizeLatex() → MathJax
```

### 当前已知问题 (v0.6.1)

1. **磁盘数据为旧版** — 需重新分析论文才看到全部修复效果
2. `T = 1 - mu1T t` 类 borderline 公式被放行 (双操作符，可能是真公式)
3. 公式 popover 中 LaTeX 未 MathJax 渲染
4. `_promote_display_math` 可能误升级独占一行的行内公式
