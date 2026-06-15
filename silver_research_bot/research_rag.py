"""科研文献检索 RAG 模块 — 混合检索 (BM25 + 向量) + LLM 重排序 + 多模态 + 增量更新"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from silver_research_bot.config.paths import get_workspace_path
from silver_research_bot.research_bm25 import BM25Scorer

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider


@dataclass
class PaperChunk:
    """文献切片。"""

    chunk_id: str
    paper_id: str
    title: str
    section: str
    text: str
    source: str = "manual"
    chunk_type: str = "text"  # "text" | "formula" | "figure" | "table"
    metadata: dict[str, Any] = field(default_factory=dict)


class ResearchRAG:
    """混合检索 RAG：BM25 + 向量相似度融合 + LLM 重排序 + 多模态 + 增量更新。"""

    def __init__(
        self,
        root: Path | None = None,
        provider: "LLMProvider | None" = None,
        config: Any | None = None,
    ) -> None:
        self.root = root or (get_workspace_path() / "research_rag")
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.corpus_path = self.root / "corpus.jsonl"
        self.logs_path = self.root / "logs.jsonl"
        self._index = self._load_index()
        self._provider = provider

        self._bm25_weight = 0.3
        self._vector_weight = 0.7
        self._coarse_top_k = 20
        self._final_top_k = 5
        self._rerank_enabled = True
        self._embedding_model = "text-embedding-3-small"
        self._embedding_dim = 1536

        if config:
            self._bm25_weight = getattr(config, "bm25_weight", 0.3)
            self._vector_weight = getattr(config, "vector_weight", 0.7)
            self._coarse_top_k = getattr(config, "coarse_top_k", 20)
            self._final_top_k = getattr(config, "final_top_k", 5)
            self._rerank_enabled = getattr(config, "rerank_enabled", True)
            self._embedding_model = getattr(config, "embedding_model", "text-embedding-3-small")
            self._embedding_dim = getattr(config, "embedding_dimensions", 1536)

        self._bm25 = BM25Scorer()
        self._embedder = None
        self._reranker = None
        self._vector_store = None
        self._initialized = False

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        from silver_research_bot.research_embedder import EmbeddingEngine
        from silver_research_bot.research_vector_store import VectorStore

        self._embedder = EmbeddingEngine(
            provider=self._provider, model=self._embedding_model, dim=self._embedding_dim,
        )
        self._vector_store = VectorStore(self.root / "vectors", dim=self._embedding_dim)
        if self._provider and self._rerank_enabled:
            from silver_research_bot.research_reranker import LLMReranker
            self._reranker = LLMReranker(self._provider, model=self._embedding_model)
        self._rebuild_bm25()
        self._initialized = True

    def _rebuild_bm25(self) -> None:
        self._bm25 = BM25Scorer()
        if not self.corpus_path.exists():
            return
        corpus: list[tuple[str, str]] = []
        for line in self.corpus_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            cid = raw.get("chunk_id", "")
            text = raw.get("text", "")
            if cid and text:
                corpus.append((cid, text))
        if corpus:
            self._bm25.fit(corpus)

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

    def _iter_chunks(self) -> list[PaperChunk]:
        if not self.corpus_path.exists():
            return []
        chunks: list[PaperChunk] = []
        for line in self.corpus_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            chunks.append(PaperChunk(
                chunk_id=raw.get("chunk_id", ""),
                paper_id=raw.get("paper_id", ""),
                title=raw.get("title", ""),
                section=raw.get("section", ""),
                text=raw.get("text", ""),
                source=raw.get("source", "manual"),
                chunk_type=raw.get("chunk_type", "text"),
                metadata=raw.get("metadata", {}),
            ))
        return chunks

    # ── Public API ────────────────────────────────────────────

    async def add_paper(
        self, title: str, abstract: str, content: str,
        authors: list[str] | None = None, tags: list[str] | None = None,
        source: str = "manual", paper_id: str | None = None,
        extracted: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_init()
        authors = authors or []
        tags = tags or []
        pid = paper_id or uuid.uuid4().hex[:10]

        if paper_id:
            self._remove_paper_chunks(pid)

        chunks: list[PaperChunk] = []
        sections = self._split_sections(content)
        for section, text in sections:
            full_text = f"{title}\n{abstract}\n{section}\n{text}"
            chunk = PaperChunk(
                chunk_id=uuid.uuid4().hex[:10],
                paper_id=pid, title=title, section=section,
                text=full_text, source=source, chunk_type="text",
                metadata={"authors": authors, "tags": tags, "abstract": abstract},
            )
            chunks.append(chunk)
            self._append_corpus(chunk)
            self._bm25.add(chunk.chunk_id, full_text)

        if extracted:
            from silver_research_bot.research_multimodal import MultimodalIndexer
            mm_chunks = MultimodalIndexer.index_from_extracted(extracted, pid, title)
            for mc in mm_chunks:
                chunks.append(mc)
                self._append_corpus(mc)
                self._bm25.add(mc.chunk_id, mc.text)

        texts = [c.text for c in chunks]
        ids = [c.chunk_id for c in chunks]
        metas = [{"paper_id": c.paper_id, "title": c.title, "chunk_type": c.chunk_type} for c in chunks]
        vectors = await self._embedder.embed_batch(texts)
        self._vector_store.add(vectors, ids, metas)

        paper_record = {
            "paper_id": pid, "title": title, "abstract": abstract,
            "authors": authors, "tags": tags,
            "chunk_count": len(chunks),
            "has_multimodal": extracted is not None,
            "created_at": self.now(),
        }
        existing = next((i for i, e in enumerate(self._index) if e.get("paper_id") == pid), None)
        if existing is not None:
            paper_record["created_at"] = self._index[existing].get("created_at", paper_record["created_at"])
            self._index[existing] = paper_record
        else:
            self._index.append(paper_record)
        self._save_index()
        self._append_log("ingest", paper_record)
        return paper_record

    def _remove_paper_chunks(self, paper_id: str) -> None:
        if not self._initialized:
            return
        chunks = self._iter_chunks()
        to_remove = [c.chunk_id for c in chunks if c.paper_id == paper_id]
        if to_remove:
            for cid in to_remove:
                self._bm25.remove(cid)
            self._vector_store.remove(to_remove)

    async def update_paper(
        self, paper_id: str, title: str, abstract: str, content: str,
        authors: list[str] | None = None, tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self.add_paper(
            title=title, abstract=abstract, content=content,
            authors=authors, tags=tags, paper_id=paper_id,
        )

    def delete_paper(self, paper_id: str) -> bool:
        self._ensure_init()
        self._remove_paper_chunks(paper_id)
        self._index = [e for e in self._index if e.get("paper_id") != paper_id]
        self._save_index()
        self._append_log("delete", {"paper_id": paper_id})
        return True

    def list_papers(self) -> list[dict[str, Any]]:
        return list(reversed(self._index))

    async def search(
        self, query: str, top_k: int = 5, tag: str | None = None,
        modality: str | None = None, rerank: bool = True,
    ) -> dict[str, Any]:
        self._ensure_init()
        coarse_k = self._coarse_top_k
        final_k = top_k or self._final_top_k

        bm25_results = self._bm25.search(query, coarse_k)
        bm25_scores = {cid: s for cid, s in bm25_results}

        query_vec = await self._embedder.embed_single(query)
        vec_results = self._vector_store.search(query_vec, coarse_k)
        vec_scores = {cid: s for cid, s in vec_results}

        all_cids = set(bm25_scores) | set(vec_scores)
        bm25_vals = list(bm25_scores.values())
        bm25_min, bm25_max = (min(bm25_vals), max(bm25_vals)) if bm25_vals else (0, 1)
        vec_vals = list(vec_scores.values())
        vec_min, vec_max = (min(vec_vals), max(vec_vals)) if vec_vals else (0, 1)

        fused: dict[str, float] = {}
        for cid in all_cids:
            bm_n = (bm25_scores.get(cid, 0) - bm25_min) / max(bm25_max - bm25_min, 0.001)
            v_n = (vec_scores.get(cid, 0) - vec_min) / max(vec_max - vec_min, 0.001)
            fused[cid] = self._bm25_weight * bm_n + self._vector_weight * v_n

        sorted_cids = sorted(fused.items(), key=lambda x: x[1], reverse=True)
        chunk_map = {c.chunk_id: c for c in self._iter_chunks()}
        filtered: list[tuple[str, str, float]] = []
        for cid, score in sorted_cids:
            chunk = chunk_map.get(cid)
            if not chunk:
                continue
            if tag and tag not in (chunk.metadata.get("tags") or []):
                continue
            if modality and chunk.chunk_type != modality:
                continue
            filtered.append((cid, chunk.text, score))

        if rerank and self._reranker and len(filtered) > final_k:
            candidates = [(cid, text) for cid, text, _ in filtered[:coarse_k]]
            reranked = await self._reranker.rerank(query, candidates, final_k)
            rm = dict(reranked)
            filtered = [(cid, text, rm.get(cid, sc)) for cid, text, sc in filtered]
            filtered.sort(key=lambda x: x[2], reverse=True)

        results = []
        for cid, _, score in filtered[:final_k]:
            chunk = chunk_map[cid]
            results.append({
                "score": round(score, 6),
                "chunk_id": chunk.chunk_id,
                "paper_id": chunk.paper_id,
                "title": chunk.title,
                "section": chunk.section,
                "text": chunk.text[:1200],
                "chunk_type": chunk.chunk_type,
                "metadata": chunk.metadata,
            })

        self._append_log("search", {"query": query, "top_k": final_k, "tag": tag, "modality": modality, "hits": len(results)})
        return {"query": query, "top_k": final_k, "tag": tag, "modality": modality, "results": results}

    async def build_context(self, query: str, top_k: int = 5, max_chars: int = 6000) -> dict[str, Any]:
        sr = await self.search(query, top_k=top_k)
        lines = [f"# 研究上下文\n\n查询：{query}\n"]
        total = 0
        for item in sr["results"]:
            block = f"## {item['title']} / {item['section']}\n分数：{item['score']:.4f}\n[{item.get('chunk_type', 'text')}]\n\n{item['text']}\n"
            if total + len(block) > max_chars:
                break
            lines.append(block)
            total += len(block)
        return {"query": query, "context": "\n".join(lines).strip(), "results": sr["results"]}

    async def suggest_research(self, query: str, top_k: int = 5) -> dict[str, Any]:
        ctx = await self.build_context(query, top_k=top_k)
        titles = [item["title"] for item in ctx["results"][:3]]
        return {
            "query": query,
            "key_papers": titles,
            "research_hint": f"结合 {', '.join(titles) if titles else '已有文献'} 提炼问题、假设和可验证指标。",
            "context": ctx["context"],
        }

    def export_snapshot(self) -> dict[str, Any]:
        return {
            "paper_count": len(self._index),
            "chunk_count": sum(item.get("chunk_count", 0) for item in self._index),
            "updated_at": self.now(),
        }

    async def reindex(self) -> dict[str, Any]:
        self._ensure_init()
        self._rebuild_bm25()
        if self._embedder and self._vector_store:
            self._vector_store = type(self._vector_store)(self.root / "vectors", dim=self._embedding_dim)
            chunks = self._iter_chunks()
            if chunks:
                texts = [c.text for c in chunks]
                ids = [c.chunk_id for c in chunks]
                metas = [{"paper_id": c.paper_id, "title": c.title, "chunk_type": c.chunk_type} for c in chunks]
                vectors = await self._embedder.embed_batch(texts)
                self._vector_store.add(vectors, ids, metas)
        return {"status": "ok", "chunk_count": len(self._iter_chunks())}

    @staticmethod
    def _split_sections(content: str) -> list[tuple[str, str]]:
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
