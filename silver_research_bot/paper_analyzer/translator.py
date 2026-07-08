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


async def translate_paper(
    full_text: str,
    provider: "LLMProvider",
    model: str,
    chunk_size: int = 2000,
    figures: list[dict] | None = None,
    tables: list[dict] | None = None,
    paper_id: str = "",
) -> str:
    """将英文论文全文翻译为中文，公式转为 LaTeX Markdown。

    按段落分块翻译，每块带前文摘要以保持连贯性（chunk_size=2000，防截断）。
    若提供 figures/tables 列表，翻译后自动将占位符替换为图片/表格引用。
    """
    system_prompt = render_template("paper/translator_system.md", strip=True)
    paragraphs = _split_into_paragraphs(full_text)
    chunks = _build_chunks(paragraphs, chunk_size)

    translated_chunks: list[str] = []
    prev_summary = ""
    input_len = sum(len(c) for c, _ in chunks)
    chunk_log: list[str] = []

    for i, (chunk, overlap) in enumerate(chunks):
        user_msg = _build_chunk_message(chunk, overlap, prev_summary, i, len(chunks))
        req_tokens = max(4096, len(chunk) * 4)
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
        # Detect truncation: retry at 1/2 then 1/4 size
        finish = getattr(response, "finish_reason", "") or ""
        if finish == "length" or (len(translated) > 10 and len(translated) < len(chunk) * 0.3):
            for level, fraction in [(1, 2), (2, 4)]:
                chunk_log.append(f"chunk {i+1}/{len(chunks)} 截断，L{level}重试(1/{fraction})...")
                paras = chunk.split("\n\n")
                smaller = "\n\n".join(paras[:max(1, len(paras) // fraction)])
                if len(smaller) >= len(chunk) * 0.8:
                    break
                retry_msg = _build_chunk_message(smaller, overlap, prev_summary, i, len(chunks))
                try:
                    rr = await provider.chat_with_retry(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": retry_msg},
                        ],
                        tools=None,
                        max_tokens=max(4096, len(smaller) * 4),
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
    result = _validate_formulas(result)
    if figures or tables:
        result = _embed_figures_tables(result, figures or [], tables or [], paper_id)
    result = _validate_translation_length(result, input_len)
    if chunk_log:
        result = "\n\n> 📋 翻译日志:\n> " + "\n> ".join(chunk_log) + "\n\n" + result
    return result


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


def _build_chunks(paragraphs: list[str], max_size: int) -> list[tuple[str, str]]:
    """构建翻译块列表，每块为 (chunk_text, overlap_text)。

    overlap_text 是上一块的最后一个段落，作为本块的上下文提示。
    """
    raw_chunks: list[str] = []
    current: list[str] = []
    cur_size = 0
    for para in paragraphs:
        ps = len(para)
        if current and cur_size + ps > max_size:
            raw_chunks.append("\n\n".join(current))
            current = [para]
            cur_size = ps
        else:
            current.append(para)
            cur_size += ps
    if current:
        raw_chunks.append("\n\n".join(current))

    # Build overlap pairs: chunk N gets last paragraph of chunk N-1 as context
    result: list[tuple[str, str]] = [(raw_chunks[0], "")]
    for i in range(1, len(raw_chunks)):
        prev_paras = raw_chunks[i - 1].split("\n\n")
        overlap = prev_paras[-1] if prev_paras else ""
        result.append((raw_chunks[i], overlap))
    return result



def _build_chunk_message(chunk: str, overlap: str, prev: str, idx: int, total: int) -> str:
    parts = [f"## 翻译任务：第 {idx + 1}/{total} 部分\n"]
    if overlap:
        parts.append(f"上文末段（已翻译，仅作上下文参考，无需重复翻译）：\n{overlap}\n")
    if prev:
        parts.append(f"前文整体摘要（供参考，无需翻译）：\n{prev}\n")
    parts.append("请翻译以下英文论文内容为中文：\n")
    parts.append(chunk)
    return "\n".join(parts)


def _extract_key_points(text: str, max_len: int = 300) -> str:
    """提取翻译结果末尾的关键内容作为下一块的上下文摘要。"""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    key = [l for l in lines if not l.startswith("$$") and not l.startswith("◈") and len(l) > 20]
    # Use the last few meaningful lines (better context for next chunk)
    return " ".join(key[-5:])[:max_len]


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


def _validate_formulas(translated: str) -> str:
    """修复常见 LaTeX 语法错误，平衡括号，检测未闭合公式块。"""
    if not translated:
        return translated
    fixed = translated
    # Fix 1: Missing backslash before math commands embedded in text
    fixed = re.sub(r"(?<![\\a-zA-Z])(mathbf|mathcal|mathbb|mathit|mathrm)\{", r"\\\1{", fixed)
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
        return sep + balanced + sep
    fixed = re.sub(r"\$\$(.+?)\$\$", _fix_block, fixed, flags=re.DOTALL)
    fixed = re.sub(r"(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)", _fix_block, fixed)
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


def _embed_figures_tables(text: str, figures: list[dict], tables: list[dict], paper_id: str = "") -> str:
    """将 ◈FIG_N◈ / ◈TBL_N◈ 不透明占位符替换为 Markdown 图片/表格引用。"""
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
        actual_placeholder = found.group(0) if found else opq
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
        text = text.replace(actual_placeholder, replacement)

    for tbl in tables:
        idx = tbl.get("index", 0)
        ph = tbl.get("placeholder", f"◈TBL_{idx}◈")
        md_table = tbl.get("markdown", "")
        if md_table:
            replacement = f"\n**表{idx}**\n\n{md_table}\n"
        else:
            replacement = f"\n> **表{idx}**\n"
        text = text.replace(ph, replacement)

    # Validate: warn if any figure/table placeholders were lost in translation
    lost_figs = [str(f["index"]) for f in figures
                 if re.search(rf"◈FIG_{f['index']}◈", text)]
    lost_tbls = [str(t["index"]) for t in tables
                 if re.search(rf"◈TBL_{t['index']}◈", text)]
    warnings = []
    if lost_figs:
        warnings.append(f"图{','.join(lost_figs)}（占位符丢失）")
    if lost_tbls:
        warnings.append(f"表{','.join(lost_tbls)}（占位符丢失）")
    if warnings:
        text += "\n\n> ⚠️ 翻译后以下图表占位符丢失: " + "；".join(warnings)
    return text
