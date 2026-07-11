"""Stage 3: 可视化分析输出 — 程序化生成分层概述 + Mermaid 图表 + HTML"""

from __future__ import annotations

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
    # Try to find sentence break: 。！？. ! ?
    for sep in ('。', '！', '？', '. ', '! ', '? ', '\n'):
        pos = s.rfind(sep, 0, max_len)
        if pos > max_len * 0.5:
            return s[:pos + len(sep.rstrip())]
    # Fallback: break at last space before max_len
    space = s.rfind(' ', 0, max_len)
    if space > max_len * 0.5:
        return s[:space]
    return s[:max_len]

def _is_table_row(line: str) -> bool:
    """Check if a line looks like a markdown table row or separator."""
    stripped = line.strip()
    if not stripped.startswith('|'):
        return False
    # Table separator: |---|----|
    if re.match(r'^\|[\s\-:|]+\|$', stripped):
        return True
    # Table row: has at least 2 pipe chars with content between
    return stripped.count('|') >= 2 and any(
        c.isalpha() or c.isdigit() for c in stripped
    )

def _extract_subsections(text: str) -> list[dict]:
    """Extract ### subsections from markdown analysis text as card candidates."""
    # Only strip display math (too large for cards); keep inline $...$ for MathJax
    _STRIP_DISPLAY = re.compile(r'\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]')
    def _clean(s: str) -> str:
        s = _STRIP_DISPLAY.sub('', s)
        # Protect inline $...$ blocks from truncation and further processing
        math_blocks = []
        def _protect(m):
            math_blocks.append(m.group(0))
            return '\x00M' + str(len(math_blocks) - 1) + '\x00'
        # Also protect \(...\) inline math blocks
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
    # Remove trailing arrow
    if parts and "layer-arrow" in parts[-1]:
        parts.pop()
    return f'<div class="overview">\n{"".join(parts)}\n</div>'


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
    """Sanitize LLM-generated Mermaid code by removing problematic characters.

    Handles # (comment), &<> (HTML-sensitive), and preserves edge-label |…| syntax.
    """
    clean_lines = []
    for line in code.split('\n'):
        stripped = line.lstrip()
        if stripped.startswith('%%') or stripped.startswith('//'):
            continue
        if re.match(r'^\s*#', line):
            continue
        # Scan char by char; track whether we're inside "…" and inside |…| edge labels
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
                pass  # strip unquoted comment char
            elif ch in '&<>' and not in_quote:
                pass  # strip HTML-sensitive chars
            else:
                result.append(ch)
        clean = ''.join(result)
        if clean.strip():
            clean_lines.append(clean)
    return '\n'.join(clean_lines)


def _build_mermaid_from_headers(text: str, title: str) -> str:
    """Fallback: Build a simple Mermaid flowchart from markdown section headers."""
    headers = re.findall(r'^### (.+)$', text, re.MULTILINE)
    if not headers:
        headers = re.findall(r'^\*\*(.+?)\*\*', text, re.MULTILINE)
    if len(headers) < 2:
        return ""

    # Build simple top-down flowchart from extracted headers
    node_ids = []
    lines = ["flowchart TD"]
    for i, h in enumerate(headers[:10]):
        nid = f"N{i}"
        label = _mermaid_safe_label(h, max_len=40)
        node_ids.append(nid)
        if i == 0:
            lines.append(f'  {nid}["{label}"]')
        else:
            if any(kw in h for kw in ("判断", "选择", "是否", "条件")):
                lines.append(f'  {nid}{{"{label}"}}')
            else:
                lines.append(f'  {nid}["{label}"]')
            lines.append(f'  {node_ids[i-1]} --> {nid}')
    if not lines[1:]:
        return ""
    mermaid = "\n".join(lines)
    return f'<div class="mermaid-wrap"><h3>{title}</h3><pre class="mermaid">\n{mermaid}\n</pre></div>'


