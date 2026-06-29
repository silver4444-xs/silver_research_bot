"""论文检索工具 — 统一搜索 arXiv / Semantic Scholar / PubMed / DBLP"""

from __future__ import annotations

import asyncio
import os
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import httpx

from silver_research_bot.agent.tools.base import Tool, tool_parameters
from silver_research_bot.agent.tools.schema import (
    IntegerSchema, StringSchema, tool_parameters_schema,
)

_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}
UA = "SilverResearchBot/0.2 (mailto:researcher@example.com)"


@tool_parameters(tool_parameters_schema(
    query=StringSchema("Search query (keywords, title, author, or paper ID)"),
    source=StringSchema("Database: arxiv, semanticscholar, pubmed, dblp, or all"),
    max_results=IntegerSchema(5, description="Max results (1-10)", minimum=1, maximum=10),
    year_from=IntegerSchema(2020, description="Filter from this year"),
    required=["query"],
))
class PaperSearchTool(Tool):
    """Search academic papers across arXiv, Semantic Scholar, PubMed, DBLP in parallel."""

    name = "paper_search"
    description = (
        "Search academic papers across multiple databases (arXiv, Semantic Scholar, PubMed, DBLP). "
        "Returns title, authors, year, abstract snippet. Use source='all' for parallel search."
    )

    def __init__(self, pubmed_email: str = "", semantic_scholar_key: str = ""):
        self.pubmed_email = pubmed_email or os.environ.get("PUBMED_EMAIL", "")
        self.ss_key = semantic_scholar_key or os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    async def _client_get(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0), headers={"User-Agent": UA})
        return self._client

    async def execute(self, query: str, source: str = "all", max_results: int = 5, year_from: int = 2020, **kwargs: Any) -> str:
        n = min(max(max_results, 1), 10)
        sources = [s.strip().lower() for s in source.split(",")]
        if "all" in sources:
            sources = ["arxiv", "semanticscholar", "pubmed", "dblp"]

        tasks = {}
        if "arxiv" in sources:
            tasks["arXiv"] = self._search_arxiv(query, n, year_from)
        if "semanticscholar" in sources:
            tasks["SemanticScholar"] = self._search_semantic_scholar(query, n, year_from)
        if "pubmed" in sources:
            tasks["PubMed"] = self._search_pubmed(query, n, year_from)
        if "dblp" in sources:
            tasks["DBLP"] = self._search_dblp(query, n, year_from)

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        parts = []
        for (src, _), result in zip(tasks.items(), results):
            text = f"Error: {result}" if isinstance(result, Exception) else str(result)
            parts.append(f"## {src}\n{text}")
        return "\n\n".join(parts) if parts else "No results."

    # ── arXiv API (free, no key) ──────────────────────────────

    async def _search_arxiv(self, query: str, n: int, year_from: int) -> str:
        url = f"http://export.arxiv.org/api/query?search_query=all:{quote(query)}&start=0&max_results={n}&sortBy=relevance&sortOrder=descending"
        client = await self._client_get()
        resp = await client.get(url)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        entries = root.findall("atom:entry", _ARXIV_NS)
        if not entries:
            return "No results."
        lines = []
        for i, e in enumerate(entries[:n], 1):
            title = self._xe(e, "atom:title")
            authors = ", ".join(
                (a.find("atom:name", _ARXIV_NS) or ET.Element("")).text or ""
                for a in e.findall("atom:author", _ARXIV_NS)
            )[:150]
            year = self._xe(e, "atom:published")[:4]
            summary = self._xe(e, "atom:summary")[:300]
            arxiv_id = next(
                (l.get("href", "").split("/")[-1] for l in e.findall("atom:link", _ARXIV_NS)
                 if l.get("rel") == "alternate"),
                "",
            )
            link = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
            lines.append(f"{i}. **{title}** | {year} | {arxiv_id}\n   {authors}\n   {summary}\n   {link}")
        return "\n\n".join(lines)

    def _xe(self, elem, tag):
        c = elem.find(tag, _ARXIV_NS)
        return (c.text or "").strip() if c is not None else ""

    # ── Semantic Scholar API (free, optional key) ─────────────

    async def _search_semantic_scholar(self, query: str, n: int, year_from: int) -> str:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params: dict = {"query": query, "limit": n, "fields": "title,authors,year,abstract,externalIds,url"}
        if year_from > 2000:
            params["year"] = f"{year_from}-"
        headers = {"x-api-key": self.ss_key} if self.ss_key else {}
        client = await self._client_get()
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        papers = resp.json().get("data", [])
        if not papers:
            return "No results."
        lines = []
        for i, p in enumerate(papers[:n], 1):
            title = p.get("title", "Unknown")
            authors = ", ".join(a.get("name", "") for a in (p.get("authors") or []))[:150]
            year = p.get("year", "?")
            abstract = (p.get("abstract") or "")[:300]
            doi = (p.get("externalIds") or {}).get("DOI", "")
            paper_url = p.get("url", "")
            citation_count = p.get("citationCount", "N/A")
            lines.append(f"{i}. **{title}** | {year} | Citations: {citation_count}\n   {authors}\n   DOI: {doi}\n   {abstract}\n   {paper_url}")
        return "\n\n".join(lines)

    # ── PubMed Entrez API (free, requires email) ──────────────

    async def _search_pubmed(self, query: str, n: int, year_from: int) -> str:
        if not self.pubmed_email:
            return "Requires pubmed_email config for NCBI rate limiting."
        base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
        client = await self._client_get()
        resp = await client.get(f"{base}/esearch.fcgi", params={
            "db": "pubmed", "term": query, "retmax": n, "retmode": "json",
            "sort": "relevance", "mindate": f"{year_from}", "maxdate": "2026",
            "datetype": "pdat", "email": self.pubmed_email,
        })
        resp.raise_for_status()
        ids = resp.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return "No results."
        resp2 = await client.get(f"{base}/efetch.fcgi", params={
            "db": "pubmed", "id": ",".join(ids), "retmode": "xml", "email": self.pubmed_email,
        })
        resp2.raise_for_status()
        root = ET.fromstring(resp2.text)
        lines = []
        for i, art in enumerate(root.findall(".//PubmedArticle")[:n], 1):
            t = art.find(".//ArticleTitle")
            title = t.text if t is not None and t.text else "Unknown"
            authors = ", ".join(
                f"{a.findtext('LastName', '')} {a.findtext('ForeName', '')}".strip()
                for a in art.findall(".//Author")[:5]
            )[:150]
            y = art.find(".//PubDate/Year")
            year = y.text if y is not None else "?"
            ab = art.find(".//AbstractText")
            abstract = (ab.text or "")[:300] if ab is not None else ""
            pmid = (art.find(".//PMID") or ET.Element("")).text or ""
            lines.append(f"{i}. **{title}** | {year} | PMID:{pmid}\n   {authors}\n   {abstract}")
        return "\n\n".join(lines)

    # ── DBLP API (free, no key) ───────────────────────────────

    async def _search_dblp(self, query: str, n: int, year_from: int) -> str:
        url = "https://dblp.org/search/publ/api"
        client = await self._client_get()
        resp = await client.get(url, params={"q": query, "h": n, "format": "json"})
        resp.raise_for_status()
        hits = resp.json().get("result", {}).get("hits", {}).get("hit", []) or []
        if not hits:
            return "No results."
        lines = []
        for i, hit in enumerate(hits[:n], 1):
            info = hit.get("info", {})
            title = info.get("title", "Unknown")
            authors_list = info.get("authors", {}).get("author", []) or []
            if isinstance(authors_list, dict):
                authors_list = [authors_list]
            authors = ", ".join(
                (a.get("text", "") if isinstance(a, dict) else str(a)) for a in authors_list
            )[:150]
            year = info.get("year", "?")
            venue = info.get("venue", "")
            doi = info.get("doi", "")
            paper_url = info.get("url", "")
            lines.append(f"{i}. **{title}** | {year} | {venue}\n   {authors}\n   DOI: {doi}\n   {paper_url}")
        return "\n\n".join(lines)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
