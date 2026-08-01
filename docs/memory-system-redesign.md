# 记忆系统重构设计方案

> 日期: 2026-07-30 | 状态: 设计阶段 | 基于 v0.7.0 代码分析

---

## 一、问题诊断

### 1.1 当前架构概览

silver_research_bot 的记忆系统由 **11 个组件** 构成，分布在两条几乎完全隔离的路径上：

```
┌─────────────────────────────────────────────────────────┐
│                    记忆系统现状                           │
├────────────────────┬────────────────────────────────────┤
│  路径A: nanoBot通用  │  路径B: 论文专用 (默认启用)         │
│  (paper_memory_mode │  (paper_memory_mode = True)        │
│   = False 时启用)   │                                    │
├────────────────────┼────────────────────────────────────┤
│ ✅ MemoryStore      │ ✅ MemoryStore (文件I/O)            │
│ ✅ Consolidator     │ ✅ Consolidator (Token压缩)         │
│ ✅ Dream (两阶段)   │ ❌ Dream (被禁用)                   │
│ ✅ ForgettingCurve  │ ❌ ForgettingCurve (被禁用)         │
│ ✅ ConflictDetector │ ❌ ConflictDetector (被禁用)        │
│ —                   │ ✅ PaperKnowledgeIndex (产物索引)   │
│ —                   │ ✅ ResearchContextTracker (状态追踪)│
│ ❌ MemoryScorer     │ ❌ MemoryScorer (从未被调用)        │
│ ❌ ActiveRetriever  │ ⚠️ ActiveRetriever (初始化为None)   │
│ ✅ AutoCompact      │ ✅ AutoCompact (空闲压缩)           │
└────────────────────┴────────────────────────────────────┘
```

### 1.2 核心问题：MEMORY.md 在论文模式下完全冻结

这是最严重的问题。当前默认配置 `paper_memory_mode=True` 导致：

```
用户消息 → _process_message()
  ├─ Consolidator → LLM摘要 → history.jsonl  ✅ 运行
  ├─ PaperKnowledgeIndex.search() → 系统提示  ✅ 运行
  ├─ ResearchContextTracker.update()          ✅ 运行
  ├─ Dream.run()                              ❌ 被禁用 (enabled=False)
  │    └─ MEMORY.md 永不被更新
  ├─ MemoryScorer.score()                     ❌ 从未被调用
  ├─ ConflictDetector.detect()                ❌ 从未被接入
  └─ ForgettingCurve.check_all()              ❌ 被禁用
```

**结果**：每次对话中学到的知识（用户偏好、研究兴趣、论文洞察）在会话结束后全部丢失。MEMORY.md 成为一个静态文件，Consolidator 产生的摘要永远"烂"在 history.jsonl 里。

### 1.3 12 项缺陷清单

#### 🔴 严重缺陷 (CRITICAL)

| # | 缺陷 | 影响 |
|---|------|------|
| 1 | **MEMORY.md 冻结**：Dream 在论文模式下被禁用，没有任何写路径 | 长期记忆完全停滞，对话知识全部丢失 |
| 2 | **Consolidator 摘要无处可去**：摘要写入 history.jsonl 后无人处理 | Token 被浪费在重复注入未处理的摘要 |
| 3 | **三层架构名存实亡**：CLAUDE.md 宣称 Active→Project→Long-term 三层，但实际无数据流动 | 架构文档与实现严重脱节 |
| 4 | **无跨会话知识积累**：用户偏好、研究兴趣、论文关联全部丢失 | 每次对话从零开始，大量重复提问 |

#### 🟡 重要缺陷 (HIGH)

| # | 缺陷 | 影响 |
|---|------|------|
| 5 | **MemoryScorer 完全孤立**：完整的 LLM 评分引擎存在但从未被调用 | 所有记忆条目重要性全为默认值5 |
| 6 | **ConflictDetector 未接入**：冲突检测代码存在但无任何调用路径 | 重复记忆、矛盾记忆无法被检测 |
| 7 | **ActiveMemoryRetriever 初始化为 None**：loop.py:326 设为 None，依赖 CLI 手动补全 | 记忆检索功能实际上不可用 |
| 8 | **三套去重机制互不协调**：keyword_overlap + content_similarity + cosine_similarity 独立运作 | 上下文可能包含冗余或矛盾信息 |

#### 🟢 一般缺陷 (MEDIUM)

| # | 缺陷 | 影响 |
|---|------|------|
| 9 | **PaperKnowledgeIndex 仅全量重建**：无增量更新，无自动触发 | 新论文分析完成后索引不更新 |
| 10 | **ResearchContextTracker 仅用正则**：无语义理解，硬限制5条发现/8个主题 | 多论文场景下容量不足 |
| 11 | **history.jsonl 无限增长**：compact_history() 仅在 Dream.run() 中调用 | 论文模式下永不压缩，文件持续膨胀 |
| 12 | **Consolidator token 估算有竞态条件**：每次构造完整系统提示做估算 | 昂贵且可能过时 |

