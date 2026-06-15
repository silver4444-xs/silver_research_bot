"""文件系统工具：读、写、编辑、列出目录"""

import difflib
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from silver_research_bot.agent.tools.base import Tool, tool_parameters
from silver_research_bot.agent.tools.schema import BooleanSchema, IntegerSchema, StringSchema, tool_parameters_schema
from silver_research_bot.agent.tools import file_state
from silver_research_bot.utils.helpers import build_image_content_blocks, detect_image_mime
from silver_research_bot.config.paths import get_media_dir


def _resolve_path(
    path: str,
    workspace: Path | None = None,
    allowed_dir: Path | None = None,
    extra_allowed_dirs: list[Path] | None = None,
) -> Path:
    """
    路径解析：相对路径基于 workspace 转换；绝对路径直接使用。
    调用 Path.resolve() 解析符号链接。
    """
    p = Path(path).expanduser()
    if not p.is_absolute() and workspace:
        p = workspace / p
    resolved = p.resolve()
    if allowed_dir:
        media_path = get_media_dir().resolve()
        all_dirs = [allowed_dir] + [media_path] + (extra_allowed_dirs or []) 
        if not any(_is_under(resolved, d) for d in all_dirs):
            raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir}")
    return resolved


def _is_under(path: Path, directory: Path) -> bool:
    """
    目录限制：
    如果传入了 allowed_dir（允许的根目录），
    则要求解析后的路径必须位于 allowed_dir、媒体目录（get_media_dir()）
    或 extra_allowed_dirs 中的任意一个之下。否则抛出 PermissionError
    """
    try:
        path.relative_to(directory.resolve())
        return True
    except ValueError:
        return False


class _FsTool(Tool):
    """文件系统工具的共享基类——通用的初始化与路径解析。"""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        extra_allowed_dirs: list[Path] | None = None,
    ):
        self._workspace = workspace
        '工作目录'
        self._allowed_dir = allowed_dir
        '允许访问的目录'
        self._extra_allowed_dirs = extra_allowed_dirs
        '额外允许访问的目录列表'

    def _resolve(self, path: str) -> Path:
        """路径解析"""
        return _resolve_path(path, self._workspace, self._allowed_dir, self._extra_allowed_dirs)


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/stdout", "/dev/stderr",
    "/dev/tty", "/dev/console",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})
'设备路径黑名单（防阻塞）'

def _is_blocked_device(path: str | Path) -> bool:
    """检查 path 是否为可能导致程序挂起或产生无限输出数据的阻塞设备."""
    import re
    raw = str(path)

    # Resolve symlinks to check the actual target
    try:
        resolved = str(Path(raw).resolve())
    except (OSError, ValueError):
        resolved = raw

    if raw in _BLOCKED_DEVICE_PATHS or resolved in _BLOCKED_DEVICE_PATHS:
        return True
    if re.match(r"/proc/\d+/fd/[012]$", raw) or re.match(r"/proc/self/fd/[012]$", raw):
        return True
    if re.match(r"/proc/\d+/fd/[012]$", resolved) or re.match(r"/proc/self/fd/[012]$", resolved):
        return True

    # Check if resolved path starts with /dev/ (covers symlinks to devices)
    if resolved.startswith("/dev/"):
        return True
    return False


