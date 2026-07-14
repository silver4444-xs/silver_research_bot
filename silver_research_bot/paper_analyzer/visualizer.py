"""Stage 3: 可视化分析输出 — ISCC 领域专家视角的 Mermaid 图表 + HTML

三模块:
  - 系统架构流程: 7 种 ISCC 架构类型识别 + Mermaid flowchart
  - 算法流程: 6 种算法类型识别 + Mermaid flowchart (含循环回边)
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from silver_research_bot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

# ── CSS constants ─────────────────────────────────────────────────
OVERVIEW_CSS = """
.overview{max-width:1100px;margin:0 auto 32px}
.layer{border-radius:14px;padding:22px 28px;margin-bottom:8px;border:1.5px solid}
.layer-title{font-size:18px;font-weight:700;margin-bottom:18px;letter-spacing:.02em}
.layer-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}
.lcard{border-radius:10px;padding:16px 20px;border:1px solid}
.lcard-title{font-size:15px;font-weight:700;margin-bottom:10px}
.lcard-item{font-size:13px;line-height:1.7;opacity:.9;margin-bottom:6px}
.lcard-item:last-child{margin-bottom:0}
.layer-arrow{text-align:center;font-size:20px;padding:6px 0;color:#999;line-height:1}
.mermaid-wrap{background:#fff;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.08);padding:24px;margin:22px 0;text-align:center}
.mermaid-wrap h3{font-size:15px;color:#444;margin-top:0;margin-bottom:14px;text-align:left;font-weight:600}
.mermaid-wrap-large{max-width:1200px}
""".strip()

COLORS = {
    "system":    ("#E6F1FB", "#185FA5", "#0C447C", "#E1F5EE", "#0F6E56", "#085041"),
    "problem":   ("#EEEDFE", "#534AB7", "#3C3489", "#FBEAF0", "#993556", "#72243E"),
    "algorithm": ("#FAEEDA", "#854F0B", "#633806", "#FAECE7", "#993C1D", "#712B13"),
    "experiment":("#EAF3DE", "#3B6D11", "#27500A", "#E1F5EE", "#0F6E56", "#085041"),
}

DIM_LABELS = {
    "system_model":           ("系统模型", "system"),
    "problem_formulation":    ("问题表述", "problem"),
    "optimization_algorithm": ("优化算法", "algorithm"),
    "experiment_design":      ("实验设计", "experiment"),
}

DIM_ORDER = ["system_model", "problem_formulation", "optimization_algorithm", "experiment_design"]


# ── HTML generation helpers ────────────────────────────────────────

def _render_md_inline(s: str) -> str:
    """Convert basic Markdown inline syntax to HTML for overview cards."""
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'\*(.+?)\*', r'<em>\1</em>', s)
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
    return s

def _truncate_at_sentence(s: str, max_len: int = 150) -> str:
    """Truncate text at nearest sentence boundary within max_len chars."""
    if len(s) <= max_len:
        return s
    for sep in ('。', '！', '？', '. ', '! ', '? ', '\n'):
        pos = s.rfind(sep, 0, max_len)
        if pos > max_len * 0.5:
            return s[:pos + len(sep.rstrip())]
    space = s.rfind(' ', 0, max_len)
    if space > max_len * 0.5:
        return s[:space]
    return s[:max_len]

def _is_table_row(line: str) -> bool:
    """Check if a line looks like a markdown table row or separator."""
    stripped = line.strip()
    if not stripped.startswith('|'):
        return False
    if re.match(r'^\|[\s\-:|]+\|$', stripped):
        return True
    return stripped.count('|') >= 2 and any(
        c.isalpha() or c.isdigit() for c in stripped
    )

def _extract_subsections(text: str) -> list[dict]:
    """Extract ### subsections from markdown analysis text as card candidates."""
    _STRIP_DISPLAY = re.compile(r'\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]')
    def _clean(s: str) -> str:
        s = _STRIP_DISPLAY.sub('', s)
        math_blocks = []
        def _protect(m):
            math_blocks.append(m.group(0))
            return '\x00M' + str(len(math_blocks) - 1) + '\x00'
        s = re.sub(r'(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)', _protect, s)
        s = re.sub(r'\\\(([^\\\n]+?)\\\)', _protect, s)
        s = re.sub(r'\s+', ' ', s).strip()
        s = _truncate_at_sentence(s)
        s = _render_md_inline(s)
        for i, block in enumerate(math_blocks):
            s = s.replace('\x00M' + str(i) + '\x00', block)
        return s
    pattern = re.compile(r'^### (.+)$', re.MULTILINE)
    sections = []
    matches = list(pattern.finditer(text))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        items = [
            _clean(l.strip("- ").strip())
            for l in body.split("\n")
            if l.strip() and not l.startswith("#") and not _is_table_row(l.strip("- ").strip())
        ][:3]
        if not items:
            items = [_clean(body[:150])]
        sections.append({"title": _clean(m.group(1).strip()), "items": items})
    if not sections:
        lines = [_clean(l.strip("- ").strip()) for l in text.split("\n") if l.strip() and not l.startswith("#")]
        if lines:
            sections = [{"title": "概述", "items": lines[:3]}]
    return sections[:5]


def _build_overview(analysis: dict[str, str]) -> str:
    """Build 4-layer CSS overview from analysis text."""
    parts: list[str] = []
    for key in DIM_ORDER:
        text = analysis.get(key, "")
        if not text:
            continue
        label, color_key = DIM_LABELS[key]
        l_bg, l_bd, l_tx, c_bg, c_bd, c_tx = COLORS[color_key]
        cards = _extract_subsections(text)
        parts.append(
            f'<div class="layer" style="background:{l_bg};border-color:{l_bd}">'
            f'<div class="layer-title" style="color:{l_tx}">'
            f'{DIM_ORDER.index(key) + 1} {label}</div>'
            f'<div class="layer-cards">'
        )
        for card in cards:
            items_html = "".join(f'<div class="lcard-item">{it}</div>' for it in card["items"])
            parts.append(
                f'<div class="lcard" style="background:{c_bg};border-color:{c_bd}">'
                f'<div class="lcard-title" style="color:{c_tx}">{card["title"]}</div>'
                f'{items_html}</div>'
            )
        parts.append('</div></div>')
        parts.append('<div class="layer-arrow">▼</div>')
    if parts and "layer-arrow" in parts[-1]:
        parts.pop()
    return f'<div class="overview">\n{"".join(parts)}\n</div>'


# ── Mermaid sanitization ───────────────────────────────────────────

def _mermaid_safe_label(text: str, max_len: int = 50) -> str:
    """Remove characters that break Mermaid syntax from label text."""
    text = re.sub(r'["#&<>(){}\[\];]', '', text)
    text = text.replace('|', '/')
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_len]


def _mermaid_safe_id(text: str) -> str:
    """Create a Mermaid-safe identifier from arbitrary text."""
    safe = re.sub(r'[^a-zA-Z0-9_一-鿿]', '_', text).strip('_')
    return safe or 'G'


def _sanitize_mermaid_code(code: str) -> str:
    """Sanitize LLM-generated Mermaid code: strip comments, HTML-sensitive chars,
    and normalize fullwidth punctuation for Chinese labels."""
    clean_lines = []
    for line in code.split('\n'):
        stripped = line.lstrip()
        if stripped.startswith('%%') or stripped.startswith('//'):
            continue
        if re.match(r'^\s*#', line):
            continue
        result = []
        in_quote = False
        in_edge_label = False
        for ch in line:
            if ch == '"':
                in_quote = not in_quote
                result.append(ch)
            elif ch == '|' and not in_quote:
                in_edge_label = not in_edge_label
                result.append(ch)
            elif ch == '#' and not in_quote and not in_edge_label:
                pass
            elif ch in '&<>' and not in_quote:
                pass
            elif ch == '（' and not in_quote:
                result.append('(')
            elif ch == '）' and not in_quote:
                result.append(')')
            elif ch == '；' and not in_quote:
                result.append(';')
            else:
                result.append(ch)
        clean = ''.join(result)
        if clean.strip():
            clean_lines.append(clean)
    return '\n'.join(clean_lines)


# ── LaTeX -> Mermaid label helper ───────────────────────────────────

def _simplify_latex_for_label(latex: str, max_len: int = 40) -> str:
    """Strip LaTeX markup for display in Mermaid labels, keeping core math symbols."""
    s = re.sub(r'\\\(|\\\)|\\\[|\\\]|\$\$?', '', latex)
    s = re.sub(r'\\sum_\{([^}]+)\}\^\{([^}]+)\}', r'∑_{\1}^{\2}', s)
    s = re.sub(r'\\prod_\{([^}]+)\}\^\{([^}]+)\}', r'∏_{\1}^{\2}', s)
    s = re.sub(r'\\int_\{([^}]+)\}\^\{([^}]+)\}', r'∫_{\1}^{\2}', s)
    s = re.sub(r'\\min\b', 'min', s)
    s = re.sub(r'\\max\b', 'max', s)
    s = re.sub(r'\\mathbb\{([^}]+)\}', r'\1', s)
    s = re.sub(r'\\mathbf\{([^}]+)\}', r'\1', s)
    s = re.sub(r'\\mathcal\{([^}]+)\}', r'\1', s)
    s = re.sub(r'\\boldsymbol\{([^}]+)\}', r'\1', s)
    s = re.sub(r'\\hat\{([^}]+)\}', r'\1̂', s)
    s = re.sub(r'\\tilde\{([^}]+)\}', r'\1̃', s)
    s = re.sub(r'\\bar\{([^}]+)\}', r'\1̄', s)
    s = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', '', s)
    s = re.sub(r'\\[a-zA-Z]+', '', s)
    s = re.sub(r'[{}\\]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:max_len]


# ── Module 1: System Architecture Diagram ──────────────────────────

async def _build_system_architecture_diagram(
    text: str, provider: "LLMProvider", model: str
) -> str:
    """Generate ISCC domain-aware system architecture Mermaid flowchart."""
    if not text or not text.strip():
        return ""

    system_prompt = render_template("paper/visualizer_system.md", system_model_text=text[:4000])

    try:
        response = await provider.chat_with_retry(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text[:4000]},
            ],
            tools=None,
        )
        content = (response.content or "").strip()
        m = re.search(r"```mermaid\s*\n([\s\S]*?)```", content)
        if m:
            code = _sanitize_mermaid_code(m.group(1))
            if code and re.search(r'-->|---|\.\.->|==>|-.->', code):
                return f'<div class="mermaid-wrap"><h3>系统架构流程</h3><pre class="mermaid">\n{code}\n</pre></div>'
        if content.startswith("flowchart ") or content.startswith("graph "):
            code = _sanitize_mermaid_code(content)
            if re.search(r'-->|---|\.\.->|==>|-.->', code):
                return f'<div class="mermaid-wrap"><h3>系统架构流程</h3><pre class="mermaid">\n{code}\n</pre></div>'
        return _system_architecture_fallback(text)
    except Exception:
        return _system_architecture_fallback(text)


def _system_architecture_fallback(text: str) -> str:
    """Build a simple system flowchart from markdown headers and entity keywords."""
    headers = re.findall(r'^### (.+)$', text, re.MULTILINE)
    if len(headers) < 2:
        headers = re.findall(r'^\*\*(.+?)\*\*', text, re.MULTILINE)
    if len(headers) < 2:
        return ""

    lines = ["flowchart TB"]
    node_ids = []
    for i, h in enumerate(headers[:10]):
        nid = f"S{i}"
        label = _mermaid_safe_label(h, max_len=40)
        node_ids.append(nid)
        if any(kw in h for kw in ("判断", "选择", "是否", "条件", "决策", "判定")):
            lines.append(f'  {nid}{{{"{label}"}}}')
        else:
            lines.append(f'  {nid}["{label}"]')
        if i > 0:
            lines.append(f'  {node_ids[i-1]} --> {nid}')
    if len(lines) <= 2:
        return ""
    mermaid = "\n".join(lines)
    return f'<div class="mermaid-wrap"><h3>系统架构流程</h3><pre class="mermaid">\n{mermaid}\n</pre></div>'


# ── Module 2: Algorithm Flow Diagram ───────────────────────────────

async def _build_algorithm_flow_diagram(
    text: str, provider: "LLMProvider", model: str
) -> str:
    """Generate ISCC domain-aware algorithm flow Mermaid flowchart."""
    if not text or not text.strip():
        return ""

    system_prompt = render_template("paper/visualizer_algorithm.md", algorithm_text=text[:4000])

    try:
        response = await provider.chat_with_retry(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text[:4000]},
            ],
            tools=None,
        )
        content = (response.content or "").strip()
        m = re.search(r"```mermaid\s*\n([\s\S]*?)```", content)
        if m:
            code = _sanitize_mermaid_code(m.group(1))
            if code and re.search(r'-->|---|\.\.->|==>|-.->', code):
                return f'<div class="mermaid-wrap"><h3>算法流程</h3><pre class="mermaid">\n{code}\n</pre></div>'
        if content.startswith("flowchart ") or content.startswith("graph "):
            code = _sanitize_mermaid_code(content)
            if re.search(r'-->|---|\.\.->|==>|-.->', code):
                return f'<div class="mermaid-wrap"><h3>算法流程</h3><pre class="mermaid">\n{code}\n</pre></div>'
        return _algorithm_flow_fallback(text)
    except Exception:
        return _algorithm_flow_fallback(text)