### 1.4 探索发现的关键事实

通过代码探索确认：

- **MemoryScorer、ConflictDetector、ForgettingCurve 三个文件在整个代码库中零引用** — 它们定义了类但没有任何 import 或调用
- **PaperKnowledgeIndex.build() 没有被任何后台任务触发** — 只在 CLI gateway 启动时创建对象，build() 从未被自动调用。新论文分析完成后索引不更新
- **Dream.run() 仅通过 cron 定时任务触发**（间隔2小时），不在消息处理流程中
- **ActiveMemoryRetriever 仅在 gateway 模式下初始化**（cli/commands.py:709-735），普通 CLI 模式和 HTTP API 模式不创建
- **论文分析完成没有回调机制** — orchestrator 写完 progress.json 和 index.json 后没有通知记忆系统更新
- **三个关键模板文件缺失**：`consolidator_archive.md`、`dream_phase1.md`、`dream_phase2.md` 在项目 `templates/agent/` 目录中不存在。这些模板在 Consolidator.archive() 和 Dream.run() 运行时被 `render_template()` 引用，如果模板不存在于 nanoBot 包中，运行时会抛出 `TemplateNotFound` 异常

---

## 二、设计目标

1. **恢复 MEMORY.md 的写入路径** — 对话中产生的事实、偏好、洞察必须被持久化
2. **接入已存在的孤立组件** — MemoryScorer、ConflictDetector 必须被实际使用
3. **轻量优先** — 不引入 Dream 那样昂贵的两阶段 AgentRunner 流程
4. **增量更新 PaperKnowledgeIndex** — 新论文分析完成时自动索引
5. **跨会话持久化研究上下文** — ResearchContextTracker 状态不过期
6. **修复 history.jsonl 无限增长** — 压缩逻辑不依赖 Dream
7. **正确初始化 ActiveMemoryRetriever** — 让记忆检索真正可用
8. **最小化额外 LLM 成本** — MemoryKeeper 仅在 Consolidator 产生新摘要时才调用 LLM

---

## 三、改进方案：四阶段实施

### 阶段一：修复核心管线（新建1个文件，修改3个文件，~175行）

#### 3.1 新建 `agent/memory_keeper.py` — 轻量级 MemoryKeeper

**设计理念**：替代 Dream 在论文模式下的角色，但成本大幅降低。

**Dream vs MemoryKeeper 对比**：

| 维度 | Dream | MemoryKeeper |
|------|-------|-------------|
| 阶段数 | 两阶段（LLM分析 + AgentRunner编辑文件） | 单阶段（LLM提取 + 直接写文件） |
| LLM 调用 | 至少2次（Phase1 + Phase2可能多次工具调用） | 1次（extract） |
| 编辑方式 | AgentRunner 带工具（read_file/edit_file/write_file） | 直接调用 MemoryStore API |
| 技能创建 | 支持 | 不支持（论文模式不需要） |
| 触发方式 | Cron 定时（2小时间隔） | 对话轮次后异步触发 |
| 处理范围 | 所有未处理历史 | 仅新的 consolidation 摘要 |

**MemoryKeeper 工作流程**：

```
MemoryKeeper.process_new_consolidations()
  │
  ├─ 1. 读取 history.jsonl 中自上次处理以来的新 consolidation 条目
  │     (source="consolidation" 且 cursor > last_keeper_cursor)
  │
  ├─ 2. 批量传给 LLM 提取事实
  │     Prompt: "从以下对话摘要中提取值得长期记忆的事实"
  │     输出格式: [{"fact": "...", "category": "preference|paper_insight|project_fact|user_info"}]
  │
  ├─ 3. 对每条提取的事实:
  │     ├─ MemoryScorer.score(fact) → 1-10 重要性评分
  │     ├─ MemoryConflictDetector.detect(fact, existing_entries)
  │     │     ├─ duplicate → 自动忽略（无需用户决策）
  │     │     ├─ update → 加入待决冲突队列（由用户决定保留哪个）
  │     │     ├─ contradiction → 加入待决冲突队列（由用户决定）
  │     │     └─ unrelated → 直接新增（无冲突，无需决策）
  │     └─ 无冲突 → 直接写入 MEMORY.md
  │
  ├─ 4. 收集所有待决冲突 → 存入 memory/.pending_conflicts.json
  │
  ├─ 5. 通过消息总线向用户发送冲突决策请求:
  │     "⚠️ 检测到 2 条记忆冲突，请选择处理方式:
  │      冲突1: [旧] Transformer在NLP中表现最优 (imp:8)
  │              [新] CNN在某些NLP任务中超越Transformer (imp:6)
  │      选项: 1.保留旧  2.保留新  3.都保留  4.都丢弃"
  │
  ├─ 6. 用户回复决策 → 下一轮对话开始时解析并执行
  │
  ├─ 7. 推进 cursor，compact_history()
  │
  └─ 超时处理: 若 3 轮对话内用户未决策 → 自动按安全策略处理
           (学术模式: 保留双方; 通用模式: 保留高重要性的一方)
```

