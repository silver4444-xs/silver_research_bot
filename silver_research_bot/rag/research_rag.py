"""科研文献检索 RAG 模块 — 查询扩展 + 混合检索 (BM25 + 向量) + RRF 融合 + LLM 重排序 + 增量更新"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from filelock import FileLock
from loguru import logger
from pydantic import BaseModel, Field

from silver_research_bot.config.paths import get_workspace_path
from silver_research_bot.rag.research_bm25 import BM25Scorer

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider


_tiktoken_enc = None
_tiktoken_tried = False

# RRF 融合常量：score = Σ 1/(_RRF_K + rank + 1)
_RRF_K = 60.0

# debug 模式下 dropped 明细的条数上限。超出即截断并置 dropped_truncated，
# 不静默丢弃——"收全了"和"收到上限"对验收是两回事。
_DEBUG_DROPPED_LIMIT = 200

# 重排前 top-K 快照里每条正文的截断长度。比 results 的 1200 短：这份快照是补充视角，
# 与 results 大面积重叠，只需够人判断相关与否即可。
_DEBUG_SNAPSHOT_TEXT_CHARS = 600


def _get_tiktoken():
    """惰性加载 tiktoken cl100k_base（匹配 text-embedding-3-small）；不可用返回 None。"""
    global _tiktoken_enc, _tiktoken_tried
    if not _tiktoken_tried:
        _tiktoken_tried = True
        try:
            import tiktoken
            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _tiktoken_enc = None
    return _tiktoken_enc


# 保守同义词表：无 provider 时的规则型查询扩展（术语 → 语义等价/双语变体）
_QUERY_SYNONYMS: dict[str, list[str]] = {
    "attention": ["self-attention", "注意力机制"],
    "transformer": ["self-attention", "编码器-解码器"],
    "rag": ["retrieval-augmented generation", "检索增强生成"],
    "llm": ["large language model", "大语言模型"],
    "rlhf": ["reinforcement learning from human feedback", "人类反馈强化学习"],
    "embedding": ["向量表示", "表征学习"],
    "ner": ["named entity recognition", "命名实体识别"],
}


def _offline_query_expansion(query: str) -> list[str]:
    """无 provider 时的规则型查询扩展：命中同义词表则生成替换变体，返回 [原始 query, ...变体]（去重）。"""
    variants: list[str] = [query]
    for term, synonyms in _QUERY_SYNONYMS.items():
        if not re.search(re.escape(term), query, re.IGNORECASE):
            continue
        for syn in synonyms:
            variant = re.sub(re.escape(term), syn, query, flags=re.IGNORECASE)
            if variant and variant != query and variant not in variants:
                variants.append(variant)
    return variants


class PaperChunk(BaseModel):
    """文献切片。"""

    chunk_id: str
    paper_id: str
    title: str
    section: str
    text: str
    source: str = "manual"
    chunk_type: str = "text"  # "text" | "analysis"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchRAG:
    """混合检索 RAG：BM25 + 向量相似度融合 + LLM 重排序 + 增量更新。"""

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

        self._coarse_top_k = 20
        self._final_top_k = 5
        self._rerank_enabled = True
        self._embedding_model = "text-embedding-3-small"
        self._embedding_dim = 1536
        self._embed_cache_enabled = True
        self._rerank_model: str | None = None
        self._rerank_cache_ttl = 3600
        self._rerank_cache_max = 512
        self._rerank_max_tokens = 512
        self._rerank_candidate_max_chars = 1000
        self._chunk_size = 800
        self._chunk_overlap = 25
        self._min_score = 0.0
        self._query_expansion_enabled = True
        self._query_expansion_count = 3
        self._query_expansion_cache_ttl = 3600
        self._query_expansion_cache_max = 512

        if config:
            self._coarse_top_k = getattr(config, "coarse_top_k", 20)
            self._final_top_k = getattr(config, "final_top_k", 5)
            self._rerank_enabled = getattr(config, "rerank_enabled", True)
            self._embedding_model = getattr(config, "embedding_model", "text-embedding-3-small")
            self._embedding_dim = getattr(config, "embedding_dimensions", 1536)
            self._embed_cache_enabled = getattr(config, "embed_cache_enabled", True)
            self._rerank_model = getattr(config, "rerank_model", None)
            self._rerank_cache_ttl = getattr(config, "rerank_cache_ttl_seconds", 3600)
            self._rerank_cache_max = getattr(config, "rerank_cache_max_entries", 512)
            self._rerank_max_tokens = getattr(config, "rerank_max_tokens", 512)
            self._rerank_candidate_max_chars = getattr(config, "rerank_candidate_max_chars", 1000)
            self._chunk_size = getattr(config, "chunk_size", 800)
            self._chunk_overlap = getattr(config, "chunk_overlap", 25)
            self._min_score = getattr(config, "min_score", 0.0)
            self._query_expansion_enabled = getattr(config, "query_expansion_enabled", True)
            self._query_expansion_count = getattr(config, "query_expansion_count", 3)
            self._query_expansion_cache_ttl = getattr(config, "query_expansion_cache_ttl_seconds", 3600)
            self._query_expansion_cache_max = getattr(config, "query_expansion_cache_max_entries", 512)

        self._bm25 = BM25Scorer()
        self._embedder = None
        self._reranker = None
        self._vector_store = None
        self._initialized = False

        # 内存 chunk 索引：消除每次 search 全量解析 corpus.jsonl 的 O(n) 开销。
        # 兼容 REST 与 Agent 双实例共享目录——按 mtime/size 新鲜度失效重载。
        self._chunks_by_id: dict[str, PaperChunk] = {}
        self._corpus_stat: tuple[float, int] | None = None
        # 查询扩展结果缓存：query → (过期单调时间, [查询变体])，避免每次检索重复调 LLM
        self._qe_cache: dict[str, tuple[float, list[str]]] = {}
        self._lock = asyncio.Lock()
        # 跨进程文件锁：REST(research_app) 与 Agent 两个实例共享同一目录，
        # asyncio.Lock 仅进程内有效，用 filelock 保证 index.json 读改写 / corpus 重写 / vectors 写的跨实例互斥。
        self._write_lock = FileLock(self.root / ".rag.lock")

    # ── 索引初始化 ────────────────────────────────────────────

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        from silver_research_bot.rag.research_embedder import EmbeddingEngine
        from silver_research_bot.rag.research_vector_store import VectorStore

        self._embedder = EmbeddingEngine(
            provider=self._provider, model=self._embedding_model, dim=self._embedding_dim,
            cache_path=(self.root / "vectors" / "embed_cache.json") if self._embed_cache_enabled else None,
        )
        self._vector_store = VectorStore(self.root / "vectors", dim=self._embedding_dim)
        if self._provider and self._rerank_enabled:
            from silver_research_bot.rag.research_reranker import LLMReranker
            # 重排用 chat 模型（None 时走 provider 默认模型），勿再误传 embedding 模型名
            self._reranker = LLMReranker(
                self._provider, model=self._rerank_model,
                ttl_seconds=self._rerank_cache_ttl, max_entries=self._rerank_cache_max,
                max_tokens=self._rerank_max_tokens, candidate_max_chars=self._rerank_candidate_max_chars,
            )
        self._load_chunks()
        self._rebuild_bm25()
        self._initialized = True

    def _corpus_file_stat(self) -> tuple[float, int] | None:
        if not self.corpus_path.exists():
            return None
        st = self.corpus_path.stat()
        return (st.st_mtime, st.st_size)

    def _load_chunks(self) -> None:
        """从 corpus.jsonl 全量解析进内存索引。"""
        chunks: dict[str, PaperChunk] = {}
        if self.corpus_path.exists():
            for line in self.corpus_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except ValueError:
                    logger.warning("RAG: corpus.jsonl 存在损坏行，已跳过")
                    continue
                chunk = PaperChunk(
                    chunk_id=raw.get("chunk_id", ""),
                    paper_id=raw.get("paper_id", ""),
                    title=raw.get("title", ""),
                    section=raw.get("section", ""),
                    text=raw.get("text", ""),
                    source=raw.get("source", "manual"),
                    chunk_type=raw.get("chunk_type", "text"),
                    metadata=raw.get("metadata", {}),
                )
                if chunk.chunk_id:
                    chunks[chunk.chunk_id] = chunk
        self._chunks_by_id = chunks
        self._corpus_stat = self._corpus_file_stat()

    def _get_chunks(self) -> dict[str, PaperChunk]:
        """返回内存 chunk 索引，若 corpus.jsonl 被外部改动则先重载。"""
        if self._corpus_stat != self._corpus_file_stat():
            self._load_chunks()
        return self._chunks_by_id

    def _rebuild_bm25(self) -> None:
        self._bm25 = BM25Scorer()
        corpus = [(c.chunk_id, c.text) for c in self._get_chunks().values() if c.chunk_id and c.text]
        if corpus:
            self._bm25.fit(corpus)

    def _maybe_sync(self) -> None:
        """若 corpus.jsonl 被外部实例改动，联动重载 chunks 并重建 BM25 / 向量索引。

        双实例共享目录时仅靠 _get_chunks() 重载内存 chunk 不够——BM25 与向量库
        也必须同步，否则新增 chunk 检索不到、删除 chunk 残留成悬空 cid。
        """
        if not self._initialized or self._corpus_stat == self._corpus_file_stat():
            return
        self._load_chunks()
        self._rebuild_bm25()
        if self._vector_store is not None:
            self._vector_store.reload()

    # ── 基础 I/O 工具 ─────────────────────────────────────────

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            # 损坏恢复：备份坏文件后重建空索引，避免整库因一处损坏不可用
            logger.warning("RAG: index.json 损坏，备份后重建空索引: {} ({})", self.index_path, exc)
            try:
                self.index_path.replace(self.index_path.with_suffix(".json.bak"))
            except OSError:
                pass
            return []
        if not isinstance(data, list):
            logger.warning("RAG: index.json 顶层非列表，备份后重建空索引: {}", self.index_path)
            try:
                self.index_path.replace(self.index_path.with_suffix(".json.bak"))
            except OSError:
                pass
            return []
        return data

    def _save_index(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self._index, ensure_ascii=False, indent=2), encoding="utf-8")

    def _index_upsert(self, paper_record: dict[str, Any]) -> None:
        """跨进程原子 upsert 一条 paper 记录（文件锁包裹读改写，防双实例丢更新）。"""
        with self._write_lock:
            idx = self._load_index()
            existing = next((i for i, e in enumerate(idx) if e.get("paper_id") == paper_record["paper_id"]), None)
            if existing is not None:
                paper_record["created_at"] = idx[existing].get("created_at", paper_record["created_at"])
                idx[existing] = paper_record
            else:
                idx.append(paper_record)
            self._index = idx
            self._save_index()

    def _index_remove(self, paper_id: str) -> bool:
        """跨进程原子删除一条 paper 记录，返回是否存在。"""
        with self._write_lock:
            idx = self._load_index()
            if not any(e.get("paper_id") == paper_id for e in idx):
                return False
            self._index = [e for e in idx if e.get("paper_id") != paper_id]
            self._save_index()
            return True

    def _atomic_write_text(self, path: Path, content: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)

    def _append_corpus(self, chunk: PaperChunk) -> None:
        # 文件锁：与 _remove_paper_chunks 的 corpus 重写互斥，避免跨实例 append 与重写交错
        with self._write_lock:
            with self.corpus_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(chunk.model_dump(), ensure_ascii=False) + "\n")
        self._chunks_by_id[chunk.chunk_id] = chunk
        self._corpus_stat = self._corpus_file_stat()

    def _append_log(self, action: str, payload: dict[str, Any]) -> None:
        with self.logs_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": self.now(), "action": action, "payload": payload}, ensure_ascii=False) + "\n")

    def _iter_chunks(self) -> list[PaperChunk]:
        return list(self._get_chunks().values())

    async def _embed_and_index(self, chunks: list[PaperChunk]) -> None:
        """嵌入 + 写向量库（重量级 numpy I/O 放线程池，不阻塞事件循环）。"""
        if not chunks:
            return
        texts = [c.text for c in chunks]
        ids = [c.chunk_id for c in chunks]
        metas = [{"paper_id": c.paper_id, "title": c.title, "chunk_type": c.chunk_type} for c in chunks]
        vectors = await self._embedder.embed_batch(texts)
        # 维度校验：provider 返回维度与配置不符时整批拒收，避免 np.vstack shape mismatch 崩溃
        if vectors and any(len(v) != self._embedding_dim for v in vectors):
            logger.warning(
                "RAG: 嵌入维度与配置不符 (期望 {}, 实际 {})，跳过向量入库，请检查 embedding_dimensions",
                self._embedding_dim, len(vectors[0]),
            )
            return
        await asyncio.to_thread(self._vector_add_locked, vectors, ids, metas)

    def _vector_add_locked(self, vectors: list[list[float]], ids: list[str], metas: list[dict[str, Any]]) -> None:
        """文件锁内写向量库：先 reload 磁盘最新态再 add，避免双实例基于 stale 内存态互相覆盖 numpy。"""
        with self._write_lock:
            self._vector_store.reload()
            self._vector_store.add(vectors, ids, metas)

    def _make_text_chunk(
        self, pid: str, title: str, abstract: str,
        section: str, text: str, authors: list[str], tags: list[str],
        source: str, year: int | None, venue: str | None,
    ) -> PaperChunk:
        """正文 chunk 构造收敛到单点，metadata 字段一致（title/abstract/body 共用）。"""
        return PaperChunk(
            chunk_id=self._content_chunk_id(pid, section, text),
            paper_id=pid, title=title, section=section, text=text,
            source=source, chunk_type="text",
            metadata={"authors": authors, "tags": tags, "abstract": abstract,
                      "year": year, "venue": venue},
        )

    def _title_abstract_chunks(
        self, pid: str, title: str, abstract: str,
        authors: list[str], tags: list[str], source: str, year: int | None, venue: str | None,
    ) -> list[PaperChunk]:
        """标题/摘要作为独立 chunk 入库（空串跳过），使按标题/摘要关键词也能被 BM25/向量召回。"""
        out: list[PaperChunk] = []
        if title.strip():
            out.append(self._make_text_chunk(pid, title, abstract, "title", title.strip(),
                                             authors, tags, source, year, venue))
        if abstract.strip():
            out.append(self._make_text_chunk(pid, title, abstract, "abstract", abstract.strip(),
                                             authors, tags, source, year, venue))
        return out

    # ── Public API ────────────────────────────────────────────

    async def add_paper(
        self, title: str, abstract: str, content: str,
        authors: list[str] | None = None, tags: list[str] | None = None,
        source: str = "manual", paper_id: str | None = None,
        year: int | None = None, venue: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._ensure_init()
            authors = authors or []
            tags = tags or []
            pid = paper_id or uuid.uuid4().hex[:10]

            if paper_id:
                self._remove_paper_chunks(pid)

            body_chunks: list[PaperChunk] = [
                self._make_text_chunk(pid, title, abstract, section, text,
                                      authors, tags, source, year, venue)
                for section, text in self._split_chunks(content)
            ]
            # 标题/摘要作为独立 chunk 入库，使按标题/摘要关键词也能被 BM25/向量召回
            chunks = self._title_abstract_chunks(pid, title, abstract, authors, tags, source, year, venue) + body_chunks
            for chunk in chunks:
                self._append_corpus(chunk)
                self._bm25.add(chunk.chunk_id, chunk.text)

            await self._embed_and_index(chunks)

            paper_record = {
                "paper_id": pid, "title": title, "abstract": abstract,
                "authors": authors, "tags": tags,
                "year": year, "venue": venue,
                "chunk_count": len(body_chunks),
                "created_at": self.now(),
            }
            self._index_upsert(paper_record)
            self._append_log("ingest", paper_record)
            return paper_record

    async def ingest_analysis(
        self, paper_id: str, title: str, artifacts: dict[str, str],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """分析产物入库：把四维分析/公式解读/翻译/关键发现作为 chunk_type="analysis" 入库（幂等）。

        metadata 可选，缺省时从 RAG index 兜底补 authors/tags/year/venue/abstract，
        使 analysis chunk 也能按这些元数据过滤（修复 analysis×过滤器互斥）。
        """
        async with self._lock:
            self._ensure_init()
            existing = [
                c for c in self._get_chunks().values()
                if c.paper_id == paper_id and c.chunk_type == "analysis"
            ]
            if existing:
                return {"status": "skipped", "paper_id": paper_id, "reason": "already_ingested", "count": len(existing)}

            inherited = metadata if metadata is not None else self._paper_metadata_from_index(paper_id)
            chunks: list[PaperChunk] = []
            for kind, content in artifacts.items():
                if not content or not content.strip():
                    continue
                for head, body in self._split_chunks(content):
                    head_text = f"\n{head}" if head and head != "全文" else ""
                    chunk_text = f"{kind}{head_text}\n{body}"
                    meta = {"artifact_kind": kind}
                    if inherited:
                        meta.update(inherited)
                    chunks.append(PaperChunk(
                        chunk_id=self._content_chunk_id(paper_id, kind, chunk_text),
                        paper_id=paper_id, title=title,
                        section=kind, text=chunk_text,
                        source="analysis", chunk_type="analysis",
                        metadata=meta,
                    ))
            if not chunks:
                return {"status": "empty", "paper_id": paper_id, "count": 0}
            for c in chunks:
                self._append_corpus(c)
                self._bm25.add(c.chunk_id, c.text)
            await self._embed_and_index(chunks)
            self._append_log("ingest_analysis", {"paper_id": paper_id, "count": len(chunks)})
            return {"status": "ok", "paper_id": paper_id, "count": len(chunks)}

    def _paper_metadata_from_index(self, paper_id: str) -> dict[str, Any]:
        """从 RAG index 查 paper 记录的 authors/tags/year/venue/abstract，供 analysis chunk 继承。

        注意：paper-analyzer 的 p_xxx 与 RAG add_paper 的 uuid[:10] 命名空间不重叠，
        此处兜底只对「同 paper_id 已通过 add_paper 入库」的论文生效；analyzer 管线的
        author/year 提取属数据源缺口（extracted.json 当前不含），超出本次范围。
        """
        rec = next((e for e in self._index if e.get("paper_id") == paper_id), None)
        if not rec:
            return {}
        return {
            "authors": rec.get("authors") or [],
            "tags": rec.get("tags") or [],
            "year": rec.get("year"),
            "venue": rec.get("venue") or "",
            "abstract": rec.get("abstract") or "",
        }

    def _remove_paper_chunks(self, paper_id: str) -> None:
        if not self._initialized:
            return
        chunks = self._get_chunks()
        removed_ids = [c.chunk_id for c in chunks.values() if c.paper_id == paper_id]
        if not removed_ids:
            return
        # 文件锁包裹：BM25/向量/内存索引的删除与 corpus 重写必须跨实例原子，避免与其他实例的 append 交错
        with self._write_lock:
            for cid in removed_ids:
                self._bm25.remove(cid)
                self._chunks_by_id.pop(cid, None)
            self._vector_store.reload()
            self._vector_store.remove(removed_ids)
            # 软删除持久化：重写 corpus.jsonl，避免重启后旧 chunk 复活
            remaining = [c for c in chunks.values() if c.paper_id != paper_id]
            content = "".join(json.dumps(c.model_dump(), ensure_ascii=False) + "\n" for c in remaining)
            self._atomic_write_text(self.corpus_path, content)
        self._corpus_stat = self._corpus_file_stat()

    async def update_paper(
        self, paper_id: str, title: str, abstract: str, content: str,
        authors: list[str] | None = None, tags: list[str] | None = None,
        year: int | None = None, venue: str | None = None,
    ) -> dict[str, Any]:
        return await self.add_paper(
            title=title, abstract=abstract, content=content,
            authors=authors, tags=tags, paper_id=paper_id,
            year=year, venue=venue,
        )

    async def delete_paper(self, paper_id: str) -> bool:
        async with self._lock:
            self._ensure_init()
            self._maybe_sync()
            if not any(e.get("paper_id") == paper_id for e in self._index):
                return False
            self._remove_paper_chunks(paper_id)
            self._append_log("delete", {"paper_id": paper_id})
            return self._index_remove(paper_id)

    def list_papers(self, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        self._maybe_sync()
        papers = list(reversed(self._index))
        if offset:
            papers = papers[offset:]
        if limit is not None:
            papers = papers[:limit]
        return papers

    def list_tags(self) -> list[str]:
        self._maybe_sync()
        tags: set[str] = set()
        for c in self._get_chunks().values():
            for t in c.metadata.get("tags") or []:
                tags.add(str(t))
        return sorted(tags)

    def list_authors(self) -> list[str]:
        self._maybe_sync()
        authors: set[str] = set()
        for c in self._get_chunks().values():
            for a in c.metadata.get("authors") or []:
                authors.add(str(a))
        return sorted(authors)

    @staticmethod
    def _filter_reason(
        chunk: PaperChunk, tag: str | None, modality: str | None,
        author: str | None, year: int | None, venue: str | None,
    ) -> str | None:
        """返回第一个不满足的过滤维度（形如 "filter:year"），全部满足返回 None。

        判定逻辑的单一真源：_matches_filters 委托到这里，避免"是否通过"与
        "为什么没通过"两份实现随时间漂移。
        """
        meta = chunk.metadata or {}
        if tag and not any(tag.lower() in str(t).lower() for t in (meta.get("tags") or [])):
            return "filter:tag"
        if author and not any(author.lower() in str(a).lower() for a in (meta.get("authors") or [])):
            return "filter:author"
        if year is not None and meta.get("year") != year:
            return "filter:year"
        if venue and venue.lower() not in str(meta.get("venue") or "").lower():
            return "filter:venue"
        if modality and chunk.chunk_type != modality:
            return "filter:modality"
        return None

    @staticmethod
    def _matches_filters(
        chunk: PaperChunk, tag: str | None, modality: str | None,
        author: str | None, year: int | None, venue: str | None,
    ) -> bool:
        return ResearchRAG._filter_reason(chunk, tag, modality, author, year, venue) is None

    async def _expand_queries(self, query: str, trace: dict[str, Any] | None = None,
                              bypass_cache: bool = False) -> list[str]:
        """查询扩展：返回 [原始 query, ...变体]，带查询级缓存与离线降级。

        - 禁用扩展 → 单 query
        - 缓存命中（TTL 内）→ 直接返回，省掉重复 LLM 调用
        - 无 provider → 离线规则扩展（同义词替换）
        - LLM 失败/无有效变体 → 回退离线规则扩展

        trace: 可选的出参字典，填入 {"source": disabled|cached|llm|offline}。
        返回类型保持不变，调用方与既有测试不受影响。

        bypass_cache: 跳过**读**缓存强制重新扩展（写缓存照旧，顺带刷新 TTL）。
            做 A/B 参数对比时，同一 query 二次检索会命中缓存、扩展结果被冻住，
            这会变成混淆变量——该开关就是用来消掉它的。
        """
        if not self._query_expansion_enabled:
            if trace is not None:
                trace["source"] = "disabled"
            return [query]

        if self._query_expansion_cache_ttl > 0 and not bypass_cache:
            cached = self._qe_cache.get(query)
            if cached is not None and cached[0] > time.monotonic():
                if trace is not None:
                    trace["source"] = "cached"
                return cached[1]

        if self._provider is not None:
            expanded = await self._llm_expand_queries(query, trace=trace)
        else:
            expanded = _offline_query_expansion(query)
            if trace is not None:
                trace["source"] = "offline"

        if self._query_expansion_cache_ttl > 0:
            self._qe_cache[query] = (time.monotonic() + self._query_expansion_cache_ttl, expanded)
            # 简单容量裁剪：按插入序淘汰最旧
            while len(self._qe_cache) > self._query_expansion_cache_max:
                self._qe_cache.pop(next(iter(self._qe_cache)))
        return expanded

    async def _llm_expand_queries(self, query: str, trace: dict[str, Any] | None = None) -> list[str]:
        """LLM 生成语义等价改写；失败/无有效变体时回退离线规则扩展。

        trace: 可选出参，如实记录最终走的是 llm 还是 offline —— 本函数有三条
        静默回退路径，只按"有没有 provider"判断来源会误报。
        """
        count = max(1, self._query_expansion_count)

        def _fallback() -> list[str]:
            if trace is not None:
                trace["source"] = "offline"
            return _offline_query_expansion(query)

        prompt = (
            "你是学术检索助手。请把下面的检索查询改写成 {n} 条语义等价但措辞不同的表述，"
            "以提升召回率（覆盖同义词、中英双语、更具体的术语）。\n"
            "每条单独一行，不要编号、不要解释。\n\n查询：{q}\n"
        ).format(n=count, q=query)
        try:
            response = await self._provider.chat_with_retry(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                max_tokens=256,
                temperature=0.0,
            )
            raw = (response.content or "").strip()
        except Exception:
            logger.warning("ResearchRAG._llm_expand_queries: LLM call failed, fallback to offline expansion")
            return _fallback()
        if not raw:
            return _fallback()
        # 解析：按行切，去编号/项目符号前缀，去空行与重复
        variants: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", line).strip()
            if line and line not in variants:
                variants.append(line)
        # 原始 query 恒为首位，去重后截断到 count 条变体
        expanded = [query]
        for v in variants:
            if v.lower() != query.lower() and v not in expanded:
                expanded.append(v)
            if len(expanded) >= count + 1:
                break
        if len(expanded) == 1:
            # LLM 未产出有效变体 → 离线兜底，避免退化回单 query
            return _fallback()
        if trace is not None:
            trace["source"] = "llm"
        return expanded

    async def search(
        self, query: str, top_k: int = 5, tag: str | None = None,
        modality: str | None = None, rerank: bool = True,
        author: str | None = None, year: int | None = None, venue: str | None = None,
        min_score: float | None = None, coarse_top_k: int | None = None,
        debug: bool = False, bypass_expansion_cache: bool = False,
    ) -> dict[str, Any]:
        """混合检索。

        debug=True 额外返回链路中间态（扩展 query、双路原始 rank+score、归一化前
        rrf_raw、被丢弃候选及原因、重排前后名次、分阶段耗时），供 RAG 验收台使用；
        契约见 docs/superpowers/specs/2026-08-25-rag-lab-page-design.md §5.1。

        debug=False 是日常路径，不构造任何额外结构，响应体与开销均与改造前一致。
        """
        self._ensure_init()
        self._maybe_sync()
        coarse_k = coarse_top_k if coarse_top_k is not None and coarse_top_k > 0 else self._coarse_top_k
        # 显式校验 top_k：None/<=0 回退默认，避免负值触发 filter[:负数] 语义错乱
        final_k = top_k if top_k is not None and top_k > 0 else self._final_top_k

        t_all = time.perf_counter()
        stages: list[dict[str, Any]] = []

        def _stage(name: str, n_in: int, n_out: int, secs: float,
                   status: str = "ok", reason: str | None = None) -> None:
            if debug:
                stages.append({"name": name, "in": n_in, "out": n_out,
                               "ms": round(secs * 1000, 3), "status": status, "reason": reason})

        # 查询扩展：多 query RRF 累加融合（无 provider / 禁用时降级为单 query）。
        # RRF score = Σ 1/(_RRF_K+rank)，仅对命中侧求和，免疫 min-max 单例退化与 BM25/向量尺度差异。
        # 变体嵌入批量化：一次 embed_batch 替代循环 embed_single，省去逐条往返。
        qe_trace: dict[str, Any] = {}
        t = time.perf_counter()
        queries = await self._expand_queries(
            query, trace=qe_trace if debug else None, bypass_cache=bypass_expansion_cache)
        _stage("expand", 1, len(queries), time.perf_counter() - t)

        q_vecs = await self._embedder.embed_batch(queries)
        fused: dict[str, float] = {}
        channels: dict[str, dict[str, list[dict[str, Any]]]] = {}
        bm25_secs = vec_secs = 0.0
        bm25_n = vec_n = 0
        for q_idx, (q, q_vec) in enumerate(zip(queries, q_vecs)):
            t = time.perf_counter()
            bm25_results = self._bm25.search(q, coarse_k)
            bm25_secs += time.perf_counter() - t
            t = time.perf_counter()
            vec_results = self._vector_store.search(q_vec, coarse_k)
            vec_secs += time.perf_counter() - t
            bm25_n += len(bm25_results)
            vec_n += len(vec_results)
            for rank, (cid, raw) in enumerate(bm25_results):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
                if debug:
                    channels.setdefault(cid, {"bm25": [], "vector": []})["bm25"].append(
                        {"q_idx": q_idx, "rank": rank, "score": round(float(raw), 6)})
            for rank, (cid, raw) in enumerate(vec_results):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
                if debug:
                    channels.setdefault(cid, {"bm25": [], "vector": []})["vector"].append(
                        {"q_idx": q_idx, "rank": rank, "score": round(float(raw), 6)})
        _stage("bm25", len(queries), bm25_n, bm25_secs)
        _stage("vector", len(queries), vec_n, vec_secs)

        # 归一化到 [0,1]（top=1.0），供 min_score 阈值与对外 score 使用。
        # 归一化前的累加值另存 rrf_raw：score 因除以全局最大值而使 top-1 恒为 1.0，
        # 不可跨 query 比较，rrf_raw 才是可比量。
        t = time.perf_counter()
        rrf_raw: dict[str, float] = dict(fused) if debug else {}
        if fused:
            max_fused = max(fused.values())
            fused = {cid: s / max_fused for cid, s in fused.items()}

        sorted_cids = sorted(fused.items(), key=lambda x: x[1], reverse=True)
        _stage("fuse", bm25_n + vec_n, len(sorted_cids), time.perf_counter() - t)

        chunk_map = self._get_chunks()
        # min_score 显式传参优先，否则回退配置默认值
        threshold = min_score if min_score is not None else self._min_score
        dropped: list[dict[str, Any]] = []
        dropped_truncated = False

        def _drop(cid: str, score: float, reason: str) -> None:
            """记录被丢弃的候选。debug=False 时是空操作，日常路径不付代价。"""
            nonlocal dropped_truncated
            if not debug:
                return
            if len(dropped) >= _DEBUG_DROPPED_LIMIT:
                dropped_truncated = True
                return
            ch = chunk_map.get(cid)
            dropped.append({
                "chunk_id": cid,
                "title": ch.title if ch else None,
                "score": round(score, 6),
                "rrf_raw": round(rrf_raw.get(cid, 0.0), 6),
                "reason": reason,
                "stage": "filter",
            })

        t = time.perf_counter()
        filtered: list[tuple[str, str, float]] = []
        for cid, score in sorted_cids:
            if score < threshold:
                _drop(cid, score, "min_score")  # 低分噪声截断（RRF 归一化后 [0,1]）
                continue
            chunk = chunk_map.get(cid)
            if not chunk:
                _drop(cid, score, "chunk_missing")  # 索引与语料短暂不一致
                continue
            reason = self._filter_reason(chunk, tag, modality, author, year, venue)
            if reason:
                _drop(cid, score, reason)
                continue
            filtered.append((cid, chunk.text, score))
        _stage("filter", len(sorted_cids), len(filtered), time.perf_counter() - t)

        # 重排前的顺序快照：没有它就无法区分"重排没跑"与"重排跑了但没改变顺序"
        order_before = [cid for cid, _, _ in filtered]
        # 融合序下的分数，仅 debug 用：重排会重排 filtered，之后分数与位置对不回去
        score_before = {cid: score for cid, _, score in filtered} if debug else {}
        rerank_scores: dict[str, float] = {}
        rr_status, rr_reason = "skipped", None
        t = time.perf_counter()
        if not rerank:
            rr_reason = "调用方传入 rerank=false"
        elif not self._reranker:
            rr_reason = "重排器不可用（无 provider 或 rerank_enabled=false）"
        elif len(filtered) <= final_k:
            rr_reason = f"候选数 {len(filtered)} 未超过 top_k {final_k}，无需重排"
        else:
            candidates = [(cid, text) for cid, text, _ in filtered[:coarse_k]]
            reranked = await self._reranker.rerank(query, candidates, final_k)
            if reranked is None:
                rr_status = "failed"
                rr_reason = "重排器返回 None（LLM 调用或解析失败），维持融合序"
            else:
                # 仅用 rerank 序重排进入 top-k 的候选，未进入的维持融合序（避免两种尺度混排）
                rank_pos = {cid: i for i, (cid, _) in enumerate(reranked)}
                rerank_scores = dict(reranked)
                head = [x for x in filtered if x[0] in rank_pos]
                head.sort(key=lambda x: rank_pos[x[0]])
                tail = [x for x in filtered if x[0] not in rank_pos]
                filtered = head + tail
                rr_status = "ok"
        _stage("rerank", len(order_before), min(len(filtered), final_k),
               time.perf_counter() - t, rr_status, rr_reason)

        before_pos = {cid: i + 1 for i, cid in enumerate(order_before)}

        # 重排前的 top-K 明细。没有它 rerank_gain 就算不出来：results 只剩重排后的
        # top-K，被重排挤出去的候选一条都不在里面（dropped 只收 filter 阶段的丢弃），
        # 拿 results 按 rank_before_rerank 重排序集合不变、P@K 不变、增益恒为 0。
        # 同时供 M5「重排 Diff」左列渲染，因此带上可判读所需的最小字段。
        rerank_before: list[dict[str, Any]] = []
        if debug:
            for i, cid in enumerate(order_before[:final_k]):
                chunk = chunk_map.get(cid)
                if not chunk:
                    continue
                rerank_before.append({
                    "rank": i + 1,
                    "chunk_id": cid,
                    "title": chunk.title,
                    "section": chunk.section,
                    "chunk_type": chunk.chunk_type,
                    "score": round(score_before.get(cid, 0.0), 6),
                    "rrf_raw": round(rrf_raw.get(cid, 0.0), 6),
                    # 被挤出者不在 results 里，没有正文就无法标注——标注是 rerank_gain 的输入
                    "text": chunk.text[:_DEBUG_SNAPSHOT_TEXT_CHARS],
                })

        results = []
        for i, (cid, _, score) in enumerate(filtered[:final_k]):
            chunk = chunk_map[cid]
            item = {
                "score": round(score, 6),
                "chunk_id": chunk.chunk_id,
                "paper_id": chunk.paper_id,
                "title": chunk.title,
                "section": chunk.section,
                "text": chunk.text[:1200],
                "chunk_type": chunk.chunk_type,
                "metadata": chunk.metadata,
            }
            if debug:
                item.update({
                    "rank": i + 1,
                    "rank_before_rerank": before_pos.get(cid, i + 1),
                    "rerank_score": rerank_scores.get(cid),
                    "rrf_raw": round(rrf_raw.get(cid, 0.0), 6),
                    "channels": channels.get(cid, {"bm25": [], "vector": []}),
                })
            results.append(item)

        self._append_log("search", {"query": query, "top_k": final_k, "tag": tag, "modality": modality, "hits": len(results)})
        payload: dict[str, Any] = {
            "query": query, "top_k": final_k, "tag": tag, "modality": modality, "results": results,
        }
        if not debug:
            return payload

        payload.update({
            "run_id": f"r_{uuid.uuid4().hex[:12]}",
            "params_effective": {
                "top_k": final_k, "coarse_top_k": coarse_k, "min_score": threshold,
                "rerank": rerank, "tag": tag, "modality": modality,
                "author": author, "year": year, "venue": venue,
                "rrf_k": int(_RRF_K),
                "query_expansion_enabled": self._query_expansion_enabled,
                "query_expansion_count": self._query_expansion_count,
                "bypass_expansion_cache": bypass_expansion_cache,
                "embedding_model": self._embedding_model,
                "rerank_model": self._rerank_model,
            },
            "expanded_queries": queries,
            "expansion_source": qe_trace.get("source", "disabled"),
            "stages": stages,
            "timings_ms": {"total": round((time.perf_counter() - t_all) * 1000, 3)},
            "dropped": dropped,
            "dropped_truncated": dropped_truncated,
            "rerank_before": rerank_before,
        })
        return payload

    async def build_context(self, query: str, top_k: int = 5, max_chars: int = 6000,
                            modality: str | None = None, tag: str | None = None,
                            author: str | None = None, year: int | None = None,
                            venue: str | None = None) -> dict[str, Any]:
        sr = await self.search(query, top_k=top_k, modality=modality, tag=tag,
                               author=author, year=year, venue=venue)
        lines = [f"# 研究上下文\n\n查询：{query}\n"]
        total = 0
        for item in sr["results"]:
            block = f"## {item['title']} / {item['section']}\n分数：{item['score']:.4f}\n[{item.get('chunk_type', 'text')}]\n\n{item['text']}\n"
            if total + len(block) > max_chars:
                break
            lines.append(block)
            total += len(block)
        return {"query": query, "context": "\n".join(lines).strip(), "results": sr["results"]}

    async def suggest_research(self, query: str, top_k: int = 5,
                               modality: str | None = None, tag: str | None = None,
                               author: str | None = None, year: int | None = None,
                               venue: str | None = None) -> dict[str, Any]:
        ctx = await self.build_context(query, top_k=top_k, modality=modality, tag=tag,
                                       author=author, year=year, venue=venue)
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

    async def reindex(self, papers_dir: Path | None = None) -> dict[str, Any]:
        async with self._lock:
            self._ensure_init()
            self._rebuild_bm25()
            if self._embedder and self._vector_store:
                from silver_research_bot.rag.research_vector_store import VectorStore
                self._vector_store = VectorStore(self.root / "vectors", dim=self._embedding_dim)
                chunks = self._get_chunks().values()
                if chunks:
                    texts = [c.text for c in chunks]
                    ids = [c.chunk_id for c in chunks]
                    metas = [{"paper_id": c.paper_id, "title": c.title, "chunk_type": c.chunk_type} for c in chunks]
                    vectors = await self._embedder.embed_batch(texts)
                    await asyncio.to_thread(self._vector_store.add, vectors, ids, metas)
        # 从论文目录回填分析产物（不持锁：ingest_analysis 自带锁，asyncio.Lock 非可重入，锁内调用会死锁）
        if papers_dir is not None and papers_dir.exists():
            from silver_research_bot.rag.research_analysis import ingest_paper_analysis
            for paper_dir in papers_dir.iterdir():
                if not paper_dir.is_dir() or not (paper_dir / "extracted.json").exists():
                    continue
                try:
                    await ingest_paper_analysis(self, papers_dir, paper_dir.name)
                except Exception:
                    logger.exception("RAG reindex backfill failed for {}", paper_dir.name)
        return {"status": "ok", "chunk_count": len(self._get_chunks())}

    _BLOCK_RE = re.compile(
        r"\$\$.*?\$\$|```.*?```|^\|[^\n]*\|(?:\n\|[^\n]*\|)*",
        re.DOTALL | re.MULTILINE,
    )

    @staticmethod
    def _content_chunk_id(paper_id: str, section: str, text: str) -> str:
        """内容寻址 chunk_id：sha256(paper_id\\0section\\0text)，跨会话稳定，支持真增量去重。"""
        return hashlib.sha256(f"{paper_id}\x00{section}\x00{text}".encode("utf-8")).hexdigest()[:16]

    def _num_tokens(self, text: str) -> int:
        """token 计数：tiktoken 可用时精确；否则字符启发式（中文1字≈1token，其余4字符≈1token）。"""
        enc = _get_tiktoken()
        if enc is not None:
            return len(enc.encode(text))
        cjk = len(re.findall(r"[一-鿿]", text))
        return cjk + max(1, (len(text) - cjk) // 4)

    def _protect_blocks(self, text: str) -> tuple[str, list[str]]:
        """把公式/代码块/表格替换为占位符，切分后还原，避免块内切断。"""
        blocks: list[str] = []

        def repl(m):
            blocks.append(m.group(0))
            return f"\n__RAG_BLOCK_{len(blocks) - 1}__\n"

        return self._BLOCK_RE.sub(repl, text), blocks

    @staticmethod
    def _restore_blocks(text: str, blocks: list[str]) -> str:
        for i, b in enumerate(blocks):
            text = text.replace(f"__RAG_BLOCK_{i}__", b)
        return text

    def _split_by_tokens(self, body: str, size_tokens: int) -> list[str]:
        """按 token 预算切块：先段后句边界；无边界时字符硬切兜底。"""
        protected, blocks = self._protect_blocks(body)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", protected) if p.strip()]
        chunks: list[str] = []
        buf = ""
        for para in paragraphs:
            if self._num_tokens(para) > size_tokens:
                if buf:
                    chunks.append(buf)
                    buf = ""
                chunks.extend(self._split_para(para, size_tokens))
            elif not buf:
                buf = para
            elif self._num_tokens(buf + "\n\n" + para) <= size_tokens:
                buf = buf + "\n\n" + para
            else:
                chunks.append(buf)
                buf = para
        if buf:
            chunks.append(buf)
        # 空白 token 级重叠（protected 空间，restore_blocks 之前）：占位符无内部空白，
        # 是原子 token，重叠尾不会切开公式/代码块。
        chunks = self._apply_chunk_overlap(chunks)
        return [self._restore_blocks(c, blocks) for c in chunks if c.strip()]

    def _apply_chunk_overlap(self, chunks: list[str]) -> list[str]:
        """把前一块末尾 chunk_overlap 个 token 前置到后一块头部，缓解块间语义割裂。

        ⚠️ 改变 chunk text → 内容寻址 chunk_id 全量变化，存量 corpus 需一次 reindex 重建。
        仅在 protected 空间（restore_blocks 之前）调用，占位符为原子 token 不会被切开。
        ⚠️ 重叠按空白分词切取（prev.split()）：CJK 等无空白文本整块只算 1 个 token，
        `prev_tokens[-overlap_tokens:]` 会取回整个前块并前置 —— 实测 chunk0=26 token 时
        chunk1 达 53 token，体积近翻倍。修此问题需改用 tiktoken 切重叠尾，会变更 chunk_id。
        """
        if self._chunk_overlap <= 0 or len(chunks) < 2:
            return chunks
        overlap_tokens = self._chunk_overlap
        out: list[str] = [chunks[0]]
        for prev, cur in zip(chunks, chunks[1:]):
            prev_tokens = prev.split()
            tail = " ".join(prev_tokens[-overlap_tokens:]) if prev_tokens else ""
            out.append((tail + " " + cur) if tail else cur)
        return out

    def _split_para(self, para: str, size_tokens: int) -> list[str]:
        """段内按句边界切块；单句超长时按字符硬切。"""
        sent_re = re.compile(r"(?<=[。！？；.!?;])\s*")
        sents = [s for s in sent_re.split(para) if s.strip()]
        chunks: list[str] = []
        buf = ""
        for s in sents:
            if self._num_tokens(s) > size_tokens:
                if buf:
                    chunks.append(buf)
                    buf = ""
                chunks.extend(self._hard_split(s, size_tokens))
            elif not buf:
                buf = s
            elif self._num_tokens(buf + s) <= size_tokens:
                buf += s
            else:
                chunks.append(buf)
                buf = s
        if buf:
            chunks.append(buf)
        return chunks

    def _hard_split(self, text: str, size_tokens: int) -> list[str]:
        """单句超长时的兜底硬切：按该文本自身的 token 密度把 token 预算换算成字符窗口。

        不直接按 tiktoken 边界切——cl100k_base 是字节级 BPE，切在多字节字符中间再 decode
        会产生 U+FFFD 替换符（CJK 尤其明显），故统一在字符空间切，仅用密度做单位换算。
        重叠上限截到窗口一半：否则 chunk_overlap ≥ chunk_size 时 step 退化为 1，
        会逐字符产出 chunk（配置边界 overlap=2000 / size=100 即可触发）。
        """
        chars_per_token = max(1.0, len(text) / max(1, self._num_tokens(text)))
        size_chars = max(1, int(size_tokens * chars_per_token))
        overlap_chars = min(int(self._chunk_overlap * chars_per_token), size_chars // 2)
        step = max(size_chars - overlap_chars, 1)
        return [text[i:i + size_chars] for i in range(0, len(text), step)]

    def _split_chunks(self, content: str) -> list[tuple[str, str]]:
        """先按标题切段，超长段再按 token 预算切块（句边界 + 公式/代码块/表格保护）。"""
        text = content.strip()
        if not text:
            return [("全文", "")]
        heading_re = re.compile(r"(?m)^(#{1,6})\s+(.+)$")
        matches = list(heading_re.finditer(text))
        if not matches:
            sections: list[tuple[str, str]] = [("全文", text)]
        else:
            sections = []
            if matches[0].start() > 0:
                preamble = text[:matches[0].start()].strip()
                if preamble:
                    sections.append(("导言", preamble))
            for i, m in enumerate(matches):
                head = m.group(2).strip()
                start = m.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                body = text[start:end].strip()
                if body:
                    sections.append((head, body))

        size_tokens = max(self._chunk_size, 64)  # chunk_size 直接作为 token 预算
        out: list[tuple[str, str]] = []
        for section, body in sections:
            if self._num_tokens(body) <= size_tokens:
                out.append((section, body))
                continue
            part_idx = 1
            for piece in self._split_by_tokens(body, size_tokens):
                out.append((f"{section} ({part_idx})", piece))
                part_idx += 1
        return out or [("全文", text)]