def _algorithm_flow_fallback(text: str) -> str:
    """Build a simple algorithm flowchart from markdown headers and step detection."""
    headers = re.findall(r'^### (.+)$', text, re.MULTILINE)
    if not headers:
        headers = re.findall(r'^\*\*(.+?)\*\*', text, re.MULTILINE)
    if not headers:
        headers = re.findall(r'^(?:\d+[\.\)、]\s*)(.+)$', text, re.MULTILINE)[:10]
    if len(headers) < 2:
        return ""

    lines = ["flowchart TD"]
    node_ids = []
    has_loop = any(kw in text for kw in ("迭代", "循环", "重复", "收敛", "更新"))
    for i, h in enumerate(headers[:10]):
        nid = f"A{i}"
        label = _mermaid_safe_label(h, max_len=40)
        node_ids.append(nid)
        if any(kw in h for kw in ("判断", "收敛", "是否", "条件", "终止", "满足")):
            lines.append(f'  {nid}{{{"{label}"}}}')
        elif i == 0:
            lines.append(f'  {nid}[["{label}"]]')
        elif i == len(headers[:10]) - 1:
            lines.append(f'  {nid}[["{label}"]]')
        else:
            lines.append(f'  {nid}["{label}"]')
        if i > 0:
            prev_label = headers[i - 1]
            if any(kw in prev_label for kw in ("判断", "收敛", "是否")):
                lines.append(f'  {node_ids[i-1]} -->|是| {nid}')
                if has_loop and i < 3:
                    lines.append(f'  {node_ids[i-1]} -.->|否/迭代| {node_ids[0]}')
            else:
                lines.append(f'  {node_ids[i-1]} --> {nid}')
    if len(lines) <= 2:
        return ""
    mermaid = "\n".join(lines)
    return f'<div class="mermaid-wrap"><h3>算法流程</h3><pre class="mermaid">\n{mermaid}\n</pre></div>'


