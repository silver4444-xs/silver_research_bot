"""Stage 2: 公式逐条解读 — HTML 卡片格式输出

数据源优先级:
  1. 英文论文: 从 translation.md 提取 $$...$$ 完整公式 → 系统解读
  2. 中文论文/回退: 从 extracted.json 提取公式片段 → 逐条解读
  3. 最终回退: 原始全文 → LLM 自主识别
"""

from __future__ import annotations

import re as _re
from typing import TYPE_CHECKING

from silver_research_bot.paper_analyzer.extractor import _is_valid_formula, _COMPLETE_FORMULA_RE, _merge_nearby_dollar_blocks
from silver_research_bot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

FORMULA_CSS = """<style>
.sec-title{font-size:13px;font-weight:500;color:var(--color-text-secondary);letter-spacing:.04em;padding:1.5rem 0 .5rem;border-top:.5px solid var(--color-border-tertiary);margin-top:.5rem}
.sec-title:first-of-type{border-top:none;padding-top:.5rem}
.frow{display:grid;grid-template-columns:56px 1fr;gap:0;border:.5px solid var(--color-border-tertiary);border-radius:var(--border-radius-md);margin-bottom:8px;overflow:hidden;background:var(--color-background-primary)}
.frow:hover{border-color:var(--color-border-secondary)}
.fnum{display:flex;align-items:center;justify-content:center;background:var(--color-background-secondary);font-size:12px;font-weight:500;color:var(--color-text-secondary);border-right:.5px solid var(--color-border-tertiary);padding:10px 6px;min-height:52px}
.fbody{padding:10px 14px}
.ftag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px;font-weight:500;margin-bottom:5px}
.tag-sys{background:#E6F1FB;color:#0C447C}
.tag-mdp{background:#EEEDFE;color:#3C3489}
.tag-alg{background:#FAEEDA;color:#633806}
.tag-rwd{background:#FAECE7;color:#712B13}
.tag-gat{background:#E1F5EE;color:#085041}
.tag-obs{background:#FBEAF0;color:#72243E}
.fexpr{font-family:var(--font-mono);font-size:12px;color:var(--color-text-secondary);margin-bottom:4px;opacity:.85}
.fmean{font-size:13px;color:var(--color-text-primary);line-height:1.55}
.fmean b{font-weight:500}
</style>"""


def _strip_fragment_cards(html: str) -> str:
    import re
    def _is_fragment(c):
        c = c.strip()
        if not c:
            return True
        # Placeholder artifacts from PDF processing
        if "◈" in c:
            return True
        # LaTeX commands → structured math
        if "\\" in c:
            return False
        # Math operators, sub/superscript → structured math
        if re.search(r'[_^=+<>≤≥≠≈≡∈⊂⊆∪∩∫∑∏∇∂]', c):
            return False
        # Strip notation chars to isolate alphabetic content
        alpha_only = re.sub(r'[\s,.;:()\[\]{}|0-9+\-*/×⋅±]', '', c)
        # Pure numeric, empty, or single letter → not a formula
        if not alpha_only or len(alpha_only) <= 1:
            return True
        # Short alpha-only (≤3 chars, no operators) → variable name fragment
        if len(alpha_only) <= 3 and re.match(r'^[a-zA-Z]+$', alpha_only):
            return True
        # 3+ English words without math notation → prose, not a formula
        words = c.split()
        if len(words) >= 3:
            alpha_words = [w for w in words if re.match(r'^[a-zA-Z]{2,}$', w)]
            if len(alpha_words) >= 3:
                return True
        return False
    result, parts = [], re.split(r'(<div class="frow">)', html)
    i = 0
    while i < len(parts):
        if parts[i] == '<div class="frow">' and i + 1 < len(parts):
            cc = parts[i + 1]
            fm = re.search(r'<div class="fexpr">([\s\S]*?)</div>', cc)
            if fm and _is_fragment(fm.group(1)):
                i += 1; depth, j = 1, 0
                while j < len(cc) and depth > 0:
                    no = cc.find('<div', j); nc = cc.find('</div>', j)
                    if nc >= 0 and (no < 0 or nc < no):
                        depth -= 1
                        if depth == 0:
                            r = cc[nc + 6:]
                            if r.strip(): result.append(r)
                            break
                        j = nc + 6
                    elif no >= 0:
                        depth += 1; j = no + 4
                    else: break
            else:
                result.append('<div class="frow">'); result.append(cc)
            i += 1
        else:
            result.append(parts[i])
        i += 1
    return ''.join(result)


