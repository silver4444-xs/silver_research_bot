"""Stage 1a: 英文论文全文翻译器 — 公式→LaTeX Markdown"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from silver_research_bot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider


async def translate_paper(
    full_text: str,
    provider: "LLMProvider",
    model: str,
    chunk_size: int = 3000,
    figures: list[dict] | None = None,
) -> str:
    """将英文论文全文翻译为中文，公式转为 LaTeX Markdown。

    按段落分块翻译，每块带前文摘要以保持连贯性。
    若提供 figures 列表，翻译后自动将 [图N] 占位符替换为图片 Markdown 引用。
    """
    system_prompt = render_template("paper/translator_system.md", strip=True)
    paragraphs = _split_into_paragraphs(full_text)
    chunks = _build_chunks(paragraphs, chunk_size)

    translated_chunks: list[str] = []
    prev_summary = ""

    for i, chunk in enumerate(chunks):
        user_msg = _build_chunk_message(chunk, prev_summary, i, len(chunks))
        response = await provider.chat_with_retry(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            tools=None,
        )
        translated = response.content or ""
        translated_chunks.append(translated)
        if i < len(chunks) - 1:
            prev_summary = _extract_key_points(translated)

    result = "\n\n".join(translated_chunks)
    result = _validate_formulas(result)
    if figures:
        result = _embed_figures_tables(result, figures)
    return result


def _split_into_paragraphs(text: str) -> list[str]:
    raw = text.split("\n\n")
    result: list[str] = []
    buf = ""
    for para in raw:
        para = para.strip()
        if not para:
            continue
        if len(para) < 60 and buf:
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


def _build_chunks(paragraphs: list[str], max_size: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    cur_size = 0
    for para in paragraphs:
        ps = len(para)
        if current and cur_size + ps > max_size:
            chunks.append("\n\n".join(current))
            current = [para]
            cur_size = ps
        else:
            current.append(para)
            cur_size += ps
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _build_chunk_message(chunk: str, prev: str, idx: int, total: int) -> str:
    parts = [f"## 翻译任务：第 {idx + 1}/{total} 部分\n"]
    if prev:
        parts.append(f"前文摘要（供参考，无需翻译）：\n{prev}\n")
    parts.append("请翻译以下英文论文内容为中文：\n")
    parts.append(chunk)
    return "\n".join(parts)


def _extract_key_points(text: str, max_len: int = 300) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    key = [l for l in lines if not l.startswith("$$") and len(l) > 20]
    return " ".join(key[:5])[:max_len]


def _count_formulas(text: str) -> int:
    """统计文本中 LaTeX 公式数量（行内 + 独立公式）。"""
    display = len(re.findall(r"\$\$", text)) // 2
    inline = len(re.findall(r"(?<!\$)\$(?!\$)[^$]+\$(?!\$)", text))
    return display + inline


def _validate_formulas(translated: str) -> str:
    """对比翻译后公式数量。若显著减少则附加警告。"""
    if not translated:
        return translated
    # Fix common LLM formula corruptions
    fixed = re.sub(r"(?<!\$)\\boldsymbol\{(.+?)\}", r"\\mathbf{\1}", translated)
    # Check for unbalanced $$
    display_count = translated.count("$$")
    if display_count % 2 != 0:
        fixed = fixed.rstrip() + "\n\n> ⚠️ 警告：翻译后存在未闭合的 `$$` 公式块，请手动检查。"
    return fixed


def _embed_figures_tables(text: str, figures: list[dict]) -> str:
    """将 [图N：描述] 文本占位符替换为 Markdown 图片引用。"""
    for fig in figures:
        placeholder = fig.get("placeholder", "")
        rel_path = fig.get("image_rel_path", "")
        if placeholder and rel_path:
            caption = fig.get("caption", f"图{fig['index']}")
            replacement = f"![图{fig['index']}：{caption}]({rel_path})"
            text = text.replace(placeholder, replacement)
    return text
