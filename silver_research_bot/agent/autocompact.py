"""自动压缩：主动压缩空闲会话，以减少令牌开销和延迟。"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger
from silver_research_bot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from silver_research_bot.agent.memory import Consolidator


class AutoCompact:
    _RECENT_SUFFIX_MESSAGES = 8
    '在压缩时，会保留会话末尾的若干条消息（最近的消息）不压缩，供下次对话时直接使用。这里默认保留最近 8 条消息'

    def __init__(self, sessions: SessionManager, consolidator: Consolidator,
                 session_ttl_minutes: int = 0):
        self.sessions = sessions
        '会话管理器，用于获取、保存会话'
        self.consolidator = consolidator
        '实际执行摘要生成的组件（负责调用 LLM 将消息列表转换为摘要文本）'
        self._ttl = session_ttl_minutes
        '会话空闲存活时间（分钟），若 <= 0 表示永不自动过期'
        self._archiving: set[str] = set()
        '集合，记录正在被归档的会话键，防止并发重复归档'
        self._summaries: dict[str, tuple[str, datetime]] = {}
        '内存中的摘要缓存，键为会话键，值为 (摘要文本, 上次活跃时间)'

    def _is_expired(self, ts: datetime | str | None,
                    now: datetime | None = None) -> bool:
        """判断会话的最后更新时间 ts 是否已经超过 _ttl 分钟"""
        if self._ttl <= 0 or not ts:
            return False
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return ((now or datetime.now()) - ts).total_seconds() >= self._ttl * 60

    @staticmethod
    def _format_summary(text: str, last_active: datetime) -> str:
        """将摘要文本格式化为注入运行时上下文的字符串，包含空闲分钟数，以便 Agent 了解会话已经暂停了多久"""
        idle_min = int((datetime.now() - last_active).total_seconds() / 60)
        return f"Inactive for {idle_min} minutes.\nPrevious conversation summary: {text}"

    def _split_unconsolidated(
        self, session: Session,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """将实时会话尾部分割为可归档的前缀和保留的近期后缀。"""
        tail = list(session.messages[session.last_consolidated:])
        if not tail:
            return [], []

        probe = Session(
            key=session.key,
            messages=tail.copy(),
            created_at=session.created_at,
            updated_at=session.updated_at,
            metadata={},
            last_consolidated=0,
        )
        probe.retain_recent_legal_suffix(self._RECENT_SUFFIX_MESSAGES)
        kept = probe.messages
        cut = len(tail) - len(kept)
        return tail[:cut], kept

    def check_expired(self, schedule_background: Callable[[Coroutine], None],
                      active_session_keys: Collection[str] = ()) -> None:
        """安排对空闲会话进行归档，并跳过那些正在执行代理任务的会话。"""
        now = datetime.now()
        for info in self.sessions.list_sessions():
            key = info.get("key", "")
            if not key or key in self._archiving:
                continue
            if key in active_session_keys:
                continue
            if self._is_expired(info.get("updated_at"), now):
                self._archiving.add(key)
                schedule_background(self._archive(key))

    async def _archive(self, key: str) -> None:
        """后台归档指定会话的旧消息。将未压缩的消息切分为“可归档部分”和“保留部分”，
        对可归档部分调用 Consolidator 生成摘要，然后用保留部分替换会话中的消息列表，
        最后将摘要存入会话元数据和内存缓存"""
        try:
            '''1.强制刷新会话'''
            self.sessions.invalidate(key)
            '''2.获取会话'''
            session = self.sessions.get_or_create(key)
            '''3.切分未压缩消息'''
            archive_msgs, kept_msgs = self._split_unconsolidated(session)
            '''4.处理空会话'''
            if not archive_msgs and not kept_msgs:
                session.updated_at = datetime.now()
                self.sessions.save(session)
                return

            last_active = session.updated_at
            '''5.生成摘要'''
            summary = ""
            if archive_msgs:
                summary = await self.consolidator.archive(archive_msgs) or ""

            '''6.保存摘要'''
            if summary and summary != "(nothing)":
                self._summaries[key] = (summary, last_active)
                session.metadata["_last_summary"] = {"text": summary, "last_active": last_active.isoformat()}
            '''7.替换会话消息列表'''
            session.messages = kept_msgs
            session.last_consolidated = 0
            session.updated_at = datetime.now()
            '''8.保存会话'''
            self.sessions.save(session)
            '''9.记录日志'''
            if archive_msgs:
                logger.info(
                    "Auto-compact: archived {} (archived={}, kept={}, summary={})",
                    key,
                    len(archive_msgs),
                    len(kept_msgs),
                    bool(summary),
                )
        except Exception:
            logger.exception("Auto-compact: failed for {}", key)
        finally:
            self._archiving.discard(key)

    def prepare_session(self, session: Session, key: str) -> tuple[Session, str | None]:
        """在每次处理会话的消息之前调用，准备会话并返回可能存在的摘要字符串。
        摘要用于注入到运行时上下文中，帮助 LLM 理解之前被压缩的对话内容"""
        if key in self._archiving or self._is_expired(session.updated_at):
            logger.info("Auto-compact: reloading session {} (archiving={})", key, key in self._archiving)
            session = self.sessions.get_or_create(key)
        # Hot path: summary from in-memory dict (process hasn't restarted).
        # Also clean metadata copy so stale _last_summary never leaks to disk.
        entry = self._summaries.pop(key, None)
        if entry:
            session.metadata.pop("_last_summary", None)
            return session, self._format_summary(entry[0], entry[1])
        if "_last_summary" in session.metadata:
            meta = session.metadata.pop("_last_summary")
            self.sessions.save(session)
            return session, self._format_summary(meta["text"], datetime.fromisoformat(meta["last_active"]))
        return session, None