**关键类签名**：

```python
class MemoryKeeper:
    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        scorer: MemoryScorer,
        conflict_detector: MemoryConflictDetector,
        forgetting: ForgettingCurve | None = None,
        batch_size: int = 5,
        enabled: bool = True,
    ): ...

    async def process_new_consolidations(self) -> int:
        """处理新的 consolidation 摘要，返回新写入的记忆条数"""
```

**新增模板** `templates/agent/memory_keeper_extract.md`：
- 告诉 LLM 只从用户陈述中提取事实（不提取助手的推理过程）
- 输出结构化 JSON 列表
- 分类: preference（偏好）、paper_insight（论文洞察）、project_fact（项目事实）、user_info（用户信息）

#### 3.1b 用户驱动的记忆冲突决策机制

**设计原则**：当检测到 `contradiction`（矛盾）或 `update`（更新）类型冲突时，不自动处理，而是将决策权交给用户。

**冲突类型与处理策略**：

| 冲突类型 | 自动处理？ | 用户选项 |
|----------|-----------|----------|
| `duplicate` | ✅ 自动忽略 | — |
| `unrelated` | ✅ 直接新增 | — |
| `update` | ❌ 需用户决策 | 保留旧 / 保留新 / 都保留 / 都丢弃 |
| `contradiction` | ❌ 需用户决策 | 保留旧 / 保留新 / 都保留 / 都丢弃 |

**完整交互流程**：

```
[第N轮对话结束]
  │
  ├─ MemoryKeeper.process_new_consolidations()
  │   ├─ 提取事实 + 评分 + 冲突检测
  │   ├─ 无冲突的事实 → 直接写入 MEMORY.md ✅
  │   └─ 有冲突的事实 → 写入 .pending_conflicts.json
  │
  ├─ MemoryKeeper 发现有 2 个待决冲突
  │
  ├─ 通过 MessageBus 向用户发送冲突提示:
  │   ┌─────────────────────────────────────────────┐
  │   │ ⚠️ 记忆冲突 — 请选择处理方式:                 │
  │   │                                             │
  │   │ 冲突 #pc1 (update):                          │
  │   │   旧记忆: "我主要关注NLP领域" (imp: 7)        │
  │   │   新信息: "我对CV方向也很感兴趣" (imp: 6)     │
  │   │   [1.保留旧] [2.保留新] [3.都保留] [4.都丢弃] │
  │   │                                             │
  │   │ 冲突 #pc2 (contradiction):                   │
  │   │   旧记忆: "Transformer在NLP中表现最优" (imp:8)│
  │   │   新信息: "CNN在部分NLP任务中超越Transformer"  │
  │   │   [1.保留旧] [2.保留新] [3.都保留] [4.都丢弃] │
  │   │                                             │
  │   │ 回复格式: "冲突#pc1: 3, 冲突#pc2: 3"         │
  │   │ 或直接说 "都保留" / "全部保留旧记忆"          │
  │   └─────────────────────────────────────────────┘
  │
  ▼
[第N+1轮对话开始]
  │
  ├─ AgentLoop._process_message()
  │   ├─ 检查是否有上一轮的待决冲突
  │   ├─ 解析用户消息中的决策指令
  │   │   (支持格式化回复 + 自然语言理解)
  │   ├─ 执行决策: 保留旧/保留新/都保留/都丢弃
  │   ├─ 写入 MEMORY.md
  │   └─ 清理 .pending_conflicts.json
  │
  └─ 正常对话继续...
```

**待决冲突存储格式** (`memory/.pending_conflicts.json`)：

```json
{
  "session_key": "cli:direct",
  "created_at": "2026-07-30T15:30:00",
  "timeout_turn": 3,
  "conflicts": [
    {
      "conflict_id": "pc_a1b2c3",
      "type": "update",
      "old_entry": {
        "uid": "mem_001",
        "text": "我主要关注NLP领域",
        "importance": 7
      },
      "new_fact": {
        "text": "我对CV方向也很感兴趣",
        "importance": 6,
        "category": "preference"
      },
      "status": "pending"
    }
  ]
}
```

**用户决策的自然语言解析**：

除了格式化回复（`冲突#pc1: 3`），系统应支持自然语言决策：

| 用户输入 | 解析结果 |
|----------|----------|
| "都保留" / "keep all" | 所有冲突 → 保留双方 |
| "保留旧的" / "keep old" | 所有冲突 → 保留旧 |
| "用新的替换" / "use new" | 所有冲突 → 保留新 |
| "都不要" / "discard all" | 所有冲突 → 都丢弃 |
| "第一个保留旧，第二个都保留" | 按顺序解析 |
| 无提及冲突的普通对话 | 本轮不处理，冲突继续保持 pending |

