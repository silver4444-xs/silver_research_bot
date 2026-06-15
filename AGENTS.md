# AGENTS — 论文研读 Agent 开发指南

## 项目概述
Silver Research 是论文研读 AI Agent 平台。上传 PDF 论文自动完成：翻译→四维分析→公式解读→可视化→审计→横向对比。

## 编码约定
- Python 3.11+, `from __future__ import annotations` 必须在文件最前
- 导入: `from silver_research_bot.xxx` (非 `nanobot`)
- 前端: Vue 3 + Vite, 纯 JS, Swiss Modernism 2.0 设计, SVG 图标禁止 emoji
- API: paper 端点需包含文件系统回退 (uvicorn reload 导致内存 index 丢失)

## 核心模块
| 模块 | 职责 |
|------|------|
| paper_analyzer/orchestrator.py | 7 阶段 Pipeline |
| paper_analyzer/extractor.py | PDF/文本提取 |
| paper_analyzer/translator.py | 英文翻译 (公式→LaTeX) |
| paper_analyzer/analyzer.py | 四维并行分析 |
| paper_analyzer/manager.py | 论文管理+横向对比 |
| research_app.py | FastAPI 路由 |
| web/src/App.vue | 前端 SPA |

## 数据流
1. 上传 → Stage 0 解析 → 立即返回 paper_id → 前端 2s 轮询 progress
2. 后台 asyncio.create_task → Stage 1a-4 → 每阶段写 progress.json
3. 完成 → manager.update_status("completed") → 前端重刷详情

## 已知坑
- uvicorn reload: 404 → API 已加文件系统回退
- from __future__ SyntaxError: 中文注释在 future 之前 → 已清理
- ModuleNotFoundError: nanobot → 全部替换为 silver_research_bot
- SilverAgent.py → 复制为 silver_research_bot.py