def _parse_page_range(pages: str, total: int) -> tuple[int, int]:
    """将类似“2-5”的页码范围解析为以0为起点的（起始页，结束页）包含范围。"""
    parts = pages.strip().split("-")
    if len(parts) == 1:
        p = int(parts[0])
        return max(0, p - 1), min(p - 1, total - 1)
    start = int(parts[0])
    end = int(parts[1])
    return max(0, start - 1), min(end - 1, total - 1)


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to read"),
        offset=IntegerSchema(
            1,
            description="Line number to start reading from (1-indexed, default 1)",
            minimum=1,
        ),
        limit=IntegerSchema(
            2000,
            description="Maximum number of lines to read (default 2000)",
            minimum=1,
        ),
        pages=StringSchema("Page range for PDF files, e.g. '1-5' (default: all, max 20 pages)"),
        required=["path"],
    )
)
class ReadFileTool(_FsTool):
    """读取文件内容，可选按行分页。"""

    _MAX_CHARS = 128_000
    '最大字符数限制'
    _DEFAULT_LIMIT = 2000
    '默认长度限制'
    _MAX_PDF_PAGES = 20
    'PDF最大解析页数'

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a file (text, image, or document). "
            "Text output format: LINE_NUM|CONTENT. "
            "Images return visual content for analysis. "
            "Supports PDF, DOCX, XLSX, PPTX documents. "
            "Use offset and limit for large text files. "
            "Reads exceeding ~128K chars are truncated."
        )

    @property
    def read_only(self) -> bool:
        """
        返回 True，表示该工具无副作用，可安全并发执行
        """
        return True

    async def execute(self, path: str | None = None, offset: int = 1, limit: int | None = None, pages: str | None = None, **kwargs: Any) -> Any:
        try:
            if not path:
                return "Error reading file: Unknown path"

            '''1.路径验证：调用 _resolve，检查设备黑名单，确保文件存在且是普通文件'''
            # Device path blacklist
            if _is_blocked_device(path):
                return f"Error: Reading {path} is blocked (device path that could hang or produce infinite output)."

            fp = self._resolve(path)
            if _is_blocked_device(fp):
                return f"Error: Reading {fp} is blocked (device path that could hang or produce infinite output)."
            if not fp.exists():
                return f"Error: File not found: {path}"
            if not fp.is_file():
                return f"Error: Not a file: {path}"

            '''
            特殊格式处理：

            -PDF：使用 pymupdf 提取文本，支持分页，最多 20 页。
            -Office 文档 (docx/xlsx/pptx)：调用 extract_text 提取文本。
            -图片：返回图片内容块（用于多模态模型）。
            '''
            # PDF support
            if fp.suffix.lower() == ".pdf":
                return self._read_pdf(fp, pages)

            # Office document support
            if fp.suffix.lower() in {".docx", ".xlsx", ".pptx"}:
                return self._read_office_doc(fp)

            raw = fp.read_bytes()
            if not raw:
                return f"(Empty file: {path})"

            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if mime and mime.startswith("image/"):
                return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")


            '''
            3.去重优化：
            通过全局 file_state（未完全展示）记录每个文件的读取历史、修改时间和内容哈希。
            当同一文件再次以相同 offset/limit 读取且内容未变化时，
            返回 [File unchanged since last read: ...] 短消息，节省 token。
            
            读取去重：相同路径 + 偏移量 + 限制 + 修改时间未变 → 占位符
            去重前务必检查是否存在外部修改
            '''
            entry = file_state._state.get(str(fp.resolve()))
            try:
                current_mtime = os.path.getmtime(fp)
            except OSError:
                current_mtime = 0.0
            if entry and entry.can_dedup and entry.offset == offset and entry.limit == limit:
                if current_mtime != entry.mtime:
                    # File was modified externally - force full read and mark as not dedupable
                    entry.can_dedup = False
                    file_state.record_read(fp, offset=offset, limit=limit)  # Update state with new mtime
                    # Continue to read full content (don't return dedup message)
                else:
                    # File unchanged - return dedup message
                    # But only if content is actually unchanged (not just mtime)
                    current_hash = file_state._hash_file(str(fp))
                    if current_hash == entry.content_hash:
                        return f"[File unchanged since last read: {path}]"
                    else:
                        # Content changed despite same mtime - force full read
                        entry.can_dedup = False
                        file_state.record_read(fp, offset=offset, limit=limit)
            else:
                # No previous state or marked as not dedupable - read full content
                file_state.record_read(fp, offset=offset, limit=limit)
                # Force full read by setting can_dedup to False for this read
                if entry:
                    entry.can_dedup = False

            '''
            去重检查后读取文本文件内容
            - 读取二进制并尝试 UTF‑8 解码；失败则报告“二进制文件”。
            - 将 CRLF 转换为 LF（跨平台一致性）。
            - 按 offset 和 limit 截取行，输出格式 行号|内容。
            - 内容超过 _MAX_CHARS（128KB）时截断，并在末尾提示继续方式。
            '''
            # Read the file content after dedup check
            raw = fp.read_bytes()
            try:
                text_content = raw.decode("utf-8")
            except UnicodeDecodeError:
                # Binary file - return error message
                mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
                if mime and mime.startswith("image/"):
                    return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")
                return f"Error: Cannot read binary file {path} (MIME: {mime or 'unknown'}). Only UTF-8 text and images are supported."

            # Normalize CRLF -> LF before line-splitting. Primarily a Windows
            # concern (git checkouts with autocrlf, editors saving CRLF) but
            # applied on all platforms so downstream StrReplace/Grep behavior
            # is consistent regardless of where the file was written.
            text_content = text_content.replace("\r\n", "\n")

            all_lines = text_content.splitlines()
            total = len(all_lines)

            if offset < 1:
                offset = 1
            if offset > total:
                return f"Error: offset {offset} is beyond end of file ({total} lines)"

            start = offset - 1
            end = min(start + (limit or self._DEFAULT_LIMIT), total)
            numbered = [f"{start + i + 1}| {line}" for i, line in enumerate(all_lines[start:end])]
            result = "\n".join(numbered)

            if len(result) > self._MAX_CHARS:
                trimmed, chars = [], 0
                for line in numbered:
                    chars += len(line) + 1
                    if chars > self._MAX_CHARS:
                        break
                    trimmed.append(line)
                end = start + len(trimmed)
                result = "\n".join(trimmed)

            if end < total:
                result += f"\n\n(Showing lines {offset}-{end} of {total}. Use offset={end + 1} to continue.)"
            else:
                result += f"\n\n(End of file — {total} lines total)"
            file_state.record_read(fp, offset=offset, limit=limit)
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {e}"

    def _read_pdf(self, fp: Path, pages: str | None) -> str:
        """用pymupdf读取pdf文件"""
        try:
            import fitz  # pymupdf
        except ImportError:
            return "Error: PDF reading requires pymupdf. Install with: pip install pymupdf"

        try:
            doc = fitz.open(str(fp))
        except Exception as e:
            return f"Error reading PDF: {e}"

        total_pages = len(doc)
        if pages:
            try:
                start, end = _parse_page_range(pages, total_pages)
            except (ValueError, IndexError):
                doc.close()
                return f"Error: Invalid page range '{pages}'. Use format like '1-5'."
            if start > end or start >= total_pages:
                doc.close()
                return f"Error: Page range '{pages}' is out of bounds (document has {total_pages} pages)."
        else:
            start = 0
            end = min(total_pages - 1, self._MAX_PDF_PAGES - 1)

        if end - start + 1 > self._MAX_PDF_PAGES:
            end = start + self._MAX_PDF_PAGES - 1

        parts: list[str] = []
        for i in range(start, end + 1):
            page = doc[i]
            text = page.get_text().strip()
            if text:
                parts.append(f"--- Page {i + 1} ---\n{text}")
        doc.close()

        if not parts:
            return f"(PDF has no extractable text: {fp})"

        result = "\n\n".join(parts)
        if end < total_pages - 1:
            result += f"\n\n(Showing pages {start + 1}-{end + 1} of {total_pages}. Use pages='{end + 2}-{min(end + 1 + self._MAX_PDF_PAGES, total_pages)}' to continue.)"
        if len(result) > self._MAX_CHARS:
            result = result[:self._MAX_CHARS] + "\n\n(PDF text truncated at ~128K chars)"
        return result

    def _read_office_doc(self, fp: Path) -> str:
        """调用 extract_text 提取文本,读取Office 文档 (docx/xlsx/pptx)"""
        from silver_research_bot.utils.document import extract_text

        result = extract_text(fp)

        if result is None:
            return f"Error: Unsupported file format: {fp.suffix}"

        if result.startswith("[error:"):
            return f"Error reading {fp.suffix.upper()} file: {result}"

        if not result:
            return f"({fp.suffix.upper().lstrip('.')} has no extractable text: {fp})"

        if len(result) > self._MAX_CHARS:
            result = result[:self._MAX_CHARS] + "\n\n(Document text truncated at ~128K chars)"

        return result


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to write to"),
        content=StringSchema("The content to write"),
        required=["path", "content"],
    )
)
class WriteFileTool(_FsTool):
    """文件写入工具"""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write content to a file. Overwrites if the file already exists; "
            "creates parent directories as needed. "
            "For partial edits, prefer edit_file instead."
        )

    async def execute(self, path: str | None = None, content: str | None = None, **kwargs: Any) -> str:
        '''

        简单：解析路径，创建父目录，直接写入 UTF‑8 文本。
        覆盖原有内容。
        调用 file_state.record_write(fp) 更新状态，使得下次读取能检测到文件已变，避免错误去重。
        返回成功字符数或错误。
        '''
        try:
            if not path:
                raise ValueError("Unknown path")
            if content is None:
                raise ValueError("Unknown content")
            fp = self._resolve(path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            file_state.record_write(fp)
            return f"Successfully wrote {len(content)} characters to {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {e}"


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

_QUOTE_TABLE = str.maketrans({
    "\u2018": "'", "\u2019": "'",  # curly single → straight
    "\u201c": '"', "\u201d": '"',  # curly double → straight
    "'": "'", '"': '"',            # identity (kept for completeness)
})
'引号转换表,将弯引号统一转换为直引号'

def _normalize_quotes(s: str) -> str:
    """将弯引号统一转换为直引号"""
    return s.translate(_QUOTE_TABLE)


def _curly_double_quotes(text: str) -> str:
    """将文本中的直双引号 " 转换为弯双引号，交替使用左弯引号 “ 和右弯引号 ”。
    用于在替换时保留目标文件的弯引号风格"""
    parts: list[str] = []
    opening = True
    for ch in text:
        if ch == '"':
            parts.append("\u201c" if opening else "\u201d")
            opening = not opening
        else:
            parts.append(ch)
    return "".join(parts)


def _curly_single_quotes(text: str) -> str:
    """将直单引号 ' 转换为弯单引号"""
    parts: list[str] = []
    opening = True
    for i, ch in enumerate(text):
        if ch != "'":
            parts.append(ch)
            continue
        prev_ch = text[i - 1] if i > 0 else ""
        next_ch = text[i + 1] if i + 1 < len(text) else ""
        if prev_ch.isalnum() and next_ch.isalnum():
            parts.append("\u2019")
            continue
        parts.append("\u2018" if opening else "\u2019")
        opening = not opening
    return "".join(parts)


def _preserve_quote_style(old_text: str, actual_text: str, new_text: str) -> str:
    """当匹配到经过引号规范化的备用方案时，保留花括号引号的样式。"""
    if _normalize_quotes(old_text.strip()) != _normalize_quotes(actual_text.strip()) or old_text == actual_text:
        return new_text

    styled = new_text
    if any(ch in actual_text for ch in ("\u201c", "\u201d")) and '"' in styled:
        styled = _curly_double_quotes(styled)
    if any(ch in actual_text for ch in ("\u2018", "\u2019")) and "'" in styled:
        styled = _curly_single_quotes(styled)
    return styled


def _leading_ws(line: str) -> str:
    """返回字符串开头的空格/制表符部分"""
    return line[: len(line) - len(line.lstrip(" \t"))]


def _reindent_like_match(old_text: str, actual_text: str, new_text: str) -> str:
    """保留实际匹配块的外层缩进。"""
    old_lines = old_text.split("\n")
    actual_lines = actual_text.split("\n")
    if len(old_lines) != len(actual_lines):
        return new_text

    comparable = [
        (old_line, actual_line)
        for old_line, actual_line in zip(old_lines, actual_lines)
        if old_line.strip() and actual_line.strip()
    ]
    if not comparable or any(
        _normalize_quotes(old_line.strip()) != _normalize_quotes(actual_line.strip())
        for old_line, actual_line in comparable
    ):
        return new_text

    old_ws = _leading_ws(comparable[0][0])
    actual_ws = _leading_ws(comparable[0][1])
    if actual_ws == old_ws:
        return new_text

    if old_ws:
        if not actual_ws.startswith(old_ws):
            return new_text
        delta = actual_ws[len(old_ws):]
    else:
        delta = actual_ws

    if not delta:
        return new_text

    return "\n".join((delta + line) if line else line for line in new_text.split("\n"))


@dataclass(slots=True)
class _MatchSpan:
    start: int
    '在内容中的起始字符索引'
    end: int
    '结束字符索引'
    text: str
    '匹配到的实际文本（保持原始大小写/空白/引号）'
    line: int
    '起始行号（1‑based）'


def _find_exact_matches(content: str, old_text: str) -> list[_MatchSpan]:
    """朴素子串查找，返回所有匹配的 _MatchSpan 列表"""
    matches: list[_MatchSpan] = []
    start = 0
    while True:
        idx = content.find(old_text, start)
        if idx == -1:
            break
        matches.append(
            _MatchSpan(
                start=idx,
                end=idx + len(old_text),
                text=content[idx : idx + len(old_text)],
                line=content.count("\n", 0, idx) + 1,
            )
        )
        start = idx + max(1, len(old_text))
    return matches


def _find_trim_matches(content: str, old_text: str, *, normalize_quotes: bool = False) -> list[_MatchSpan]:
    """按行拆分后，将每行去除首尾空白（可选同时标准化引号）后进行比较"""
    old_lines = old_text.splitlines()
    if not old_lines:
        return []

    content_lines = content.splitlines()
    content_lines_keepends = content.splitlines(keepends=True)
    if len(content_lines) < len(old_lines):
        return []

    offsets: list[int] = []
    pos = 0
    for line in content_lines_keepends:
        offsets.append(pos)
        pos += len(line)
    offsets.append(pos)

    if normalize_quotes:
        stripped_old = [_normalize_quotes(line.strip()) for line in old_lines]
    else:
        stripped_old = [line.strip() for line in old_lines]

    matches: list[_MatchSpan] = []
    window_size = len(stripped_old)
    for i in range(len(content_lines) - window_size + 1):
        window = content_lines[i : i + window_size]
        if normalize_quotes:
            comparable = [_normalize_quotes(line.strip()) for line in window]
        else:
            comparable = [line.strip() for line in window]
        if comparable != stripped_old:
            continue

        start = offsets[i]
        end = offsets[i + window_size]
        if content_lines_keepends[i + window_size - 1].endswith("\n"):
            end -= 1
        matches.append(
            _MatchSpan(
                start=start,
                end=end,
                text=content[start:end],
                line=i + 1,
            )
        )
    return matches


def _find_quote_matches(content: str, old_text: str) -> list[_MatchSpan]:
    """对整个内容字符串和 old_text 进行引号标准化（弯→直），然后进行子串查找"""
    norm_content = _normalize_quotes(content)
    norm_old = _normalize_quotes(old_text)
    matches: list[_MatchSpan] = []
    start = 0
    while True:
        idx = norm_content.find(norm_old, start)
        if idx == -1:
            break
        matches.append(
            _MatchSpan(
                start=idx,
                end=idx + len(old_text),
                text=content[idx : idx + len(old_text)],
                line=content.count("\n", 0, idx) + 1,
            )
        )
        start = idx + max(1, len(norm_old))
    return matches


def _find_matches(content: str, old_text: str) -> list[_MatchSpan]:
    """使用逐步放宽的策略查找所有匹配项。"""
    for matcher in (
        lambda: _find_exact_matches(content, old_text),
        lambda: _find_trim_matches(content, old_text),
        lambda: _find_trim_matches(content, old_text, normalize_quotes=True),
        lambda: _find_quote_matches(content, old_text),
    ):
        matches = matcher()
        if matches:
            return matches
    return []


def _find_match_line_numbers(content: str, old_text: str) -> list[int]:
    """返回当前匹配策略的起始行号（从1开始计数）。"""
    return [match.line for match in _find_matches(content, old_text)]


def _collapse_internal_whitespace(text: str) -> str:
    """将每行内部的连续空格合并为一个空格，用于比较时忽略多余空格"""
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def _diagnose_near_match(old_text: str, actual_text: str) -> list[str]:
    """返回可操作的提示，说明文本为何接近但并不完全匹配."""
    hints: list[str] = []

    if old_text.lower() == actual_text.lower() and old_text != actual_text:
        hints.append("letter case differs")
    if _collapse_internal_whitespace(old_text) == _collapse_internal_whitespace(actual_text) and old_text != actual_text:
        hints.append("whitespace differs")
    if old_text.rstrip("\n") == actual_text.rstrip("\n") and old_text != actual_text:
        hints.append("trailing newline differs")
    if _normalize_quotes(old_text) == _normalize_quotes(actual_text) and old_text != actual_text:
        hints.append("quote style differs")

    return hints


def _best_window(old_text: str, content: str) -> tuple[float, int, list[str], list[str]]:
    """查找最接近的行-窗口匹配，并返回比率/起始位置/代码片段/提示."""
    lines = content.splitlines(keepends=True)
    old_lines = old_text.splitlines(keepends=True)
    window = max(1, len(old_lines))

    best_ratio, best_start = -1.0, 0
    best_window_lines: list[str] = []

    for i in range(max(1, len(lines) - window + 1)):
        current = lines[i : i + window]
        ratio = difflib.SequenceMatcher(None, old_lines, current).ratio()
        if ratio > best_ratio:
            best_ratio, best_start = ratio, i
            best_window_lines = current

    actual_text = "".join(best_window_lines).replace("\r\n", "\n").rstrip("\n")
    hints = _diagnose_near_match(old_text.replace("\r\n", "\n").rstrip("\n"), actual_text)
    return best_ratio, best_start, best_window_lines, hints


def _find_match(content: str, old_text: str) -> tuple[str | None, int]:
    """使用多级回退链在内容中查找 old_text：

    1. 精确子字符串匹配
    2. 去空行的滑动窗口（可处理缩进差异）
    3. 智能引号规范化（花括号引号 ↔ 直角引号）

    两个输入都应使用 LF 换行符（调用方负责将 CRLF 规范化）。
    返回 (matched_fragment, count) 或 (None, 0)。
    """
    matches = _find_matches(content, old_text)
    if not matches:
        return None, 0
    return matches[0].text, len(matches)


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to edit"),
        old_text=StringSchema("The text to find and replace"),
        new_text=StringSchema("The text to replace with"),
        replace_all=BooleanSchema(description="Replace all occurrences (default false)"),
        required=["path", "old_text", "new_text"],
    )
)
class EditFileTool(_FsTool):
    """通过使用备用匹配来替换文本以编辑文件."""

    _MAX_EDIT_FILE_SIZE = 1024 * 1024 * 1024
    '可编辑文件的最大大小（1 GiB）'
    _MARKDOWN_EXTS = frozenset({".md", ".mdx", ".markdown"})
    'Markdown 格式文件扩展名集合'

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing old_text with new_text. "
            "Tolerates minor whitespace/indentation differences and curly/straight quote mismatches. "
            "If old_text matches multiple times, you must provide more context "
            "or set replace_all=true. Shows a diff of the closest match on failure."
        )

    @staticmethod
    def _strip_trailing_ws(text: str) -> str:
        """去除每行末尾的空格."""
        return "\n".join(line.rstrip() for line in text.split("\n"))

    async def execute(
        self, path: str | None = None, old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False, **kwargs: Any,
    ) -> str:
        try:
            '''1.参数校验'''
            if not path:
                raise ValueError("Unknown path")
            if old_text is None:
                raise ValueError("Unknown old_text")
            if new_text is None:
                raise ValueError("Unknown new_text")

            '''2..ipynb 检测：提示使用 notebook_edit 工具'''
            # .ipynb detection
            if path.endswith(".ipynb"):
                return "Error: This is a Jupyter notebook. Use the notebook_edit tool instead of edit_file."

            '''3.路径解析（调用 _resolve，已做安全限制）'''
            fp = self._resolve(path)

            '''4.文件不存在且 old_text == ""：创建文件并写入 new_text'''
            # Create-file semantics: old_text='' + file doesn't exist → create
            if not fp.exists():
                if old_text == "":
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(new_text, encoding="utf-8")
                    file_state.record_write(fp)
                    return f"Successfully created {fp}"
                return self._file_not_found_msg(path, fp)

            # File size protection
            try:
                fsize = fp.stat().st_size
            except OSError:
                fsize = 0
            if fsize > self._MAX_EDIT_FILE_SIZE:
                return f"Error: File too large to edit ({fsize / (1024**3):.1f} GiB). Maximum is 1 GiB."

            '''5.文件存在且 old_text == ""：如果文件非空则拒绝（防止误覆盖），否则清空文件并写入新内容'''
            # Create-file: old_text='' but file exists and not empty → reject
            if old_text == "":
                raw = fp.read_bytes()
                content = raw.decode("utf-8")
                if content.strip():
                    return f"Error: Cannot create file — {path} already exists and is not empty."
                fp.write_text(new_text, encoding="utf-8")
                file_state.record_write(fp)
                return f"Successfully edited {fp}"

            '''6.读前检查：file_state.check_read(fp) 如果文件在上次读取后被外部修改，返回警告'''
            # Read-before-edit check
            warning = file_state.check_read(fp)

            '''7.读取文件内容，统一换行符为 \n，记录原文件是否包含 CRLF'''
            raw = fp.read_bytes()
            uses_crlf = b"\r\n" in raw
            content = raw.decode("utf-8").replace("\r\n", "\n")
            norm_old = old_text.replace("\r\n", "\n")

            '''8.调用 _find_matches(content, norm_old) 查找所有匹配。
            若无匹配：返回 _not_found_msg（包含最相似区块的 diff 或诊断提示）。
            若匹配多个且 replace_all == False：返回警告，列出前几个匹配的行号，要求提供更多上下文或启用 replace_all'''
            matches = _find_matches(content, norm_old)

            if not matches:
                return self._not_found_msg(old_text, content, path)
            count = len(matches)
            if count > 1 and not replace_all:
                line_numbers = [match.line for match in matches]
                preview = ", ".join(f"line {n}" for n in line_numbers[:3])
                if len(line_numbers) > 3:
                    preview += ", ..."
                location_hint = f" at {preview}" if preview else ""
                return (
                    f"Warning: old_text appears {count} times{location_hint}. "
                    "Provide more context to make it unique, or set replace_all=true."
                )

            '''9.规范化 new_text 换行符，并根据文件扩展名决定是否去除每行尾随空白（Markdown 除外）'''
            norm_new = new_text.replace("\r\n", "\n")

            # Trailing whitespace stripping (skip markdown to preserve double-space line breaks)
            if fp.suffix.lower() not in self._MARKDOWN_EXTS:
                norm_new = self._strip_trailing_ws(norm_new)

            '''
            10.生成新内容：
            从后向前遍历选择的匹配（replace_all=True 时全部，否则仅第一个），避免影响后续位置的索引。
            对每个匹配，调用 _preserve_quote_style 和 _reindent_like_match 调整替换文本。
            如果 new_text 为空且匹配块不以换行结尾，但内容中匹配后紧跟一个换行，则将该换行也删除（避免留下空行）。
            替换后更新 new_content
            '''
            selected = matches if replace_all else matches[:1]
            new_content = content
            for match in reversed(selected):
                '''11.写回文件：如果原文件有 CRLF，将换行符转换回 CRLF；否则保留 LF'''
                replacement = _preserve_quote_style(norm_old, match.text, norm_new)
                replacement = _reindent_like_match(norm_old, match.text, replacement)

                # Delete-line cleanup: when deleting text (new_text=''), consume trailing
                # newline to avoid leaving a blank line
                end = match.end
                if replacement == "" and not match.text.endswith("\n") and content[end:end + 1] == "\n":
                    end += 1

                new_content = new_content[: match.start] + replacement + new_content[end:]
            if uses_crlf:
                new_content = new_content.replace("\n", "\r\n")

            '''12.记录写入（file_state.record_write），返回成功消息（可能附带读前警告）'''
            fp.write_bytes(new_content.encode("utf-8"))
            file_state.record_write(fp)
            msg = f"Successfully edited {fp}"
            if warning:
                msg = f"{warning}\n{msg}"
            return msg
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {e}"

    def _file_not_found_msg(self, path: str, fp: Path) -> str:
        """构建包含“您是否想说……？”建议的错误提示。"""
        parent = fp.parent
        suggestions: list[str] = []
        if parent.is_dir():
            siblings = [f.name for f in parent.iterdir() if f.is_file()]
            close = difflib.get_close_matches(fp.name, siblings, n=3, cutoff=0.6)
            suggestions = [str(parent / c) for c in close]
        parts = [f"Error: File not found: {path}"]
        if suggestions:
            parts.append("Did you mean: " + ", ".join(suggestions) + "?")
        return "\n".join(parts)

    @staticmethod
    def _not_found_msg(old_text: str, content: str, path: str) -> str:
        '''如果 old_text 在文件中找不到，调用 _best_window 找到最相似的窗口，
        并生成 unified diff 显示差异，同时给出诊断提示（大小写、空白、引号等）。
        如果相似度很低，仅返回诊断提示'''
        best_ratio, best_start, best_window_lines, hints = _best_window(old_text, content)
        if best_ratio > 0.5:
            diff = "\n".join(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                best_window_lines,
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            ))
            hint_text = ""
            if hints:
                hint_text = "\nPossible cause: " + ", ".join(hints) + "."
            return (
                f"Error: old_text not found in {path}."
                f"{hint_text}\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
            )

        if hints:
            return (
                f"Error: old_text not found in {path}. "
                f"Possible cause: {', '.join(hints)}. "
                "Copy the exact text from read_file and try again."
            )
        return f"Error: old_text not found in {path}. No similar text found. Verify the file content."


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The directory path to list"),
        recursive=BooleanSchema(description="Recursively list all files (default false)"),
        max_entries=IntegerSchema(
            200,
            description="Maximum entries to return (default 200)",
            minimum=1,
        ),
        required=["path"],
    )
)
class ListDirTool(_FsTool):
    """列出目录内容，可选递归。"""

    _DEFAULT_MAX = 200
    _IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".coverage", "htmlcov",
    }

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. "
            "Set recursive=true to explore nested structure. "
            "Common noise directories (.git, node_modules, __pycache__, etc.) are auto-ignored."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self, path: str | None = None, recursive: bool = False,
        max_entries: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            if path is None:
                raise ValueError("Unknown path")
            dp = self._resolve(path)
            if not dp.exists():
                return f"Error: Directory not found: {path}"
            if not dp.is_dir():
                return f"Error: Not a directory: {path}"

            cap = max_entries or self._DEFAULT_MAX
            items: list[str] = []
            total = 0

            if recursive:
                for item in sorted(dp.rglob("*")):
                    if any(p in self._IGNORE_DIRS for p in item.parts):
                        continue
                    total += 1
                    if len(items) < cap:
                        rel = item.relative_to(dp)
                        items.append(f"{rel}/" if item.is_dir() else str(rel))
            else:
                for item in sorted(dp.iterdir()):
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        pfx = "📁 " if item.is_dir() else "📄 "
                        items.append(f"{pfx}{item.name}")

            if not items and total == 0:
                return f"Directory {path} is empty"

            result = "\n".join(items)
            if total > cap:
                result += f"\n\n(truncated, showing first {cap} of {total} entries)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {e}"