**超时与安全兜底**：

```
若连续 3 轮对话用户未对冲突做出决策:
  ├─ 学术模式 (paper_memory_mode=True):
  │   └─ 自动 "都保留" (保守策略，不丢失信息)
  └─ 通用模式 (paper_memory_mode=False):
      └─ 保留重要性更高的一方 (高分优先)
```

冲突超时后，系统发送通知：
> "之前的 2 条记忆冲突已自动处理（3轮未决策）: 冲突#pc1 → 都保留, 冲突#pc2 → 都保留"

**MemoryKeeper 完整类签名**：

```python
class MemoryKeeper:
    """轻量级记忆写入器，替代 Dream 在论文模式下的角色。
    
    处理 Consolidator 产生的摘要 → 提取事实 → 评分 → 冲突检测
    → 无冲突直接写入 MEMORY.md / 有冲突提交用户决策。
    """
    
    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        scorer: MemoryScorer,
        conflict_detector: MemoryConflictDetector,
        bus: MessageBus | None = None,          # 用于向用户发送冲突提示
        forgetting: ForgettingCurve | None = None,
        batch_size: int = 5,
        conflict_timeout_turns: int = 3,         # 冲突超时轮数
        enabled: bool = True,
    ): ...
    
    async def process_new_consolidations(self) -> tuple[int, int]:
        """处理新的 consolidation 摘要。
        Returns: (直接写入数, 待决冲突数)
        """
    
    def _has_pending_conflicts(self) -> bool:
        """检查是否有未解决的记忆冲突"""
    
    def _load_pending_conflicts(self) -> list[dict]:
        """加载待决冲突列表"""
    
    async def resolve_conflicts(self, decisions: dict[str, str]) -> int:
        """
        根据用户决策解决冲突。
        decisions: {"pc_a1b2c3": "keep_old", "pc_d4e5f6": "keep_both", ...}
        有效决策: "keep_old" | "keep_new" | "keep_both" | "discard_both"
        Returns: 实际写入 MEMORY.md 的条目数
        """
    
    def _any_conflicts_expired(self) -> bool:
        """检查是否有超时未处理的冲突"""
    
    def _auto_resolve_expired(self) -> list[str]:
        """自动解决超时冲突，返回已解决的 conflict_id 列表。
        学术模式 → keep_both; 通用模式 → 保留重要性高的一方
        """
    
    async def _send_conflict_prompt(self, conflicts: list[dict]) -> None:
        """通过消息总线向用户发送格式化的冲突决策提示"""
```

**AgentLoop 中的冲突检查钩子** (添加到 `_process_message` 开头)：

```python
# 在 _process_message 的用户消息分支中，先检查是否有待决冲突
if self.memory_keeper and self.memory_keeper._has_pending_conflicts():
    decisions = self._parse_conflict_decisions(msg.content)
    if decisions:
        resolved = await self.memory_keeper.resolve_conflicts(decisions)
        if resolved:
            # 向用户确认
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content=f"✅ 已处理 {len(decisions)} 条记忆冲突，写入 {resolved} 条记忆。",
            ))
    elif self.memory_keeper._any_conflicts_expired():
        # 超时自动处理
        expired = self.memory_keeper._auto_resolve_expired()
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=f"⏰ {len(expired)} 条记忆冲突已超时自动处理（3轮未决策，学术模式：都保留）。",
        ))
```

**前端交互增强（可选，后续阶段）**：

在 Web UI 的 AgentChat 中，冲突提示可以渲染为交互式按钮：
- 每个冲突显示为一张卡片，包含旧/新记忆对比
- 四个按钮直接发送决策：`[保留旧的] [保留新的] [都保留] [都丢弃]`
- 点击后自动发送 `冲突#pc1: 2` 格式的决策消息
- 冲突卡片在决策后消失，显示 ✅ 已处理

#### 3.2 修改 `agent/loop.py` — 接入 MemoryKeeper

**变更点 1**：`__init__` 方法（第310-326行附近）

