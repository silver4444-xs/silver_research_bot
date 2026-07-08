# Silver Research — 论文研读 AI Agent

上传 PDF 论文即可自动完成：全文翻译(英文)→ 四维系统分析 → 公式逐条解读 → Mermaid 可视化 → 质量审计 → 横向对比。

## 快速开始

```bash
# 1. 配置 API Key: 编辑 ~/.silver_research_bot/config.json
# 2. 启动后端
cd silver_research_bot
pip install fastapi uvicorn pymupdf
uvicorn silver_research_bot.research_app:app --reload --port 8765

# 3. 启动前端
cd web && npm install && npm run dev
# → http://localhost:5173
```

## 分析能力

| 功能 | 英文 | 中文 |
|------|:--:|:--:|
| 全文翻译 (公式→LaTeX) | ✅ | — |
| 系统模型分析 | ✅ | ✅ |
| 问题表述分析 | ✅ | ✅ |
| 优化算法分析 | ✅ | ✅ |
| 实验设计分析 | ✅ | ✅ |
| 公式逐条解读 | ✅ | ✅ |
| Mermaid+HTML 可视化 | ✅ | ✅ |
| 质量审计 | ✅ | ✅ |
| 横向对比 | ✅ | ✅ |

## 架构

```
Vue 3 前端 (Swiss Modernism 2.0 + MathJax + Mermaid)
       ↕ HTTP API
FastAPI (research_app.py)
       ↕
paper_analyzer/  (Pipeline: 解析→翻译→分析→公式→可视化→审计)
       ↕
LLM Provider 层 (DeepSeek / OpenAI / Anthropic / Azure)
```

## Pipeline (7 阶段)

```
Stage 0: 解析 → Stage 1a: 翻译 → Stage 1b: 四维分析(并行)
→ Stage 2: 公式 → Stage 3: 可视化 → Stage 4: 审计 → Stage 5: 入库
```

## API

```
POST   /api/paper/upload        GET /api/paper/list
GET    /api/paper/{id}          GET /api/paper/{id}/progress
GET    /api/paper/{id}/{type}   POST /api/paper/compare
DELETE /api/paper/{id}
```

## 项目结构

```
├── paper_analyzer/     核心引擎 (11 模块)
├── agent/              Agent 框架 (ReAct 循环)
├── providers/           LLM Provider 层
├── templates/paper/    9 个 Prompt 模板
├── research_app.py     FastAPI 应用
├── web/                Vue 3 前端
├── CLAUDE.md           自动加载文档
└── AGENTS.md           开发指南
```

## 技术栈

Python 3.11+ / FastAPI + Uvicorn / Vue 3.5 + Vite 6 / PyMuPDF / MathJax 3 / Mermaid / Jinja2 / Swiss Modernism 2.0
