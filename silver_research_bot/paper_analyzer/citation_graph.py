"""Stage 5: 引用图谱 — LLM 提取参考文献 + D3.js 交互式力导向图"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

_EXTRACT_PROMPT = """Extract all references/citations from the paper text below.
For each reference output a JSON array with objects containing:
  id (R1, R2, ...), title, authors (first author et al.), year, venue.
Output ONLY the JSON array, no other text.

Paper text (end of document where references appear):
{text}"""

_DAG_PROMPT = """Classify each reference's relationship to the paper AND identify
cross-references among the listed references.

Relationships to the paper:
- "foundation": paper builds upon this work
- "comparison": paper compares against this work
- "background": general context

Cross-references: if two references are related (one cites another, or they
address the same problem), list the pair under "related_to" on the citing
reference.

Output a JSON array with objects: {{"id": "R1", "relationship": "foundation",
"related_to": ["R3", "R5"]}}. The "related_to" field is optional — omit if
the reference has no cross-reference to other listed refs. Only output JSON.

Paper: {title}
References: {refs}"""


def _js_escape(s: str) -> str:
    """Escape a string for safe embedding inside a <script> tag."""
    return (s
        .replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("${", "\\${")
        .replace("</script>", "<\\/script>")
        .replace("</Script>", "<\\/Script>")
        .replace("</SCRIPT>", "<\\/SCRIPT>"))


def _json_embed(data: object, indent: int | None = None) -> str:
    """Serialize data to JSON and escape for embedding in <script>."""
    raw = _json.dumps(data, ensure_ascii=False, indent=indent)
    return _js_escape(raw)


async def extract_references(full_text: str, provider: "LLMProvider", model: str) -> list[dict]:
    """Extract references from the end of the paper text via LLM."""
    # Fix 3: use last 16000 chars (was 8000) and higher token limit (4000)
    text = full_text[-16000:]
    try:
        response = await provider.chat_with_retry(
            model=model,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(text=text)}],
            tools=None, max_tokens=4000, temperature=0.0,
        )
        content = (response.content or "[]").strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        return _json.loads(content)
    except Exception:
        return []


async def build_citation_html(
    refs: list[dict], paper_title: str, provider: "LLMProvider", model: str
) -> str:
    """Build a self-contained HTML citation graph page.

    Extracts relationship classifications and cross-reference edges via LLM,
    then generates an interactive D3.js force-directed graph.
    """
    if not refs:
        return _wrap_graph([], [], paper_title, "No references extracted.")

    # --- Classify relationships (Fix 4: also extract cross-reference edges) ---
    relations: dict[str, str] = {}
    cross_refs: list[tuple[str, str]] = []
    try:
        refs_text = "\n".join(
            f"{r.get('id','?')}: {r.get('title','?')} ({r.get('year','?')})"
            for r in refs
        )
        response = await provider.chat_with_retry(
            model=model,
            messages=[{"role": "user", "content": _DAG_PROMPT.format(
                title=paper_title, refs=refs_text)}],
            tools=None,
            max_tokens=1500,  # Fix 5: was 500, now 1500 for related_to data
            temperature=0.0,
        )
        content = (response.content or "[]").strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        rel_list = _json.loads(content)
        for r in rel_list:
            relations[r["id"]] = r.get("relationship", "background")
            for target in r.get("related_to", []):
                cross_refs.append((r["id"], target))
    except Exception:
        pass  # Fix 5: graceful degradation — all refs get "background" color

    # --- Build graph data ---
    # Fix 6: use actual paper title for the P node, not hardcoded "本文"
    paper = {
        "id": "P",
        "label": paper_title[:80],
        "group": "paper",
    }
    nodes = [paper]
    for ref in refs:
        rid = ref.get("id", "?")
        rtitle = ref.get("title", "?")[:60]
        ryear = ref.get("year", "?")
        nodes.append({
            "id": rid,
            "label": f"{rtitle} ({ryear})" if ryear != "?" else rtitle,
            "group": relations.get(rid, "background"),
        })

    links: list[dict] = []
    seen_ref_ids = {n["id"] for n in nodes}
    for ref in refs:
        rid = ref.get("id", "?")
        links.append({"source": "P", "target": rid})
    # Fix 4: inter-reference edges from LLM classification
    for src, tgt in cross_refs:
        if src in seen_ref_ids and tgt in seen_ref_ids:
            links.append({"source": src, "target": tgt})

    return _wrap_graph(nodes, links, paper_title)


def _wrap_graph(
    nodes: list[dict],
    links: list[dict],
    title: str,
    message: str | None = None,
) -> str:
    """Generate a self-contained HTML page with an interactive D3.js citation graph.

    Fix 1 & 2: Data is embedded as JSON — no Mermaid text, no regex parsing,
    no template-literal injection. The JS parses JSON directly.
    """
    safe_title = _js_escape(title[:80])
    nodes_json = _json_embed(nodes)
    links_json = _json_embed(links)
    msg_json = _json_embed(message) if message else "null"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  body {{
    font-family: system-ui, -apple-system, sans-serif;
    background: #0A0B1F; color: #e0e0e0;
    padding: 24px; margin: 0;
  }}
  h1 {{ color: #c4b5fd; font-size: 1.2rem; margin: 0 0 16px; }}
  h1 span {{ font-size: 12px; color: #888; }}
  #graph {{
    width: 100%; height: 75vh;
    border-radius: 12px; background: #111338;
  }}
  .links line {{ stroke: #555; stroke-opacity: 0.6; }}
  .nodes circle {{ stroke: #fff; stroke-width: 1.5px; cursor: pointer; }}
  .nodes text {{ fill: #c0c0d0; font-size: 10px; pointer-events: none; }}
  .legend {{ display: flex; gap: 16px; margin-top: 12px; font-size: 11px; color: #888; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
</style>
</head>
<body>
<h1>
  Citation Graph: {safe_title}
  <span>(drag nodes &bull; scroll to zoom)</span>
</h1>
<div id="graph"></div>
<div class="legend">
  <div class="legend-item"><span class="legend-dot" style="background:#534AB7"></span>This Paper</div>
  <div class="legend-item"><span class="legend-dot" style="background:#E6F1FB"></span>Foundation</div>
  <div class="legend-item"><span class="legend-dot" style="background:#FAEEDA"></span>Comparison</div>
  <div class="legend-item"><span class="legend-dot" style="background:#EAF3DE"></span>Background</div>
</div>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
(function() {{
  var colors = {{
    paper: "#534AB7",
    foundation: "#E6F1FB",
    comparison: "#FAEEDA",
    background: "#EAF3DE"
  }};

  var nodes = {nodes_json};
  var links = {links_json};
  var message = {msg_json};

  if (message) {{
    document.getElementById("graph").innerHTML =
      '<p style="padding:40px;color:#888;text-align:center">' + message + '</p>';
    return;
  }}

  if (!nodes.length) {{
    document.getElementById("graph").innerHTML =
      '<p style="padding:40px;color:#888;text-align:center">No references to display.</p>';
    return;
  }}

  var W = 900, H = 600;
  var svg = d3.select("#graph")
    .append("svg")
    .attr("viewBox", "0 0 " + W + " " + H)
    .style("background", "#111338")
    .style("border-radius", "12px");

  var g = svg.append("g");

  var sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id(function(d) {{ return d.id; }}).distance(120))
    .force("charge", d3.forceManyBody().strength(-400))
    .force("center", d3.forceCenter(W / 2, H / 2))
    .force("collision", d3.forceCollide().radius(40));

  var link = g.selectAll("line")
    .data(links)
    .join("line")
    .attr("stroke", "#555")
    .attr("stroke-opacity", 0.6)
    .attr("stroke-width", function(d) {{
      return d.source.id === "P" || d.target.id === "P" ? 1.5 : 0.8;
    }})
    .attr("stroke-dasharray", function(d) {{
      return d.source.id !== "P" && d.target.id !== "P" ? "4,2" : null;
    }});

  var node = g.selectAll(".node")
    .data(nodes)
    .join("g")
    .attr("class", "node")
    .call(d3.drag()
      .on("start", function(e, d) {{
        if (!e.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      }})
      .on("drag", function(e, d) {{
        d.fx = e.x; d.fy = e.y;
      }})
      .on("end", function(e, d) {{
        if (!e.active) sim.alphaTarget(0);
        d.fx = null; d.fy = null;
      }})
    );

  node.append("circle")
    .attr("r", function(d) {{ return d.group === "paper" ? 14 : 9; }})
    .attr("fill", function(d) {{ return colors[d.group] || "#666"; }})
    .append("title")
    .text(function(d) {{ return (d.label || d.id) + "\\n" + d.group; }});

  node.append("text")
    .text(function(d) {{ return d.label || d.id; }})
    .attr("dx", 16)
    .attr("dy", 4)
    .style("fill", function(d) {{ return d.group === "paper" ? "#c4b5fd" : "#c0c0d0"; }})
    .style("font-size", function(d) {{ return d.group === "paper" ? "12px" : "10px"; }})
    .style("font-weight", function(d) {{ return d.group === "paper" ? "600" : "400"; }});

  sim.on("tick", function() {{
    link
      .attr("x1", function(d) {{ return d.source.x; }})
      .attr("y1", function(d) {{ return d.source.y; }})
      .attr("x2", function(d) {{ return d.target.x; }})
      .attr("y2", function(d) {{ return d.target.y; }});
    node.attr("transform", function(d) {{
      return "translate(" + d.x + "," + d.y + ")";
    }});
  }});

  svg.call(d3.zoom()
    .scaleExtent([0.5, 4])
    .on("zoom", function(e) {{ g.attr("transform", e.transform); }})
  );
}})();
</script>
</body>
</html>"""