# ── Experiment table ───────────────────────────────────────────────

async def _llm_experiment_table(
    exp_text: str, provider: "LLMProvider", model: str
) -> str:
    """Use LLM to extract experiment comparison data into an HTML table."""
    system = (
        "你是一个数据提取器。从实验分析文本中提取性能对比数据，"
        "输出一个 HTML `<table class=\"cmp-table\">`。"
        "表头：方法 | 指标1 | 指标2 | 指标3。至少2行数据。"
        "只输出 `<table>` 标签，禁止任何解释文字或代码围栏。"
    )
    try:
        response = await provider.chat_with_retry(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": exp_text[:3000]},
            ],
            tools=None,
        )
        content = (response.content or "").strip()
        if "<table" in content:
            return content
    except Exception:
        pass
    raw_lines = exp_text.strip().split("\n")
    md_rows = [l.strip() for l in raw_lines if l.strip().startswith("|") and l.strip().count("|") >= 2]
    if len(md_rows) >= 2:
        header_row = md_rows[0]
        data_rows = [r for r in md_rows[1:] if not re.match(r'^\|[\s\-:|]+\|$', r)]
        header_cells = "".join(
            f"<th>{c.strip()}</th>" for c in header_row.split("|")[1:-1]
        )
        data_html = ""
        for row in data_rows[:10]:
            cells = row.split("|")[1:-1]
            data_html += "<tr>" + "".join(f"<td>{c.strip()}</td>" for c in cells) + "</tr>"
        if header_cells and data_html:
            return f"<table class=\"cmp-table\"><tr>{header_cells}</tr>{data_html}</table>"

    lines = []
    for l in raw_lines:
        l = l.strip()
        l = re.sub(r'\*\*([^*]+)\*\*', r'\1', l)
        l = l.strip("- ").strip()
        if ":" in l or "%" in l:
            lines.append(l)
    if lines:
        rows = "".join(
            f"<tr><td>{l.split(':')[0].strip()}</td><td>{l.split(':')[1].strip() if ':' in l else l.strip()}</td></tr>"
            for l in lines[:10]
        )
        return f"<table class=\"cmp-table\"><tr><th>指标</th><th>数值</th></tr>{rows}</table>"
    return ""


