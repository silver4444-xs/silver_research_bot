"""Ebbinghaus 遗忘曲线 — 基于时间的记忆衰减 + 巩固触发"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from silver_research_bot.agent.memory import MemoryEntry


@dataclass
class ConsolidationReminder:
    entry_uid: str
    entry_text: str
    retention: float  # 0.0 = 完全遗忘, 1.0 = 完整记忆
    days_since_access: float
    recommended_action: str  # "review" | "refresh" | "remove"


class ForgettingCurve:
    r"""Ebbinghaus 遗忘曲线: R = e^(-t/S), S = halflife / ln(2)."""

    def __init__(self, halflife_days: float = 7.0, importance_threshold: float = 3.0):
        self.halflife = halflife_days
        self._S = halflife_days / math.log(2)
        self.importance_threshold = importance_threshold

    def retention(self, days_since_last_access: float) -> float:
        if days_since_last_access <= 0:
            return 1.0
        return math.exp(-days_since_last_access / self._S)

    def effective_importance(self, entry: "MemoryEntry") -> float:
        r = self.retention(self._days_since(entry.last_accessed))
        return entry.importance * r

    def needs_consolidation(self, entry: "MemoryEntry") -> bool:
        r = self.retention(self._days_since(entry.last_accessed))
        return r < 0.3 and entry.importance >= self.importance_threshold

    def should_remove(self, entry: "MemoryEntry") -> bool:
        r = self.retention(self._days_since(entry.last_accessed))
        return r < 0.1 and entry.importance < self.importance_threshold

    def check_all(self, entries: list["MemoryEntry"]) -> list[ConsolidationReminder]:
        reminders = []
        for entry in entries:
            days = self._days_since(entry.last_accessed)
            r = self.retention(days)
            if r < 0.1 and entry.importance < self.importance_threshold:
                reminders.append(ConsolidationReminder(
                    entry_uid=entry.uid, entry_text=entry.text[:200],
                    retention=r, days_since_access=days,
                    recommended_action="remove",
                ))
            elif r < 0.3 and entry.importance >= self.importance_threshold:
                reminders.append(ConsolidationReminder(
                    entry_uid=entry.uid, entry_text=entry.text[:200],
                    retention=r, days_since_access=days,
                    recommended_action="review",
                ))
            elif r < 0.6 and entry.importance >= 7:
                reminders.append(ConsolidationReminder(
                    entry_uid=entry.uid, entry_text=entry.text[:200],
                    retention=r, days_since_access=days,
                    recommended_action="refresh",
                ))
        reminders.sort(key=lambda r: (r.retention, -1 / max(r.days_since_access, 0.01)))
        return reminders

    @staticmethod
    def _days_since(iso_timestamp: str) -> float:
        try:
            dt = datetime.fromisoformat(iso_timestamp)
            now = datetime.now(timezone.utc)
            return (now - dt.replace(tzinfo=timezone.utc)).total_seconds() / 86400.0
        except Exception:
            return 0.0
