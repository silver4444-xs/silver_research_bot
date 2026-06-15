"""跟踪文件读取状态，用于“编辑前读取”警告和读取去重."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ReadState:
    mtime: float
    '文件最后修改时间戳'
    offset: int
    '读取的起始偏移位置'
    limit: int | None
    '最大读取长度限制，无限制则为None'
    content_hash: str
    '文件内容哈希值，用于去重校验'
    can_dedup: bool
    '是否允许内容去重'


_state: dict[str, ReadState] = {}


def _hash_file(p: str) -> str | None:
    """计算文件的 SHA256 哈希值"""
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


def record_read(path: str | Path, offset: int = 1, limit: int | None = None) -> None:
    """记录已读取文件（在读取成功后调用）。"""
    p = str(Path(path).resolve())
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return
    _state[p] = ReadState(
        mtime=mtime,
        offset=offset,
        limit=limit,
        content_hash=_hash_file(p),
        can_dedup=True,
    )


def record_write(path: str | Path) -> None:
    """记录文件已写入（更新状态中的修改时间）。"""
    p = str(Path(path).resolve())
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        _state.pop(p, None)
        return
    _state[p] = ReadState(
        mtime=mtime,
        offset=1,
        limit=None,
        content_hash=_hash_file(p),
        can_dedup=False,
    )


def check_read(path: str | Path) -> str | None:
    """检查文件是否已被读取且内容为最新。

    如果检查成功，则返回 None；否则返回警告字符串。
    当 mtime 发生变化但文件内容保持不变时（例如执行 touch 命令或编辑器保存操作），
    检查将通过，以避免出现虚假的过期警告。
    """
    p = str(Path(path).resolve())
    entry = _state.get(p)
    if entry is None:
        return "Warning: file has not been read yet. Read it first to verify content before editing."
    try:
        current_mtime = os.path.getmtime(p)
    except OSError:
        return None
    if current_mtime != entry.mtime:
        if entry.content_hash and _hash_file(p) == entry.content_hash:
            entry.mtime = current_mtime
            return None
        return "Warning: file has been modified since last read. Re-read to verify content before editing."
    # mtime unchanged - still check content hash to detect quick modifications
    if entry.content_hash and _hash_file(p) != entry.content_hash:
        return "Warning: file has been modified since last read. Re-read to verify content before editing."
    return None


def is_unchanged(path: str | Path, offset: int = 1, limit: int | None = None) -> bool:
    """如果文件此前曾使用相同参数读取过且内容未发生变化，则返回 True。"""
    p = str(Path(path).resolve())
    entry = _state.get(p)
    if entry is None:
        return False
    if not entry.can_dedup:
        return False
    if entry.offset != offset or entry.limit != limit:
        return False
    try:
        current_mtime = os.path.getmtime(p)
    except OSError:
        return False
    if current_mtime != entry.mtime:
        # mtime changed - check if content also changed
        current_hash = _hash_file(p)
        if current_hash != entry.content_hash:
            # Content actually changed - don't dedup
            entry.can_dedup = False
            return False
        # Content identical despite mtime change (e.g. touch) - mark as not dedupable to force full read next time
        entry.can_dedup = False
        return True
    # mtime unchanged - content must be identical
    return True


def clear() -> None:
    """清除所有已跟踪的状态（适用于测试）。"""
    _state.clear()