# ── Main API ─────────────────────────────────────────────────────

def _wrap_html(body: str, title: str) -> str:
    body = re.sub(r'<div\s+class="mermaid"\s*>(.*?)</div>',
                  r'<pre class="mermaid">\1</pre>', body, flags=re.DOTALL)
    if "<!DOCTYPE html>" not in body and "<html" not in body:
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — 可视化分析</title>
<script>MathJax={{tex:{{inlineMath:[['$','$'],['\\(','\\)']],displayMath:[['$$','$$'],['\\[','\\]']]}}}};</script>
<script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif;
         max-width: 1100px; margin: 0 auto; padding: 28px 24px;
         background: #f5f6f8; color: #222; line-height: 1.65; }}
  h1 {{ border-bottom: 3px solid #2563eb; padding-bottom: 8px; }}
  h2 {{ border-bottom: 2px solid #93c5fd; padding-bottom: 4px; margin-top: 32px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 10px 12px; text-align: left; }}
  th {{ background: #2563eb; color: white; }}
  .cmp-table {{ font-size: 14px; }}
  .cmp-table tr:nth-child(even) {{ background: #f8fafc; }}
  .card {{ background: white; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.12);
           padding: 16px; margin: 12px 0; }}
  .mermaid {{ text-align: center; margin: 20px 0; }}
  .mermaid-error {{ background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px;
                    padding: 12px; margin: 12px 0; color: #856404; font-size: 13px; }}
  .mermaid-error::before {{ content: "⚠ Mermaid 渲染失败"; display: block;
                            font-weight: bold; margin-bottom: 6px; }}
  {OVERVIEW_CSS}
</style>
</head>
<body>
<h1>{title} — 可视化分析</h1>
{body}
<script>
mermaid.initialize({{startOnLoad:true, theme:'default', securityLevel:'loose'}});
window.addEventListener('error',function(e){{
  if(e.target&&e.target.classList.contains('mermaid')){{
    var div=e.target;
    var pre=document.createElement('pre');
    pre.className='mermaid-error';
    pre.textContent=div.textContent.slice(0,300);
    div.parentNode.replaceChild(pre,div);
  }}
}},true);
</script>
</body>
</html>"""
    return body


async def generate_visualization(
    analysis: dict[str, str],
    paper_title: str,
    provider: "LLMProvider",
    model: str,
) -> str:
    """Generate visualization HTML: overview + ISCC domain-aware Mermaid diagrams."""
    # Part 1: Programmatic 4-layer overview (no LLM)
    overview = _build_overview(analysis)

    # Part 2: ISCC domain-aware system architecture diagram (1 LLM call)
    sys_mermaid = await _build_system_architecture_diagram(
        analysis.get("system_model", ""), provider, model
    )

    # Part 3: ISCC domain-aware algorithm flow diagram (1 LLM call)
    algo_mermaid = await _build_algorithm_flow_diagram(
        analysis.get("optimization_algorithm", ""), provider, model
    )

    # Part 4: Experiment comparison table (1 LLM call)
    exp_html = await _llm_experiment_table(
        analysis.get("experiment_design", ""), provider, model
    )

    body_parts = [overview]
    if sys_mermaid:
        body_parts.append(sys_mermaid)
    if algo_mermaid:
        body_parts.append(algo_mermaid)
    if exp_html:
        body_parts.append(f"<h2>实验对比</h2>\n{exp_html}")

    body = "\n".join(body_parts)
    return _wrap_html(body, paper_title)
