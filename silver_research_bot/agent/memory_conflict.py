"""记忆冲突检测 — 语义相似度 + 矛盾解决，确保记忆一致性"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider
    from silver_research_bot.agent.memory import MemoryEntry

CONFLICT_PROMPT = """比较以下两条信息的关系（仅输出一个词）：
- contradiction: 两者矛盾，不可调和
- duplicate: 语义等价，重复内容
- update: 新信息是旧信息的更新补充
- unrelated: 完全不同话题

旧信息: {old_text}
新信息: {new_text}"""


@dataclass
class Conflict:
    existing_uid: str
    existing_text: str
    new_text: str
    conflict_type: str  # "contradiction" | "duplicate" | "update" | "unrelated"
    resolution: str = ""  # "replace" | "merge" | "keep_new" | "keep_old" | "ignore"


@dataclass
class MemoryAction:
    action: str  # "replace" | "merge" | "keep_new" | "keep_old" | "ignore"
    target_uid: str | None = None
    new_text: str | None = None
    reason: str = ""


class MemoryConflictDetector:
    """新记忆写入时检测与已有记忆的语义冲突。"""

    def __init__(self, provider: "LLMProvider", model: str | None = None):
        self.provider = provider
        self.model = model

    async def detect(
        self, new_text: str, existing: list["MemoryEntry"]
    ) -> list[Conflict]:
        if not existing:
            return []
        top = sorted(existing, key=lambda e: e.importance, reverse=True)[:5]
        tasks = [self._compare(entry, new_text) for entry in top]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        conflicts = []
        for r in results:
            if isinstance(r, Conflict) and r.conflict_type != "unrelated":
                conflicts.append(r)
        return conflicts

    async def _compare(self, entry: "MemoryEntry", new_text: str) -> Conflict:
        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[{"role": "user", "content": CONFLICT_PROMPT.format(
                    old_text=entry.text[:500], new_text=new_text[:500],
                )}],
                tools=None,
                max_tokens=20,
                temperature=0.0,
            )
            ctype = (response.content or "").strip().lower()
        except Exception:
            ctype = "unrelated"

        valid = ("contradiction", "duplicate", "update", "unrelated")
        ctype = ctype if ctype in valid else "unrelated"
        conflict = Conflict(
            existing_uid=entry.uid,
            existing_text=entry.text[:200],
            new_text=new_text[:200],
            conflict_type=ctype,
        )
        if conflict.conflict_type != "unrelated":
            conflict.resolution = self._resolve(conflict, entry.importance)
        return conflict

    def _resolve(self, conflict: Conflict, existing_importance: int) -> str:
        if conflict.conflict_type == "duplicate":
            return "ignore"
        if conflict.conflict_type == "update":
            return "replace"
        if conflict.conflict_type == "contradiction":
            return "keep_old" if existing_importance >= 8 else "replace"
        return "ignore"

    @staticmethod
    def to_actions(conflicts: list[Conflict]) -> list[MemoryAction]:
        return [
            MemoryAction(
                action=c.resolution or "ignore",
                target_uid=c.existing_uid,
                new_text=c.new_text,
                reason=f"{c.conflict_type}: new info {c.resolution} existing",
            )
            for c in conflicts
        ]