```python
# 当前代码:
self.dream = Dream(..., enabled=not paper_memory_mode)
self.research_tracker = None
self.active_memory_retriever = None
if paper_memory_mode:
    self.research_tracker = ResearchContextTracker()
    self.active_memory_retriever = None  # 延迟创建

# 改为:
# MemoryScorer 和 ConflictDetector 始终创建（不再孤立）
self.memory_scorer = MemoryScorer(provider=provider, model=self.model)
self.memory_conflict = MemoryConflictDetector(
    provider=provider, model=self.model,
    academic_mode=paper_memory_mode,
)

if paper_memory_mode:
    # 论文模式：用 MemoryKeeper 替代 Dream
    self.memory_keeper = MemoryKeeper(
        store=self.context.memory,
        provider=provider, model=self.model,
        scorer=self.memory_scorer,
        conflict_detector=self.memory_conflict,
        forgetting=ForgettingCurve(
            halflife_days=config.memory.decay_halflife_days,
            enabled=config.memory.forgetting_enabled,
        ),
    )
    self.research_tracker = ResearchContextTracker()
    self.active_memory_retriever = None  # embedder 就绪后补全
    self.dream = None
else:
    # 通用模式：保持 Dream
    self.dream = Dream(..., enabled=True)
    self.memory_keeper = None
    self.research_tracker = None
    self.active_memory_retriever = None
```

**变更点 2**：`_process_message` 方法（第1376行后）

```python
# 在 self.sessions.save(session) 之后、return 之前添加:
if self.memory_keeper:
    self._schedule_background(self._run_memory_keeper())

# 新增方法:
async def _run_memory_keeper(self) -> None:
    """后台运行 MemoryKeeper"""
    try:
        n = await self.memory_keeper.process_new_consolidations()
        if n:
            logger.info("MemoryKeeper: 持久化 {} 条新记忆", n)
    except Exception:
        logger.exception("MemoryKeeper: 后台处理失败")
```

**变更点 3**：记忆检索调用点（第1082-1098行、第1189-1198行）

现有代码已经正确设置了 `_paper_context`、`_research_context_block`、`_active_memory_text` 的注入逻辑，问题仅在于 `active_memory_retriever` 是 None。需要确保 embedder 就绪时初始化它。

#### 3.3 修改 `agent/memory.py` — 添加 cursor 文件 + 修复压缩

**变更点 1**：MemoryStore.__init__ 添加新 cursor 文件

```python
self._memory_keeper_cursor_file = self.memory_dir / ".memory_keeper_cursor"
```

**变更点 2**：添加 cursor 读写方法

```python
def get_last_memory_keeper_cursor(self) -> int: ...
def set_last_memory_keeper_cursor(self, cursor: int) -> None: ...
```

**变更点 3**：修复 history.jsonl 无限增长

当前 `compact_history()` 仅在 `Dream.run()` 末尾调用。需要在 `MemoryStore.append_history()` 中添加轻量检查：

```python
def append_history(self, entry, ...):
    # ... 现有逻辑 ...
    # 轻量压缩：超过阈值时在本次调用中裁剪
    if self.max_history_entries > 0:
        entries = self._read_entries()
        if len(entries) > self.max_history_entries:
            kept = entries[-self.max_history_entries:]
            self._write_entries(kept)
            logger.debug("history.jsonl compacted: {} → {} entries",
                        len(entries), len(kept))
```

同时在 `MemoryKeeper.process_new_consolidations()` 末尾也调用一次压缩。

#### 3.4 修改 `agent/context.py` — 确保 ActiveMemoryRetriever 被使用

ContextBuilder 中已有的 `_active_memory_text` 注入逻辑（第86-87行）是正确的，`build_system_prompt()` 也接受 `active_memory_text` 参数。不需要修改代码逻辑，只需要确保 `ActiveMemoryRetriever` 被正确初始化并传入。

---

### 阶段二：PaperKnowledgeIndex 增量更新（修改2个文件，~65行）

#### 3.5 修改 `agent/paper_knowledge.py` — 添加增量索引方法

当前 `build()` 是全量重建，需要新增 `add_paper()`：

```python
async def add_paper(self, paper_id: str) -> bool:
    """增量索引单篇论文的分析产物。返回 True 表示添加了新 chunk。"""
    if self._is_paper_indexed(paper_id):
        return False

    # 1. 从 index.json 读取论文元数据
    papers_index = self.papers_dir / "index.json"
    if not papers_index.exists():
        return False
    all_papers = json.loads(papers_index.read_text(encoding="utf-8"))
    paper_meta = all_papers.get(paper_id)
    if not paper_meta or paper_meta.get("status") != "completed":
        return False

    # 2. 提取产物 chunk（复用 build() 中的逻辑）
    new_chunks = []
    title = paper_meta.get("title", paper_id)
    paper_dir = self.papers_dir / paper_id
    for kind, cfg in _ARTIFACT_CONFIG.items():
        fpath = paper_dir / cfg["filename"]
        if not fpath.exists():
            continue
        text = fpath.read_text(encoding="utf-8")[:cfg["max_chars"]]
        if not text.strip():
            continue
        new_chunks.append({
            "chunk_id": f"{paper_id}:{kind}",
            "paper_id": paper_id, "title": title,
            "artifact_kind": kind, "text": text,
            "path": str(fpath),
        })

    if not new_chunks:
        return False

    # 3. 批量计算 embedding
    texts = [c["text"] for c in new_chunks]
    vectors = await self.embedder.embed_batch(texts)

    # 4. 追加到内存和缓存
    self._chunks.extend(new_chunks)
    self._vectors.extend(vectors)
    self._save_index_meta({"_mtime": papers_index.stat().st_mtime, "_built": True, "chunks": self._chunks})

    # 5. 增量更新 embedding 缓存
    self._save_vectors_to_cache(texts, vectors)

    # 6. 重建关系图（只更新涉及新论文的关系）
    self._build_relations()

    logger.info("PaperKnowledgeIndex: 索引新论文 {} ({} chunks)", paper_id, len(new_chunks))
    return True

def _is_paper_indexed(self, paper_id: str) -> bool:
    return any(c["paper_id"] == paper_id for c in self._chunks)
```

