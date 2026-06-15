# 自主人工智能研究助手设计说明

## 1. 项目目标

`silver_research_bot` 已重构为一个面向科研场景的自主人工智能研究助手，重点支持：

1. 科研原型验证：快速把研究想法转成完整的实验工作空间。
2. 自主实验：系统自动生成代码、提交 CPU 训练、分析结果。
3. 基准批量生成：对多个课题批量运行，生成可重复的实验结果。
4. 论文初稿辅助：基于真实实验数据生成 LaTeX 草稿，辅助写作。
5. 科研审计流程：保留工作空间、中间产物和日志，支持回溯。
6. 文献检索 RAG：把论文入库、检索、上下文组装和研究建议串成一条链路。

## 2. 总体架构

### 后端

- 框架：`FastAPI`
- 科研核心：`silver_research_bot.research_core.ResearchCore`
- 文献检索：`silver_research_bot.research_rag.ResearchRAG`
- 启动入口：`silver_research_bot.research_app`
- 主要职责：
  - 创建/执行研究任务
  - 管理科研工作区
  - 生成实验分析结果
  - 输出论文初稿
  - 记录审计事件
  - 管理文献库与 RAG 检索

### 前端

- 框架：`Vue 3 + Vite`
- 入口：`web/src/App.vue`
- 主要职责：
  - Web 端与 Agent 对话
  - 新建研究任务
  - 批量运行研究主题
  - 查看任务详情与结果
  - 文献入库、检索、上下文构建

## 3. 核心工作流

### 3.1 科研原型验证

用户输入研究主题、假设、约束后，系统会自动生成：

- `briefs/ideation_output.json`
- `plans/experiment_blueprint.json`
- `code/run_experiment.py`
- `code/experiment_spec.json`

### 3.2 自主实验

系统会基于 seeds 与 epochs 生成可执行的 CPU 实验结果，并写入：

- `runs/latest/results.json`
- `runs/latest/metrics.json`
- `analysis/summary.json`
- `analysis/summary.md`
- `paper/main.tex`
- `logs/execution.log`

### 3.3 批量基准生成

批量接口支持一次输入多个主题，统一创建多个研究任务，以便后续进行对比和排名。

### 3.4 论文初稿辅助

`paper/main.tex` 根据真实实验结果自动生成，包含：

- Introduction
- Method
- Experimental Setup
- Results
- Discussion

### 3.5 科研审计

所有关键动作都写入 `audit/events.jsonl`，保证每一步都可追溯。

### 3.6 文献检索 RAG

RAG 模块支持以下能力：

- 文献手工入库
- 基于简单向量表示的相似度检索
- 上下文拼接
- 生成研究建议
- 统计知识库快照

其设计目标是：让 Agent 在提出实验假设之前，先从已有文献中提炼研究空白、常用方法和评价指标。

## 4. 工作区结构

每个研究任务会生成独立工作区，结构如下：

```text
~/.silver_research_bot/workspace/research/session-xxxxxxx/
├── briefs/
├── plans/
├── code/
├── runs/latest/
├── analysis/
├── paper/
├── audit/
├── logs/
├── datasets/
└── notes/
```

文献 RAG 独立存储在：

```text
~/.silver_research_bot/workspace/research_rag/
├── index.json
├── corpus.jsonl
└── logs.jsonl
```

## 5. API 说明

### 健康检查

- `GET /api/health`

### 创建研究任务

- `POST /api/research/run`

请求示例：

```json
{
  "topic": "Adaptive Sparse Attention",
  "hypothesis": "验证 CPU 原型环境中稀疏注意力的收益",
  "constraints": ["仅 CPU", "可复现"],
  "seeds": [7, 13, 29],
  "epochs": 8,
  "dry_run": false
}
```

### 执行已有任务

- `POST /api/research/run/{run_id}/execute`

### 批量运行

- `POST /api/research/batch`

### 查看任务详情

- `GET /api/research/runs/{run_id}`

### 查看审计日志

- `GET /api/research/runs/{run_id}/audit`

### 生成论文大纲

- `GET /api/research/runs/{run_id}/paper-outline`

### 任务对比

- `GET /api/research/compare?run_ids=...`

### 文献 RAG

- `GET /api/rag/papers`
- `POST /api/rag/papers`
- `POST /api/rag/search`
- `POST /api/rag/context`
- `POST /api/rag/suggest`
- `GET /api/rag/snapshot`

### Web 对话

- `POST /api/agent/chat`

## 6. 新增与改动说明

### 新增 `research_core.py`

把科研领域逻辑从 API 层剥离出来，统一管理：

- 工作区创建
- 研究 brief
- 实验计划
- 模拟 CPU 实验
- 分析结果
- LaTeX 草稿
- 审计日志
- 论文提纲
- 研究笔记

### 新增 `research_rag.py`

新增文献检索模块，使用轻量级本地向量表示实现：

- 文献入库
- 文献切片
- 相似度检索
- 上下文构建
- 研究建议生成

### 改造 `research_app.py`

后端现在只负责：

- 暴露 HTTP API
- 参数校验
- 错误处理
- 静态前端挂载
- 文献 RAG 接口

### 改造前端

前端从简单页面升级为科研工作台，支持：

- 新建任务
- 批量任务
- Agent 对话
- 任务列表
- 任务详情查看
- 文献入库与检索
- RAG 上下文与建议展示

## 7. 下一步可扩展方向

建议后续继续加入：

1. 异步任务队列：将 CPU 训练与批量实验改为后台任务。
2. 真实向量数据库：替换当前轻量向量实现，接入 FAISS / Chroma / Qdrant。
3. 文献自动抓取：支持 arXiv、Semantic Scholar、Crossref 等来源。
4. 真实训练器：替换模拟实验为真实 Python/CPU 训练脚本。
5. 实验对比看板：多 run 统计图、leaderboard、趋势图。
6. 导出功能：导出 ZIP、PDF、LaTeX、Markdown 报告。
7. 更强 Agent 编排：将对话、规划、执行、审计分成独立子 Agent。

## 8. 本次改造总结

本次更新在原有“自主科研助手骨架”基础上加入了文献检索 RAG，使系统具备“先读文献、再做研究、再跑实验、再写论文”的闭环能力，并继续以 Web 端作为主要交互方式。
