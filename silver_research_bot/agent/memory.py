"""Memory 系统: 纯 I/O 文件存储, 轻量级整合器, 以及 Dream 处理器."""

from __future__ import annotations

import asyncio
import json
import re
import weakref
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

from loguru import logger

from silver_research_bot.utils.prompt_templates import render_template
from silver_research_bot.utils.helpers import ensure_dir, estimate_message_tokens, estimate_prompt_tokens_chain, strip_think

from silver_research_bot.agent.runner import AgentRunSpec, AgentRunner
from silver_research_bot.agent.tools.registry import ToolRegistry
from silver_research_bot.utils.gitstore import GitStore

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider
    from silver_research_bot.session.manager import Session, SessionManager


# ---------------------------------------------------------------------------
# MemoryStore — pure file I/O layer
# ---------------------------------------------------------------------------

class MemoryStore:
    """纯 I/O 文件存储，管理 Agent 的长期记忆和交互历史: MEMORY.md, history.jsonl, SOUL.md, USER.md."""

    _DEFAULT_MAX_HISTORY = 1000
    '历史文件（history.jsonl）默认保留的最大条目数'
    _LEGACY_ENTRY_START_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}[^\]]*)\]\s*")
    '匹配旧版 HISTORY.md 中一个条目的起始行'
    _LEGACY_TIMESTAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]\s*")
    '更严格的时间戳正则，专门匹配 [YYYY-MM-DD HH:MM] 格式（精确到分钟）'
    _LEGACY_RAW_MESSAGE_RE = re.compile(
        r"^\[\d{4}-\d{2}-\d{2}[^\]]*\]\s+[A-Z][A-Z0-9_]*(?:\s+\[tools:\s*[^\]]+\])?:"
    )
    '匹配旧版中原始消息（RAW）格式的头部'

    def __init__(self, workspace: Path, max_history_entries: int = _DEFAULT_MAX_HISTORY):
        self.workspace = workspace
        '工作目录根路径'
        self.max_history_entries = max_history_entries
        '最大历史记录条目数量'
        self.memory_dir = ensure_dir(workspace / "memory")
        '记忆存储目录（自动创建）'
        self.memory_file = self.memory_dir / "MEMORY.md"
        '核心记忆文件'
        self.history_file = self.memory_dir / "history.jsonl"
        '结构化历史记录文件'
        self.legacy_history_file = self.memory_dir / "HISTORY.md"
        '旧版历史记录文件'
        self.soul_file = workspace / "SOUL.md"
        '角色设定/灵魂文件'
        self.user_file = workspace / "USER.md"
        '用户信息文件'
        self._cursor_file = self.memory_dir / ".cursor"
        '历史记录游标文件（内部使用）'
        self._dream_cursor_file = self.memory_dir / ".dream_cursor"
        '梦境模式游标文件（内部使用）'
        self._corruption_logged = False
        '限流非整数游标警告（内部标记）'
        self._git = GitStore(workspace, tracked_files=[
            "SOUL.md", "USER.md", "memory/MEMORY.md",
        ])
        'Git 存储管理器，追踪核心配置与记忆文件'

        '迁移旧版历史'
        self._maybe_migrate_legacy_history()

    @property
    def git(self) -> GitStore:
        """Git 存储管理器，追踪核心配置与记忆文件"""
        return self._git

    # -- generic helpers -----------------------------------------------------

    @staticmethod
    def read_file(path: Path) -> str:
        """文件读写基础方法"""
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def _maybe_migrate_legacy_history(self) -> None:
        """
        从旧版 HISTORY.md 文件一次性升级至 history.jsonl 格式。

        此次迁移力求做到最好，优先考虑尽可能多地保留内容，
        而非追求完美的解析。
        """
        if not self.legacy_history_file.exists():
            return
        if self.history_file.exists() and self.history_file.stat().st_size > 0:
            return

        try:
            legacy_text = self.legacy_history_file.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            logger.exception("Failed to read legacy HISTORY.md for migration")
            return

        entries = self._parse_legacy_history(legacy_text)
        try:
            if entries:
                self._write_entries(entries)
                last_cursor = entries[-1]["cursor"]
                self._cursor_file.write_text(str(last_cursor), encoding="utf-8")
                # Default to "already processed" so upgrades do not replay the
                # user's entire historical archive into Dream on first start.
                self._dream_cursor_file.write_text(str(last_cursor), encoding="utf-8")

            backup_path = self._next_legacy_backup_path()
            self.legacy_history_file.replace(backup_path)
            logger.info(
                "Migrated legacy HISTORY.md to history.jsonl ({} entries)",
                len(entries),
            )
        except Exception:
            logger.exception("Failed to migrate legacy HISTORY.md")

    def _parse_legacy_history(self, text: str) -> list[dict[str, Any]]:
        """将旧 HISTORY.md 文本解析为结构化条目列表（每条有 cursor, timestamp, content）"""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return []

        fallback_timestamp = self._legacy_fallback_timestamp()
        entries: list[dict[str, Any]] = []
        chunks = self._split_legacy_history_chunks(normalized)

        for cursor, chunk in enumerate(chunks, start=1):
            timestamp = fallback_timestamp
            content = chunk
            match = self._LEGACY_TIMESTAMP_RE.match(chunk)
            if match:
                timestamp = match.group(1)
                remainder = chunk[match.end():].lstrip()
                if remainder:
                    content = remainder

            entries.append({
                "cursor": cursor,
                "timestamp": timestamp,
                "content": content,
            })
        return entries

    def _split_legacy_history_chunks(self, text: str) -> list[str]:
        """基于行首时间戳和特定分隔规则分块"""
        lines = text.split("\n")
        chunks: list[str] = []
        current: list[str] = []
        saw_blank_separator = False

        for line in lines:
            if saw_blank_separator and line.strip() and current:
                chunks.append("\n".join(current).strip())
                current = [line]
                saw_blank_separator = False
                continue
            if self._should_start_new_legacy_chunk(line, current):
                chunks.append("\n".join(current).strip())
                current = [line]
                saw_blank_separator = False
                continue
            current.append(line)
            saw_blank_separator = not line.strip()

        if current:
            chunks.append("\n".join(current).strip())
        return [chunk for chunk in chunks if chunk]

    def _should_start_new_legacy_chunk(self, line: str, current: list[str]) -> bool:
        """通过特定分隔规则判断是否应该分块"""
        if not current:
            return False
        if not self._LEGACY_ENTRY_START_RE.match(line):
            return False
        if self._is_raw_legacy_chunk(current) and self._LEGACY_RAW_MESSAGE_RE.match(line):
            return False
        return True

    def _is_raw_legacy_chunk(self, lines: list[str]) -> bool:
        """通过特定分隔规则判断是否是原始记忆块"""
        first_nonempty = next((line for line in lines if line.strip()), "")
        match = self._LEGACY_TIMESTAMP_RE.match(first_nonempty)
        if not match:
            return False
        return first_nonempty[match.end():].lstrip().startswith("[RAW]")

    def _legacy_fallback_timestamp(self) -> str:
        """使用文件修改时间作为 fallback 时间戳"""
        try:
            return datetime.fromtimestamp(
                self.legacy_history_file.stat().st_mtime,
            ).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return datetime.now().strftime("%Y-%m-%d %H:%M")

    def _next_legacy_backup_path(self) -> Path:
        """将旧文件重命名为 HISTORY.md.bak"""
        candidate = self.memory_dir / "HISTORY.md.bak"
        suffix = 2
        while candidate.exists():
            candidate = self.memory_dir / f"HISTORY.md.bak.{suffix}"
            suffix += 1
        return candidate

    # -- MEMORY.md (long-term facts) -----------------------------------------

    def read_memory(self) -> str:
        return self.read_file(self.memory_file)

    def write_memory(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    # -- SOUL.md -------------------------------------------------------------

    def read_soul(self) -> str:
        return self.read_file(self.soul_file)

    def write_soul(self, content: str) -> None:
        self.soul_file.write_text(content, encoding="utf-8")

    # -- USER.md -------------------------------------------------------------

    def read_user(self) -> str:
        return self.read_file(self.user_file)

    def write_user(self, content: str) -> None:
        self.user_file.write_text(content, encoding="utf-8")

    # -- context injection (used by context.py) ------------------------------

    def get_memory_context(self) -> str:
        """得到MEMORY.md中内容，用于构建上下文"""
        long_term = self.read_memory()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    # -- history.jsonl — append-only, JSONL format ---------------------------

    def append_history(self, entry: str) -> int:
        """
        追加历史条目
        -生成下一个自增 cursor（通过 _next_cursor()）
        -生成当前时间戳（格式 YYYY-MM-DD HH:MM）。
        -关键净化：调用 strip_think(entry) 去除 <think> 标签和模板泄漏字符（如未闭合的 <think 前缀）。若原内容非空但净化后为空，仍然写入空字符串（防止再次污染）。
        -将 {"cursor": ..., "timestamp": ..., "content": content} 以 JSON 格式追加到 history.jsonl（每行一个 JSON）。
        -更新 .cursor 文件为新游标值。
        -返回游标。
        """
        cursor = self._next_cursor()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        raw = entry.rstrip()
        content = strip_think(raw)
        if raw and not content:
            logger.debug(
                "history entry {} stripped to empty (likely template leak); "
                "persisting empty content to avoid re-polluting context",
                cursor,
            )
        record = {"cursor": cursor, "timestamp": ts, "content": content}
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._cursor_file.write_text(str(cursor), encoding="utf-8")
        return cursor

    @staticmethod
    def _valid_cursor(value: Any) -> int | None:
        """严格拒绝布尔值（因为 isinstance(True, int) 为真），只有真正的整数才视为有效游标"""
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    def _iter_valid_entries(self) -> Iterator[tuple[dict[str, Any], int]]:
        """对于具有整型游标的条目，返回 ``(条目, 游标)``；若数据损坏，则发出一次警告。."""
        poisoned: Any = None
        for entry in self._read_entries():
            raw = entry.get("cursor")
            if raw is None:
                continue
            cursor = self._valid_cursor(raw)
            if cursor is None:
                poisoned = raw
                continue
            yield entry, cursor
        if poisoned is not None and not self._corruption_logged:
            self._corruption_logged = True
            logger.warning(
                "history.jsonl contains a non-int cursor ({!r}); dropping it. "
                "Usually caused by an external writer; further occurrences suppressed.",
                poisoned,
            )

    def _next_cursor(self) -> int:
        """读取当前光标计数器并返回下一个值."""
        if self._cursor_file.exists():
            try:
                return int(self._cursor_file.read_text(encoding="utf-8").strip()) + 1
            except (ValueError, OSError):
                pass
        # 快速路径：若文件尾部完整，则直接采用尾部数据。否则扫描整个文件并取 ``max`` —— 即使单调
        # 不变性因外部写入操作而失效，此方法仍能保持正确性。
        last = self._read_last_entry() or {}
        cursor = self._valid_cursor(last.get("cursor"))
        if cursor is not None:
            return cursor + 1
        return max((c for _, c in self._iter_valid_entries()), default=0) + 1

    def read_unprocessed_history(self, since_cursor: int) -> list[dict[str, Any]]:
        """遍历所有有效条目，返回 cursor > since_cursor 的条目列表"""
        return [e for e, c in self._iter_valid_entries() if c > since_cursor]

    def compact_history(self) -> None:
        """压缩历史 若文件条目数超过 max_history_entries（默认 1000），只保留最后 max_history_entries 条"""
        if self.max_history_entries <= 0:
            return
        entries = self._read_entries()
        if len(entries) <= self.max_history_entries:
            return
        kept = entries[-self.max_history_entries:]
        self._write_entries(kept)

    # -- JSONL helpers -------------------------------------------------------

    def _read_entries(self) -> list[dict[str, Any]]:
        """读取history.jsonl全部条目."""
        entries: list[dict[str, Any]] = []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except FileNotFoundError:
            pass
        return entries

    def _read_last_entry(self) -> dict[str, Any] | None:
        """仅读取history.jsonl最后一条"""
        try:
            with open(self.history_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return None
                read_size = min(size, 4096)
                f.seek(size - read_size)
                data = f.read().decode("utf-8")
                lines = [l for l in data.split("\n") if l.strip()]
                if not lines:
                    return None
                return json.loads(lines[-1])
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        """覆写整个history.jsonl文件."""
        with open(self.history_file, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -- dream cursor --------------------------------------------------------

    def get_last_dream_cursor(self) -> int:
        """获得dream上次处理到的位置"""
        if self._dream_cursor_file.exists():
            try:
                return int(self._dream_cursor_file.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pass
        return 0

    def set_last_dream_cursor(self, cursor: int) -> None:
        self._dream_cursor_file.write_text(str(cursor), encoding="utf-8")

    # -- message formatting utility ------------------------------------------

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        """将消息列表（通常是某个对话轮次中的所有 assistant/user/tool 消息）格式化为类似旧版纯文本 HISTORY.md 的单块字符串，用于降级归档"""
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    def raw_archive(self, messages: list[dict]) -> None:
        """当正常的记忆合并（consolidation）失败或退化时（例如 LLM 不可用、摘要生成出错），
        作为后备方案，将原始消息列表以纯文本形式直接追加到历史文件（history.jsonl）中，而不是丢弃它们"""
        self.append_history(
            f"[RAW] {len(messages)} messages\n"
            f"{self._format_messages(messages)}"
        )
        logger.warning(
            "Memory consolidation degraded: raw-archived {} messages", len(messages)
        )



# ---------------------------------------------------------------------------
# Consolidator — 基于 Token 预算的轻量压缩
# ---------------------------------------------------------------------------


class Consolidator:
    """轻量压缩: Consolidator 负责在对话即将超出上下文窗口时，自动将最早的一批消息压缩成摘要，
    并存入 history.jsonl，同时从会话消息列表中移除它们，使后续 LLM 请求能一直保持在窗口限制内"""

    _MAX_CONSOLIDATION_ROUNDS = 5
    '最多执行的压缩轮数（每轮可能压缩一批消息）'
    _MAX_CHUNK_MESSAGES = 60  # hard cap per consolidation round
    '单次压缩处理的最大消息条数（硬上限）'
    _SAFETY_BUFFER = 1024  # extra headroom for tokenizer estimation drift
    'Token 估算的安全缓冲区，防止估算误差导致超限'

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        sessions: SessionManager,
        context_window_tokens: int,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        max_completion_tokens: int = 4096,
    ):
        self.store = store
        '存储对象,用于读写 history.jsonl（存储摘要）'
        self.provider = provider
        'LLM 服务提供者'
        self.model = model
        '用于生成摘要的模型名称（通常与主 Agent 相同，也可以不同）'
        self.sessions = sessions
        '会话管理容器'
        self.context_window_tokens = context_window_tokens
        '上下文窗口token数'
        self.max_completion_tokens = max_completion_tokens
        '最大生成token数'
        self._build_messages = build_messages
        '消息构建函数,用于构建临时消息列表进行 token 估算'
        self._get_tool_definitions = get_tool_definitions
        '获取当前工具定义，用于更准确的 token 估算'
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        '''
        会话异步锁字典（弱引用）
        目的：为每个会话键（session_key）缓存一个 asyncio.Lock 对象，用于防止同一个会话的压缩任务并发执行。
        
        为什么用弱引用：当会话被销毁（例如会话过期、SessionManager 不再持有该会话对象）时，
        与该会话关联的锁对象也应该被垃圾回收，否则会一直占用内存。使用 WeakValueDictionary 后，
        一旦外部没有其他强引用指向锁对象，该条目会自动从字典中移除。
        
        效果：self._locks 像一个自动清理的缓存，不会因为字典长期存在而导致内存无限增长。
        '''

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """返回会话对应的锁，保证同一时刻只有一个压缩任务运行"""
        return self._locks.setdefault(session_key, asyncio.Lock())

    def pick_consolidation_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """
        选择一个能清除足够多旧提示词的用户轮次边界。
        确保压缩总是以 user 消息为结束点，保证不会切断 assistant 回复与后续 user 消息的连续性
        """
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    def _cap_consolidation_boundary(
        self,
        session: Session,
        end_idx: int,
    ) -> int | None:
        """在不破坏用户轮次边界的情况下固定块大小，防止单次压缩的消息数过多"""
        start = session.last_consolidated
        if end_idx - start <= self._MAX_CHUNK_MESSAGES:
            return end_idx

        capped_end = start + self._MAX_CHUNK_MESSAGES
        for idx in range(capped_end, start, -1):
            if session.messages[idx].get("role") == "user":
                return idx
        return None

    def estimate_session_prompt_tokens(
        self,
        session: Session,
        *,
        session_summary: str | None = None,
    ) -> tuple[int, str]:
        """估算普通会话历史记录视图中的当前token大小，"""
        history = session.get_history(max_messages=0)
        channel, chat_id = (session.key.split(":", 1) if ":" in session.key else (None, None))
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
            session_summary=session_summary,
        )
        return estimate_prompt_tokens_chain(
            self.provider,
            self.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    async def archive(self, messages: list[dict]) -> str | None:
        """通过大型语言模型（LLM）对消息进行摘要，并将其追加到 history.jsonl 中。

        成功时返回摘要文本；若无内容可归档，则返回 None。
        """
        if not messages:
            return None
        try:
            formatted = MemoryStore._format_messages(messages)
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": render_template(
                            "agent/consolidator_archive.md",
                            strip=True,
                        ),
                    },
                    {"role": "user", "content": formatted},
                ],
                tools=None,
                tool_choice=None,
            )
            if response.finish_reason == "error":
                raise RuntimeError(f"LLM returned error: {response.content}")
            summary = response.content or "[no summary]"
            self.store.append_history(summary)
            return summary
        except Exception:
            logger.warning("Consolidation LLM call failed, raw-dumping to history")
            self.store.raw_archive(messages)
            return None

    async def maybe_consolidate_by_tokens(
        self,
        session: Session,
        *,
        session_summary: str | None = None,
    ) -> None:
        """循环：归档旧消息，直到提示符能安全地容纳在预算范围内。

        该预算预留了空间用于完成token和安全缓冲区，
        以确保 LLM 请求永远不会超出上下文窗口。

        流程：
        1.获取会话锁，计算安全预算：
        2.估算当前 token 用量 estimated。若 estimated < budget，直接返回（不需要压缩）。
        3.进入循环（最多 _MAX_CONSOLIDATION_ROUNDS 轮）：
            -若 estimated <= target，退出。
            -调用 pick_consolidation_boundary 获取需要移除的消息边界，计算需要移除的 token 数为 max(1, estimated - target)。
            -对边界调用 _cap_consolidation_boundary 限制 chunk 大小。
            -提取 chunk = session.messages[last_consolidated: end_idx]。
            -调用 archive(chunk) 生成摘要（可能成功或降级为 raw archive）。
            -更新 session.last_consolidated = end_idx 并保存会话。
            -重新估算 token，继续下一轮。
        4.若最后一轮产生了有效摘要，将其存入 session.metadata["_last_summary"]，供下次 AutoCompact.prepare_session 注入运行时上下文
        """
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            budget = self.context_window_tokens - self.max_completion_tokens - self._SAFETY_BUFFER
            target = budget // 2
            try:
                estimated, source = self.estimate_session_prompt_tokens(
                    session,
                    session_summary=session_summary,
                )
            except Exception:
                logger.exception("Token estimation failed for {}", session.key)
                estimated, source = 0, "error"
            if estimated <= 0:
                return
            if estimated < budget:
                unconsolidated_count = len(session.messages) - session.last_consolidated
                logger.debug(
                    "Token consolidation idle {}: {}/{} via {}, msgs={}",
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    unconsolidated_count,
                )
                return

            last_summary = None
            for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
                if estimated <= target:
                    break

                boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                if boundary is None:
                    logger.debug(
                        "Token consolidation: no safe boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    break

                end_idx = boundary[0]
                end_idx = self._cap_consolidation_boundary(session, end_idx)
                if end_idx is None:
                    logger.debug(
                        "Token consolidation: no capped boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    break

                chunk = session.messages[session.last_consolidated:end_idx]
                if not chunk:
                    break

                logger.info(
                    "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs",
                    round_num,
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    len(chunk),
                )
                summary = await self.archive(chunk)
                if summary:
                    last_summary = summary
                else:
                    break
                session.last_consolidated = end_idx
                self.sessions.save(session)

                try:
                    estimated, source = self.estimate_session_prompt_tokens(
                        session,
                        session_summary=session_summary,
                    )
                except Exception:
                    logger.exception("Token estimation failed for {}", session.key)
                    estimated, source = 0, "error"
                if estimated <= 0:
                    break

            # Persist the last summary to session metadata so it can be injected
            # into the runtime context on the next prepare_session() call, aligning
            # the summary injection strategy with AutoCompact._archive().
            if last_summary and last_summary != "(nothing)":
                session.metadata["_last_summary"] = {
                    "text": last_summary,
                    "last_active": session.updated_at.isoformat(),
                }
                self.sessions.save(session)


# ---------------------------------------------------------------------------
# Dream — 重量级的后台记忆整合器
# ---------------------------------------------------------------------------


_STALE_THRESHOLD_DAYS = 14
'判断一行记忆是否“过时”的天数阈值，用于在 _annotate_with_ages 中添加 ← Nd 标记'

class Dream:
    """两阶段内存处理器：分析 history.jsonl 文件，然后通过 AgentRunner 编辑文件。

    第一阶段生成分析摘要（普通 LLM 调用）。
    第二阶段将任务委托给 AgentRunner，并使用 read_file / edit_file 工具，以便
    LLM 能够进行有针对性的增量编辑，而非替换整个文件。
    """

    def __init__(
        self,
        store: MemoryStore,
        provider: LLMProvider,
        model: str,
        max_batch_size: int = 20,
        max_iterations: int = 10,
        max_tool_result_chars: int = 16_000,
        annotate_line_ages: bool = True,
    ):
        self.store = store
        '存储实例'
        self.provider = provider
        'LLM 服务提供者'
        self.model = model
        '使用的模型名称'
        self.max_batch_size = max_batch_size
        '每次最多处理的历史条目数'
        self.max_iterations = max_iterations
        'Phase 2 的 AgentRunner 最大迭代次数'
        self.max_tool_result_chars = max_tool_result_chars
        '工具结果截断长度'
        self.annotate_line_ages = annotate_line_ages
        '是否为记忆文件行添加时间标注（基于git-blame），默认启用，关闭则直接传入原始MEMORY.md'
        self._runner = AgentRunner(provider)
        '智能体执行器'
        self._tools = self._build_tools()
        '构建后的工具集合'

    # -- tool registry -------------------------------------------------------

    def _build_tools(self) -> ToolRegistry:
        """
        为 Dream 代理构建一个精简的工具注册表。

        -ReadFileTool：允许读取工作区任何文件，并额外允许读取内置技能目录。
        -EditFileTool：允许编辑工作区内任何文件。
        -WriteFileTool：只能写入 workspace/skills/ 目录，用于创建新技能
        """
        from silver_research_bot.agent.skills import BUILTIN_SKILLS_DIR
        from silver_research_bot.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool

        tools = ToolRegistry()
        workspace = self.store.workspace
        # Allow reading builtin skills for reference during skill creation
        extra_read = [BUILTIN_SKILLS_DIR] if BUILTIN_SKILLS_DIR.exists() else None
        tools.register(ReadFileTool(
            workspace=workspace,
            allowed_dir=workspace,
            extra_allowed_dirs=extra_read,
        ))
        tools.register(EditFileTool(workspace=workspace, allowed_dir=workspace))
        # write_file resolves relative paths from workspace root, but can only
        # write under skills/ so the prompt can safely use skills/<name>/SKILL.md.
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        tools.register(WriteFileTool(workspace=workspace, allowed_dir=skills_dir))
        return tools

    # -- skill listing --------------------------------------------------------

    def _list_existing_skills(self) -> list[str]:
        """以 'name — description' 的格式列出现有技能.用于 Phase 2 避免重复创建同名技能"""
        import re as _re

        from silver_research_bot.agent.skills import BUILTIN_SKILLS_DIR

        _DESC_RE = _re.compile(r"^description:\s*(.+)$", _re.MULTILINE | _re.IGNORECASE)
        entries: dict[str, str] = {}
        for base in (self.store.workspace / "skills", BUILTIN_SKILLS_DIR):
            if not base.exists():
                continue
            for d in base.iterdir():
                if not d.is_dir():
                    continue
                skill_md = d / "SKILL.md"
                if not skill_md.exists():
                    continue
                # Prefer workspace skills over builtin (same name)
                if d.name in entries and base == BUILTIN_SKILLS_DIR:
                    continue
                content = skill_md.read_text(encoding="utf-8")[:500]
                m = _DESC_RE.search(content)
                desc = m.group(1).strip() if m else "(no description)"
                entries[d.name] = desc
        return [f"{name} — {desc}" for name, desc in sorted(entries.items())]

    # -- main entry ----------------------------------------------------------

    def _annotate_with_ages(self, content: str) -> str:
        """在 MEMORY.md 内容中为每行添加过期时间后缀。

        每行非空内容，若其过期时间超过 ``_STALE_THRESHOLD_DAYS``，将添加类似 ``← 30d`` 的后缀，表示自上次修改以来的天数。
        若 git 不可用、注释失败，或行数与天数不匹配时，这可能发生在工作树中存在未提交的编辑时——
        此时跳过注释比标记错误的行更妥当），则返回原始内容且不作更改。
        SOUL.md 和 USER.md 绝不进行注释。
        """
        file_path = "memory/MEMORY.md"
        try:
            ages = self.store.git.line_ages(file_path)
        except Exception:
            logger.debug("line_ages failed for {}", file_path)
            return content
        if not ages:
            return content

        had_trailing = content.endswith("\n")
        lines = content.splitlines()
        # If HEAD-blob line count disagrees with the working-tree content we
        # received, ages would be assigned to the wrong lines — skip entirely
        # and feed the LLM un-annotated content rather than misleading data.
        if len(lines) != len(ages):
            logger.debug(
                "line_ages length mismatch for {} (lines={}, ages={}); skipping annotation",
                file_path, len(lines), len(ages),
            )
            return content

        annotated: list[str] = []
        for line, age in zip(lines, ages):
            if not line.strip():
                annotated.append(line)
                continue
            if age.age_days > _STALE_THRESHOLD_DAYS:
                annotated.append(f"{line}  \u2190 {age.age_days}d")
            else:
                annotated.append(line)
        result = "\n".join(annotated)
        if had_trailing:
            result += "\n"
        return result

    async def run(self) -> bool:
        """
        处理未处理的历史记录条目。如果处理成功，则返回 True
        步骤：
        1.获取上次处理游标 last_cursor，读取未处理的历史记录。
        2.若无新记录，返回 False。
        3.取前 max_batch_size 条记录，构建 history_text。
        4.读取当前 MEMORY.md、SOUL.md、USER.md 内容，对 MEMORY.md 进行年龄标注（如果启用）。
        5.Phase 1：用系统提示模板 agent/dream_phase1.md 分析 history_text + 文件内容，得到分析结果 analysis。
        6.Phase 2：
            - 构建 Phase 2 的系统提示（agent/dream_phase2.md，其中包含 skill_creator_path 引用）。
            - 用户消息 = analysis + 文件内容 + 已有技能列表（若存在）。
            - 调用 self._runner.run(AgentRunSpec) 执行，允许使用的工具为 read_file, edit_file, write_file（限制写入 skills 目录）。
        7.从 result.tool_events 提取生成变更日志 changelog。
        8.无论 Phase 2 成功与否，都更新 dream cursor 到当前批次的最后一条游标，并调用 store.compact_history() 压缩历史（保留最近 1000 条）。
        9.若产生变更且 Git 已初始化，提交 commit，commit message 包含时间戳和变更数量。
        10.返回 True 表示处理了记录
        """
        from silver_research_bot.agent.skills import BUILTIN_SKILLS_DIR

        last_cursor = self.store.get_last_dream_cursor()
        entries = self.store.read_unprocessed_history(since_cursor=last_cursor)
        if not entries:
            return False

        batch = entries[: self.max_batch_size]
        logger.info(
            "Dream: processing {} entries (cursor {}→{}), batch={}",
            len(entries), last_cursor, batch[-1]["cursor"], len(batch),
        )

        # Build history text for LLM
        history_text = "\n".join(
            f"[{e['timestamp']}] {e['content']}" for e in batch
        )

        # Current file contents + per-line age annotations (MEMORY.md only)
        current_date = datetime.now().strftime("%Y-%m-%d")
        raw_memory = self.store.read_memory() or "(empty)"
        current_memory = (
            self._annotate_with_ages(raw_memory)
            if self.annotate_line_ages
            else raw_memory
        )
        current_soul = self.store.read_soul() or "(empty)"
        current_user = self.store.read_user() or "(empty)"

        file_context = (
            f"## Current Date\n{current_date}\n\n"
            f"## Current MEMORY.md ({len(current_memory)} chars)\n{current_memory}\n\n"
            f"## Current SOUL.md ({len(current_soul)} chars)\n{current_soul}\n\n"
            f"## Current USER.md ({len(current_user)} chars)\n{current_user}"
        )

        # Phase 1: Analyze (no skills list — dedup is Phase 2's job)
        phase1_prompt = (
            f"## Conversation History\n{history_text}\n\n{file_context}"
        )

        try:
            phase1_response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": render_template(
                            "agent/dream_phase1.md",
                            strip=True,
                            stale_threshold_days=_STALE_THRESHOLD_DAYS,
                        ),
                    },
                    {"role": "user", "content": phase1_prompt},
                ],
                tools=None,
                tool_choice=None,
            )
            analysis = phase1_response.content or ""
            logger.debug("Dream Phase 1 analysis ({} chars): {}", len(analysis), analysis[:500])
        except Exception:
            logger.exception("Dream Phase 1 failed")
            return False

        # Phase 2: Delegate to AgentRunner with read_file / edit_file
        existing_skills = self._list_existing_skills()
        skills_section = ""
        if existing_skills:
            skills_section = (
                "\n\n## Existing Skills\n"
                + "\n".join(f"- {s}" for s in existing_skills)
            )
        phase2_prompt = f"## Analysis Result\n{analysis}\n\n{file_context}{skills_section}"

        tools = self._tools
        skill_creator_path = BUILTIN_SKILLS_DIR / "skill-creator" / "SKILL.md"
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": render_template(
                    "agent/dream_phase2.md",
                    strip=True,
                    skill_creator_path=str(skill_creator_path),
                ),
            },
            {"role": "user", "content": phase2_prompt},
        ]

        try:
            result = await self._runner.run(AgentRunSpec(
                initial_messages=messages,
                tools=tools,
                model=self.model,
                max_iterations=self.max_iterations,
                max_tool_result_chars=self.max_tool_result_chars,
                fail_on_tool_error=False,
            ))
            logger.debug(
                "Dream Phase 2 complete: stop_reason={}, tool_events={}",
                result.stop_reason, len(result.tool_events),
            )
            for ev in (result.tool_events or []):
                logger.info("Dream tool_event: name={}, status={}, detail={}", ev.get("name"), ev.get("status"), ev.get("detail", "")[:200])
        except Exception:
            logger.exception("Dream Phase 2 failed")
            result = None

        # Build changelog from tool events
        changelog: list[str] = []
        if result and result.tool_events:
            for event in result.tool_events:
                if event["status"] == "ok":
                    changelog.append(f"{event['name']}: {event['detail']}")

        # Advance cursor — always, to avoid re-processing Phase 1
        new_cursor = batch[-1]["cursor"]
        self.store.set_last_dream_cursor(new_cursor)
        self.store.compact_history()

        if result and result.stop_reason == "completed":
            logger.info(
                "Dream done: {} change(s), cursor advanced to {}",
                len(changelog), new_cursor,
            )
        else:
            reason = result.stop_reason if result else "exception"
            logger.warning(
                "Dream incomplete ({}): cursor advanced to {}",
                reason, new_cursor,
            )

        # Git auto-commit (only when there are actual changes)
        if changelog and self.store.git.is_initialized():
            ts = batch[-1]["timestamp"]
            summary = f"dream: {ts}, {len(changelog)} change(s)"
            commit_msg = f"{summary}\n\n{analysis.strip()}"
            sha = self.store.git.auto_commit(commit_msg)
            if sha:
                logger.info("Dream commit: {}", sha)

        return True