#### 3.6 修改 `paper_analyzer/orchestrator.py` — 分析完成时触发索引更新

在 orchestrator 的 Finalize 阶段（`analysis.completed_at` 设置之后），添加回调触发：

```python
# 在 _run_pipeline 的最后阶段:
analysis.completed_at = datetime.now().isoformat()
self._manager.update_status(paper_id, status="completed")

# 新增: 通知外部观察者（如 PaperKnowledgeIndex）
if hasattr(self, '_on_analysis_complete'):
    try:
        self._on_analysis_complete(paper_id)
    except Exception:
        logger.exception("分析完成回调失败")
```

在 `loop.py` 或 `research_app.py` 中注册回调：

```python
# loop.py 中 PaperKnowledgeIndex 创建后:
orchestrator._on_analysis_complete = lambda pid: asyncio.create_task(
    paper_knowledge.add_paper(pid)
)
```

---

### 阶段三：跨会话研究状态持久化（修改2个文件，~60行）

#### 3.7 修改 `agent/research_context.py` — 添加磁盘持久化

当前 `ResearchContextTracker` 将状态仅存储在 `session.metadata` 中，会话过期或 /reset 后全部丢失。

**新增能力**：

1. **磁盘持久化** — 将研究上下文保存到 `memory/research_state_{session_key}.json`
2. **跨会话恢复** — 新会话启动时从磁盘恢复
3. **自然衰减** — 主题列表满时移除最旧的主题

```python
class ResearchContextTracker:
    def __init__(self, paper_knowledge=None, persist_dir: Path | None = None):
        self.paper_knowledge = paper_knowledge
        self._persist_dir = persist_dir
        self._MAX_AGE_TURNS = 10  # 主题超过10轮未出现则移除

    def save_to_disk(self, session: "Session") -> None:
        """持久化研究上下文到磁盘"""
        if not self._persist_dir:
            return
        ctx = self._load(session)
        if not any(ctx.values()):
            return
        safe_key = re.sub(r'[^a-zA-Z0-9_-]', '_', session.key)
        path = self._persist_dir / f"research_state_{safe_key}.json"
        path.write_text(json.dumps(ctx, ensure_ascii=False, indent=2))

    def load_from_disk(self, session: "Session") -> None:
        """从磁盘恢复研究上下文到会话"""
        if not self._persist_dir or self._META_KEY in session.metadata:
            return  # 会话已有更新的状态，不覆盖
        path = self._persist_path(session.key)
        if not path.exists():
            return
        try:
            ctx = json.loads(path.read_text(encoding="utf-8"))
            # 检查新鲜度：超过7天的状态视为过期
            age_days = (time.time() - path.stat().st_mtime) / 86400
            if age_days > 7:
                path.unlink(missing_ok=True)
                return
            session.metadata[self._META_KEY] = ctx
        except Exception:
            pass
```

#### 3.8 修改 `agent/loop.py` — 接入持久化

在 `_process_message` 中：

```python
# 会话加载后立即恢复研究上下文
session = self.sessions.get_or_create(key)
if self.research_tracker:
    self.research_tracker.load_from_disk(session)

# ... 消息处理 ...

# research_tracker.update() 之后保存到磁盘
if self.research_tracker:
    self.research_tracker.update(session, ...)
    self.research_tracker.save_to_disk(session)
```

---

### 阶段四：启用 ForgettingCurve（修改2个文件，~18行）

#### 3.9 修改 `config/schema.py`

```python
# 当前:
forgetting_enabled: bool = False

# 改为:
forgetting_enabled: bool = True   # 轻量级检查，不会增加 LLM 成本
```

#### 3.10 修改 `agent/memory_keeper.py`

在 `process_new_consolidations()` 中，写入新记忆之前先清理衰减条目：

```python
async def process_new_consolidations(self) -> int:
    # 0. 先清理衰减记忆
    if self.forgetting and self.forgetting.enabled:
        entries = self.store.parse_memory_entries()
        for entry in entries:
            if self.forgetting.should_remove(entry):
                self.store.delete_entry(entry.uid)
                logger.info("ForgettingCurve: 移除衰减记忆 {}", entry.uid[:8])
```

---

