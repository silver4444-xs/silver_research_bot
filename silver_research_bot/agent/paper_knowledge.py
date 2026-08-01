"""论文知识索引 — 对论文分析产物进行语义索引，替代 Dream 做论文场景记忆。

PaperKnowledgeIndex 直接索引 workspace/papers/ 中的结构化分析产物，
预计算 embedding 并缓存到磁盘，提供快速语义搜索。
在 paper_memory_mode 下替代 Dream 的 LLM 二次摘要流程。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

# 技术关键词（用于关系发现）
_TOPIC_RE = re.compile(
    r"(transformer|attention|diffusion|reinforcement.learning|"
    r"fine.tun|pretrain|embedding|tokenization|decoder|encoder|"
    r"CNN|RNN|LSTM|GAN|BERT|GPT|LLM|ViT|MoE|LoRA|RLHF|DPO|"
    r"CNN|RNN|GAN|VAE|SGD|Adam|BatchNorm|LayerNorm|Dropout|ResNet)",
    re.IGNORECASE,
)

if TYPE_CHECKING:
    from silver_research_bot.rag.research_embedder import EmbeddingEngine

# 需要索引的分析产物及其最大字符数
_ARTIFACT_CONFIG: dict[str, dict] = {
    "analysis_system_model":    {"filename": "analysis_system_model.md",    "max_chars": 2000},
    "analysis_problem":         {"filename": "analysis_problem.md",         "max_chars": 2000},
    "analysis_algorithm":       {"filename": "analysis_algorithm.md",       "max_chars": 2000},
    "analysis_experiment":      {"filename": "analysis_experiment.md",      "max_chars": 2000},
    "formula_explanations":     {"filename": "formula_explanations.md",     "max_chars": 3000},
    "review_theory":            {"filename": "review_theory.md",            "max_chars": 1500},
    "review_engineering":       {"filename": "review_engineering.md",       "max_chars": 1500},
    "review_domain":            {"filename": "review_domain.md",            "max_chars": 1500},
}

# 产物类型 → 人类可读标签
_KIND_LABELS: dict[str, str] = {
    "analysis_system_model":    "系统模型",
    "analysis_problem":         "问题表述",
    "analysis_algorithm":       "优化算法",
    "analysis_experiment":      "实验设计",
    "formula_explanations":     "公式解读",
    "review_theory":            "理论审稿",
    "review_engineering":       "工程审稿",
    "review_domain":            "领域审稿",
}


def _title_match(ref_title: str, paper_title: str) -> bool:
    """检查引用标题是否匹配已索引论文标题（简单子串/关键词匹配）。"""
    ref_lower = ref_title.lower().strip()
    paper_lower = paper_title.lower().strip()
    if not ref_lower or not paper_lower:
        return False
    # 精确匹配
    if ref_lower == paper_lower:
        return True
    # 子串匹配（一个包含另一个）
    if ref_lower in paper_lower or paper_lower in ref_lower:
        return True
    # 关键词重叠 ≥ 70%
    ref_words = set(ref_lower.split())
    paper_words = set(paper_lower.split())
    if not ref_words or not paper_words:
        return False
    # 过滤停用词
    stopwords = {"a", "an", "the", "of", "in", "on", "for", "and", "to", "with", "using", "via", "based"}
    ref_words -= stopwords
    paper_words -= stopwords
    if not ref_words or not paper_words:
        return False
    overlap = len(ref_words & paper_words)
    total = len(ref_words | paper_words)
    return (overlap / total) >= 0.5 if total > 0 else False


class PaperKnowledgeIndex:
    """对论文分析产物进行语义索引，支持磁盘缓存 embedding。

    用法::

        index = PaperKnowledgeIndex(papers_dir, embedder, cache_dir)
        await index.build()                        # 扫描并构建索引
        results = await index.search("transformer")  # 语义搜索
        ctx = await index.build_context(query)       # 构建上下文块
    """

    def __init__(
        self,
        papers_dir: Path,
        embedder: "EmbeddingEngine",
        cache_dir: Path,
    ):
        self.papers_dir = papers_dir
        '论文目录（含 index.json）'
        self.embedder = embedder
        '嵌入引擎'
        self.cache_dir = cache_dir
        '缓存目录'
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.cache_dir / "paper_index.json"
        '知识索引文件（chunk 元数据）'
        self._cache_path = self.cache_dir / "embedding_cache.json"
        'embedding 缓存文件（hash → vector）'
        self._chunks: list[dict[str, Any]] = []
        '索引块列表 [{chunk_id, paper_id, title, artifact_kind, text, path}]'
        self._vectors: list[list[float]] = []
        '预计算的嵌入向量'
        self._relations: dict[str, list[dict[str, str]]] = {}
        '论文关系图: {paper_id: [{target: paper_id, type: cites|similar_method|extends}]}'
        self._built = False
        '是否已构建'

    # -- hash helpers ---------------------------------------------------------

    @staticmethod
    def _content_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    # -- build ----------------------------------------------------------------

    async def build(self) -> None:
        """扫描 papers_dir，分块产物，计算 embedding，持久化缓存。

        若 index.json 的 mtime 未变且缓存存在，则直接从磁盘加载。
        """
        papers_index = self.papers_dir / "index.json"
        if not papers_index.exists():
            logger.info("PaperKnowledgeIndex: no papers/index.json, skipping build")
            return

        # 检查缓存新鲜度
        index_mtime = papers_index.stat().st_mtime
        stored = self._load_index_meta()
        if stored and stored.get("_mtime") == index_mtime and stored.get("_built"):
            logger.info("PaperKnowledgeIndex: cache is fresh, loading {} chunks from disk",
                        len(stored.get("chunks", [])))
            self._chunks = stored.get("chunks", [])
            await self._load_vectors_from_cache()
            self._built = True
            return

        # 加载论文索引
        try:
            all_papers = json.loads(papers_index.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("PaperKnowledgeIndex: failed to read papers/index.json")
            return

        # 收集产物文本
        chunks: list[dict[str, Any]] = []
        for paper_id, paper_meta in all_papers.items():
            if paper_meta.get("status") != "completed":
                continue
            title = paper_meta.get("title", paper_id)
            paper_dir = self.papers_dir / paper_id

            for kind, cfg in _ARTIFACT_CONFIG.items():
                fpath = paper_dir / cfg["filename"]
                if not fpath.exists():
                    continue
                try:
                    text = fpath.read_text(encoding="utf-8")[:cfg["max_chars"]]
                except Exception:
                    continue
                if not text.strip():
                    continue
                chunks.append({
                    "chunk_id": f"{paper_id}:{kind}",
                    "paper_id": paper_id,
                    "title": title,
                    "artifact_kind": kind,
                    "text": text,
                    "path": str(fpath),
                })

        if not chunks:
            logger.info("PaperKnowledgeIndex: no artifacts to index")
            return

        # 批量计算 embedding
        texts = [c["text"] for c in chunks]
        vectors = await self.embedder.embed_batch(texts)

        self._chunks = chunks
        self._vectors = vectors
        self._built = True

        # 持久化
        self._save_index_meta({
            "_mtime": index_mtime,
            "_built": True,
            "chunks": chunks,
        })
        self._save_vectors_to_cache(texts, vectors)
        logger.info(
            "PaperKnowledgeIndex: built index with {} chunks from {} papers",
            len(chunks), len({c["paper_id"] for c in chunks}),
        )

        # 构建论文关系图（无额外 LLM 调用）
        self._build_relations()

    # -- search ---------------------------------------------------------------

    async def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """按语义相似度搜索论文知识。

        返回: [{"paper_id", "title", "artifact_kind", "score", "text_snippet"}, …]
        """
        if not self._built or not self._chunks:
            return []

        query_vec = await self.embedder.embed_single(query)
        scored: list[tuple[int, float]] = []
        for i, vec in enumerate(self._vectors):
            sim = self._cosine_similarity(query_vec, vec)
            if sim > 0.2:
                scored.append((i, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        scored = scored[:top_k]

        results: list[dict[str, Any]] = []
        for idx, score in scored:
            chunk = self._chunks[idx]
            results.append({
                "paper_id": chunk["paper_id"],
                "title": chunk["title"],
                "artifact_kind": chunk["artifact_kind"],
                "score": round(score, 3),
                "text_snippet": chunk["text"][:300],
            })
        return results

    async def build_context(self, query: str, top_k: int = 5) -> str:
        """构建可注入系统提示的论文知识上下文块。

        按论文分组排布，每篇论文列出相关产物的摘要片段。
        """
        results = await self.search(query, top_k)
        if not results:
            return ""

        # 按论文分组
        paper_groups: dict[str, list[dict[str, Any]]] = {}
        for r in results:
            paper_groups.setdefault(r["paper_id"], []).append(r)

        lines = ["## Paper Knowledge（相关论文分析结果）"]
        for paper_id, items in paper_groups.items():
            title = items[0]["title"]
            lines.append(f"\n### {title}")
            for item in items:
                label = _KIND_LABELS.get(item["artifact_kind"], item["artifact_kind"])
                snippet = item["text_snippet"].replace("\n", " ")[:200]
                lines.append(f"- [{label}] {snippet}")
        return "\n".join(lines) + "\n"

    # -- cache management -----------------------------------------------------

    # -- relations ------------------------------------------------------------

    def _build_relations(self) -> None:
        """从论文分析产物中提取论文间关系，构建轻量关系图。

        关系类型:
        - cites: 引用关系（从 analysis_summary.json 的 references 字段或
          citation_graph 产物推断）
        - similar_method: 方法相似（基于 analysis_algorithm 的标题关键词重叠）
        - extends: 扩展关系（一篇论文的方法基于另一篇的改进）
        """
        relations: dict[str, list[dict[str, str]]] = {}

        # 收集每篇论文的关键词和引用信息
        paper_info: dict[str, dict] = {}
        for chunk in self._chunks:
            pid = chunk["paper_id"]
            if pid not in paper_info:
                paper_info[pid] = {
                    "title": chunk["title"],
                    "keywords": set(),
                }
            # 从文本中提取技术关键词
            keywords = _TOPIC_RE.findall(chunk["text"])
            paper_info[pid]["keywords"].update(k.lower() for k in keywords)

        paper_ids = list(paper_info.keys())

        # 关系发现: 关键词重叠 → similar_method
        for i, pid_a in enumerate(paper_ids):
            for pid_b in paper_ids[i + 1:]:
                kw_a = paper_info[pid_a]["keywords"]
                kw_b = paper_info[pid_b]["keywords"]
                if not kw_a or not kw_b:
                    continue
                overlap = len(kw_a & kw_b)
                total = len(kw_a | kw_b)
                if total > 0 and overlap / total >= 0.3:
                    relations.setdefault(pid_a, []).append({
                        "target": pid_b,
                        "type": "similar_method",
                    })
                    relations.setdefault(pid_b, []).append({
                        "target": pid_a,
                        "type": "similar_method",
                    })

        # 尝试从 analysis_summary.json 读取引用和扩展关系
        for pid in paper_ids:
            summary_path = self.papers_dir / pid / "analysis_summary.json"
            if not summary_path.exists():
                continue
            try:
                import json as _json
                summary = _json.loads(summary_path.read_text(encoding="utf-8"))
                # 引用关系: 从 references / citations 字段
                refs = summary.get("references") or summary.get("citations") or []
                for ref in refs:
                    ref_title = ref.get("title", "") if isinstance(ref, dict) else str(ref)
                    # 尝试匹配已索引论文
                    for other_pid, info in paper_info.items():
                        if other_pid != pid and _title_match(ref_title, info["title"]):
                            relations.setdefault(pid, []).append({
                                "target": other_pid,
                                "type": "cites",
                            })
                            break
            except Exception:
                pass

        self._relations = relations
        if relations:
            logger.info(
                "PaperKnowledgeIndex: built relations for {} papers ({} edges)",
                len(relations),
                sum(len(v) for v in relations.values()),
            )

    def get_related_papers(self, paper_id: str) -> list[dict[str, str]]:
        """获取与指定论文相关的其他论文列表。

        返回: [{"target": paper_id, "type": "cites|similar_method|extends"}, …]
        """
        return self._relations.get(paper_id, [])

    def build_relation_context(self, paper_ids: list[str]) -> str:
        """构建论文关系上下文块，用于系统提示注入。

        参数:
            paper_ids: 需要展示关系的论文 ID 列表
        """
        if not self._relations:
            return ""

        shown: set[tuple[str, str]] = set()
        lines = ["## Paper Relations（论文间关系）"]
        for pid in paper_ids:
            rels = self._relations.get(pid, [])
            if not rels:
                continue
            title = self._resolve_title(pid)
            for rel in rels:
                target = rel["target"]
                # 避免重复展示双向关系
                edge = tuple(sorted([pid, target]))
                if edge in shown:
                    continue
                shown.add(edge)

                rtype = rel["type"]
                target_title = self._resolve_title(target)
                label = {
                    "cites": "引用",
                    "similar_method": "方法相似",
                    "extends": "扩展",
                }.get(rtype, rtype)
                lines.append(f"- **{title}** {label} **{target_title}**")

        return "\n".join(lines) + "\n" if len(lines) > 1 else ""

    def _resolve_title(self, paper_id: str) -> str:
        """从 chunks 中解析论文标题。"""
        for chunk in self._chunks:
            if chunk["paper_id"] == paper_id:
                return chunk.get("title", paper_id)
        return paper_id

    def invalidate(self, paper_id: str | None = None) -> None:
        """使缓存失效。若不指定 paper_id 则清空全部。"""
        if paper_id is None:
            for f in (self._index_path, self._cache_path):
                if f.exists():
                    f.unlink()
            self._built = False
            self._chunks.clear()
            self._vectors.clear()
            logger.info("PaperKnowledgeIndex: full cache invalidated")
            return
        # 选择性失效：移除特定论文的 chunk 和对应 vector
        stored = self._load_index_meta()
        if stored:
            old_chunks = stored.get("chunks", [])
            stored["chunks"] = [
                c for c in old_chunks if c["paper_id"] != paper_id
            ]
            self._save_index_meta(stored)
        self._built = False
        logger.info("PaperKnowledgeIndex: cache invalidated for paper {}", paper_id)

    # -- incremental update --------------------------------------------------

    def _is_paper_indexed(self, paper_id: str) -> bool:
        """检查论文是否已被索引。"""
        return any(c["paper_id"] == paper_id for c in self._chunks)

    async def add_paper(self, paper_id: str) -> bool:
        """增量索引单篇论文的分析产物。返回 True 表示添加了新 chunk。

        若论文尚未完成分析或已被索引，返回 False。
        """
        papers_index = self.papers_dir / "index.json"
        if not papers_index.exists():
            return False

        if self._is_paper_indexed(paper_id):
            logger.debug("PaperKnowledgeIndex: paper {} already indexed", paper_id)
            return False

        try:
            all_papers = json.loads(papers_index.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False

        paper_meta = all_papers.get(paper_id)
        if not paper_meta or paper_meta.get("status") != "completed":
            return False

        title = paper_meta.get("title", paper_id)
        paper_dir = self.papers_dir / paper_id

        # Collect artifact chunks (same logic as build())
        new_chunks: list[dict[str, Any]] = []
        for kind, cfg in _ARTIFACT_CONFIG.items():
            fpath = paper_dir / cfg["filename"]
            if not fpath.exists():
                continue
            try:
                text = fpath.read_text(encoding="utf-8")[:cfg["max_chars"]]
            except Exception:
                continue
            if not text.strip():
                continue
            new_chunks.append({
                "chunk_id": f"{paper_id}:{kind}",
                "paper_id": paper_id,
                "title": title,
                "artifact_kind": kind,
                "text": text,
                "path": str(fpath),
            })

        if not new_chunks:
            logger.info("PaperKnowledgeIndex: no artifacts for paper {}", paper_id)
            return False

        # Compute embeddings
        texts = [c["text"] for c in new_chunks]
        vectors = await self.embedder.embed_batch(texts)

        # Append to in-memory index
        self._chunks.extend(new_chunks)
        self._vectors.extend(vectors)
        self._built = True

        # Update index meta
        stored = self._load_index_meta() or {}
        stored["_built"] = True
        stored["chunks"] = self._chunks
        if "_mtime" in stored:
            del stored["_mtime"]  # invalidate mtime cache
        self._save_index_meta(stored)

        # Update embedding cache
        self._save_vectors_to_cache(texts, vectors)

        # Rebuild relations (cheap, no LLM call)
        self._build_relations()

        logger.info(
            "PaperKnowledgeIndex: added paper {} ({} chunks, total {} chunks)",
            paper_id, len(new_chunks), len(self._chunks),
        )
        return True

    # -- internal: index meta I/O --------------------------------------------

    def _load_index_meta(self) -> dict[str, Any] | None:
        if not self._index_path.exists():
            return None
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _save_index_meta(self, meta: dict[str, Any]) -> None:
        self._index_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # -- internal: vector cache I/O ------------------------------------------

    async def _load_vectors_from_cache(self) -> None:
        """从磁盘缓存恢复 embedding 向量。

        先恢复 embedder 的内存缓存，再用 embed_batch（全缓存命中）获取向量。
        """
        if not self._cache_path.exists() or not self._chunks:
            return
        try:
            cache_data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            for h, vec in cache_data.items():
                self.embedder._cache[h] = vec
        except Exception:
            logger.warning("PaperKnowledgeIndex: failed to load embedding cache")
            return

        # 用 embed_batch 获取向量（此时全部命中内存缓存，无 API 调用）
        texts = [c["text"] for c in self._chunks]
        self._vectors = await self.embedder.embed_batch(texts)

    def _save_vectors_to_cache(self,
                               texts: list[str],
                               vectors: list[list[float]]) -> None:
        """将 hash→vector 映射持久化到磁盘。"""
        cache_data: dict[str, list[float]] = {}
        for text, vec in zip(texts, vectors):
            h = self._content_hash(text)
            cache_data[h] = vec
        self._cache_path.write_text(
            json.dumps(cache_data, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- internal: similarity ------------------------------------------------

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) + 1e-10
        nb = math.sqrt(sum(y * y for y in b)) + 1e-10
        return dot / (na * nb)