async def _llm_mermaid_diagram(
    text: str, diagram_type: str, provider: "LLMProvider", model: str
) -> str | None:
    """Use LLM to generate a professional Mermaid flowchart from analysis text."""
    if not text or not text.strip():
        return None

    if diagram_type == "system":
        system_prompt = (
            "你是一个系统架构可视化专家。根据分析文本生成 Mermaid flowchart。\n\n"
            "规则：\n"
            "1. 使用 flowchart TB（上到下）\n"
            "2. 将系统实体按层次用 subgraph 分组（如设备层/边缘层/云端层/数据流）\n"
            "3. 节点标签需从文本中提炼，描述具体组件名称和功能（10-20字）\n"
            "4. 使用不同形状: [矩形]表示实体, [(圆柱)]表示数据存储, [/平行四边形/]表示输入输出\n"
            "5. 边必须加标签说明数据流/控制流/通信链路的含义（如「上传数据」「下发指令」「状态同步」）\n"
            "6. 至少包含 5 个节点和 5 条边\n"
            "7. ⚠️标签中禁止使用这些字符: # & < > \" ' ( ) [ ] { } | ;\n"
            "8. 只输出 ```mermaid 代码块，不要任何解释文字\n"
        )
    else:
        system_prompt = (
            "你是一个算法流程可视化专家。根据分析文本生成 Mermaid flowchart。\n\n"
            "规则：\n"
            "1. 使用 flowchart TD（上到下）\n"
            "2. 用 [矩形] 表示算法步骤，{菱形} 表示判断/分支条件\n"
            "3. 边必须加标签：分支边上标注「是」「否」或具体条件，循环边上标注「迭代」或「重复」\n"
            "4. 体现算法的迭代/循环结构（用回边连接后续步骤到前序步骤）\n"
            "5. 步骤描述需从文本中提炼关键操作（10-20字）\n"
            "6. 至少包含 6 个节点\n"
            "7. ⚠️标签中禁止使用这些字符: # & < > \" ' ( ) [ ] { } | ;\n"
            "8. 只输出 ```mermaid 代码块，不要任何解释文字\n"
        )

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
                return code
        if content.startswith("flowchart ") or content.startswith("graph "):
            code = _sanitize_mermaid_code(content)
            if re.search(r'-->|---|\.\.->|==>|-.->', code):
                return code
        return None
    except Exception:
        return None


async def _build_mermaid_from_text(
    text: str, title: str, provider: "LLMProvider", model: str
) -> str:
    """Generate a Mermaid flowchart: LLM-driven for quality, fallback to headers."""
    if not text or not text.strip():
        return ""
    diagram_type = "system" if "系统" in title or "架构" in title else "algorithm"
    mermaid = await _llm_mermaid_diagram(text, diagram_type, provider, model)
    if mermaid:
        return f'<div class="mermaid-wrap"><h3>{title}</h3><pre class="mermaid">\n{mermaid}\n</pre></div>'
    return _build_mermaid_from_headers(text, title)