## 四、改动文件汇总

| 文件 | 阶段 | 改动 | 预计行数 |
|------|------|------|----------|
| `agent/memory_keeper.py` | 1 | **新建** — MemoryKeeper + PendingConflict 模型 + 冲突队列 + 用户决策解析 | +180 |
| `templates/agent/memory_keeper_extract.md` | 1 | **新建** — 事实提取 prompt | +20 |
| `agent/memory.py` | 1, 2 | 添加 cursor 文件 + 修复压缩 + pending_conflicts I/O | +35 |
| `agent/memory_conflict.py` | 1 | 微调 `_resolve()` 移除自动决策逻辑，改为返回 `needs_user_decision` 标记 | +10 |
| `agent/loop.py` | 1, 3 | 接入 MemoryKeeper + 冲突检查钩子 + 用户决策解析 + 持久化 | +70 |
| `agent/context.py` | 1 | 确保 ActiveMemoryRetriever 接入（验证即可） | ~5 |
| `agent/paper_knowledge.py` | 2 | 添加增量 `add_paper()` 方法 | +50 |
| `paper_analyzer/orchestrator.py` | 2 | 分析完成时触发索引更新回调 | +15 |
| `agent/research_context.py` | 3 | 添加磁盘持久化 + 自然衰减 | +50 |
| `config/schema.py` | 4 | 启用 ForgettingCurve + 添加 conflict_timeout_turns 配置 | +5 |
| **合计** | | | **约 440 行** |

---

## 五、改进后的数据流

```
用户消息 → _process_message()
  │
  ├─ [消息前] 检查是否有待决记忆冲突 ← 解析用户决策 ← 执行决策 ← 写入 MEMORY.md  [新增]
  │
  ├─ [消息前] Consolidator.maybe_consolidate_by_tokens()
  │           └─ LLM 摘要 → history.jsonl (source="consolidation")
  │
  ├─ [消息前] PaperKnowledgeIndex.search(query)        ✅ 论文知识
  ├─ [消息前] ActiveMemoryRetriever.retrieve(query)    ✅ 活跃记忆 (现已可用)
  ├─ [消息前] ResearchContextTracker.load_from_disk()  ✅ 跨会话恢复 (新增)
  │
  ├─ [LLM 调用] 系统提示包含完整记忆上下文
  │
  ├─ [消息后] _save_turn() → session messages
  ├─ [消息后] ResearchContextTracker.update() + save_to_disk()  ✅ 持久化 (新增)
  │
  └─ [后台] MemoryKeeper.process_new_consolidations()  ✅ 记忆写入 (新增)
            ├─ 读取新 consolidation 条目
            ├─ LLM 提取事实 → MemoryScorer.score()     ✅ 评分 (已接入)
            ├─ MemoryConflictDetector.detect()          ✅ 冲突检测 (已接入)
            ├─ 无冲突 → 直接写入 MEMORY.md              ✅
            ├─ 有冲突 → 写入 .pending_conflicts.json    [新增]
            │         → MessageBus 发送冲突提示给用户    [新增]
            ├─ ForgettingCurve 清理衰减条目              ✅ 遗忘曲线 (已启用)
            └─ compact_history()                        ✅ 压缩 (已修复)
```

---

## 六、关键设计决策

### 6.1 为什么不用 Dream？

Dream 的两阶段设计（Phase1 LLM 分析 + Phase2 AgentRunner 带文件工具编辑）功能强大但成本高昂。对于论文研读场景：

- **不需要 Phase2 的 AgentRunner**：论文模式下不需要创建技能、不需要编辑 SOUL.md/USER.md
- **不需要 2 小时间隔**：Consolidator 在每轮对话后产生摘要，MemoryKeeper 应在摘要产生后及时处理
- **MemoryKeeper 成本预估**：每次运行约 500 tokens 输入 + 100 tokens 输出，每月约 $0.10（远低于 Dream 的 $2-5/月）

### 6.2 MemoryKeeper 触发策略

采用"对话轮次后异步触发"而非 Dream 的"cron 定时触发"：

- **优点**：及时性更好，memory_keeper_cursor 推进更快，不会积压
- **防护**：通过 cursor 机制保证幂等性，同一批 consolidation 只处理一次
- **去重**：MemoryKeeper 只处理 `source="consolidation"` 的条目，跳过 `raw_archive`

### 6.3 学术模式的冲突处理

论文研读场景下，不同论文可能有矛盾的结论（如方法A优于方法B vs 方法B优于方法A）。`conflict_academic_mode=True` 时冲突检测返回 `keep_both` 而非删除一方，保留学术讨论的完整性。

### 6.4 用户驱动的冲突决策（核心交互设计）

**为什么需要用户决策**：记忆冲突涉及对用户偏好和知识的判断，LLM 无法准确猜测用户的真实意图。例如：

