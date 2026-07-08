"""Stage 0: PDF 文档解析器 — 提取文本、公式区域和章节结构"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

# ── 数学符号 → LaTeX 映射表 ──────────────────────────────────────────
SYMBOL_TO_LATEX: dict[int, str] = {
    # Greek lowercase
    0x03B1: r"\alpha", 0x03B2: r"\beta", 0x03B3: r"\gamma",
    0x03B4: r"\delta", 0x03B5: r"\epsilon", 0x03B6: r"\zeta",
    0x03B7: r"\eta", 0x03B8: r"\theta", 0x03B9: r"\iota",
    0x03BA: r"\kappa", 0x03BB: r"\lambda", 0x03BC: r"\mu",
    0x03BD: r"\nu", 0x03BE: r"\xi", 0x03C0: r"\pi",
    0x03C1: r"\rho", 0x03C3: r"\sigma", 0x03C4: r"\tau",
    0x03C5: r"\upsilon", 0x03C6: r"\phi", 0x03C7: r"\chi",
    0x03C8: r"\psi", 0x03C9: r"\omega",
    # Greek uppercase
    0x0393: r"\Gamma", 0x0394: r"\Delta", 0x0398: r"\Theta",
    0x039B: r"\Lambda", 0x039E: r"\Xi", 0x03A0: r"\Pi",
    0x03A3: r"\Sigma", 0x03A6: r"\Phi", 0x03A8: r"\Psi",
    0x03A9: r"\Omega",
    # Greek variants
    0x03D1: r"\vartheta", 0x03D5: r"\varphi", 0x03D6: r"\varpi",
    0x03F1: r"\varrho", 0x03F5: r"\epsilon",
    # Mathematical operators not in Greek block
    0x2206: r"\Delta", 0x2212: "-",
    # Operators
    0x2200: r"\forall", 0x2201: r"\complement", 0x2202: r"\partial",
    0x2203: r"\exists", 0x2204: r"\nexists", 0x2205: r"\emptyset",
    0x2207: r"\nabla", 0x2208: r"\in", 0x2209: r"\notin",
    0x220B: r"\ni", 0x220F: r"\prod", 0x2210: r"\coprod",
    0x2211: r"\sum", 0x2217: r"\ast", 0x2218: r"\circ",
    0x2219: r"\bullet", 0x221A: r"\sqrt{}", 0x221D: r"\propto",
    0x221E: r"\infty", 0x2220: r"\angle", 0x2225: r"\parallel",
    0x2227: r"\wedge", 0x2228: r"\vee", 0x2229: r"\cap",
    0x222A: r"\cup", 0x222B: r"\int", 0x222C: r"\iint",
    0x222D: r"\iiint", 0x222E: r"\oint", 0x2234: r"\therefore",
    0x2235: r"\because", 0x223C: r"\sim", 0x2245: r"\cong",
    0x2248: r"\approx", 0x2260: r"\neq", 0x2261: r"\equiv",
    0x2264: r"\leq", 0x2265: r"\geq", 0x226A: r"\ll",
    0x226B: r"\gg", 0x227A: r"\prec", 0x227B: r"\succ",
    0x227C: r"\preceq", 0x227D: r"\succeq", 0x2282: r"\subset",
    0x2283: r"\supset", 0x2286: r"\subseteq", 0x2287: r"\supseteq",
    0x2295: r"\oplus", 0x2296: r"\ominus", 0x2297: r"\otimes",
    0x2298: r"\oslash", 0x2299: r"\odot", 0x22C5: r"\cdot",
    0x22C6: r"\star", 0x22C8: r"\bowtie",
    # Arrows
    0x2190: r"\leftarrow", 0x2191: r"\uparrow", 0x2192: r"\rightarrow",
    0x2193: r"\downarrow", 0x2194: r"\leftrightarrow",
    0x21D0: r"\Leftarrow", 0x21D1: r"\Uparrow",
    0x21D2: r"\Rightarrow", 0x21D3: r"\Downarrow",
    0x21D4: r"\Leftrightarrow",
    # Binary ops / relations
    0x00B1: r"\pm", 0x00D7: r"\times", 0x00F7: r"\div",
    0x22A5: r"\bot", 0x22A4: r"\top", 0x22C0: r"\bigwedge",
    0x22C1: r"\bigvee", 0x22C2: r"\bigcap", 0x22C3: r"\bigcup",
    # Blackboard
    0x2115: r"\mathbb{N}", 0x2119: r"\mathbb{P}", 0x211A: r"\mathbb{Q}",
    0x211D: r"\mathbb{R}", 0x2124: r"\mathbb{Z}", 0x2102: r"\mathbb{C}",
}

MATH_FONT_PATTERNS = [
    re.compile(r"CM(MI|SY|EX|BX|R|TT|SS|IT)", re.IGNORECASE),
    re.compile(r"Math", re.IGNORECASE),
    re.compile(r"Cambria\s*Math", re.IGNORECASE),
    re.compile(r"Symbol", re.IGNORECASE),
    re.compile(r"XITS", re.IGNORECASE),
    re.compile(r"STIX", re.IGNORECASE),
    re.compile(r"Asana", re.IGNORECASE),
    re.compile(r"Libertinus", re.IGNORECASE),
    re.compile(r"MT(?:Pro|Extra)", re.IGNORECASE),
]

SECTION_PATTERNS = [
    re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:Abstract|摘要)$", re.IGNORECASE),
    re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:Introduction|引言|绪论)$", re.IGNORECASE),
    re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:Related\s+Work|相关工作)$", re.IGNORECASE),
    re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:System\s+Model|系统模型)$", re.IGNORECASE),
    re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:Problem\s+Formulation|问题表述)$", re.IGNORECASE),
    re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:Experiment|实验|仿真)"),
    re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:Conclusion|总结|结论)$", re.IGNORECASE),
    re.compile(r"^(?:\d+(?:\.\d+)*\s+)?[A-Z][A-Za-z\s]{2,50}$"),
]

FORMULA_MARKERS = [
    re.compile(r"\b(min|max|argmin|argmax|sup|inf|lim|det|tr|s\.t\.)\b"),
    re.compile(r"\\begin\{[a-z]+\}"),
    re.compile(r"\\[a-zA-Z]+(\{[^}]*\})*"),
    re.compile(r"[-=+*/<>≤≥≠≈≡∈⊂⊆∪∩∫∑∏∮∇∂⋅×±−]"),
    re.compile(r"[Α-ωϑϕϖϱϵ∆]"),
    re.compile(r"\b(?:subject\s+to|for\s+all|there\s+exists)\b"),
    re.compile(r"\\mathbf|\\mathcal|\\mathbb|\\boldsymbol|\\mathit|\\mathrm|\\mathsf|\\mathtt"),
]


def _is_math_font(font_name: str) -> bool:
    if not font_name:
        return False
    return any(p.search(font_name) for p in MATH_FONT_PATTERNS)


def _unicode_to_latex(char: str) -> str:
    code = ord(char)
    if code in SYMBOL_TO_LATEX:
        return SYMBOL_TO_LATEX[code]
    return char


# Canonical regex for LaTeX math commands & Unicode math symbols — single source of truth
# NOTE: ASCII operators (= + -) are NOT here — they belong in FORMULA_MARKERS/ops regex
# BS+BS = literal backslash in compiled regex; BS+'b' = word boundary in compiled regex
_BS = "\\"
_COMPLETE_FORMULA_RE = re.compile(
    # Unicode math operators & relations
    r"[≤≥≠≈≡∝∼∈⊂⊆→⇒]"
    r"|[−×⋅±∇∂∆]"  # Unicode: MINUS, TIMES, DOT, PLUS-MINUS, NABLA, PARTIAL, INCREMENT
    # LaTeX relations
    "|" + _BS + _BS + "leq|" + _BS + _BS + "geq|" + _BS + _BS + "neq|"
    + _BS + _BS + "approx|" + _BS + _BS + "equiv|" + _BS + _BS + "propto|"
    + _BS + _BS + "sim|" + _BS + _BS + "simeq"
    + "|" + _BS + _BS + "triangleq|" + _BS + _BS + "doteq|"
    + _BS + _BS + "mapsto|" + _BS + _BS + "implies|" + _BS + _BS + "iff|"
    + _BS + _BS + "colon"
    # LaTeX arithmetic
    + "|" + _BS + _BS + "times|" + _BS + _BS + "cdot|" + _BS + _BS + "pm|"
    + _BS + _BS + "mp|" + _BS + _BS + "div|" + _BS + _BS + "ast|"
    + _BS + _BS + "star|" + _BS + _BS + "circ"
    + "|" + _BS + _BS + "oplus|" + _BS + _BS + "ominus|" + _BS + _BS + "otimes|"
    + _BS + _BS + "odot|" + _BS + _BS + "oslash"
    # LaTeX structural
    + "|" + _BS + _BS + "frac|" + _BS + _BS + "dfrac|" + _BS + _BS + "tfrac"
    + "|" + _BS + _BS + "sum|" + _BS + _BS + "prod|" + _BS + _BS + "coprod"
    + "|" + _BS + _BS + "int|" + _BS + _BS + "iint|" + _BS + _BS + "iiint|" + _BS + _BS + "oint"
    + "|" + _BS + _BS + "sqrt|" + _BS + _BS + "binom"
    + "|" + _BS + _BS + "begin" + _BS + "{"
    + "|" + _BS + _BS + "over" + _BS + "b"
    # LaTeX math functions
    + "|" + _BS + _BS + "min|" + _BS + _BS + "max|"
    + _BS + _BS + "argmin|" + _BS + _BS + "argmax|"
    + _BS + _BS + "sup|" + _BS + _BS + "inf"
    + "|" + _BS + _BS + "lim|" + _BS + _BS + "det|"
    + _BS + _BS + "gcd|" + _BS + _BS + "lcm"
    + "|" + _BS + _BS + "sin|" + _BS + _BS + "cos|"
    + _BS + _BS + "tan|" + _BS + _BS + "cot|"
    + _BS + _BS + "sec|" + _BS + _BS + "csc"
    + "|" + _BS + _BS + "arcsin|" + _BS + _BS + "arccos|" + _BS + _BS + "arctan"
    + "|" + _BS + _BS + "log|" + _BS + _BS + "ln|" + _BS + _BS + "exp"
    + "|" + _BS + _BS + "dim|" + _BS + _BS + "ker|"
    + _BS + _BS + "deg|" + _BS + _BS + "arg|" + _BS + _BS + "mod"
    + "|" + _BS + _BS + "Pr|" + _BS + _BS + "mathbb" + _BS + "{E" + _BS + "}"
    # LaTeX math symbols
    + "|" + _BS + _BS + "partial|" + _BS + _BS + "nabla|" + _BS + _BS + "Delta|" + _BS + _BS + "Box"
)


def _looks_like_formula(text: str) -> bool:
    if not text.strip():
        return False
    stripped = text.strip()

    score = 0
    has_greek = False
    for i, marker in enumerate(FORMULA_MARKERS):
        if marker.search(text):
            score += 1
            if i == 4:
                has_greek = True

    # Multiple math indicators → definitely math
    if score >= 2:
        # Reject English compound words: hyphen connecting 2+ long alpha words
        # that only trigger keyword (marker[0]) + hyphen (marker[3])
        if re.search(r'[-—–]', stripped):
            parts = re.split(r'[-—–]', stripped)
            if '' not in parts:
                alpha_parts = [p for p in parts if re.sub(r'[^a-zA-Z]', '', p)]
                if len(alpha_parts) >= 2:
                    max_len = max(len(re.sub(r'[^a-zA-Z]', '', p)) for p in alpha_parts)
                    if max_len >= 3 and not has_greek and not re.search(r'[_^{}\\]', stripped):
                        return False
        return True

    # Greek letters are inherently mathematical, regardless of length
    if has_greek:
        return True

    # Single indicator: check if it's just a hyphen/dash in English text
    if score == 1:
        # Pure math operator with no letters → always math (=, +, ×, −, etc.)
        if re.match(r'^[=+*/<>≤≥≠≈≡∈⊂⊆∪∩∫∑∏∮∇∂⋅×±−]+$', stripped):
            return True
        # If the only indicator is a hyphen or dash, check for English compound words
        if re.search(r'[-—–]', stripped):
            # Spaces around hyphen → math operator (max - min)
            if not re.search(r'\s[-—–]\s', stripped):
                parts = re.split(r'[-—–]', stripped)
                # Trailing/leading hyphen: opera-, -mail
                if '' in parts:
                    return False
                # Alpha on BOTH sides with one long = English compound (e-mail, AoI-Aware)
                # Short-alpha both sides = math (x-y). Digit on one side = math (TIt-1)
                alpha_parts = [p for p in parts if p.replace(' ', '').isalpha()]
                if len(alpha_parts) >= 2:
                    max_len = max(len(p.replace(' ', '')) for p in alpha_parts)
                    if max_len >= 3:
                        return False
        # Non-hyphen single indicator (e.g. ∆t containing a Greek letter): needs length ≥ 2
        if len(stripped) >= 2:
            return True

    return any(c in text for c in "∑∏∫∂∇")


# CJK Unicode ranges for prose contamination checks
_CJK_CHARS_RE = re.compile(r'[一-鿿㐀-䶿豈-﫿぀-ゟ゠-ヿ가-힯]')
# Trailing binary/relational operator: formula is incomplete (RHS missing).
# Excludes + and - when preceded by ^ or _ (superscript/subscript: x^+, x_-)
_TRAILING_OP_RE = re.compile(r'(?:[=<>≤≥≠≈≡∈⊂⊆]|(?<![_^])[+−\-])\s*$')
# Leading binary/relational operator: formula is a fragment (LHS missing)
_LEADING_OP_RE = re.compile(r'^\s*[=+−\-<>≤≥≠≈≡∈⊂⊆]')
# Generic LaTeX command (backslash + 2+ letters) — catches \varphi, \emptyset, etc.
# that are not in the curated _COMPLETE_FORMULA_RE whitelist
_GENERIC_LATEX_RE = re.compile(r'\\[a-zA-Z]{2,}')


def _is_valid_formula(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 3:
        return False

    # Reject merge artifacts: $ inside the extracted content means two
    # $...$ blocks were incorrectly fused by _merge_nearby_dollar_blocks
    if '$' in stripped:
        return False

    # Reject any formula containing CJK characters — these are prose
    # fragments that got accidentally merged into $...$ blocks
    if _CJK_CHARS_RE.search(stripped):
        return False

    # Reject formulas ending with a bare binary/relational operator
    # (= + - ≤ ≥ etc.) — the RHS was stripped away, this is a fragment
    if _TRAILING_OP_RE.search(stripped):
        return False

    # Reject formulas starting with a bare binary/relational operator
    # (= \emptyset, + x, ≤ R) — the LHS was stripped away, this is a fragment
    if _LEADING_OP_RE.search(stripped):
        return False

    has_sub_sup = bool(re.search(r'[_^]', stripped))
    has_latex = bool(_COMPLETE_FORMULA_RE.search(stripped))  # Canonical LaTeX check
    has_any_latex = bool(_GENERIC_LATEX_RE.search(stripped))  # \varphi, \emptyset, etc.
    has_greek = bool(re.search(r'[Α-ωϑϕϖϱϵ∆]', stripped))
    has_math_kw = bool(re.search(r'\b(min|max|argmin|argmax|sup|inf|lim|det|tr|s\.t\.)\b', stripped))
    ops = re.findall(r'[-=+*/<>≤≥≠≈≡∈⊂⊆∪∩∫∑∏∮∇∂⋅×±−]', stripped)

    math_signals = sum([has_sub_sup, has_latex, has_any_latex, has_greek, has_math_kw, len(ops) >= 1])
    if math_signals < 1:
        return False

    # Reject lone LaTeX symbol: \phi, \varphi without equation structure
    if (has_latex or has_any_latex) and math_signals == 1 and not has_sub_sup and not has_greek:
        cmds = re.findall(r'\\[a-zA-Z]+', stripped)
        args = re.findall(r'\{[^}]*\}', stripped)
        if len(cmds) >= 1 and len(args) == 0:
            # Check if significant non-LaTeX content exists beyond the commands
            rest = re.sub(r'\\[a-zA-Z]+', '', stripped).strip()
            if not rest:
                return False  # Only LaTeX command(s) with no other content

    # Reject English compound words with hyphens or dashes
    if re.search(r'[-—–]', stripped):
        if not re.search(r'\s[-—–]\s', stripped):
            parts = re.split(r'[-—–]', stripped)
            if '' in parts:
                return False
            # Extract alpha-only content from each part (strips parens, colons, etc.)
            def _alpha(s):
                return re.sub(r'[^a-zA-Z]', '', s)
            # Any alpha on both sides + one long = English compound
            # (e-mail, (MEC)-enabled, (e-mail:)
            alpha_parts = [_alpha(p) for p in parts if _alpha(p)]
            if len(alpha_parts) >= 2:
                max_len = max(len(a) for a in alpha_parts)
                if max_len >= 3 and math_signals == 1 and not has_sub_sup:
                    return False

    # Reject prose: 3+ letter sequences >= 3 without sub/sup.
    # Strip LaTeX commands first — \frac, \partial etc. are math, not English.
    _no_latex = re.sub(r'\\[a-zA-Z]+(\{[^}]*\})*', ' ', stripped)
    letter_seqs = re.findall(r'[a-zA-Z]{3,}', _no_latex)
    if len(letter_seqs) >= 3 and math_signals <= 2 and not has_sub_sup:
        return False

    # Reject garbled short-token soup: = D t nyt n,n type fragments
    tokens = stripped.split()
    short_tokens = [t for t in tokens if len(t) <= 2]
    # Threshold ≥4: "x + y" (3 short tokens) is a valid simple formula
    if len(short_tokens) >= 4 and len(ops) <= 1 and not has_sub_sup:
        return False

    # Reject prose-like formulas: ops + short tokens only, no LaTeX/sub/sup.
    # Only reject "many short tokens with few operators" prose patterns.
    # Simple "x + y" / "x = y" are valid formulas; leading/trailing operator
    # checks catch truly incomplete fragments like "= \emptyset" and "x =".
    if not has_sub_sup and not has_latex and not has_greek:
        non_op_tokens = [t for t in tokens if t not in {
            '=', '+', '-', '−', '×', '⋅', '<', '>', '≤', '≥', '∈', '⊂', '⊆',
        }]
        short_count = sum(1 for t in non_op_tokens if len(t) <= 2)
        if short_count >= 4 and len(ops) < len(tokens) * 0.35:
            return False

    # Reject lone generic LaTeX symbol with garbled suffix:
    # "\varphi t", "\varphi t n,mdt n,m n (1)" — only signal is \varphi,
    # the rest is fragment soup. Allow structured formulas like "x \in [0, 2\pi]"
    # where brackets contain real math (numbers, operators).
    if math_signals == 1 and has_any_latex and not has_latex and not has_sub_sup and not has_greek:
        no_cmd = re.sub(r'\\[a-zA-Z]+', '', stripped).strip()
        no_cmd_tokens = no_cmd.split()
        # 0-1 tokens after stripping LaTeX → lone symbol + fragment: "\varphi t"
        if len(no_cmd_tokens) <= 1:
            return False
        # Has balanced brackets with math content → structured: "x \in [0, 2\pi]"
        if no_cmd.count('[') == no_cmd.count(']') and no_cmd.count('(') == no_cmd.count(')'):
            outside = re.sub(r'\[[^\]]*\]|\([^\)]*\)', '', no_cmd).strip()
            if outside:
                outside_tokens = outside.split()
                short_outside = sum(1 for t in outside_tokens if len(t) <= 3)
                if short_outside >= 3:
                    return False  # "t n,mdt n,m n (1)" type
            # Otherwise: structured brackets → keep
        elif no_cmd_tokens and all(len(t) <= 5 for t in no_cmd_tokens):
            return False  # unbalanced + all short tokens → garbled

    # Reject generic LaTeX + prose-like tokens: "\varphi t n,m = 1"
    # where the only LaTeX is a weak command (\varphi, \varpi, etc.) and
    # the non-LaTeX content has ≥3 isolated single letters (prose pattern).
    # Strip brace-wrapped arguments too — they're part of LaTeX structure.
    if has_any_latex and not has_latex and not has_sub_sup and len(ops) <= 1:
        no_cmd = re.sub(r'\\[a-zA-Z]+', '', stripped)
        no_braces = re.sub(r'\{[^}]*\}', '', no_cmd)
        single_letters = re.findall(r'\b[a-zA-Z]\b', no_braces)
        if len(single_letters) >= 3:
            return False

    # Reject LaTeX operator + bare English word: "\leq R max" type fragments
    # where "max" is a plain word, not \max with braces
    if has_latex and not has_sub_sup and not has_greek and len(tokens) <= 4:
        bare_words = [t for t in tokens if re.match(r'^[a-zA-Z]{2,}$', t)]
        if bare_words and len(tokens) <= 3:
            return False

    # Reject unbalanced brackets — incomplete fragments like "\in [0, 2\pi"
    # where the closing bracket/paren was outside the $...$ block
    bracket_pairs = [('{', '}'), ('[', ']'), ('(', ')')]
    for op_br, cl_br in bracket_pairs:
        depth = 0
        i = 0
        while i < len(stripped):
            if stripped[i] == '\\' and i + 1 < len(stripped):
                i += 2  # Skip escaped brace
                continue
            if stripped[i] == op_br:
                depth += 1
            elif stripped[i] == cl_br:
                depth -= 1
            i += 1
        if depth < 0:
            return False  # Extra closing bracket
        if depth > 0:
            # Unbalanced opening bracket — OK only if there's strong math structure
            if not has_sub_sup and not (_COMPLETE_FORMULA_RE.search(stripped)):
                return False

    core = re.sub(r'[\s,.;:()\[\]{}|]', '', stripped)
    if len(core) < 3:
        return False

    return True


def _convert_formula_text(text: str) -> str:
    result = []
    for char in text:
        if ord(char) > 127:
            result.append(_unicode_to_latex(char))
        else:
            result.append(char)
    return "".join(result)


def _merge_nearby_dollar_blocks(text: str) -> str:
    r"""Merge fragmented $...$ blocks separated by short sub/superscript text.

    PDF sub/super-script spans often fail _looks_like_formula (no math markers),
    creating fragments like: $\varpi$ U,t n $\in$ $[0,1]$
    This merges them back: $\varpi U,t n \in [0,1]$
    """
    if "$" not in text:
        return text

    import re as _re

    # CJK Unicode ranges: Chinese, Japanese, Korean
    _CJK_RE = _re.compile(r'[一-鿿㐀-䶿豈-﫿぀-ゟ゠-ヿ가-힯]')

    def _should_merge(first, gap, second):
        gap = gap.strip()
        if not gap:
            return True
        if len(gap) > 8:
            return False
        if _re.search(r"[.!?;:]", gap):
            return False
        if _re.search(r"[a-zA-Z]{3,}", gap):
            return False
        # Reject non-ASCII gaps — subscript/superscript text is always ASCII
        if any(ord(c) > 127 for c in gap):
            return False
        # Reject CJK characters — explanatory prose between math symbols
        if _CJK_RE.search(gap):
            return False
        # Reject comma-separated short tokens (e.g. "t n,m") — likely
        # prose fragments, not genuine sub/superscript notation
        if "," in gap:
            tokens = gap.replace(",", " ").split()
            if tokens and all(len(t) <= 2 for t in tokens):
                return False
        return True

    prev = None
    result = text
    while prev != result:
        prev = result
        result = _re.sub(
            r"(?<!\$)\$([^$]+?)\$"
            r"(?!\$)"
            r"\s*"
            r"([^$\n]{0,8}?)"
            r"\s*"
            r"(?<!\$)\$([^$]+?)\$",
            lambda m: (
                "$" + " ".join(p for p in [
                    m.group(1).strip(),
                    m.group(2).strip(),
                    m.group(3).strip(),
                ] if p) + "$"
                if _should_merge(m.group(1), m.group(2), m.group(3))
                else m.group(0)
            ),
            result,
        )
    return result


METADATA_PATTERNS = [
    re.compile(r'\b(?:DOI|doi)\s*[:：]\s*10\.\d{4,}/'),
    re.compile(r'arXiv\s*[:：]\s*\d{4}\.\d{4,}'),
    re.compile(r'(?:Published|Received|Accepted|Submitted)\s*[:：]?\s*\d{1,2}\s+\w+\s+\d{4}'),
    re.compile(r'\d{4}\s*(?:IEEE|ACM|Springer|Elsevier|CVPR|ICCV|NeurIPS|ICML|ICLR|AAAI|ACL|EMNLP)'),
    re.compile(r'[©©]\s*\d{4}'),
    re.compile(r'https?://[^\s]{10,}'),
    re.compile(r'^\d{1,3}\s*$'),
]

AFFILIATION_PATTERNS = [
    re.compile(r'(?:Department|School|College|Institute|Laboratory|Lab)\s+of\s+\w+'),
    re.compile(r'University\s+of\s+\w+'),
    re.compile(r'\b\w+@\w+\.\w+\b'),
]


def _filter_metadata_lines(text: str) -> str:
    """过滤学术 PDF 页眉页脚中的元数据行（DOI、期刊名、日期等）。"""
    lines = text.split("\n")
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if len(stripped) < 3:
            continue
        if any(p.search(stripped) for p in METADATA_PATTERNS):
            continue
        if len(stripped) < 120 and any(p.search(stripped) for p in AFFILIATION_PATTERNS):
            continue
        kept.append(line)
    return "\n".join(kept)


def extract_pdf_text(pdf_path: str | Path, output_dir: str | Path | None = None) -> dict[str, Any]:
    """从 PDF 中提取文本、公式区域和章节结构。

    若提供 output_dir，将导出图片到 {output_dir}/figures/ 目录。

    返回 dict: pages, sections, formulas, figures, tables, full_text,
               page_count, formula_count, figure_count, table_count
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        import fitz
    except ImportError:
        return _extract_fallback(pdf_path)

    doc = fitz.open(str(pdf_path))
    page_count = doc.page_count
    pages: list[dict] = []
    sections: list[dict] = []
    formulas: list[dict] = []
    figures: list[dict] = []
    tables: list[dict] = []
    all_text_parts: list[str] = []
    formula_idx = 0
    figure_idx = 0
    table_idx = 0
    current_section = ""
    page_block_sizes: dict[int, list[tuple[float, str]]] = {}

    FIGURE_CAPTION_RE = re.compile(r"(?:^Fig\.?|^Figure|^图)\s*\d+", re.IGNORECASE)

    for page_num in range(page_count):
        page = doc[page_num]
        blocks = page.get_text("dict")["blocks"]
        page_blocks: list[dict] = []
        block_sizes: list[tuple[float, str]] = []
        page_text_parts: list[str] = []

        for i, block in enumerate(blocks):
            if block["type"] == 1:
                figure_idx += 1
                bbox = block.get("bbox", [0, 0, 0, 0])
                w = block.get("width", 0)
                h = block.get("height", 0)
                caption = ""
                # Heuristic caption detection: look at next few blocks
                img_bottom = bbox[3] if len(bbox) > 3 else 0
                for j in range(i + 1, min(i + 4, len(blocks))):
                    nb = blocks[j]
                    if nb.get("type") != 0:
                        continue
                    nb_bbox = nb.get("bbox", [0, 0, 0, 0])
                    text_top = nb_bbox[1] if len(nb_bbox) > 1 else 0
                    if 0 < text_top - img_bottom < 70:
                        nb_text = " ".join(
                            s.get("text", "") for line in nb.get("lines", [])
                            for s in line.get("spans", [])
                        ).strip()
                        if len(nb_text) < 300 and FIGURE_CAPTION_RE.search(nb_text):
                            caption = nb_text[:200]
                            break
                placeholder = f"\n[图{figure_idx}：{caption or '见原文图' + str(figure_idx)}]\n"
                page_text_parts.append(placeholder)
                figures.append({
                    "index": figure_idx, "page": page_num + 1,
                    "bbox": bbox, "width": w, "height": h,
                    "caption": caption, "placeholder": placeholder.strip(),
                })
                # Export image to PNG if output_dir is specified
                if output_dir:
                    try:
                        fig_dir = Path(output_dir) / "figures"
                        fig_dir.mkdir(parents=True, exist_ok=True)
                        pix = page.get_pixmap(clip=bbox, dpi=150)
                        img_name = f"figure_{figure_idx}.png"
                        pix.save(str(fig_dir / img_name))
                        figures[-1]["image_path"] = str(fig_dir / img_name)
                        figures[-1]["image_rel_path"] = f"figures/{img_name}"
                    except Exception:
                        pass
                continue

            if block["type"] != 0:
                continue
            block_lines: list[str] = []
            block_has_formula = False
            block_formulas: list[str] = []
            block_max_size = 0.0

            for line in block["lines"]:
                line_text = ""
                line_has_math = False
                prev_span_was_math = False

                math_buffer: list[str] = []
                prev_math_size = 0.0
                prev_math_y0 = 0.0
                for span in line["spans"]:
                    text = span["text"]
                    font = span.get("font", "")
                    size = span.get("size", 0)
                    block_max_size = max(block_max_size, size)

                    has_unicode_math = any(ord(c) in SYMBOL_TO_LATEX for c in text)
                    is_math = _is_math_font(font) or _looks_like_formula(text) or has_unicode_math
                    # Adjacency: small text near math span → likely sub/superscript
                    if not is_math and prev_span_was_math and prev_math_size > 0 and size > 0:
                        span_bbox = span.get("bbox", None)
                        span_y0 = span_bbox[1] if span_bbox else 0
                        size_ratio = prev_math_size / max(size, 0.5)
                        y_diff = span_y0 - prev_math_y0
                        if size_ratio >= 1.25 and abs(y_diff) > 1.0:
                            is_math = True
                    if is_math:
                        converted = _convert_formula_text(text)
                        # Sub/superscript recovery: detect font size change
                        span_bbox = span.get("bbox", None)
                        span_y0 = span_bbox[1] if span_bbox else 0
                        if prev_span_was_math and prev_math_size > 0 and size > 0:
                            size_ratio = prev_math_size / max(size, 0.5)
                            y_diff = span_y0 - prev_math_y0
                            if size_ratio >= 1.3:
                                if y_diff > 1.5:  # lower → subscript
                                    converted = "_" + converted
                                elif y_diff < -1.5:  # higher → superscript
                                    converted = "^" + converted
                        math_buffer.append(converted)
                        line_has_math = True
                        block_has_formula = True
                        if prev_span_was_math and block_formulas:
                            block_formulas[-1] += " " + converted
                        else:
                            block_formulas.append(converted)
                        prev_span_was_math = True
                        prev_math_size = size
                        prev_math_y0 = span_y0
                    else:
                        if math_buffer:
                            line_text += f" ${' '.join(math_buffer)}$ "
                            math_buffer = []
                        line_text += text
                        prev_span_was_math = False
                        prev_math_size = 0.0
                        prev_math_y0 = 0.0

                if math_buffer:
                    line_text += f" ${' '.join(math_buffer)}$ "

                merged = _merge_nearby_dollar_blocks(line_text.strip())
                block_lines.append(merged)

            block_text = " ".join(block_lines).strip()
            block_text = _merge_nearby_dollar_blocks(block_text)  # cross-line merge
            if not block_text:
                continue
            block_sizes.append((block_max_size, block_text))
            page_text_parts.append(block_text)

            # Section header detection via font size
            for pattern in SECTION_PATTERNS:
                if pattern.match(block_text) and block_max_size > 10:
                    current_section = block_text
                    sections.append({
                        "title": block_text, "page": page_num + 1,
                        "level": block_text.count("."),
                    })
                    break

            if block_has_formula:
                # Extract $...$ blocks from merged block_text (unified path)
                for m in re.finditer(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", block_text):
                    fm_text = m.group(1).strip()
                    if len(fm_text) > 3 and _is_valid_formula(fm_text):
                        formula_idx += 1
                        formulas.append({
                            "index": formula_idx, "latex": fm_text,
                            "context": block_text, "page": page_num + 1,
                        })

            page_blocks.append({
                "text": block_text, "has_formula": block_has_formula,
                "section": current_section,
            })

        # Table detection
        try:
            page_tables = page.find_tables()
            for t in page_tables:
                cells = t.extract()
                if not cells or len(cells) < 2:
                    continue
                table_idx += 1
                md_table = _cells_to_markdown_table(cells)
                page_text_parts.append(f"\n[表{table_idx}]\n{md_table}\n")
                tables.append({
                    "index": table_idx, "page": page_num + 1,
                    "bbox": list(t.bbox) if t.bbox else [],
                    "rows": len(cells), "cols": len(cells[0]) if cells else 0,
                    "markdown": md_table,
                })
        except Exception:
            pass

        page_block_sizes[page_num] = block_sizes
        pages.append({"page": page_num + 1, "blocks": page_blocks})
        all_text_parts.append("\n".join(page_text_parts))

    doc.close()

    # Fallback section detection from text pattern
    if len(sections) <= 1:
        full = "\n".join(all_text_parts)
        section_re = re.compile(
            r"^(?:\d+(?:\.\d+)*\.?\s+)?"
            r"(?:Abstract|Introduction|Related\s+Work|System\s+Model|"
            r"Problem\s+Formulation|Proposed|Experiment|"
            r"Performance\s+Evaluation|Conclusion|"
            r"摘要|引言|相关工作|系统模型|问题表述|实验|结论)",
            re.MULTILINE | re.IGNORECASE,
        )
        for m in section_re.finditer(full):
            sections.append({
                "title": m.group().strip(), "level": 1,
                "page": 1,
            })

    full_text = "\n\n".join(all_text_parts)
    full_text = _filter_metadata_lines(full_text)

    return {
        "pages": pages,
        "sections": sections,
        "formulas": formulas,
        "figures": figures,
        "tables": tables,
        "full_text": full_text,
        "page_count": page_count,
        "formula_count": len(formulas),
        "figure_count": len(figures),
        "table_count": len(tables),
    }


def _cells_to_markdown_table(cells: list[list[str]]) -> str:
    """将 find_tables() 提取的单元格列表转为 Markdown 表格字符串。"""
    if not cells or not cells[0]:
        return ""
    col_count = len(cells[0])
    lines = ["| " + " | ".join(str(c) for c in cells[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(col_count)) + " |")
    for row in cells[1:]:
        padded = list(row) + [""] * (col_count - len(row))
        lines.append("| " + " | ".join(str(c) for c in padded[:col_count]) + " |")
    return "\n".join(lines)


def _extract_fallback(pdf_path: Path) -> dict[str, Any]:
    """Fallback: 使用 pypdf 进行基本文本提取"""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError("需要 pymupdf 或 pypdf: pip install pymupdf")

    reader = PdfReader(str(pdf_path))
    page_count = len(reader.pages)
    all_text: list[str] = []
    pages: list[dict] = []

    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        all_text.append(text)
        pages.append({
            "page": i + 1,
            "blocks": [{"text": text, "has_formula": False, "section": ""}],
        })

    return {
        "pages": pages, "sections": [], "formulas": [],
        "figures": [], "tables": [],
        "full_text": "\n\n".join(all_text),
        "page_count": page_count, "formula_count": 0,
        "figure_count": 0, "table_count": 0,
    }


def extract_paper_meta(file_path: str | Path, workspace: str | Path) -> dict[str, Any]:
    """提取论文元数据，保存文件到工作区。支持 PDF 和纯文本(.txt/.md)。"""
    path = Path(file_path)
    paper_id = "p_" + uuid.uuid4().hex[:8]

    if path.suffix.lower() in (".txt", ".md", ".text"):
        return _extract_text_paper(path, paper_id, workspace)

    ws = Path(workspace)
    paper_dir = ws / "papers" / paper_id
    paper_dir.mkdir(parents=True, exist_ok=True)
    dest = paper_dir / "original.pdf"
    if dest != path.resolve():
        dest.write_bytes(path.read_bytes())

    extracted = extract_pdf_text(path, output_dir=paper_dir)
    full_text = extracted.get("full_text", "")
    chinese_chars = sum(1 for c in full_text if '一' <= c <= '鿿')
    language = "zh" if chinese_chars > 100 else "en"
    lines = [l.strip() for l in full_text[:800].split("\n") if l.strip()]
    title = lines[0] if lines else path.stem
    if len(title) < 10 and len(lines) > 1:
        title = lines[1]

    (paper_dir / "extracted.json").write_text(
        json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"paper_id": paper_id, "title": title[:300], "language": language,
            "page_count": extracted.get("page_count", 0),
            "formula_count": extracted.get("formula_count", 0),
            "figure_count": extracted.get("figure_count", 0),
            "table_count": extracted.get("table_count", 0),
            "sections": extracted.get("sections", []), "full_text": full_text,
            "file_path": str(dest), "workspace_dir": str(paper_dir)}


def _extract_text_paper(path: Path, paper_id: str, workspace: str | Path) -> dict[str, Any]:
    """处理纯文本论文 — 直接读取文本，检测 LaTeX 公式和 Markdown 章节。"""
    full_text = path.read_text(encoding="utf-8")
    if not full_text.strip():
        raise ValueError("论文内容为空")

    chinese_chars = sum(1 for c in full_text if '一' <= c <= '鿿')
    language = "zh" if chinese_chars > 100 else "en"

    lines = [l.strip() for l in full_text[:800].split("\n") if l.strip()]
    title = lines[0] if lines else path.stem
    if len(title) < 10 and len(lines) > 1:
        title = lines[1]

    # Detect LaTeX formulas — filter through unified validator
    fm_re = re.compile(r'\$\$([^$]+)\$\$|\$([^$]+)\$')
    formulas = []
    for m in fm_re.finditer(full_text):
        latex = (m.group(1) or m.group(2)).strip()
        if len(latex) > 3 and _is_valid_formula(latex):
            formulas.append({"index": len(formulas) + 1,
                             "latex": latex,
                             "context": full_text[max(0, m.start() - 60):m.end() + 60],
                             "page": 1})

    # Detect markdown sections
    sec_re = re.compile(r'^#{1,4}\s+(.+)$', re.MULTILINE)
    sections = [{"title": m.group(1).strip(), "level": 1, "page": 1}
                for m in sec_re.finditer(full_text)]

    page_count = max(1, len(full_text) // 3000)
    ws = Path(workspace)
    paper_dir = ws / "papers" / paper_id
    paper_dir.mkdir(parents=True, exist_ok=True)
    (paper_dir / "original.txt").write_text(full_text, encoding="utf-8")

    extracted = {"pages": [], "sections": sections, "formulas": formulas,
                 "figures": [], "tables": [],
                 "full_text": full_text, "page_count": page_count,
                 "formula_count": len(formulas),
                 "figure_count": 0, "table_count": 0}
    (paper_dir / "extracted.json").write_text(
        json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"paper_id": paper_id, "title": title[:300], "language": language,
            "page_count": page_count, "formula_count": len(formulas),
            "sections": sections, "full_text": full_text,
            "file_path": str(paper_dir / "original.txt"),
            "workspace_dir": str(paper_dir)}
