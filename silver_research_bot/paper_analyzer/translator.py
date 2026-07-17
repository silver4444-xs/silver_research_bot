"""Stage 1a: 英文论文全文翻译器 — 公式→LaTeX Markdown"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from silver_research_bot.utils.prompt_templates import render_template

SECTION_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s+)?"
    r"(?:Abstract|Introduction|Related\s+Work|Background|System\s+Model|"
    r"Problem\s+Formulation|Proposed|Method|Experiment|"
    r"Performance\s+Evaluation|Conclusion|Discussion|"
    r"Future\s+Work|Appendix|Reference|Acknowledgment)",
    re.IGNORECASE,
)

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider


def _normalize_placeholders(text: str, figures: list[dict], tables: list[dict]) -> tuple[str, list[dict], list[dict]]:
    """将提取器的 [Fig N: caption] / [Table N] 转为不透明占位符 ◈FIG_N◈ / ◈TBL_N◈。

    提取器在 full_text 中产生人类可读的占位符，但系统提示词和
    _embed_figures_tables() 期望的是不透明的菱形标记。
    此函数在 LLM 翻译之前对齐格式。
    """
    import copy
    figures = copy.deepcopy(figures)
    tables = copy.deepcopy(tables)

    for fig in figures:
        idx = fig.get("index", 0)
        text = re.sub(
            rf"\[Fig\s+{idx}\s*:?\s*[^\]]*\]",
            f"◈FIG_{idx}◈",
            text,
            flags=re.IGNORECASE,
        )
        fig["opaque_placeholder"] = f"◈FIG_{idx}◈"

    for tbl in tables:
        idx = tbl.get("index", 0)
        text = re.sub(
            rf"\[Table\s+{idx}\s*\]",
            f"◈TBL_{idx}◈",
            text,
            flags=re.IGNORECASE,
        )
        tbl["placeholder"] = f"◈TBL_{idx}◈"

    return text, figures, tables


# ── Meta-commentary patterns that LLMs sometimes emit despite instructions ──
_META_PATTERNS = [
    re.compile(r"(?:以下|好的|OK|Sure|Here)[,，].*?(?:翻译|部分|translation).*?[：:]\s*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^#{1,4}\s*翻译(?:结果|内容|文本|译文)?\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^#{1,4}\s*第\s*\d+\s*/\s*\d+\s*部分\s*$", re.MULTILINE),
    re.compile(r"^第\s*\d+\s*/\s*\d+\s*部分.*?(?:翻译)?\s*$", re.MULTILINE),
    re.compile(r"^#{1,4}\s*(?:Translation|Chinese\s+Translation)\s*$", re.MULTILINE | re.IGNORECASE),
]


def _strip_meta_commentary(text: str) -> str:
    """Remove LLM meta-commentary that leaked into translation output."""
    for pat in _META_PATTERNS:
        text = pat.sub("", text)
    return text.strip()


# ── English detection ──────────────────────────────────────────────────

def _english_ratio(para: str) -> tuple[float, int]:
    """Return (ascii_alpha_ratio, cjk_char_count) for a paragraph, excluding formulas."""
    stripped = re.sub(r"\$\$[\s\S]*?\$\$", "", para)
    stripped = re.sub(r"\$[^$\n]*?\$", "", stripped)
    stripped = re.sub(r"◈[A-Z]+_\d+◈", "", stripped)
    stripped = re.sub(r"</?[^>]+>", "", stripped)
    ascii_alpha = len(re.findall(r"[a-zA-Z]", stripped))
    cjk = len(re.findall(r"[一-鿿㐀-䶿]", stripped))
    total = len(stripped.strip())
    if total == 0:
        return 0.0, 0
    return ascii_alpha / total, cjk


def _detect_untranslated_english(text: str) -> dict:
    """Detect blocks of untranslated English in translation output.

    Returns {"blocks": [{start, end, snippet, severity}], "summary": str}
    """
    paragraphs = text.split("\n\n")
    flagged: list[dict] = []
    i = 0
    while i < len(paragraphs):
        ratio, cjk = _english_ratio(paragraphs[i])
        if ratio > 0.70 and cjk == 0 and len(paragraphs[i].strip()) > 50:
            start = i
            while i < len(paragraphs):
                r, c = _english_ratio(paragraphs[i])
                if r <= 0.70 or c > 0:
                    break
                i += 1
            block_text = "\n\n".join(paragraphs[start:i])
            block_len = len(block_text)
            severity = "high" if block_len > 200 else "warning"
            flagged.append({
                "start": start,
                "end": i,
                "snippet": block_text[:200],
                "length": block_len,
                "severity": severity,
            })
        else:
            i += 1

    summary = ""
    if flagged:
        high = [b for b in flagged if b["severity"] == "high"]
        warn = [b for b in flagged if b["severity"] == "warning"]
        parts = []
        if high:
            parts.append(f"{len(high)} 个未翻译英文块（严重）")
        if warn:
            parts.append(f"{len(warn)} 个未翻译英文块（轻微）")
        summary = "；".join(parts)
    return {"blocks": flagged, "summary": summary}


# ── Page artifact filtering ────────────────────────────────────────────

_PAGE_ARTIFACT_PATTERNS = [
    re.compile(r"^(?:\d+\s+)?VOLUME\s+\d+,\s*\d{4}\s*(?:\d+)?\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^卷\s*\d+[,，]\s*\d{4}\s*\d*\s*$", re.MULTILINE),
    re.compile(r"^DOI:\s*10\.\S+\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^Digital\s+Object\s+Identifier\b.*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^arXiv:\s*\d{4}\.\d+\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(
        r"^[A-Z][A-Z\s]{3,}(?:et\s+al\.?)?\s*[：:]\s*[A-Z][A-Z\s,/-]{10,}\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    re.compile(
        r"^[A-Z][A-Z\s]{3,}(?:等)?[：:]\s*.{10,}\s*$",
        re.MULTILINE,
    ),
    # IEEE journal header: "48336 IEEE INTERNET OF THINGS JOURNAL, VOL. 12, NO. 22, 15 NOVEMBER 2025"
    re.compile(
        r"^\d+\s+IEEE\s+[\w\s]+(?:JOURNAL|TRANSACTIONS|LETTERS|MAGAZINE)\s*,?\s*VOL\.\s*\d+\s*,?\s*NO\.\s*\d+\s*,?\s*\d{1,2}\s+\w+\s+\d{4}\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # IEEE running header: "ZHANG et al.: JOINT TASK OFFLOADING ... 48337"
    re.compile(
        r"^[A-Z]{3,}(?:\s+et\s+al\.?)?\s*:\s*.+\s*\d{3,}\s*$",
        re.MULTILINE,
    ),
    # IEEE copyright line 1: "Authorized licensed use limited to: ..."
    re.compile(
        r"^Authorized\s+licensed\s+use\s+limited\s+to:\s*.+\.?\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # IEEE copyright line 2: "Downloaded on ... UTC from IEEE Xplore."
    re.compile(
        r"^Downloaded\s+on\s+.+?UTC\s+from\s+IEEE\s+Xplore\.?\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # IEEE copyright line 3: "Restrictions apply."
    re.compile(
        r"^Restrictions\s+apply\.?\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    # IEEE copyright/ISSN: "$2327-4662$ (c)2025 IEEE ..."
    re.compile(
        r"^\$?\d{4}[-–]\d{4}\$?\s*.+IEEE[,;]?\s*(?:Personal\s+use\s+is\s+permitted\b.*)?$",
        re.MULTILINE,
    ),
    # Sub-figure label orphans: "(a) (b) (c)" or "(a)-(c)"
    re.compile(
        r"^\s*(?:\([a-z]\)[\s,;]+)+(?:\([a-z]\))\s*$",
        re.MULTILINE,
    ),
    re.compile(
        r"^\s*\([a-z]\)\s*[-–—]\s*\([a-z]\)\s*$",
        re.MULTILINE,
    ),
]


def _is_page_number_line(line: str) -> bool:
    """Check if a line is likely a page number artifact."""
    stripped = line.strip()
    if not re.match(r"^\d{3,4}\s*$", stripped):
        return False
    num = int(stripped)
    return 100 <= num <= 9999


def _filter_page_artifacts_pre(text: str) -> str:
    """Remove journal metadata artifacts BEFORE translation (English text)."""
    # Multi-line IEEE watermark: collapse the entire block across line breaks
    text = re.sub(
        r"Authorized\s+licensed\s+use\s+limited\s+to:\s*.+?Restrictions\s+apply\.?",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for pat in _PAGE_ARTIFACT_PATTERNS:
        text = pat.sub("", text)
    lines = text.split("\n")
    filtered = []
    for i, line in enumerate(lines):
        if _is_page_number_line(line):
            prev_lower = lines[i - 1].strip().lower() if i > 0 else ""
            next_lower = lines[i + 1].strip().lower() if i + 1 < len(lines) else ""
            markers = ["volume", "ieee", "transactions", "journal", "vol.", "received", "accepted"]
            if any(m in prev_lower or m in next_lower for m in markers):
                continue
        filtered.append(line)
    return "\n".join(filtered)


def _filter_page_artifacts_post(text: str) -> str:
    """Remove journal metadata artifacts AFTER translation (Chinese text)."""
    # Multi-line Chinese IEEE watermark
    text = re.sub(
        r"授权许可使用仅限于：.+?限制适用[。.]?",
        "",
        text,
        flags=re.DOTALL,
    )
    # Single-line Chinese IEEE fragments
    text = re.sub(
        r"^授权许可使用仅限于：.+$",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^下载于\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*.+?UTC[,，]?\s*来自\s*IEEE\s*Xplore[。.]?\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^限制适用[。.]?\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    # Chinese IEEE journal header: "48336 IEEE 物联网杂志, 第12卷, 第22期, 2025年11月15日"
    text = re.sub(
        r"^\d+\s+IEEE\s+物联网杂志[,，]\s*第?\s*\d+\s*卷[,，]\s*第?\s*\d+\s*期[,，]\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    cn_header = re.compile(
        r"^[A-Z][A-Z\s]{3,}(?:等)?[：:]\s*.{8,}\s*$",
        re.MULTILINE,
    )
    text = cn_header.sub("", text)
    text = re.sub(r"^卷\s*\d+[,，]\s*\d{4}\s*\d*\s*$", "", text, flags=re.MULTILINE)
    lines = text.split("\n")
    filtered = []
    for i, line in enumerate(lines):
        if _is_page_number_line(line):
            prev_lower = lines[i - 1].strip().lower() if i > 0 else ""
            next_lower = lines[i + 1].strip().lower() if i + 1 < len(lines) else ""
            markers = ["volume", "ieee", "transactions", "journal", "vol.", "卷"]
            if any(m in prev_lower or m in next_lower for m in markers):
                continue
        filtered.append(line)
    return "\n".join(filtered)


# ── Diagram label garbage filter ───────────────────────────────────────

def _filter_diagram_label_garbage(text: str) -> str:
    """Remove garbled text extracted from diagram/figure labels.

    PDF extractors capture text labels from within figures (flowcharts,
    architecture diagrams, etc.) as regular prose. These produce lines like:
        "演员网络 / 评论家网络1 / 评论家网络2 / 目标演员网络"
        "更新 更新 / 更新 / 1, L / 2, L / 1 Q / 2 Q"
    which are not natural language and should be filtered pre-translation.
    """
    lines = text.split("\n")
    filtered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            filtered.append(line)
            continue

        # Heuristic 1: Slash-separated fragments (3+ segments, each ≤ 25 chars)
        slash_parts = [p.strip() for p in stripped.split("/") if p.strip()]
        if len(slash_parts) >= 3:
            all_short = all(len(p) <= 25 for p in slash_parts)
            has_sentence = bool(re.search(r"[.。!！?？]\s+[A-Z一-鿿]", stripped))
            if all_short and not has_sentence:
                continue

        # Heuristic 2: Repeated identical short tokens (e.g. "更新 更新")
        tokens = stripped.split()
        if 3 <= len(tokens) <= 6:
            seen: dict[str, int] = {}
            for t in tokens:
                seen[t] = seen.get(t, 0) + 1
            if max(seen.values()) >= 2:
                continue

        # Heuristic 3: Mixed CJK/ASCII token soup (5+ tokens, mostly short)
        if len(tokens) >= 5:
            short_count = sum(1 for t in tokens if len(t) <= 3)
            has_cjk = any(re.search(r"[一-鿿]", t) for t in tokens)
            has_ascii = any(re.match(r"^[a-zA-Z0-9,]+$", t) for t in tokens)
            no_punct = not re.search(r"[。.，,！!？?]", stripped)
            if short_count >= 4 and has_cjk and has_ascii and no_punct:
                continue

        filtered.append(line)

    return "\n".join(filtered)


# ── Deduplication ──────────────────────────────────────────────────────

def _normalize_for_dedup(para: str) -> str:
    """Normalize paragraph for deduplication comparison."""
    p = para.strip()
    p = re.sub(r"^#{1,4}\s*", "", p)
    p = re.sub(r"\$\$[\s\S]*?\$\$", "█MATH█", p)
    p = re.sub(r"\$[^$\n]*?\$", "█INLINE█", p)
    p = re.sub(r"◈[A-Z]+_\d+◈", "█PH█", p)
    p = re.sub(r"\s+", " ", p)
    p = re.sub(r"[，。！？、；：""''（）《》【】,.!?;:;\-]", "", p)
    return p.strip().lower()


_PLACEHOLDER_ONLY_RE = re.compile(r"^\s*◈\s*[A-Z]+\s*_\d+\s*◈\s*$")


def _deduplicate_paragraphs(text: str) -> str:
    """Detect and merge duplicate paragraphs within a 5-paragraph sliding window.

    Placeholder-only paragraphs (◈FIG_N◈ / ◈TBL_N◈) are always dedup-eligible
    regardless of length, preventing the LLM from spamming the same placeholder
    across consecutive empty lines.
    """
    paragraphs = text.split("\n\n")
    if len(paragraphs) < 2:
        return text

    kept: list[str] = []
    seen_hashes: dict[str, int] = {}
    dup_count = 0

    for i, para in enumerate(paragraphs):
        stripped = para.strip()
        if not stripped:
            kept.append(para)
            continue
        norm = _normalize_for_dedup(stripped)
        is_placeholder_only = bool(_PLACEHOLDER_ONLY_RE.match(stripped))
        if len(norm) < 20 and not is_placeholder_only:
            kept.append(para)
            seen_hashes[norm] = i
            continue

        dup_idx = seen_hashes.get(norm)
        if dup_idx is not None and (i - dup_idx) <= 5:
            dup_count += 1
            continue

        seen_hashes[norm] = i
        stale = [h for h, idx in seen_hashes.items() if i - idx > 5]
        for h in stale:
            del seen_hashes[h]
        kept.append(para)

    result = "\n\n".join(kept)
    if dup_count:
        result += f"\n\n> ⚠️ 翻译去重：已移除 {dup_count} 个疑似重复段落。\n"
    return result


async def translate_paper(
    full_text: str,
    provider: "LLMProvider",
    model: str,
    chunk_size: int = 3000,
    figures: list[dict] | None = None,
    tables: list[dict] | None = None,
    paper_id: str = "",
) -> tuple[str, list[str]]:
    """将英文论文全文翻译为中文，公式转为 LaTeX Markdown。

    按段落分块翻译，每块带前文摘要以保持连贯性（chunk_size=3000，防截断）。
    若提供 figures/tables 列表，翻译后自动将占位符替换为图片/表格引用。
    返回 (translated_text, chunk_log_entries)。
    """
    system_prompt = render_template("paper/translator_system.md", strip=True)
    if figures or tables:
        full_text, figures, tables = _normalize_placeholders(
            full_text, figures or [], tables or [],
        )
    full_text = _filter_page_artifacts_pre(full_text)
    full_text = _filter_diagram_label_garbage(full_text)
    paragraphs = _split_into_paragraphs(full_text)
    chunks = _build_chunks(paragraphs, chunk_size)

    translated_chunks: list[str] = []
    prev_summary = ""
    input_len = sum(len(c) for c, _ in chunks)
    chunk_log: list[str] = []

    for i, (chunk, overlap) in enumerate(chunks):
        prev_tail_en = ""
        if i > 0:
            prev_chunk_text = chunks[i - 1][0]
            sentences = re.split(r"(?<=[.])\s+", prev_chunk_text)
            prev_tail_en = " ".join(sentences[-2:]) if len(sentences) >= 2 else prev_chunk_text[-200:]
        user_msg = _build_chunk_message(chunk, overlap, prev_summary, i, len(chunks), prev_tail_en)
        req_tokens = min(max(6144, len(chunk) * 5), 131072)
        response = await provider.chat_with_retry(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            tools=None,
            max_tokens=req_tokens,
        )
        translated = response.content or ""
        translated = _strip_meta_commentary(translated)
        # Detect and retry untranslated English blocks
        eng_check = _detect_untranslated_english(translated)
        if eng_check["blocks"]:
            high_blocks = [b for b in eng_check["blocks"] if b["severity"] == "high"]
            if high_blocks:
                chunk_log.append(
                    f"chunk {i+1}/{len(chunks)} 英文未翻译块({len(high_blocks)}处严重)，"
                    f"追加指令重试..."
                )
                retry_msg = _build_chunk_message(
                    chunk, overlap, prev_summary, i, len(chunks), prev_tail_en,
                )
                retry_msg += (
                    "\n\n⚠️ 警告：上一个翻译结果中以下英文段落未被翻译。"
                    "请完整翻译所有内容，不得保留英文原文：\n"
                    + "\n---\n".join(b["snippet"] for b in high_blocks[:3])
                )
                try:
                    rr = await provider.chat_with_retry(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": retry_msg},
                        ],
                        tools=None,
                        max_tokens=req_tokens,
                    )
                    if rr.content and _detect_untranslated_english(rr.content)["blocks"]:
                        rerun = _detect_untranslated_english(rr.content)
                        if not any(b["severity"] == "high" for b in rerun["blocks"]):
                            translated = _strip_meta_commentary(rr.content)
                            chunk_log.append(f"chunk {i+1} 英文重试成功")
                        else:
                            chunk_log.append(f"chunk {i+1} 英文重试后仍存在未翻译块，已保留")
                    elif rr.content:
                        translated = _strip_meta_commentary(rr.content)
                        chunk_log.append(f"chunk {i+1} 英文重试成功")
                except Exception:
                    chunk_log.append(f"chunk {i+1} 英文重试异常，保留原文")
            else:
                chunk_log.append(f"chunk {i+1} 英文未翻译块({eng_check['summary']})，已保留")
        # Detect truncation: retry at 1/2 then 1/4 size.
        # EN→ZH translations are naturally 30-50% of the source character count,
        # so only lengths below 15% signal genuine truncation.
        finish = getattr(response, "finish_reason", "") or ""
        if finish == "length" or (len(translated) > 10 and len(translated) < len(chunk) * 0.15):
            for level, fraction in [(1, 2), (2, 4)]:
                chunk_log.append(f"chunk {i+1}/{len(chunks)} 截断，L{level}重试(1/{fraction})...")
                paras = chunk.split("\n\n")
                smaller = "\n\n".join(paras[:max(1, len(paras) // fraction)])
                if len(smaller) >= len(chunk) * 0.8:
                    break
                retry_msg = _build_chunk_message(smaller, overlap, prev_summary, i, len(chunks), prev_tail_en)
                try:
                    rr = await provider.chat_with_retry(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": retry_msg},
                        ],
                        tools=None,
                        max_tokens=min(max(6144, len(smaller) * 5), 131072),
                    )
                    if rr.content and len(rr.content) > len(translated) * 1.2:
                        translated = rr.content
                        chunk_log.append(f"chunk {i+1} L{level}重试成功 ({len(translated)} 字符)")
                        break
                except Exception:
                    chunk_log.append(f"chunk {i+1} L{level}重试异常")
        translated_chunks.append(translated)
        if i < len(chunks) - 1:
            prev_summary = _extract_key_points(translated)

    result = "\n\n".join(translated_chunks)
    result = _strip_meta_commentary(result)
    result = _filter_page_artifacts_post(result)
    result = _deduplicate_paragraphs(result)
    result = _dedup_chunk_boundaries(result)
    result = _validate_formulas(result)
    result = _merge_formula_fragments(result)
    # Strip control characters (except tab, LF, CR) that break MathJax rendering.
    # NUL and SOH bytes are common PDF extraction artifacts embedded in math blocks.
    result = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", result)
    result = _wrap_bare_latex_commands(result)
    if figures or tables:
        result = _embed_figures_tables(result, figures or [], tables or [], paper_id)
    result = _validate_translation_length(result, input_len)
    # Final English block check on the complete output
    final_eng = _detect_untranslated_english(result)
    if final_eng["summary"]:
        result += (
            f"\n\n> ⚠️ 翻译完整性警告：最终输出中仍存在 {final_eng['summary']}。"
            f" 请对比原文进行人工补译。\n"
        )
    return result, chunk_log


def _split_into_paragraphs(text: str) -> list[str]:
    raw = text.split("\n\n")
    result: list[str] = []
    buf = ""
    for para in raw:
        para = para.strip()
        if not para:
            continue
        # Section headers: short, possibly numbered, with keywords
        is_header = (
            len(para) < 80 and (
                para.isupper() or
                bool(SECTION_RE.search(para)) or
                para.startswith(("Abstract", "Introduction", "Related Work",
                    "Method", "Experiment", "Conclusion", "Reference",
                    "Acknowledg", "Appendix"))
            )
        )
        if is_header:
            if buf:
                result.append(buf)
                buf = ""
            result.append(para)
        elif len(para) < 60 and buf:
            buf += "\n" + para
        elif buf and len(buf) < 200:
            buf += "\n\n" + para
        else:
            if buf:
                result.append(buf)
            buf = para
    if buf:
        result.append(buf)
    return result


def _count_inline_dollars(text: str) -> int:
    """统计文本中单 $ 的数量（排除 $$ 对）。"""
    return text.count("$") - text.count("$$") * 2


def _safe_para_split(text: str, target: int) -> int:
    """在 target 附近找一个不在 $...$ 内的安全切分点。"""
    before = text[:target]
    if _count_inline_dollars(before) % 2 == 0:
        return target  # 不在 $...$ 中
    next_dollar = text.find("$", target)
    return next_dollar + 1 if next_dollar != -1 else target


def _dedup_chunk_boundaries(text: str) -> str:
    """Remove duplicated text at chunk boundaries caused by LLM re-translation of overlap context."""
    paragraphs = text.split("\n\n")
    if len(paragraphs) < 3:
        return text

    kept = [paragraphs[0]]
    for i in range(1, len(paragraphs)):
        prev_norm = _normalize_for_dedup(kept[-1])
        curr_norm = _normalize_for_dedup(paragraphs[i])
        if prev_norm and curr_norm and len(curr_norm) > 20:
            overlap_len = min(30, len(prev_norm), len(curr_norm))
            if prev_norm[-overlap_len:] == curr_norm[:overlap_len]:
                continue
        kept.append(paragraphs[i])

    return "\n\n".join(kept)


def _build_chunks(paragraphs: list[str], max_size: int) -> list[tuple[str, str]]:
    """构建翻译块列表，每块为 (chunk_text, overlap_text)。

    overlap_text 是上一块的最后一个段落，作为本块的上下文提示。
    """
    raw_chunks: list[str] = []
    current: list[str] = []
    cur_size = 0
    for para in paragraphs:
        ps = len(para)
        # Force-split oversized paragraphs to prevent max_tokens overflow
        if ps > max_size * 2:
            for start in range(0, ps, max_size):
                end = min(start + max_size, ps)
                if end < ps:
                    end = _safe_para_split(para, end)
                if current:
                    raw_chunks.append("\n\n".join(current))
                    current = []
                    cur_size = 0
                current.append(para[start:end])
                cur_size = len(para[start:end])
            continue
        if current and cur_size + ps > max_size:
            # Sentence-boundary-aware: avoid splitting mid-sentence across chunks.
            last_para = current[-1] if current else ""
            ends_sentence = bool(re.search(r"[.。!！?？]\s*$", last_para.strip()))
            if not ends_sentence and len(current) >= 2:
                rolled = current.pop()
                raw_chunks.append("\n\n".join(current))
                current = [rolled, para]
                cur_size = len(rolled) + ps
            else:
                raw_chunks.append("\n\n".join(current))
                current = [para]
                cur_size = ps
        else:
            current.append(para)
            cur_size += ps
    if current:
        raw_chunks.append("\n\n".join(current))

    # Post-process: merge chunks that split a $$...$$ formula block.
    # An odd number of $$ delimiters in a chunk means an unclosed display
    # math block — the formula would be split across the chunk boundary.
    merged: list[str] = []
    i = 0
    while i < len(raw_chunks):
        chunk = raw_chunks[i]
        dd_count = chunk.count("$$")
        if dd_count % 2 == 1 and i + 1 < len(raw_chunks):
            chunk = chunk + "\n\n" + raw_chunks[i + 1]
            i += 2
        else:
            i += 1
        merged.append(chunk)
    raw_chunks = merged

    # Second pass: merge chunks with unbalanced inline $...$ formulas.
    merged2: list[str] = []
    i = 0
    while i < len(raw_chunks):
        chunk = raw_chunks[i]
        inline_count = _count_inline_dollars(chunk)
        if inline_count % 2 == 1 and i + 1 < len(raw_chunks):
            chunk = chunk + "\n\n" + raw_chunks[i + 1]
            i += 2
        else:
            i += 1
        merged2.append(chunk)
    raw_chunks = merged2

    # Build chunk list: overlap context is handled via prev_summary (Chinese) only.
    # English overlap was causing LLMs to re-translate it, producing duplicate output.
    result: list[tuple[str, str]] = [(c, "") for c in raw_chunks]
    return result



def _build_chunk_message(
    chunk: str, overlap: str, prev: str, idx: int, total: int, prev_tail_en: str = "",
) -> str:
    parts = [f"## 翻译任务：第 {idx + 1}/{total} 部分\n"]
    if prev:
        parts.append(f"上文核心内容（已翻译，仅供上下文参考，无需重复翻译）：\n{prev}\n")
    if prev_tail_en:
        parts.append(
            f"上文末尾原文（仅供参考上下文衔接，**切勿重复翻译此段**）：\n{prev_tail_en}\n"
        )
    parts.append("请翻译以下英文论文内容为中文：\n")
    parts.append(chunk)
    return "\n".join(parts)


def _extract_key_points(text: str, max_len: int = 500) -> str:
    """提取翻译结果末尾的中文关键内容作为下一块的上下文摘要。"""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    key = [
        l for l in lines
        if not l.startswith("$$") and not l.startswith("◈")
        and not l.startswith("#") and len(l) > 15
        and any('一' <= c <= '鿿' for c in l)
    ]
    return " ".join(key[-6:])[:max_len]


def _count_formulas(text: str) -> int:
    """统计文本中 LaTeX 公式数量（行内 + 独立公式）。"""
    display = len(re.findall(r"\$\$", text)) // 2
    inline = len(re.findall(r"(?<!\$)\$(?!\$)[^$]+\$(?!\$)", text))
    return display + inline


def _balance_latex_braces(latex: str) -> str:
    """Balance curly braces in a LaTeX expression.

    Adds missing closing braces or removes extra closing braces.
    Correctly handles escaped braces (\\{, \\}) and double-backslash
    line breaks (\\\\{ = real brace, \\\\{ = escaped brace).
    """
    depth = 0
    i = 0
    chars = list(latex)
    while i < len(chars):
        if chars[i] == '\\' and i + 1 < len(chars) and chars[i + 1] in ('{', '}'):
            # Count consecutive backslashes ending at i: odd=escaped, even=real
            bs = 1
            j = i - 1
            while j >= 0 and chars[j] == '\\':
                bs += 1
                j -= 1
            if bs % 2 == 1:  # odd -> escaped brace, skip
                i += 2
                continue
            # even -> literal backslash + real brace, fall through
        if chars[i] == '{':
            depth += 1
        elif chars[i] == '}':
            depth -= 1
        i += 1
    while depth > 0:
        chars.append('}')
        depth -= 1
    # Remove extra } — try from end first, verify, then from start if needed
    while depth < 0:
        removed = False
        for j in range(len(chars) - 1, -1, -1):
            if chars[j] == '}' and not (
                j > 0 and chars[j - 1] == '\\'
                and (j < 2 or chars[j - 2] != '\\')
            ):
                chars.pop(j)
                depth += 1
                removed = True
                break
        if not removed:
            break
    return ''.join(chars)


def _merge_formula_fragments(text: str) -> str:
    """Post-translation: merge fragmented $...$ blocks into coherent formulas.

    Three passes:
      1. Merge adjacent $...$ blocks (gap ≤ 35, no sentence breaks)
      2. Promote display-like $...$ to $$...$$
      3. Fix subscript/superscript brace wrapping
    """
    if not text or "$" not in text:
        return text

    D = "$"

    # ── Pass 1: Iteratively merge adjacent $...$ blocks ──────────────
    def _should_merge_gap(gap: str) -> bool:
        """Check if the text between two $...$ blocks is math-like, not prose."""
        gap = gap.strip()
        if not gap:
            return True
        if len(gap) > 35:
            return False
        # Sentence-ending punctuation → hard boundary
        if re.search(r"[.。!！?？;:；:]", gap):
            return False
        # CJK characters → explanatory prose
        if re.search(r"[一-鿿㐀-䶿぀-ゟ가-힯]", gap):
            return False
        # 5+ space-separated English words → prose sentence
        words = gap.split()
        alpha_words = [w for w in words if re.search(r"[a-zA-Z]{3,}", w)]
        if len(alpha_words) >= 3 and len(words) >= 5:
            return False
        return True

    def _find_dollar_blocks(t: str) -> list:
        """Find all $...$ (inline) blocks in text. Returns [(start, end, inner), ...]."""
        blocks = []
        i = 0
        while i < len(t):
            if t[i] == "\\" and i + 1 < len(t) and t[i + 1] == "$":
                i += 2  # skip escaped \$
                continue
            if t[i:i + 2] == "$$":
                # Skip existing display math blocks
                end = t.find("$$", i + 2)
                i = end + 2 if end >= 0 else i + 2
                continue
            if t[i] == "$":
                j = i + 1
                while j < len(t):
                    if t[j] == "\\" and j + 1 < len(t) and t[j + 1] == "$":
                        j += 2
                        continue
                    if t[j] == "$" and (j + 1 >= len(t) or t[j + 1] != "$"):
                        blocks.append((i, j + 1, t[i + 1:j]))
                        break
                    j += 1
                i = j + 1 if j < len(t) else i + 1
            else:
                i += 1
        return blocks

    prev = None
    result = text
    while prev != result:
        prev = result
        blocks = _find_dollar_blocks(result)
        if len(blocks) < 2:
            break
        parts = []
        last_end = 0
        i = 0
        while i < len(blocks):
            start_i, end_i, inner_i = blocks[i]
            parts.append(result[last_end:start_i])
            if i + 1 < len(blocks):
                start_j, end_j, inner_j = blocks[i + 1]
                gap = result[end_i:start_j]
                if _should_merge_gap(gap):
                    merged_inner = inner_i + " " + result[end_i:start_j].strip() + " " + inner_j
                    merged_inner = " ".join(merged_inner.split())  # normalize whitespace
                    parts.append(D + merged_inner + D)
                    last_end = end_j
                    i += 2
                    continue
            parts.append(result[start_i:end_i])
            last_end = end_i
            i += 1
        parts.append(result[last_end:])
        result = "".join(parts)

    # ── Pass 2: Promote display-like $...$ to $$...$$ ────────────────
    def _should_promote(inner: str, after_text: str) -> bool:
        if re.search(
            r"\\(?:frac|dfrac|tfrac|sum|prod|int|iint|iiint|oint|sqrt|begin|lim|max|min|argmin|argmax|sup|inf|det|gcd|lcm)\b",
            inner,
        ):
            return True
        if "=" in inner and len(inner) > 50:
            return True
        if re.match(r"^\s*\(\d+[a-z]?\)", after_text):
            return True
        return False

    blocks2 = _find_dollar_blocks(result)
    if blocks2:
        parts2 = []
        last_end2 = 0
        for start_i, end_i, inner_i in blocks2:
            parts2.append(result[last_end2:start_i])
            after = result[end_i:end_i + 20] if end_i < len(result) else ""
            if _should_promote(inner_i, after):
                parts2.append("\n\n$$" + inner_i.strip() + "$$\n\n")
            else:
                parts2.append(D + inner_i + D)
            last_end2 = end_i
        parts2.append(result[last_end2:])
        result = "".join(parts2)

    # ── Pass 3: Fix multi-char subscript/superscript braces ─────────
    def _fix_sub_sup_braces(m: re.Match) -> str:
        sep = "$$" if m.group(0).startswith("$$") else "$"
        inner = m.group(1) if m.lastindex else m.group(0)[len(sep):-len(sep)]
        # Strip control characters that break MathJax (NUL, SOH, etc.)
        inner = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", inner)
        # Escape TeX special characters that break MathJax rendering
        inner = inner.replace("#", r"\#").replace("%", r"\%")
        inner = re.sub(r"(?<!\\)~(?!textasciitilde)", r"\\textasciitilde{}", inner)
        # _X or _XY... → _{X} or _{XY...}
        inner = re.sub(r"(?<!\\)_([a-zA-Z][a-zA-Z0-9,.]{0,10})(?![\w{])", r"_{\1}", inner)
        # ^X or ^XY... → ^{X} or ^{XY...}
        inner = re.sub(r"(?<!\\)\^([a-zA-Z][a-zA-Z0-9,.]{0,10})(?![\w{])", r"^{\1}", inner)
        return sep + inner + sep

    result = re.sub(r"\$\$(.+?)\$\$", _fix_sub_sup_braces, result, flags=re.DOTALL)
    result = re.sub(r"(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)", _fix_sub_sup_braces, result)
    return result


def _validate_formulas(translated: str) -> str:
    """修复常见 LaTeX 语法错误，平衡括号，检测未闭合公式块。"""
    if not translated:
        return translated
    fixed = translated
    # Fix 1: Missing backslash before math commands embedded in text
    fixed = re.sub(r"(?<![\\a-zA-Z])(mathbf|mathcal|mathbb|mathit|mathrm|boldsymbol)\{", r"\\\1{", fixed)
    # Fix 3: Unescaped _ and ^ outside $...$ (convert to LaTeX subscript/superscript)
    # Only fix within formula blocks (between $...$ or $$...$$)
    # Fix 4: Multi-char subscripts inside $...$ — ensure braces via callback
    def _fix_subs(m: re.Match) -> str:
        inner = m.group(1)
        inner = re.sub(r"_([a-zA-Z]{2,})(?![{a-zA-Z])", r"_{\1}", inner)
        return "$" + inner + "$"
    fixed = re.sub(r"\$([^$]+?)\$", _fix_subs, fixed)
    # Fix 5: Balance braces and escape stray & in each formula block
    _ALIGN_ENVS = (
        r'align|aligned|matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix'
        r'|cases|array|gather|gathered|split|eqnarray'
    )
    def _fix_block(m: re.Match) -> str:
        sep = m.group(0)[:2] if m.group(0).startswith('$$') else '$'
        inner = m.group(1) if m.lastindex else m.group(0)[len(sep):-len(sep)]
        # Escape stray & not inside alignment environments
        if '&' in inner and not re.search(
            rf'\\begin\{{(?:{_ALIGN_ENVS})\*?\}}', inner
        ):
            inner = inner.replace('&', r'\&')
        balanced = _balance_latex_braces(inner)
        # Strip \tag{...} from aligned/gathered/split — MathJax rejects it here
        if re.search(r'\\begin\{(aligned|gathered|split)\*?\}', balanced):
            balanced = re.sub(r'\\tag\{[^}]*\}', '', balanced)
        return sep + balanced + sep
    fixed = re.sub(r"\$\$(.+?)\$\$", _fix_block, fixed, flags=re.DOTALL)
    fixed = re.sub(r"(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)", _fix_block, fixed)
    # Also escape & inside \[...\] and \(...\) (MathJax processEscapes delimiters)
    def _fix_bracket_block(m: re.Match) -> str:
        inner = m.group(1)
        if '&' in inner and not re.search(
            rf'\\begin\{{(?:{_ALIGN_ENVS})\*?\}}', inner
        ):
            inner = inner.replace('&', r'\&')
        balanced = _balance_latex_braces(inner)
        return r'\[' + balanced + r'\]'
    fixed = re.sub(r"\\\[(.+?)\\\]", _fix_bracket_block, fixed, flags=re.DOTALL)
    def _fix_paren_block(m: re.Match) -> str:
        inner = m.group(1)
        if '&' in inner:
            inner = inner.replace('&', r'\&')
        balanced = _balance_latex_braces(inner)
        return r'\(' + balanced + r'\)'
    fixed = re.sub(r"\\\((.+?)\\\)", _fix_paren_block, fixed)
    # Fix 6 (new): Detect corrupted set notation ($&$ instead of \mathcal{N})
    _CORRUPTED_SET_RE = re.compile(r"\$&?\$|&?\$&?\$")
    corrupted_sets = _CORRUPTED_SET_RE.findall(fixed)
    if corrupted_sets:
        fixed += (
            f"\n\n> ⚠️ LaTeX 损坏警告：检测到 {len(corrupted_sets)} 处疑似损坏的集合符号 "
            f"（`$&$` 或类似模式），这些应为 `\\mathcal{{N}}` 等命令。"
            f" 请对比原文中的集合符号进行修复。\n"
        )

    # Detect stray commas inside math subscripts/superscripts
    _STRAY_COMMA_IN_SUB_RE = re.compile(r"[_{^]\{[^}]*,[^}]*\}")
    stray_commas = _STRAY_COMMA_IN_SUB_RE.findall(fixed)
    if stray_commas:
        unique = list(set(stray_commas))[:5]
        fixed += (
            f"\n\n> ⚠️ LaTeX 格式问题：检测到 {len(stray_commas)} 处下标/上标内含有"
            f" 多余逗号（如 `{unique[0]}`），请手动检查并移除。\n"
        )

    # Detect broken display math: $$= ... -$$ pattern (garbled formula)
    _BROKEN_DISPLAY = re.compile(r"\$\$\s*=\s*.{0,100}?\-\s*\$\$")
    broken = _BROKEN_DISPLAY.findall(fixed)
    if broken:
        fixed += (
            f"\n\n> ⚠️ LaTeX 公式损坏警告：检测到 {len(broken)} 处"
            f" 可能已损坏的独立公式（`$$=` 或 `-$$` 模式），请手动对比原文修复。\n"
        )

    # Check unbalanced $$ display math
    display_count = fixed.count("$$")
    if display_count % 2 != 0:
        fixed = fixed.rstrip() + "\n\n> ⚠️ 警告：翻译后存在未闭合的 `$$` 公式块，请手动检查。"
    # Check unbalanced $ inline math
    inline_count = len(re.findall(r"(?<!\$)\$(?!\$)", fixed))
    if inline_count % 2 != 0:
        fixed = fixed.rstrip() + "\n\n> ⚠️ 警告：翻译后存在未闭合的行内 `$` 公式，请手动检查。"
    return fixed


def _validate_translation_length(output: str, input_len: int) -> str:
    """检测翻译后内容是否显著短于原文（可能漏翻），若差异过大则附加警告。"""
    out_len = len(output)
    # English text roughly 1.5-2x more characters than equivalent Chinese.
    # If output is less than 40% of input length, something was likely skipped.
    if input_len > 500 and out_len < input_len * 0.4:
        pct = round(out_len / max(input_len, 1) * 100)
        output += (
            f"\n\n> ⚠️ 翻译完整性警告：输出长度 ({out_len} 字符) "
            f"仅为原文 ({input_len} 字符) 的 {pct}%，可能存在漏翻。"
        )
    return output


_UNWRAPPED_MATH_CMDS = re.compile(
    r"(?<![$\\])\\(mathbf|mathcal|mathbb|boldsymbol|mathit|mathrm|mathsf|mathtt|operatorname)\{[^}]+\}(?!\$)"
)


def _wrap_bare_latex_commands(text: str) -> str:
    """将翻译后未包裹的 LaTeX 数学命令（如 \\mathbf{X}）加上 $...$。

    仅处理明确不会出现在自然语言中的数学字体命令。
    已有 $...$ 或 $$...$$ 包裹的内容会被保护后跳过。
    """
    math_blocks: list[str] = []

    def _protect(m: re.Match) -> str:
        math_blocks.append(m.group(0))
        return f"M{len(math_blocks) - 1}"

    text = re.sub(r"\$\$[\s\S]*?\$\$", _protect, text)
    text = re.sub(r"(?<!\$)\$[^$\n]+?\$(?!\$)", _protect, text)
    text = _UNWRAPPED_MATH_CMDS.sub(r"$&$", text)
    for i, block in enumerate(math_blocks):
        text = text.replace(f"M{i}", block)
    return text


def _embed_figures_tables(text: str, figures: list[dict], tables: list[dict], paper_id: str = "") -> str:
    """将 ◈FIG_N◈ / ◈TBL_N◈ 不透明占位符替换为 Markdown 图片/表格引用。

    每个占位符仅替换 FIRST 出现。后续重复出现视为 LLM 幻觉/复制，在清理阶段移除。
    """
    import re
    from pathlib import Path as _Path

    for fig in figures:
        idx = fig.get("index", 0)
        opq = fig.get("opaque_placeholder") or fig.get("placeholder", f"◈FIG_{idx}◈")
        caption = fig.get("caption", "")
        if not opq:
            continue
        # Fuzzy match: LLM may slightly corrupt the placeholder during translation
        fuzzy = rf"◈\s*F\s*I\s*G\s*_{idx}\s*◈"
        found = re.search(fuzzy, text)
        if not found:
            continue
        actual_placeholder = found.group(0)
        img_path = fig.get("image_path", "")
        img_exists = bool(img_path and _Path(img_path).exists())
        rel = fig.get("image_rel_path", "")
        img_filename = rel.replace("figures/", "") if rel else f"figure_{idx}.png"
        if paper_id and img_exists:
            img_url = f"/api/paper/{paper_id}/figures/{img_filename}"
            replacement = (
                f'\n<div style="text-align:center;margin:16px 0">'
                f'<img src="{img_url}" alt="图{idx}" style="max-width:100%;border-radius:8px"'
                f' onerror="var d=document.createElement(\'div\');'
                f'd.textContent=\'⚠️ 图{idx} 图片加载失败 — 请查看PDF原文\';'
                f'd.style.cssText=\'padding:16px;color:#ff8c42;border:1px dashed #ff8c42;'
                f'border-radius:8px;text-align:center;font-size:14px\';'
                f'this.replaceWith(d)">'
                f'</div>\n'
            )
        elif paper_id:
            replacement = (
                f'\n<div style="padding:12px 16px;margin:12px 0;'
                f'border-left:3px solid #ff8c42;background:rgba(255,140,66,0.08);'
                f'border-radius:0 8px 8px 0;font-size:14px;color:#ccc">'
                f'🖼 <strong>图{idx}</strong>'
                f'{": " + caption if caption else ""}'
                f' <span style="color:#ff8c42">（图片未导出）</span>'
                f'</div>\n'
            )
        else:
            replacement = f"\n> 🖼 **图{idx}**：{caption}\n"
        text = text.replace(actual_placeholder, replacement, 1)

    for tbl in tables:
        idx = tbl.get("index", 0)
        ph = tbl.get("placeholder", f"◈TBL_{idx}◈")
        md_table = tbl.get("markdown", "")
        if md_table:
            replacement = f"\n**表{idx}**\n\n{md_table}\n"
        else:
            replacement = f"\n> **表{idx}**\n"
        text = text.replace(ph, replacement, 1)

    # Validate: warn if any figure/table placeholders were lost in translation
    # (check BEFORE cleanup so we don't mistake orphan-cleanup for loss)
    lost_figs = [str(f["index"]) for f in figures
                 if re.search(rf"◈FIG_{f['index']}◈", text)]
    lost_tbls = [str(t["index"]) for t in tables
                 if re.search(rf"◈TBL_{t['index']}◈", text)]
    warnings = []
    if lost_figs:
        warnings.append(f"图{','.join(lost_figs)}（占位符丢失）")
    if lost_tbls:
        warnings.append(f"表{','.join(lost_tbls)}（占位符丢失）")

    # Clean up orphaned placeholder duplicates (LLM hallucinations / repeated copies)
    orphan_pattern = re.compile(r"\s*◈\s*[A-Z]+\s*_\d+\s*◈\s*")
    orphan_matches = orphan_pattern.findall(text)
    if orphan_matches:
        text = orphan_pattern.sub("\n\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

    if warnings:
        text += "\n\n> ⚠️ 翻译后以下图表占位符丢失: " + "；".join(warnings)
    return text