- 旧记忆: "我主要关注NLP领域" vs 新信息: "我对CV也很感兴趣" — 是兴趣转移（应替换）还是兴趣扩展（应都保留）？只有用户知道
- 旧记忆: "Transformer在NLP中表现最优" vs 新信息: "CNN在某些任务中超越Transformer" — 是知识更新（应替换）还是补充（应都保留）？需要用户判断

**决策粒度**：用户可以对每个冲突单独决策，也可以用一句话全局决策（"都保留"）。

**不阻塞正常对话**：冲突提示在对话回复中附带呈现，用户可以选择立即决策，也可以先继续正常对话，冲突会在后续轮次中再次提示。3轮后自动按安全策略处理。

**实现要点**：
- `duplicate`（重复）和 `unrelated`（无关）不触发用户决策，直接自动处理
- 仅 `update` 和 `contradiction` 需要用户介入
- 冲突存储在 `memory/.pending_conflicts.json`，跨会话持久化
- 用户决策通过自然语言解析，不要求精确格式
- 如果用户重新上传了同一篇论文，旧论文的相关冲突自动失效

---

## 七、验证方案

### 7.1 单元测试

新建 `tests/test_memory_keeper.py`：

| 测试用例 | 验证点 |
|----------|--------|
| `test_extract_facts_from_consolidation` | LLM 从摘要文本中正确提取事实 |
| `test_scorer_integration` | MemoryScorer 被调用并返回合理分数 |
| `test_conflict_detection_duplicate` | 重复记忆被正确忽略 |
| `test_conflict_detection_contradiction_academic` | 学术模式下矛盾结论保留双方 |
| `test_cursor_advancement` | 处理后 cursor 正确推进 |
| `test_forgetting_curve_cleanup` | 衰减记忆被正确移除 |
| `test_compaction_on_overflow` | history.jsonl 超过上限时自动裁剪 |

### 7.2 集成测试（端到端）

**场景 1：基本记忆持久化**
1. 启动 bot，上传一篇论文
2. 对话中说："我主要关注 transformer 架构的注意力机制改进"
3. 检查 `MEMORY.md` — 应新增一条 `imp:7` 左右的偏好条目
4. 检查 `memory/.memory_keeper_cursor` — 应大于 0

**场景 2：跨会话记忆检索**
1. 完成场景 1 后重启 bot
2. 开启新对话，问："我之前关注什么研究方向？"
3. ActiveMemoryRetriever 应从 MEMORY.md 检索到之前的偏好
4. 检查系统提示中是否包含 `## Active Memory (相关长期记忆)` 块

**场景 3：PaperKnowledgeIndex 增量更新**
1. 上传一篇新论文，等待分析完成
2. 无需重启，直接在对话中搜索论文关键词
3. PaperKnowledgeIndex 应返回新论文的分析结果

**场景 4：非论文模式回归**
1. 设置 `paper_memory_mode=false`
2. 验证 Dream cron 任务正常运行
3. 验证 MEMORY.md 通过 Dream 更新

---

## 八、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| MemoryKeeper LLM 调用增加成本 | 低 | 低 | 仅在 Consolidator 产生新摘要时触发（约每5-10轮对话一次），单次成本极低 |
| ConflictDetector 误判 | 中 | 低 | 仅 `duplicate` 自动处理，`update`/`contradiction` 交给用户决策，由用户最终把关 |
| 用户忽略冲突提示导致积压 | 中 | 中 | 3轮超时自动处理；冲突不会阻塞正常对话；每次新对话开始时检查过期冲突 |
| 自然语言决策解析失败 | 中 | 低 | 支持格式化回复作为可靠后备；解析失败时向用户确认"请问你是指...？" |
| PaperKnowledgeIndex 增量索引在崩溃后不一致 | 低 | 中 | 启动时检查 index.json mtime，如不一致则全量重建 |
| embedder 不可用导致 ActiveMemoryRetriever 失败 | 中 | 低 | 优雅降级：返回空列表，不影响正常对话流程 |
| MemoryKeeper cursor 文件损坏 | 低 | 中 | 损坏时退回到 `max(cursor)` 扫描，不会丢失数据 |
| 冲突文件 `.pending_conflicts.json` 损坏 | 低 | 低 | JSON 解析失败时创建备份并重置，最多丢失正在等待决策的冲突 |

---

## 九、后续可扩展方向（非本阶段）

1. **Project Memory 跨项目共享**：`MemoryStore.load_project_memory()` 已有骨架，可接入 MemoryKeeper 实现项目间知识共享
2. **记忆可视化**：前端增加"记忆面板"显示 MEMORY.md 条目及衰减曲线
3. **PaperKnowledgeIndex 关系图增强**：用 LLM 替代纯关键词匹配发现论文间深层次关系
4. **主动记忆提醒**：Agent 在对话开始时主动告知用户"根据之前的讨论，你可能对这个方向感兴趣"