def _wrap_html(body: str) -> str:
    import re
    body = re.sub(r'^```html?\s*\n?', '', body.strip())
    body = re.sub(r'\n?```\s*$', '', body)
    body = _strip_fragment_cards(body)
    body = _balance_fmean_braces(body)
    body = _balance_fexpr_braces(body)
    if '<style>' not in body: body = FORMULA_CSS + '\n' + body
    if '<h2 class="sr-only"' not in body: body = '<h2 class="sr-only">论文公式逐条解析</h2>\n' + body
    return body


def _balance_fmean_braces(html: str) -> str:
    """Balance braces inside $...$ math within .fmean divs.

    Also escapes unmatched $ signs that would cause MathJax to treat
    subsequent prose as math, leading to "Extra close brace" errors.
    """
    import re

    def _fix_fmean(m: re.Match) -> str:
        prefix, body, suffix = m.group(1), m.group(2), m.group(3)

        # Step 0: Escape unmatched $ signs that would break MathJax.
        # An unmatched opening $ causes everything after it to be parsed
        # as LaTeX math, and any } in prose triggers "Extra close brace".
        body = _escape_unmatched_dollars(body)

        def _fix_dollar(dm: re.Match) -> str:
            content = dm.group(1)
            balanced = _balance_braces(content)
            return "$" + balanced + "$"

        body = re.sub(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", _fix_dollar, body)
        return prefix + body + suffix

    return re.sub(
        r"(<div class=\"fmean\">)([\s\S]*?)(</div>)",
        _fix_fmean,
        html,
    )


def _escape_unmatched_dollars(text: str) -> str:
    """Ensure $ signs form valid pairs to prevent MathJax errors.

    Unmatched $ delimiters cause MathJax to treat subsequent prose as math,
    turning innocent braces into "Extra close brace or missing open brace" errors.

    Strategy: single $ count (ignoring $$ pairs) must be even. If odd,
    escape the last single $ that isn't part of a $$ block.
    """
    # Count single $ by removing valid $$ pairs first
    cleaned = text.replace('$$', '')
    single_count = cleaned.count('$')
    if single_count % 2 == 0:
        return text

    # Odd count: find the rightmost single $ (not part of $$) and escape it
    i = len(text) - 1
    while i >= 0:
        if text[i] == '$':
            # Check if this $ is part of a $$ pair
            if i + 1 < len(text) and text[i + 1] == '$':
                i -= 2  # Skip $$ pair
                continue
            if i > 0 and text[i - 1] == '$':
                i -= 2  # Skip $$ pair
                continue
            # Found a single $ — escape it
            return text[:i] + '\\$' + text[i + 1:]
        i -= 1
    return text


def _balance_fexpr_braces(html: str) -> str:
    """Balance braces directly in .fexpr divs (pure LaTeX, no $...$ wrapping)."""
    import re

    def _fix_fexpr(m: re.Match) -> str:
        prefix, latex, suffix = m.group(1), m.group(2), m.group(3)
        balanced = _balance_braces(latex.strip())
        return prefix + balanced + suffix

    return re.sub(
        r"(<div class=\"fexpr\">)([\s\S]*?)(</div>)",
        _fix_fexpr,
        html,
    )


def _balance_braces(latex: str) -> str:
    depth, i, chars = 0, 0, list(latex)
    while i < len(chars):
        if chars[i] == '\\' and i + 1 < len(chars) and chars[i + 1] in ('{', '}'):
            bs, j = 1, i - 1
            while j >= 0 and chars[j] == '\\': bs += 1; j -= 1
            if bs % 2 == 1: i += 2; continue
        if chars[i] == '{': depth += 1
        elif chars[i] == '}': depth -= 1
        i += 1
    while depth > 0: chars.append('}'); depth -= 1
    while depth < 0:
        removed = False
        for j in range(len(chars) - 1, -1, -1):
            if chars[j] == '}' and not (j > 0 and chars[j - 1] == '\\' and (j < 2 or chars[j - 2] != '\\')):
                chars.pop(j); depth += 1; removed = True; break
        if not removed: break
    return ''.join(chars)


def _merge_equation_fragments(text: str) -> str:
    """Merge fragmented $lhs$ = $rhs$ patterns into single $...$ blocks.

    Before: $\varpi$ t = $\frac{...}{...}$ (5)  or  acct ( $\varpi$ t|S ) = $\frac{...}{...}$ (6)
    After:  $\varpi t = \frac{...}{...}$ (5)   or   $acct ( \varpi t|S ) = \frac{...}{...}$ (6)
    """
    def _merge_line(line):
        # Pattern: optional-prefix $lhs$ gap = $rhs$ → merge into single $ block
        # Captures up to 40 non-$ chars before first $ for LHS context (e.g. "acct (")
        return _re.sub(
            r'([^$\n]{0,40}?)\$([^$]+)\$\s*'
            r'([^$\n]{0,30}?)\s*=\s*'
            r'\$([^$]+)\$',
            lambda m: (
                '$' + _re.sub(r'[^\x00-\x7F\\_{}^$]', '', m.group(1).strip()) + ' ' + m.group(2) + ' '
                + m.group(3).strip() + ' = '
                + m.group(4) + '$'
            ),
            line,
        )
    lines = text.split('\n')
    return '\n'.join(_merge_line(line) for line in lines)


def _promote_display_math(text: str) -> str:
    """Promote standalone $...$ to display $$...$$ based on structural context only."""
    def _replace(m):
        inner, start, end_pos = m.group(1), m.start(), m.end()
        # English prose detection: 5+ letter sequences WITHOUT LaTeX → don't promote
        if '\\' not in inner:
            if len(_re.findall(r'[a-zA-Z]{2,}', inner)) >= 5:
                return m.group(0)
        # Content-based promotion: structural LaTeX commands → always display math
        if _re.search(r'\\(?:frac|dfrac|tfrac|sum|prod|int|iint|iiint|oint|sqrt|begin|lim|max|min|argmin|argmax|sup|inf|det|gcd|lcm)\b', inner):
            return "$$" + inner + "$$"
        # Long formulas → display math
        if len(inner) > 80:
            return "$$" + inner + "$$"
        # Standalone on its own line → display math
        ls = text.rfind("\n", 0, start) + 1; le = text.find("\n", end_pos)
        if le == -1: le = len(text)
        if not text[ls:start].strip() and not text[end_pos:le].strip(): return "$$" + inner + "$$"
        # Followed by equation number (N) → display math
        after = text[end_pos:end_pos + 10].strip()
        if _re.match(r"^\(\d+[a-z]?\)", after): return "$$" + inner + "$$"
        # Contains alignment/multi-line environments → display math
        if _re.search(r'\\begin\{(?:aligned|align|gather|array|cases|split)', inner):
            return "$$" + inner + "$$"
        # Contains LaTeX line breaks (\\\\) → display math
        if '\\\\' in inner:
            return "$$" + inner + "$$"
        return m.group(0)
    return _re.sub(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", _replace, text)


def _is_substantial_math(latex: str) -> bool:
    """Inline $...$ is a real formula, not a lone LaTeX symbol like \\varpi or $x$."""
    if not latex or len(latex) < 5:
        return False

    # Equation structure (operators / relations) -> always substantial
    if _re.search(r'[=+<>≤≥≠≈≡∈⊂⊆∪∩∫∑∏∇∂⋅×±−]', latex):
        return True

    # Subscript/superscript -> substantial
    if _re.search(r'[_^]', latex):
        return True

    # Strip LaTeX commands + notation, measure remaining content
    stripped = _re.sub(r'\\[a-zA-Z]+(\{[^}]*\})*', ' ', latex)
    stripped = _re.sub(r'[\s,.;:()\[\]{}|−]', '', stripped)

    if not stripped:
        # Only LaTeX commands remain — reject lone \varpi, \epsilon, \in{}
        cmds = _re.findall(r'\\[a-zA-Z]+', latex)
        if len(cmds) == 1:
            structural = {'frac', 'dfrac', 'tfrac', 'sqrt', 'binom', 'sum', 'prod',
                          'int', 'iint', 'iiint', 'oint', 'begin'}
            return cmds[0][1:] in structural
        return len(cmds) >= 2

    return len(stripped) >= 1


def extract_formulas_from_translation(translation_text: str) -> list[dict]:
    # Step 0a: Merge fragmented $ blocks (sub/superscript spans between math spans)
    text = _merge_nearby_dollar_blocks(translation_text)
    # Step 0b: Merge fragmented equation pairs ($lhs$ = $rhs$)
    text = _merge_equation_fragments(text)
    # Step 1: Promote standalone $...$ to $$...$$ based on structural context
    text = _promote_display_math(text)
    formulas, idx = [], 0
    blocks = []
    # Extract display formulas ($$...$$ and \[...\])
    for m in _re.finditer(r"\$\$(.+?)\$\$", text, _re.DOTALL):
        blocks.append({"start": m.start(), "end": m.end(), "latex": m.group(1).strip()})
    for m in _re.finditer(r"\\\[(.+?)\\\]", text, _re.DOTALL):
        blocks.append({"start": m.start(), "end": m.end(), "latex": m.group(1).strip()})
    # Also extract substantial inline $...$ formulas (embedded in prose after merge)
    for m in _re.finditer(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", text):
        inner = m.group(1).strip()
        if len(inner) < 5: continue
        if "◈" in inner: continue
        if not _is_substantial_math(inner): continue
        blocks.append({"start": m.start(), "end": m.end(), "latex": inner})
    for block in blocks:
        latex = block["latex"]
        if not latex or len(latex) < 5: continue
        if "◈" in latex: continue
        # Reject formulas containing CJK characters — explanatory prose contamination.
        # Strip \text{...} first — CJK inside LaTeX \text{} commands is legitimate.
        _cjk_check = _re.sub(r'\\text\{[^}]*\}', '', latex)
        if _re.search(r'[一-鿿㐀-䶿豈-﫿぀-ゟ゠-ヿ가-힯]', _cjk_check): continue
        # Gate through unified validator (same as PDF extraction path)
        if not _is_valid_formula(latex): continue
        eq_num = None
        tm = _re.search(r"\\tag\{(\d+[a-z]?)\}", latex)
        if tm: eq_num = tm.group(1); latex = _re.sub(r"\\tag\{\d+[a-z]?\}", "", latex).strip()
        latex = _balance_braces(latex)
        # Minimal filtering for translation path (translation is authoritative source)
        # Only filter obvious English prose, not formula content
        if '\\' not in latex:
            # Hyphen connecting two alpha words → English compound (e-mail, AoI-Aware)
            if _re.search(r'[-—–]', latex):
                parts = _re.split(r'[-—–]', latex.replace(" ", ""))
                if '' not in parts:
                    ap = [_re.sub(r'[^a-zA-Z]', '', p) for p in parts]
                    ap = [a for a in ap if len(a) >= 2]
                    if len(ap) >= 2: continue
            # 5+ two-letter sequences without LaTeX → English prose
            if len(_re.findall(r'[a-zA-Z]{2,}', latex)) >= 5: continue
        # English prose word blacklist — LLM may accidentally output English in $$ blocks
        # Strip \text{...} blocks first to avoid rejecting legitimate formulas
        _cleaned = _re.sub(r'\\text\{[^}]*\}', '', latex)
        if _re.search(r"\b(?:the|and|for|are|was|not|but|all|has|had|have|can|may|our|their|this|that|with|from|they|were|been|will|would|using|based|given|shown|found|used|made|taken|seen|said|proposed|defined|described|obtained|derived|computed|method|system|model|result|paper|figure|table|section|problem|approach|algorithm|scheme|strategy|technique|performance|simulation|experiment|analysis|scenario|respectively|therefore|however|moreover|furthermore|denotes|represents|indicates|corresponds|follows|satisfies|line|lines|Algorithm|Run|respect|regard|terms|order)\b", _cleaned, _re.IGNORECASE): continue
        if not eq_num:
            post = text[block["end"]:block["end"] + 20]
            tr = _re.match(r"^\s*\((\d+[a-z]?)\)", post)
            if tr: eq_num = tr.group(1)
        ctx_start = max(0, block["start"] - 120)
        ctx_end = min(len(text), block["end"] + 30)
        context = (text[ctx_start:block["start"]].strip()[-80:] + " " + text[block["end"]:ctx_end].strip()[:30]).strip()
        idx += 1
        formulas.append({"index": idx, "latex": latex, "equation_number": eq_num, "context": context})
    return formulas


async def extract_formulas_from_raw_text(
    full_text: str, provider: "LLMProvider", model: str
) -> list[dict]:
    """LLM extracts and normalizes complete $$...$$ formulas from raw paper text.

    Used for non-English papers that skip translation — the LLM reconstructs
    complete formulas from PDF fragments, fixing subscript/superscript artifacts
    just like the translation path does for English papers.
    """
    sp = render_template("paper/formula_extractor.md", strip=True)
    chunk_size, overlap = 8000, 500
    all_formulas: list[dict] = []
    seen_latex: set[str] = set()
    idx = 0

    st = 0
    while st < len(full_text):
        chunk = full_text[st:st + chunk_size]
        um = f"从以下论文章节提取所有完整的数学公式（$$...$$ 格式）：\n\n{chunk}"
        try:
            r = await provider.chat_with_retry(
                model=model,
                messages=[{"role": "system", "content": sp}, {"role": "user", "content": um}],
                tools=None,
            )
            if r.content:
                for m in _re.finditer(r"\$\$(.+?)\$\$", r.content, _re.DOTALL):
                    latex = m.group(1).strip()
                    if len(latex) < 5:
                        continue
                    if "◈" in latex:
                        continue
                    norm = _re.sub(r'\s+', ' ', latex).strip()
                    if norm in seen_latex:
                        continue
                    seen_latex.add(norm)
                    if not _is_valid_formula(latex):
                        continue
                    idx += 1
                    all_formulas.append({
                        "index": idx, "latex": latex,
                        "equation_number": None, "context": chunk[:200],
                    })
        except Exception:
            pass
        st += chunk_size - overlap
        if st >= len(full_text):
            break

    return all_formulas


async def explain_formulas(
    formulas: list[dict], full_text: str, provider: "LLMProvider", model: str,
    batch_size: int = 8, translation_text: str | None = None,
) -> str:
    if translation_text:
        tf = extract_formulas_from_translation(translation_text)
        if tf: return await _explain_translation_formulas(tf, full_text, provider, model, batch_size)
        return _wrap_html("""<p class="sec-title">公式检测结果</p><div class="frow"><div class="fnum">!</div><div class="fbody"><div class="fmean">翻译中未检测到展示公式（$$...$$），或所有公式均被过滤。建议检查翻译产物中公式的质量。</div></div>""")
    # Non-English papers: use LLM to extract normalized formulas from raw text,
    # then explain them through the same structured path as the translation branch.
    if not translation_text and formulas:
        raw_formulas = await extract_formulas_from_raw_text(full_text, provider, model)
        if raw_formulas:
            return await _explain_translation_formulas(raw_formulas, full_text, provider, model, batch_size)
    if not formulas:
        return _wrap_html(await _explain_from_text(full_text, provider, model))
    valid_formulas = [f for f in formulas if _is_valid_formula(f.get("latex", ""))]
    if not valid_formulas: return _wrap_html(await _explain_from_text(full_text, provider, model))
    sp = render_template("paper/formula_explainer.md", strip=True)
    batches = [valid_formulas[i:i + batch_size] for i in range(0, len(valid_formulas), batch_size)]
    parts = []
    for i, batch in enumerate(batches):
        ft = "\n\n".join(f"公式{f['index']} (仅解释此LaTeX，必须完整复制到fexpr): {f.get('latex', '')}\n(参考上下文，不解释): {f.get('context', '')[:200]}" for f in batch)
        um = f"## 第 {i + 1}/{len(batches)} 批公式\n\n重要：仅解释「公式N (仅解释此LaTeX)」列中的数学表达式。「(参考上下文，不解释)」列仅供理解公式来源，其中的英文单词不是公式，不要解释。\n每个公式的 LaTeX 已标注「必须完整复制到fexpr」，请原样复制到 fexpr 中，禁止截断或修改。\n\n请按 HTML 卡片格式逐条解释：\n\n{ft}"
        r = await provider.chat_with_retry(model=model, messages=[{"role": "system", "content": sp}, {"role": "user", "content": um}], tools=None)
        if r.content: parts.append(r.content)
    return _wrap_html("\n\n".join(parts))


async def _explain_translation_formulas(
    formulas: list[dict], full_text: str, provider: "LLMProvider", model: str,
    batch_size: int = 8,
) -> str:
    if not formulas: return _wrap_html('<p class="sec-title">公式检测结果</p><div class="frow"><div class="fnum">!</div><div class="fbody"><div class="fmean">翻译路径提取的公式均被二次过滤拦截，建议检查翻译产物中公式区块的质量。</div></div>')
    sp = render_template("paper/formula_explainer.md", strip=True)
    batches = [formulas[i:i + batch_size] for i in range(0, len(formulas), batch_size)]
    parts = []
    for i, batch in enumerate(batches):
        ft = "\n\n".join(f"公式 {f['index']} (编号: {f.get('equation_number', '?')})\n【⚠️必须完整复制到fexpr，禁止截断】: {f['latex']}\n上下文: {f.get('context', '')[:150]}" for f in batch)
        um = f"## 第 {i + 1}/{len(batches)} 批公式 (来自全文翻译，共{len(formulas)}个显示公式)\n\n请对以下完整数学公式进行系统解读。每个公式的 LaTeX 已标注【必须完整复制到fexpr】，请原样复制，不要修改或截断。\n\n{ft}"
        r = await provider.chat_with_retry(model=model, messages=[{"role": "system", "content": sp}, {"role": "user", "content": um}], tools=None)
        if r.content: parts.append(r.content)
    return _wrap_html("\n\n".join(parts))


async def _explain_from_text(full_text: str, provider: "LLMProvider", model: str) -> str:
    sp = render_template("paper/formula_explainer.md", strip=True)
    cs, ov, parts, ci = 12000, 500, [], 0
    st = 0
    while st < len(full_text):
        ch = full_text[st:st + cs]; ci += 1
        um = f"以下是论文第{ci}部分。请识别文中所有数学公式。\n\n【严格筛选规则】\n- 数学公式必须包含 LaTeX 命令（\\frac, \\sum 等）、数学运算符（=, +, ×, ≤, ±, ∫ 等）、希腊字母（α, β, Σ, Ω 等）、上下标（^, _）中的至少一种\n- 纯英文单词（如 computation, aerial, method, proposed）不是公式，禁止提取\n- 【关键】带连字符的英文复合词（如 AoI-Aware, Inter-UAV, Computing-Enabled, state-of-the-art, non-convex, semi-definite）是英文词组，不是数学公式，禁止提取\n- 单独的数学符号（如单个 - 或 +）不是完整公式，禁止提取\n- 变量名（如 x, y, N, T）单独出现不算公式，须伴随数学运算符或表达式才可提取\n- 不确定时宁可跳过，不要强行提取\n\n按 HTML 卡片格式逐条解释含义，按类别分组，公式表达式不要加 $ 包裹，直接输出 HTML：\n\n---\n\n" + ch
        try:
            r = await provider.chat_with_retry(model=model, messages=[{"role": "system", "content": sp}, {"role": "user", "content": um}], tools=None)
            if r.content: parts.append(r.content)
        except Exception: pass
        st += cs - ov
        if st >= len(full_text): break
    return _wrap_html("\n\n".join(parts))
