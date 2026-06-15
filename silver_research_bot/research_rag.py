"""科研文献检索 RAG 模块。"""

from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from silver_research_bot.config.paths import get_workspace_path


@dataclass
class PaperChunk:
    """文献切片。"""

    chunk_id: str
    paper_id: str
    title: str
    section: str
    text: str
    source: str = "manual"
    metadata: dict[str, Any] = field(default_factory=dict)


class ResearchRAG:
    """轻量级本地 RAG：支持文献入库、检索与上下文组装。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (get_workspace_path() / "research_rag")
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.corpus_path = self.root / "corpus.jsonl"
        self.logs_path = self.root / "logs.jsonl"
        self._index = self._load_index()

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _save_index(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self._index, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_corpus(self, chunk: PaperChunk) -> None:
        with self.corpus_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")

    def _append_log(self, action: str, payload: dict[str, Any]) -> None:
        with self.logs_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": self.now(), "action": action, "payload": payload}, ensure_ascii=False) + "\n")

    def _normalize(self, text: str) -> list[str]:
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
        return tokens

    def _vectorize(self, text: str) -> dict[str, float]:
        tokens = self._normalize(text)
        counts: dict[str, float] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0.0) + 1.0
        norm = math.sqrt(sum(v * v for v in counts.values())) or 1.0
        return {k: v / norm for k, v in counts.items()}

    def _cosine(self, a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        if len(a) > len(b):
            a, b = b, a
        return sum(value * b.get(key, 0.0) for key, value in a.items())

    def add_paper(
        self,
        title: str,
        abstract: str,
        content: str,
        authors: list[str] | None = None,
        tags: list[str] | None = None,
        source: str = "manual",
    ) -> dict[str, Any]:
        """将文献入库，并按段切片。"""
        paper_id = uuid.uuid4().hex[:10]
        authors = authors or []
        tags = tags or []
        sections = self._split_sections(content)
        chunks: list[PaperChunk] = []
        for section, text in sections:
            chunk = PaperChunk(
                chunk_id=uuid.uuid4().hex[:10],
                paper_id=paper_id,
                title=title,
                section=section,
                text=text,
                source=source,
                metadata={"authors": authors, "tags": tags, "abstract": abstract},
            )
            chunk.metadata["embedding"] = self._vectorize(f"{title}\n{abstract}\n{section}\n{text}")
            chunks.append(chunk)
            self._append_corpus(chunk)

        paper_record = {
            "paper_id": paper_id,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "tags": tags,
            "chunk_count": len(chunks),
            "created_at": self.now(),
        }
        self._index.append(paper_record)
        self._save_index()
        self._append_log("ingest", paper_record)
        return paper_record

    def _split_sections(self, content: str) -> list[tuple[str, str]]:
        parts = [p.strip() for p in re.split(r"\n{2,}", content) if p.strip()]
        if not parts:
            return [("全文", content.strip())]
        sections: list[tuple[str, str]] = []
        for idx, part in enumerate(parts, start=1):
            lines = part.splitlines()
            head = lines[0][:40] if lines else f"Section {idx}"
            body = "\n".join(lines[1:]).strip() if len(lines) > 1 else part
            sections.append((head or f"Section {idx}", body or part))
        return sections

    def list_papers(self) -> list[dict[str, Any]]:
        return list(reversed(self._index))

    def _iter_chunks(self) -> list[PaperChunk]:
        if not self.corpus_path.exists():
            return []
        chunks: list[PaperChunk] = []
        for line in self.corpus_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            chunks.append(PaperChunk(**{k: raw[k] for k in ["chunk_id", "paper_id", "title", "section", "text", "source", "metadata"]}))
        return chunks

    def search(self, query: str, top_k: int = 5, tag: str | None = None) -> dict[str, Any]:
        query_vec = self._vectorize(query)
        scored: list[dict[str, Any]] = []
        for chunk in self._iter_chunks():
            if tag and tag not in (chunk.metadata.get("tags") or []):
                continue
            chunk_vec = chunk.metadata.get("embedding") or self._vectorize(f"{chunk.title}\n{chunk.section}\n{chunk.text}")
            score = self._cosine(query_vec, chunk_vec)
            scored.append({
                "score": round(score, 6),
                "chunk_id": chunk.chunk_id,
                "paper_id": chunk.paper_id,
                "title": chunk.title,
                "section": chunk.section,
                "text": chunk.text,
                "metadata": chunk.metadata,
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        results = scored[:top_k]
        self._append_log("search", {"query": query, "top_k": top_k, "tag": tag, "hits": len(results)})
        return {"query": query, "top_k": top_k, "tag": tag, "results": results}

    def build_context(self, query: str, top_k: int = 5, max_chars: int = 6000) -> dict[str, Any]:
        search_result = self.search(query, top_k=top_k)
        lines = [f"# 研究上下文\n\n查询：{query}\n"]
        total = 0
        for item in search_result["results"]:
            block = f"## {item['title']} / {item['section']}\n分数：{item['score']:.4f}\n\n{item['text']}\n"
            if total + len(block) > max_chars:
                break
            lines.append(block)
            total += len(block)
        context = "\n".join(lines).strip()
        return {"query": query, "context": context, "results": search_result["results"]}

    def suggest_research(self, query: str, top_k: int = 5) -> dict[str, Any]:
        context = self.build_context(query, top_k=top_k)
        titles = [item["title"] for item in context["results"][:3]]
        return {
            "query": query,
            "key_papers": titles,
            "research_hint": f"结合 {', '.join(titles) if titles else '已有文献'} 提炼问题、假设和可验证指标。",
            "context": context["context"],
        }

    def export_snapshot(self) -> dict[str, Any]:
        return {
            "paper_count": len(self._index),
            "chunk_count": sum(item.get("chunk_count", 0) for item in self._index),
            "updated_at": self.now(),
        }
