"""MemoryKeeper — 轻量级记忆写入器，替代 Dream 在论文模式下的角色。

单阶段 LLM 提取 + MemoryScorer 评分 + MemoryConflictDetector 冲突检测
+ 用户决策 pending 队列 + MEMORY.md 写入。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel

from silver_research_bot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from silver_research_bot.agent.memory import MemoryEntry, MemoryStore
    from silver_research_bot.agent.memory_conflict import MemoryConflictDetector
    from silver_research_bot.agent.memory_forgetting import ForgettingCurve
    from silver_research_bot.agent.memory_scorer import MemoryScorer
    from silver_research_bot.bus.queue import MessageBus
    from silver_research_bot.providers.base import LLMProvider


# ---------------------------------------------------------------------------
# PendingConflict — 待用户决策的记忆冲突
# ---------------------------------------------------------------------------

class PendingConflict(BaseModel):
    """一条等待用户决策的记忆冲突。"""

    conflict_id: str
    conflict_type: str  # "update" | "contradiction"
    old_uid: str
    old_text: str
    old_importance: int
    new_text: str
    new_category: str = "project_fact"
    new_importance: int = 5
    status: str = "pending"  # "pending" | "resolved" | "expired"
    created_at: str = ""  # ISO timestamp
    turn_count: int = 0  # 已等待的对话轮数

    @staticmethod
    def generate_id() -> str:
        import hashlib
        raw = f"pc_{time.time()}_{id(object)}"
        return f"pc_{hashlib.sha256(raw.encode()).hexdigest()[:8]}"


# ---------------------------------------------------------------------------
# MemoryKeeper
# ---------------------------------------------------------------------------

class MemoryKeeper:
    """轻量级记忆写入器，替代 Dream 在论文模式下的角色。

    处理 Consolidator 产生的摘要 → 提取事实 → 评分 → 冲突检测
    → 无冲突直接写入 MEMORY.md / 有冲突提交用户决策。

    用法::

        keeper = MemoryKeeper(store, provider, model, scorer, conflict_detector)
        written, pending = await keeper.process_new_consolidations()
        if pending:
            await keeper.send_conflict_prompt()
    """

    _CONFLICT_TIMEOUT_TURNS = 3
    _MAX_EXTRACTION_CHARS = 4000

    def __init__(
        self,
        store: "MemoryStore",
        provider: "LLMProvider",
        model: str,
        scorer: "MemoryScorer | None" = None,
        conflict_detector: "MemoryConflictDetector | None" = None,
        bus: "MessageBus | None" = None,
        forgetting: "ForgettingCurve | None" = None,
        batch_size: int = 5,
        conflict_timeout_turns: int = 3,
        enabled: bool = True,
    ):
        self.enabled = enabled
        self.store = store
        self.provider = provider
        self.model = model
        self.scorer = scorer
        self.conflict_detector = conflict_detector
        self.bus = bus
        self.forgetting = forgetting
        self.batch_size = batch_size
        self.conflict_timeout_turns = conflict_timeout_turns

        # Cursor file (独立于 Dream 的 .dream_cursor)
        self._cursor_file = store.memory_dir / ".memory_keeper_cursor"
        self._pending_file = store.memory_dir / ".pending_conflicts.json"

    # -- cursor management ---------------------------------------------------

    def _get_last_cursor(self) -> int:
        if self._cursor_file.exists():
            try:
                return int(self._cursor_file.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pass
        return 0

    def _set_last_cursor(self, cursor: int) -> None:
        self._cursor_file.write_text(str(cursor), encoding="utf-8")

    # -- pending conflicts I/O -----------------------------------------------

    def _load_pending_conflicts(self) -> list[dict[str, Any]]:
        """加载待决冲突列表。"""
        if not self._pending_file.exists():
            return []
        try:
            data = json.loads(self._pending_file.read_text(encoding="utf-8"))
            return data.get("conflicts", [])
        except (json.JSONDecodeError, OSError):
            logger.warning("MemoryKeeper: failed to load pending conflicts, resetting")
            self._pending_file.unlink(missing_ok=True)
            return []

    def _save_pending_conflicts(self, conflicts: list[dict[str, Any]]) -> None:
        """保存待决冲突列表。"""
        payload = {
            "updated_at": datetime.now().isoformat(),
            "timeout_turns": self.conflict_timeout_turns,
            "conflicts": conflicts,
        }
        self._pending_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def has_pending_conflicts(self) -> bool:
        """检查是否有未解决的记忆冲突。"""
        conflicts = self._load_pending_conflicts()
        return any(c.get("status") == "pending" for c in conflicts)

    def any_conflicts_expired(self) -> bool:
        """检查是否有超时未处理的冲突。"""
        conflicts = self._load_pending_conflicts()
        return any(
            c.get("status") == "pending"
            and c.get("turn_count", 0) >= self.conflict_timeout_turns
            for c in conflicts
        )

    # -- main processing -----------------------------------------------------

    async def process_new_consolidations(self) -> tuple[int, int]:
        """处理新的 consolidation 摘要。

        Returns:
            (written_count, pending_count): 直接写入 MEMORY.md 的条目数和送入待决队列的冲突数
        """
        if not self.enabled:
            return 0, 0

        last_cursor = self._get_last_cursor()
        entries = self.store.read_unprocessed_history(since_cursor=last_cursor)

        # Filter: only process consolidation entries
        consolidation_entries = [
            e for e in entries
            if e.get("source") == "consolidation"
            and not str(e.get("content", "")).startswith("[RAW]")
        ]
        if not consolidation_entries:
            return 0, 0

        batch = consolidation_entries[:self.batch_size]
        logger.info(
            "MemoryKeeper: processing {} consolidation entries (cursor {}→{})",
            len(batch), last_cursor, batch[-1]["cursor"],
        )

        # 1) ForgettingCurve: clean up decayed entries first
        if self.forgetting and self.forgetting.enabled:
            self._cleanup_decayed()

        # 2) Build summaries text
        summaries_text = "\n\n---\n\n".join(
            f"[{e.get('timestamp', '?')}] {e.get('content', '')}"
            for e in batch
        )

        # 3) Get existing memories for dedup
        existing_entries = self.store.parse_memory_entries()
        existing_text = "\n".join(
            f"- [{e.uid[:8]}] imp:{e.importance} {e.text[:200]}"
            for e in existing_entries
        ) if existing_entries else "(empty)"

        # 4) LLM extraction
        try:
            extracted_facts = await self._extract_via_llm(summaries_text, existing_text)
        except Exception:
            logger.exception("MemoryKeeper: LLM extraction failed")
            new_cursor = batch[-1]["cursor"]
            self._set_last_cursor(new_cursor)
            return 0, 0

        if not extracted_facts:
            new_cursor = batch[-1]["cursor"]
            self._set_last_cursor(new_cursor)
            return 0, 0

        # 5) Score, detect conflicts, classify
        written = 0
        pending_conflicts = self._load_pending_conflicts()

        for fact in extracted_facts:
            fact_text = fact.get("fact", "").strip()
            if not fact_text:
                continue
            category = fact.get("category", "project_fact")
            importance_hint = int(fact.get("importance_hint", 5))

            # Score importance
            importance = importance_hint
            if self.scorer:
                try:
                    importance = await self.scorer.score(fact_text)
                except Exception:
                    logger.debug("MemoryKeeper: scoring failed, using hint")

            # Skip low-importance facts
            if importance < 3:
                continue

            # Conflict detection
            conflicts = []
            if self.conflict_detector and existing_entries:
                try:
                    conflicts = await self.conflict_detector.detect(
                        fact_text, existing_entries
                    )
                except Exception:
                    logger.debug("MemoryKeeper: conflict detection failed")

            has_pending_conflict = False
            if conflicts:
                for conflict in conflicts:
                    if conflict.conflict_type == "duplicate":
                        logger.debug(
                            "MemoryKeeper: auto-ignoring duplicate of {}",
                            conflict.existing_uid[:8],
                        )
                        continue
                    elif conflict.conflict_type in ("update", "contradiction"):
                        # Find old entry importance
                        old_imp = 5
                        for e in existing_entries:
                            if e.uid == conflict.existing_uid:
                                old_imp = e.importance
                                break

                        pc = PendingConflict(
                            conflict_id=PendingConflict.generate_id(),
                            conflict_type=conflict.conflict_type,
                            old_uid=conflict.existing_uid,
                            old_text=conflict.existing_text[:300],
                            old_importance=old_imp,
                            new_text=fact_text,
                            new_category=category,
                            new_importance=importance,
                            created_at=datetime.now().isoformat(),
                        )
                        pending_conflicts.append(pc.model_dump())
                        has_pending_conflict = True
                        logger.info(
                            "MemoryKeeper: conflict '{}' → pending user decision",
                            pc.conflict_id,
                        )
                    # unrelated: fall through to direct write

            # Direct write: no conflicts or all were duplicate/unrelated
            if not has_pending_conflict:
                self._write_fact(fact_text, importance, category)
                written += 1
                # Refresh existing entries
                existing_entries = self.store.parse_memory_entries()

        # 6) Save pending conflicts
        if pending_conflicts:
            self._save_pending_conflicts(pending_conflicts)

        # 7) Advance cursor
        new_cursor = batch[-1]["cursor"]
        self._set_last_cursor(new_cursor)

        # 8) Compact history
        self.store.compact_history()

        # 9) Git auto-commit
        if written and self.store.git.is_initialized():
            ts = batch[-1].get("timestamp", datetime.now().isoformat())
            self.store.git.auto_commit(
                f"memory_keeper: {ts}, {written} new memories"
            )

        pending_count = len(
            [c for c in pending_conflicts if c.get("status") == "pending"]
        )
        logger.info(
            "MemoryKeeper: {} written, {} pending conflicts, cursor → {}",
            written, pending_count, new_cursor,
        )
        return written, pending_count

    # -- conflict resolution -------------------------------------------------

    async def resolve_conflicts(self, decisions: dict[str, str]) -> int:
        """根据用户决策解决冲突。

        Args:
            decisions: {"pc_xxx": "keep_old|keep_new|keep_both|discard_both", ...}

        Returns:
            实际写入 MEMORY.md 的条目数
        """
        conflicts = self._load_pending_conflicts()
        if not conflicts:
            return 0

        written = 0
        for c in conflicts:
            if c.get("status") != "pending":
                continue
            cid = c.get("conflict_id", "")
            decision = decisions.get(cid, "")

            if not decision:
                continue

            c["status"] = "resolved"
            c["decision"] = decision

            if decision == "keep_both":
                self._write_fact(
                    c["new_text"], c.get("new_importance", 5),
                    c.get("new_category", "project_fact"),
                )
                written += 1
            elif decision == "keep_new":
                self.store.update_entry(
                    c["old_uid"], c["new_text"], c.get("new_importance")
                )
                written += 1
            elif decision == "keep_old":
                self.store.touch_entry(c["old_uid"])
            elif decision == "discard_both":
                self.store.delete_entry(c["old_uid"])
            else:
                logger.warning(
                    "MemoryKeeper: unknown decision '{}' for {}", decision, cid
                )

        self._save_pending_conflicts(conflicts)

        if written and self.store.git.is_initialized():
            self.store.git.auto_commit(
                f"memory_keeper: resolved {len(decisions)} conflicts, {written} written"
            )

        return written

    def auto_resolve_expired(self) -> list[str]:
        """自动解决超时的冲突。

        Returns:
            已解决的 conflict_id 列表
        """
        conflicts = self._load_pending_conflicts()
        resolved_ids: list[str] = []

        for c in conflicts:
            if c.get("status") != "pending":
                continue
            if c.get("turn_count", 0) < self.conflict_timeout_turns:
                continue

            c["status"] = "expired"
            c["decision"] = "keep_both"
            resolved_ids.append(c["conflict_id"])

            self._write_fact(
                c["new_text"], c.get("new_importance", 5),
                c.get("new_category", "project_fact"),
            )

        if resolved_ids:
            self._save_pending_conflicts(conflicts)
            logger.info(
                "MemoryKeeper: auto-resolved {} expired conflicts (keep_both)",
                len(resolved_ids),
            )

        return resolved_ids

    def bump_pending_turn_counts(self) -> None:
        """增加所有待决冲突的等待轮数。每次对话轮次开始时调用。"""
        conflicts = self._load_pending_conflicts()
        updated = False
        for c in conflicts:
            if c.get("status") == "pending":
                c["turn_count"] = c.get("turn_count", 0) + 1
                updated = True
        if updated:
            self._save_pending_conflicts(conflicts)

    async def send_conflict_prompt(self) -> bool:
        """通过消息总线向用户发送冲突决策提示。返回是否发送了提示。"""
        conflicts = self._load_pending_conflicts()
        pending = [c for c in conflicts if c.get("status") == "pending"]
        if not pending:
            return False

        lines = ["⚠️ **记忆冲突 — 请选择处理方式:**\n"]
        for i, c in enumerate(pending, 1):
            ctype_label = {
                "update": "更新",
                "contradiction": "矛盾",
            }.get(c.get("conflict_type", ""), c.get("conflict_type", ""))
            lines.append(
                f"**冲突 #{c['conflict_id']}** ({ctype_label}):\n"
                f"  旧记忆: \"{c['old_text'][:200]}\" (重要性: {c['old_importance']})\n"
                f"  新信息: \"{c['new_text'][:200]}\" (重要性: {c.get('new_importance', 5)})\n"
                f"  [1.保留旧] [2.保留新] [3.都保留] [4.都丢弃]"
            )
        lines.append(
            "\n回复格式: `冲突#id: 数字` 或直接说 \"都保留\" / \"保留旧的\" / \"都不要\""
        )

        prompt_text = "\n".join(lines)

        if self.bus:
            from silver_research_bot.bus.events import OutboundMessage
            await self.bus.publish_outbound(OutboundMessage(
                channel="cli",
                chat_id="direct",
                content=prompt_text,
                metadata={"_memory_conflict_prompt": True},
            ))
            logger.info(
                "MemoryKeeper: sent conflict prompt ({} pending)", len(pending)
            )
            return True
        else:
            logger.warning(
                "MemoryKeeper: {} pending conflicts but no bus configured",
                len(pending),
            )
            return False

    # -- internal helpers ----------------------------------------------------

    async def _extract_via_llm(
        self, summaries_text: str, existing_memories: str
    ) -> list[dict[str, Any]]:
        """调用 LLM 从摘要中提取事实。"""
        prompt = render_template(
            "agent/memory_keeper_extract.md",
            existing_memories=existing_memories[:2000],
            summaries=summaries_text[:self._MAX_EXTRACTION_CHARS],
            strip=True,
        )

        response = await self.provider.chat_with_retry(
            model=self.model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "请从以上对话摘要中提取值得长期记忆的事实。"},
            ],
            tools=None,
            tool_choice=None,
            temperature=0.0,
            max_tokens=1024,
        )

        content = response.content or ""
        return self._parse_facts(content)

    @staticmethod
    def _parse_facts(content: str) -> list[dict[str, Any]]:
        """解析 LLM 输出的 JSONL 格式事实列表。"""
        facts: list[dict[str, Any]] = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and "fact" in obj:
                    facts.append(obj)
                    continue
            except json.JSONDecodeError:
                pass
            # Try to find JSON objects in the line
            m = re.search(r'\{.*"fact".*\}', line)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    if isinstance(obj, dict) and "fact" in obj:
                        facts.append(obj)
                except json.JSONDecodeError:
                    continue
        return facts

    def _write_fact(
        self, text: str, importance: int, category: str = "project_fact"
    ) -> str:
        """将一条事实写入 MEMORY.md。返回新条目的 uid。"""
        import hashlib
        ts = datetime.now().isoformat()
        uid = f"mem_{hashlib.sha256(f'{text}{ts}'.encode()).hexdigest()[:12]}"

        tag = f"<!-- uid:{uid} imp:{importance} ts:{ts} acc:{ts} -->"
        category_prefix = {
            "preference": "💡 偏好",
            "paper_insight": "📄 论文",
            "project_fact": "📋 项目",
            "user_info": "👤 用户",
        }.get(category, "📋 项目")

        new_line = f"{category_prefix}: {text} {tag}"

        current = self.store.read_memory()
        if current.strip():
            self.store.write_memory(current.rstrip() + "\n" + new_line + "\n")
        else:
            self.store.write_memory(new_line + "\n")

        logger.info(
            "MemoryKeeper: wrote '{}' (imp={}, cat={})",
            uid[:8], importance, category,
        )
        return uid

    def _cleanup_decayed(self) -> int:
        """清理衰减记忆条目。返回移除的条目数。"""
        if not self.forgetting or not self.forgetting.enabled:
            return 0
        entries = self.store.parse_memory_entries()
        removed = 0
        for entry in entries:
            if self.forgetting.should_remove(entry):
                self.store.delete_entry(entry.uid)
                removed += 1
                logger.debug("MemoryKeeper: removed decayed memory {}", entry.uid[:8])
        if removed:
            logger.info("MemoryKeeper: cleaned up {} decayed memories", removed)
        return removed


# ---------------------------------------------------------------------------
# 用户决策的自然语言解析（供 loop.py 使用）
# ---------------------------------------------------------------------------

_SINGLE_DECISION_RE = re.compile(
    r"(?:冲突\s*#?\s*)?(pc_[a-z0-9]+)\s*[:：]\s*(\d|keep_old|keep_new|keep_both|discard_both)",
    re.IGNORECASE,
)

_DECISION_MAP: dict[str, str] = {
    "1": "keep_old",
    "2": "keep_new",
    "3": "keep_both",
    "4": "discard_both",
}

_GLOBAL_KEYWORDS: dict[str, str] = {
    # Chinese
    "都保留": "keep_both", "保留所有": "keep_both", "全部保留": "keep_both",
    "保留旧": "keep_old", "保留旧的": "keep_old", "保留原来": "keep_old", "用旧的": "keep_old",
    "保留新": "keep_new", "保留新的": "keep_new", "用新的": "keep_new", "替换": "keep_new",
    "都丢": "discard_both", "都不保留": "discard_both", "不要": "discard_both",
    "丢弃": "discard_both", "全部丢弃": "discard_both",
    # English
    "keep all": "keep_both",
    "keep old": "keep_old",
    "keep new": "keep_new", "use new": "keep_new",
    "discard all": "discard_both",
}


def parse_conflict_decisions(user_text: str) -> dict[str, str] | None:
    """从用户消息中解析冲突决策。

    支持两种格式:
    - 单冲突: "冲突#pc_a1b2c3: 3" 或 "pc_a1b2c3: keep_both"
    - 全局: "都保留" / "保留旧的" / "用新的替换" / "都不要"

    Returns:
        {"pc_xxx": "keep_old|keep_new|keep_both|discard_both", ...}
        如果消息中不包含任何冲突决策，返回 None
    """
    decisions: dict[str, str] = {}

    # 1) Try single-conflict format first
    for m in _SINGLE_DECISION_RE.finditer(user_text):
        cid = m.group(1)
        raw = m.group(2).strip().lower()
        decision = _DECISION_MAP.get(raw, raw)
        if decision in ("keep_old", "keep_new", "keep_both", "discard_both"):
            decisions[cid] = decision

    if decisions:
        return decisions

    # 2) Try global keywords
    text_lower = user_text.lower().strip()
    for keyword, decision in _GLOBAL_KEYWORDS.items():
        if keyword in text_lower:
            return {"__global__": decision}

    return None


def expand_global_decisions(
    decisions: dict[str, str], pending_conflicts: list[dict[str, Any]]
) -> dict[str, str]:
    """将全局决策 (__global__) 展开为每个待决冲突的独立决策。"""
    global_decision = decisions.get("__global__")
    if not global_decision:
        return decisions

    expanded: dict[str, str] = {}
    for c in pending_conflicts:
        if c.get("status") == "pending":
            expanded[c["conflict_id"]] = global_decision
    return expanded
