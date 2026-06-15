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
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{font-family:system-ui;background:#0A0B1F;color:#e0e0e0;padding:24px}}
h1{{color:#c4b5fd}} .mermaid{{background:#111338;border-radius:12px;padding:16px;margin-top:16px}}</style>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true,theme:'dark'}})</script></head><body>
<h1>Citation Graph: {title}</h1><pre class="mermaid">{mermaid}</pre></body></html>"""
