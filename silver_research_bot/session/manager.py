"""对话历史记录的会话管理"""

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from silver_research_bot.config.paths import get_legacy_sessions_dir
from silver_research_bot.utils.helpers import ensure_dir, find_legal_message_start, safe_filename


@dataclass
class Session:
    """一个交谈会话"""

    key: str
    '会话唯一标识，格式为 channel:chat_id'
    messages: list[dict[str, Any]] = field(default_factory=list)
    '会话消息列表，存储对话内容'
    created_at: datetime = field(default_factory=datetime.now)
    '会话创建时间'
    updated_at: datetime = field(default_factory=datetime.now)
    '会话最后更新时间'
    metadata: dict[str, Any] = field(default_factory=dict)
    '会话元数据，存储额外自定义信息'
    last_consolidated: int = 0
    '已持久化/归档的消息数量'

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """向会话中追加消息"""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """返回针对 LLM 输入的未合并消息，并确保对齐到合法的对话轮次边界"""
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]

        # 确保开头是用户消息
        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                sliced = sliced[i:]
                break

        # 丢弃开头的孤儿工具结果
        start = find_legal_message_start(sliced)
        if start:
            sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        for message in sliced:
            entry: dict[str, Any] = {"role": message["role"], "content": message.get("content", "")}
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)
        return out

    def clear(self) -> None:
        """清除所有消息并将会话重置为初始状态."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()

    def retain_recent_legal_suffix(self, max_messages: int) -> None:
        """保留一个合法的近期后缀，并遵循 get_history 的边界规则。"""
        if max_messages <= 0:
            self.clear()
            return
        if len(self.messages) <= max_messages:
            return

        start_idx = max(0, len(self.messages) - max_messages)

        # If the cutoff lands mid-turn, extend backward to the nearest user turn.
        while start_idx > 0 and self.messages[start_idx].get("role") != "user":
            start_idx -= 1

        retained = self.messages[start_idx:]

        # Mirror get_history(): avoid persisting orphan tool results at the front.
        start = find_legal_message_start(retained)
        if start:
            retained = retained[start:]

        dropped = len(self.messages) - len(retained)
        self.messages = retained
        self.last_consolidated = max(0, self.last_consolidated - dropped)
        self.updated_at = datetime.now()


class SessionManager:
    """
    管理对话会话。

    会话以 JSONL 文件的形式存储在 sessions 目录中。
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        '工作目录根路径'
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        '会话存储目录，确保目录存在'
        self.legacy_sessions_dir = get_legacy_sessions_dir()
        '旧版会话存储目录'
        self._cache: dict[str, Session] = {}
        '会话内存缓存，键为会话ID，值为会话对象'

    @staticmethod
    def safe_key(key: str) -> str:
        """HTTP 处理程序使用的公共辅助类，用于将任意键映射到稳定的文件名根。"""
        return safe_filename(key.replace(":", "_"))

    def _get_session_path(self, key: str) -> Path:
        """获取会话的文件路径。"""
        return self.sessions_dir / f"{self.safe_key(key)}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """旧版全局会话路径"""
        return self.legacy_sessions_dir / f"{self.safe_key(key)}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        获取现有会话或创建新会话。

        参数：
            key：会话键（通常为 channel:chat_id）。

        返回值：
            会话对象。
        """
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        """从磁盘加载会话。"""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            updated_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        updated_at = datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered session {} from corrupt file ({} messages)", key, len(repaired.messages))
            return repaired

    def _repair(self, key: str) -> Session | None:
        """尝试从损坏的 JSONL 文件中恢复会话。"""
        path = self._get_session_path(key)
        if not path.exists():
            return None

        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: datetime | None = None
            updated_at: datetime | None = None
            last_consolidated = 0
            skipped = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                        continue

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        if data.get("created_at"):
                            try:
                                created_at = datetime.fromisoformat(data["created_at"])
                            except (ValueError, TypeError):
                                pass
                        if data.get("updated_at"):
                            try:
                                updated_at = datetime.fromisoformat(data["updated_at"])
                            except (ValueError, TypeError):
                                pass
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            if skipped:
                logger.warning("Skipped {} corrupt lines in session {}", skipped, key)

            if not messages and not metadata:
                return None

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Repair failed for session {}: {}", key, e)
            return None

    @staticmethod
    def _session_payload(session: Session) -> dict[str, Any]:
        return {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "messages": session.messages,
        }

    def save(self, session: Session) -> None:
        """原子写入（防止写入中途崩溃导致文件损坏）"""
        path = self._get_session_path(session.key)
        tmp_path = path.with_suffix(".jsonl.tmp")

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                metadata_line = {
                    "_type": "metadata",
                    "key": session.key,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "metadata": session.metadata,
                    "last_consolidated": session.last_consolidated
                }
                f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
                for msg in session.messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")

            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        self._cache[session.key] = session

    def invalidate(self, key: str) -> None:
        """从内存缓存中移除一个会话。"""
        self._cache.pop(key, None)

    def delete_session(self, key: str) -> bool:
        """从磁盘和内存缓存中移除一个会话。

        如果找到了 JSONL 文件并已解除关联，则返回 True。
        """
        path = self._get_session_path(key)
        self.invalidate(key)
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError as e:
            logger.warning("Failed to delete session file {}: {}", path, e)
            return False

    def read_session_file(self, key: str) -> dict[str, Any] | None:
        """从磁盘加载会话，不进行缓存；适用于只读的 HTTP 端点。

        返回 ``{“key”, “created_at”, “updated_at”, ‘metadata’, “messages”}``，或者
        当会话文件不存在或解析失败时返回 ``None``。
        """
        path = self._get_session_path(key)
        if not path.exists():
            return None
        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: str | None = None
            updated_at: str | None = None
            stored_key: str | None = None
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = data.get("created_at")
                        updated_at = data.get("updated_at")
                        stored_key = data.get("key")
                    else:
                        messages.append(data)
            return {
                "key": stored_key or key,
                "created_at": created_at,
                "updated_at": updated_at,
                "metadata": metadata,
                "messages": messages,
            }
        except Exception as e:
            logger.warning("Failed to read session {}: {}", key, e)
            repaired = self._repair(key)
            if repaired is not None:
                logger.info("Recovered read-only session view {} from corrupt file", key)
                return self._session_payload(repaired)
            return None

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        列出所有会话。

        返回：
            会话信息字典列表。
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            fallback_key = path.stem.replace("_", ":", 1)
            try:
                # Read just the metadata line
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            sessions.append({
                                "key": key,
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                repaired = self._repair(fallback_key)
                if repaired is not None:
                    sessions.append({
                        "key": repaired.key,
                        "created_at": repaired.created_at.isoformat(),
                        "updated_at": repaired.updated_at.isoformat(),
                        "path": str(path)
                    })
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