def _build_formula_mermaid(formulas: list[dict], analysis: dict[str, str] | None = None) -> str:
    """Build formula relationship Mermaid graph grouped by paper sections."""
    if not formulas or len(formulas) < 2:
        if formulas:
            # Single formula: show standalone node with safe label
            f = formulas[0]
            raw = f.get("latex", "")[:60]
            safe = re.sub(r'\\\(|\\\)|\\\[|\\\]|\$\$?', '', raw)
            safe = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', '', safe)
            safe = re.sub(r'\\[a-zA-Z]+', '', safe)
            safe = re.sub(r'[{}\\]', '', safe).strip()
            safe = _mermaid_safe_label(safe, max_len=35)
            return f'<div class="mermaid-wrap"><h3>公式关系图谱</h3><pre class="mermaid">\nflowchart TD\n  F{f["index"]}["式{f["index"]}: {safe}"]\n</pre></div>'
        return ""

    # Extract section headers from analysis text to map formula context to sections
    section_ranges = []
    if analysis:
        combined = "\n".join(v for v in analysis.values() if v)
        sec_pattern = re.compile(r'^### (.+)$', re.MULTILINE)
        sec_matches = list(sec_pattern.finditer(combined))
        for i, m in enumerate(sec_matches):
            start = m.start()
            end = sec_matches[i + 1].start() if i + 1 < len(sec_matches) else len(combined)
            section_ranges.append((m.group(1).strip()[:20], start, end))

    # Map each formula to the best-matching section by context overlap
    formula_sections = {}
    for f in formulas:
        ctx = f.get("context", "")
        best_sec = None
        best_score = 0
        for sec_name, sec_start, sec_end in section_ranges:
            # Count overlapping characters between context and section range
            ctx_in_combined = "\n".join(v for v in analysis.values() if v)
            # Simple approach: check if context keywords appear in section text
            ctx_words = set(ctx.split()[:10])
            sec_text = ctx_in_combined[sec_start:sec_end]
            score = sum(1 for w in ctx_words if w in sec_text)
            if score > best_score:
                best_score = score
                best_sec = sec_name
        if best_sec and best_score > 0:
            formula_sections[f["index"]] = best_sec

    # Group formulas by section
    groups: dict[str, list[dict]] = {}
    ungrouped = []
    for f in formulas:
        sec = formula_sections.get(f["index"])
        if sec:
            groups.setdefault(sec, []).append(f)
        else:
            ungrouped.append(f)

    lines = ["flowchart TD"]
    prev_last_node = None

    def _emit_group(label: str, group_formulas: list[dict]):
        nonlocal prev_last_node
        safe_id = _mermaid_safe_id(label)
        safe_display = _mermaid_safe_label(label[:30])
        lines.append(f'  subgraph {safe_id}["{safe_display}"]')
        row_nodes = []
        for f in group_formulas:
            nid = f'F{f["index"]}'
            latex_raw = f.get("latex", "")[:60]
            flabel = re.sub(r'\\\(|\\\)|\\\[|\\\]|\$\$?', '', latex_raw)
            flabel = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', '', flabel)
            flabel = re.sub(r'\\[a-zA-Z]+', '', flabel)
            flabel = re.sub(r'[{}\\]', '', flabel)
            flabel = _mermaid_safe_label(flabel.strip(), max_len=35)
            lines.append(f'    {nid}["式{f["index"]}: {flabel}"]')
            row_nodes.append(nid)
        # Connect within group in index order
        for i in range(1, len(row_nodes)):
            lines.append(f'    {row_nodes[i-1]} --> {row_nodes[i]}')
        lines.append('  end')
        if row_nodes:
            if prev_last_node:
                # Light connector between groups
                lines.append(f'  {prev_last_node} -.-> {row_nodes[0]}')
            prev_last_node = row_nodes[-1]

    for sec_name, group in groups.items():
        _emit_group(sec_name, sorted(group, key=lambda f: f["index"]))
    if ungrouped:
        _emit_group("其他公式", sorted(ungrouped, key=lambda f: f["index"]))

    mermaid = "\n".join(lines[:80])
    return f'<div class="mermaid-wrap"><h3>公式关系图谱</h3><pre class="mermaid">\n{mermaid}\n</pre></div>'


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
    # Fallback: try to build table from markdown or text
    raw_lines = exp_text.strip().split("\n")
    # Detect markdown table: lines with pipes
    md_rows = [l.strip() for l in raw_lines if l.strip().startswith("|") and l.strip().count("|") >= 2]
    if len(md_rows) >= 2:
        # First row = header, skip separator (|---|), rest = data
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

    # Fallback: key-value pairs (key: value or key=value)
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
    formulas_text: str,
    paper_title: str,
    provider: "LLMProvider",
    model: str,
    formulas: list[dict] | None = None,
) -> str:
    """程序化生成可视化 HTML 页面：4层概述 + Mermaid流程图 + 实验表格。

    formulas: 可选的 [{index, latex, context}, ...] 结构化公式数据，
              用于程序化生成公式依赖关系图。
    """
    # Part 1: Programmatic 4-layer overview
    overview = _build_overview(analysis)

    # Part 2: LLM-driven Mermaid diagrams (fallback to header-based)
    sys_mermaid = await _build_mermaid_from_text(
        analysis.get("system_model", ""), "系统架构流程", provider, model
    )
    algo_mermaid = await _build_mermaid_from_text(
        analysis.get("optimization_algorithm", ""), "算法流程", provider, model
    )

    # Part 3: Formula dependency graph from structured data
    formula_mermaid = _build_formula_mermaid(formulas or [], analysis)

    # Part 4: LLM for experiment comparison table only
    exp_html = await _llm_experiment_table(
        analysis.get("experiment_design", ""), provider, model
    )

    body_parts = [overview]
    if sys_mermaid:
        body_parts.append(sys_mermaid)
    if algo_mermaid:
        body_parts.append(algo_mermaid)
    if formula_mermaid:
        body_parts.append(formula_mermaid)
    if exp_html:
        body_parts.append(f"<h2>实验对比</h2>\n{exp_html}")

    body = "\n".join(body_parts)
    return _wrap_html(body, paper_title)
