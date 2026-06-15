"""Stage 5: 引用图谱 — LLM 提取参考文献 + Mermaid DAG 可视化"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

_EXTRACT_PROMPT = """Extract all references/citations from the paper text below. For each reference output JSON with id (R1,R2...), title, authors (first author et al.), year, venue. Only output JSON array, no other text.

Paper text (end of document where references appear):
{text}"""

_DAG_PROMPT = """Classify each reference's relationship to the paper:
- "foundation": paper builds upon this
- "comparison": paper compares against this
- "background": general context

Paper: {title}
References: {refs}

Output JSON array with id and relationship fields. Only JSON."""


async def extract_references(full_text: str, provider: "LLMProvider", model: str) -> list[dict]:
    text = full_text[-8000:]
    try:
        import json as _json
        response = await provider.chat_with_retry(
            model=model,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(text=text)}],
            tools=None, max_tokens=2000, temperature=0.0,
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
    if not refs:
        return _wrap("<p>No references extracted.</p>", paper_title)

    try:
        import json as _json
        refs_text = "\n".join(
            f"{r.get('id','?')}: {r.get('title','?')} ({r.get('year','?')})" for r in refs
        )
        response = await provider.chat_with_retry(
            model=model,
            messages=[{"role": "user", "content": _DAG_PROMPT.format(title=paper_title, refs=refs_text)}],
            tools=None, max_tokens=500, temperature=0.0,
        )
        content = (response.content or "[]").strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        relations = {r["id"]: r.get("relationship", "background") for r in _json.loads(content)}
    except Exception:
        relations = {}

    safe_title = paper_title.replace('"', "'")[:50]
    mermaid = "flowchart TD\n"
    mermaid += f'  P["{safe_title}"]\n  P:::paper\n'
    for ref in refs[:15]:
        rid = ref.get("id", "?")
        rtitle = ref.get("title", "?")[:40].replace('"', "'")
        mermaid += f'  {rid}["{rtitle}<br/>{ref.get("year","?")}"]\n'
        mermaid += f'  {rid}:::{relations.get(rid, "background")}\n  P --> {rid}\n'
    mermaid += "  classDef paper fill:#534AB7,color:#fff\n"
    mermaid += "  classDef foundation fill:#E6F1FB,stroke:#185FA5\n"
    mermaid += "  classDef comparison fill:#FAEEDA,stroke:#854F0B\n"
    mermaid += "  classDef background fill:#EAF3DE,stroke:#3B6D11\n"
    return _wrap(mermaid, paper_title)


def _wrap(mermaid: str, title: str) -> str:
    # D3.js interactive force-directed citation graph
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{font-family:system-ui;background:#0A0B1F;color:#e0e0e0;padding:24px;margin:0}}
h1{{color:#c4b5fd;font-size:1.2rem}} #graph{{width:100%;height:70vh;border-radius:12px;background:#111338}}
.links line{{stroke:#555;stroke-opacity:0.6}} .nodes circle{{stroke:#fff;stroke-width:1.5px;cursor:pointer}}
.nodes text{{fill:#c0c0d0;font-size:10px}}</style></head><body>
<h1>Citation Graph: {title} <span style="font-size:12px;color:#888">(drag nodes | scroll to zoom)</span></h1>
<div id="graph"></div>
<script src="https://d3js.org/d3.v7.min.js"></script><script>
const colors={{paper:"#534AB7",foundation:"#E6F1FB",comparison:"#FAEEDA",background:"#EAF3DE"}};
const nodes=[],links=[];
const ls=`{mermaid}`.split("\\n");
for(const l of ls){{const nm=l.match(/^\\s*(\\w+)\\[/);const ar=l.match(/^\\s*(\\w+)\\s*-->\\s*(\\w+)/);
if(nm&&nm[1]!=="P")nodes.push({{id:nm[1],group:"background"}});
if(ar)links.push({{source:ar[1],target:ar[2]}});
const cl=l.match(/:::(\\w+)/);if(cl&&nodes.length)nodes[nodes.length-1].group=cl[1];}}
nodes.unshift({{id:"Paper",group:"paper"}});
const W=800,H=500,svg=d3.select("#graph").append("svg").attr("viewBox",`0 0 ${{W}} ${{H}}`).style("background","#111338").style("border-radius","12px");
const g=svg.append("g"),sim=d3.forceSimulation(nodes).force("link",d3.forceLink(links).id(d=>d.id).distance(100)).force("charge",d3.forceManyBody().strength(-300)).force("center",d3.forceCenter(W/2,H/2));
const link=g.selectAll("line").data(links).join("line").attr("stroke","#555").attr("stroke-opacity",0.6).attr("stroke-width",1.5);
const node=g.selectAll(".node").data(nodes).join("g").attr("class","node").call(d3.drag().on("start",(e,d)=>{{if(!e.active)sim.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y;}}).on("drag",(e,d)=>{{d.fx=e.x;d.fy=e.y;}}).on("end",(e,d)=>{{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}}));
node.append("circle").attr("r",d=>d.id==="Paper"?14:9).attr("fill",d=>colors[d.group]||"#666").append("title").text(d=>d.id+"\\n"+d.group);
node.append("text").text(d=>d.id).attr("dx",16).attr("dy",4).style("fill","#c0c0d0").style("font-size","10px");
sim.on("tick",()=>{{link.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y).attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);node.attr("transform",d=>`translate(${{d.x}},${{d.y}})`);}});
svg.call(d3.zoom().scaleExtent([0.5,3]).on("zoom",e=>g.attr("transform",e.transform)));
</script></body></html>"""
