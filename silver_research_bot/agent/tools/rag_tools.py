"""Agent RAG 工具 — UnifiedRAG 统一检索适配器 + 4 个 Agent Tool

UnifiedRAG 以 ResearchRAG 为单一检索源（文献库 + 分析产物，chunk_type 区分），
PaperKnowledgeIndex 仅提供关系图（rag_compare 用）。工具层通过 @tool_parameters
装饰器注册为 Agent 可调用工具。
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from loguru import logger

from silver_research_bot.agent.tools.base import Tool, tool_parameters
from silver_research_bot.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    NumberSchema,
    StringSchema,
    tool_parameters_schema,
)

if TYPE_CHECKING:
    from silver_research_bot.agent.paper_knowledge import PaperKnowledgeIndex
    from silver_research_bot.config.schema import RAGToolConfig
    from silver_research_bot.providers.base import LLMProvider
    from silver_research_bot.rag.research_rag import ResearchRAG

# 技术关键词提取正则 (用于后续 rag_ingest 自动打标)
_TECH_KEYWORD_RE = re.compile(
    r"(transformer|attention|diffusion|reinforcement.learning|"
    r"fine.tun|pretrain|embedding|tokenization|decoder|encoder|"
    r"CNN|RNN|LSTM|GAN|BERT|GPT|LLM|ViT|MoE|LoRA|RLHF|DPO|"
    r"CNN|RNN|GAN|VAE|SGD|Adam|BatchNorm|LayerNorm|Dropout|ResNet|"
    r"graph.neural|knowledge.graph|federated.learning|"
    r"contrastive.learning|self.supervised|few.shot|zero.shot)",
    re.IGNORECASE,
)


class UnifiedRAG:
    """统一检索适配器 — 以 ResearchRAG 为单一检索源，PaperKnowledgeIndex 仅提供关系图。"""

    def __init__(
        self,
        research_rag: "ResearchRAG | None" = None,
        paper_knowledge: "PaperKnowledgeIndex | None" = None,
        config: "RAGToolConfig | None" = None,
    ) -> None:
        self._rag = research_rag
        self._pk = paper_knowledge
        self._config = config
        self._fallback_score = config.fallback_score_threshold if config else 0.5
        self._fallback_count = config.fallback_count_threshold if config else 3

    async def search(
        self,
        query: str,
        top_k: int = 5,
        modality: str | None = None,
        tag: str | None = None,
        author: str | None = None,
        year: int | None = None,
        venue: str | None = None,
        rerank: bool = True,
        min_score: float | None = None,
    ) -> list[dict[str, Any]]:
        """单一检索源：仅查 ResearchRAG，`source` 由 chunk_type 推导。

        返回: [{chunk_id, paper_id, title, text, score, source, chunk_type, section, metadata}, ...]
        """
        if not self._rag:
            return []
        try:
            sr = await self._rag.search(
                query, top_k=top_k, tag=tag, modality=modality, rerank=rerank,
                author=author, year=year, venue=venue, min_score=min_score,
            )
        except Exception:
            logger.exception("UnifiedRAG: ResearchRAG search failed")
            return []
        results = sr.get("results", [])
        for r in results:
            r["source"] = "analysis" if r.get("chunk_type") == "analysis" else "literature"
        return results

    async def build_context(
        self,
        query: str,
        top_k: int = 5,
        modality: str | None = None,
        tag: str | None = None,
        author: str | None = None,
        year: int | None = None,
        venue: str | None = None,
    ) -> str:
        """构建可注入 LLM 的上下文块。"""
        results = await self.search(
            query, top_k=top_k, modality=modality, tag=tag,
            author=author, year=year, venue=venue,
        )
        if not results:
            return ""

        lines = ["## RAG 检索结果"]
        for i, r in enumerate(results, 1):
            source_label = {"literature": "文献库", "analysis": "分析产物"}.get(
                r.get("source", ""), r.get("source", "")
            )
            lines.append(
                f"\n### {i}. {r.get('title', 'Untitled')} [{source_label}] "
                f"(score: {r.get('score', 0):.3f})"
            )
            text = r.get("text", "")[:500]
            lines.append(f"{text}")
            if r.get("paper_id"):
                lines.append(f"*paper_id: {r['paper_id']}*")
        return "\n".join(lines) + "\n"

    async def ask(
        self,
        question: str,
        top_k: int = 5,
        provider: "LLMProvider | None" = None,
        modality: str | None = None,
        tag: str | None = None,
        author: str | None = None,
        year: int | None = None,
        venue: str | None = None,
    ) -> dict[str, Any]:
        """基于检索结果用 LLM 合成学术回答 (带引用)。

        返回: {"answer": str, "sources": [{chunk_id, paper_id, title}], "context": str}
        """
        results = await self.search(
            question, top_k=top_k, modality=modality, tag=tag,
            author=author, year=year, venue=venue,
        )
        if not results:
            return {
                "answer": "未在知识库中找到相关信息。",
                "sources": [],
                "context": "",
            }

        context = await self.build_context(
            question, top_k=top_k, modality=modality, tag=tag,
            author=author, year=year, venue=venue,
        )
        sources = [
            {"chunk_id": r.get("chunk_id", ""), "paper_id": r.get("paper_id", ""), "title": r.get("title", "")}
            for r in results
        ]

        if not provider:
            return {"answer": context, "sources": sources, "context": context}

        prompt = (
            f"你是一个学术研究助手。基于以下检索结果回答用户问题。\n"
            f"在回答中标注引用来源(用 [1], [2] 等编号)。\n\n"
            f"{context}\n\n"
            f"用户问题: {question}\n\n"
            f"请用中文回答，不超过 2000 字，标注引用。"
        )
        max_chars = self._config.ask_max_answer_chars if self._config else 2000
        temperature = self._config.ask_model_temperature if self._config else 0.0

        try:
            response = await provider.chat_with_retry(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                max_tokens=min(max_chars // 2, 2000),
                temperature=temperature,
            )
            answer = (response.content or "")[:max_chars]
        except Exception:
            logger.exception("UnifiedRAG.ask: LLM call failed")
            answer = f"(LLM 调用失败，以下为检索原始结果)\n\n{context}"

        return {"answer": answer, "sources": sources, "context": context}

    async def compare(
        self, query: str, paper_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """对比检索结果与已分析论文的关联。

        返回: {"matches": [...], "relations": [...], "related_papers": [...]}
        """
        results = await self.search(query, top_k=5)
        matches = [
            {"paper_id": r.get("paper_id", ""), "title": r.get("title", ""), "score": r.get("score", 0)}
            for r in results
        ]

        relations: list[dict[str, str]] = []
        related_papers: list[dict[str, Any]] = []

        if self._pk:
            target_ids = paper_ids or [r.get("paper_id", "") for r in results if r.get("paper_id")]
            for pid in target_ids:
                rels = self._pk.get_related_papers(pid)
                for rel in rels:
                    relations.append({
                        "from": pid,
                        "to": rel.get("target", ""),
                        "type": rel.get("type", "unknown"),
                    })
                related_papers.extend(rels)

        return {
            "matches": matches,
            "relations": relations,
            "related_papers": related_papers,
        }

# ═══════════════════════════════════════════════════════════════
# Agent Tools
# ═══════════════════════════════════════════════════════════════

@tool_parameters(tool_parameters_schema(
    query=StringSchema("搜索查询（关键词、研究问题或论文标题）"),
    top_k=IntegerSchema(5, description="返回结果数量 (1-10)", minimum=1, maximum=10),
    modality=StringSchema("内容类型过滤: text, analysis, 或留空表示全部"),
    tag=StringSchema("按标签过滤（如 transformer, diffusion），留空表示不过滤"),
    author=StringSchema("按作者过滤（子串匹配），留空表示不过滤"),
    year=IntegerSchema(None, description="按发表年份过滤（精确匹配），留空表示不过滤", nullable=True),
    venue=StringSchema("按出处/会议过滤（子串匹配），留空表示不过滤"),
    rerank=BooleanSchema(description="是否启用 LLM 重排序（关闭则用融合序，更快但精度略降）", default=True),
    min_score=NumberSchema(None, description="最低相关性分数阈值 (0-1)，低于该分的 chunk 被截断", minimum=0.0, maximum=1.0, nullable=True),
    fallback=BooleanSchema(description="内部库不足时是否自动回退到外部学术搜索", default=True),
    required=["query"],
))
class RagSearchTool(Tool):
    """统一检索内部知识库（文献库 + 论文分析产物），不足时自动回退到外部学术搜索。"""

    def __init__(self, unified: UnifiedRAG | None = None,
                 paper_search_tool: Any | None = None,
                 web_search_tool: Any | None = None) -> None:
        self._unified = unified
        self._paper_search = paper_search_tool
        self._web_search = web_search_tool

    @property
    def name(self) -> str:
        return "rag_search"

    @property
    def description(self) -> str:
        return (
            "搜索内部知识库（已入库文献 + 已分析论文产物）。"
            "内部结果不足时自动回退到 arXiv/Semantic Scholar/PubMed/DBLP 外部学术搜索。"
            "使用 modality 参数可按内容类型（text/analysis）过滤。"
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, query: str, top_k: int = 5,
                      modality: str = "", tag: str = "",
                      author: str = "", year: int | None = None, venue: str = "",
                      rerank: bool = True, min_score: float | None = None,
                      fallback: bool = True, **kwargs: Any) -> str:
        if not self._unified:
            return "Error: RAG unified search not configured"

        mod = modality.strip() if modality else None
        t = tag.strip() if tag else None
        a = author.strip() if author else None
        v = venue.strip() if venue else None
        results = await self._unified.search(query, top_k=top_k, modality=mod, tag=t,
                                             author=a, year=year, venue=v,
                                             rerank=rerank, min_score=min_score)

        # Quality check for fallback
        top_score = results[0]["score"] if results else 0.0
        needs_fallback = fallback and (
            len(results) < (self._unified._fallback_count if self._unified else 3)
            or top_score < (self._unified._fallback_score if self._unified else 0.5)
        )

        parts: list[str] = []

        if results:
            parts.append(f"## 内部知识库 ({len(results)} 条)")
            for i, r in enumerate(results, 1):
                src = {"literature": "文献库", "analysis": "分析产物"}.get(
                    r.get("source", ""), r.get("source", ""))
                parts.append(
                    f"{i}. **{r.get('title', 'Untitled')}** [{src}] "
                    f"score={r.get('score', 0):.3f}\n"
                    f"   paper_id: `{r.get('paper_id', '')}`\n"
                    f"   {r.get('text', '')[:300]}"
                )
        else:
            parts.append("## 内部知识库\n未找到相关内容。")

        if needs_fallback:
            parts.append("\n## 外部搜索 (内部库覆盖不足，自动扩展)")
            ext_parts: list[str] = []

            if self._paper_search:
                try:
                    ext_res = await self._paper_search.execute(query=query, source="all", max_results=top_k)
                    ext_parts.append(f"### 学术搜索\n{ext_res}")
                except Exception:
                    ext_parts.append("### 学术搜索\n搜索失败")

            if self._web_search:
                try:
                    web_res = await self._web_search.execute(query=query, max_results=min(top_k, 3))
                    ext_parts.append(f"### Web 搜索\n{web_res}")
                except Exception:
                    ext_parts.append("### Web 搜索\n搜索失败")

            if ext_parts:
                parts.extend(ext_parts)
            else:
                parts.append("(外部搜索工具未配置)")

        return "\n\n".join(parts)


@tool_parameters(tool_parameters_schema(
    question=StringSchema("学术问题（基于知识库内容回答）"),
    top_k=IntegerSchema(5, description="检索结果数量 (1-10)", minimum=1, maximum=10),
    modality=StringSchema("内容类型过滤: text, analysis, 或留空表示全部"),
    tag=StringSchema("按标签过滤，留空表示不过滤"),
    author=StringSchema("按作者过滤（子串匹配），留空表示不过滤"),
    year=IntegerSchema(None, description="按发表年份过滤（精确匹配），留空表示不过滤", nullable=True),
    venue=StringSchema("按出处/会议过滤（子串匹配），留空表示不过滤"),
    required=["question"],
))
class RagAskTool(Tool):
    """基于内部知识库检索结果，用 LLM 合成带引用来源的学术回答。"""

    def __init__(self, unified: UnifiedRAG | None = None,
                 provider: "LLMProvider | None" = None) -> None:
        self._unified = unified
        self._provider = provider

    @property
    def name(self) -> str:
        return "rag_ask"

    @property
    def description(self) -> str:
        return (
            "基于内部知识库（已入库文献 + 论文分析产物）回答学术问题。"
            "自动检索相关内容，用 LLM 合成答案，标注引用来源 (paper_id + chunk_id)。"
            "适合需要综合多篇文献信息的复杂问题。"
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, question: str, top_k: int = 5,
                      modality: str = "", tag: str = "",
                      author: str = "", year: int | None = None, venue: str = "",
                      **kwargs: Any) -> str:
        if not self._unified:
            return "Error: RAG unified search not configured"

        mod = modality.strip() if modality else None
        t = tag.strip() if tag else None
        a = author.strip() if author else None
        v = venue.strip() if venue else None
        answer_data = await self._unified.ask(
            question, top_k=top_k, provider=self._provider,
            modality=mod, tag=t, author=a, year=year, venue=v,
        )

        parts = [f"## 回答\n{answer_data['answer']}"]

        sources = answer_data.get("sources", [])
        if sources:
            parts.append("\n## 引用来源")
            for i, s in enumerate(sources, 1):
                parts.append(
                    f"{i}. **{s.get('title', 'Unknown')}** "
                    f"(`{s.get('paper_id', '')}` / `{s.get('chunk_id', '')}`)"
                )

        return "\n\n".join(parts)


@tool_parameters(tool_parameters_schema(
    title=StringSchema("论文标题"),
    abstract=StringSchema("论文摘要 (可为空)"),
    content=StringSchema("论文正文内容或 URL（若为 URL 请先 web_fetch 获取内容）"),
    authors=StringSchema("作者列表，逗号分隔 (如 'Hinton, LeCun, Bengio')"),
    tags=StringSchema("标签，逗号分隔 (如 'transformer, attention, NLP')"),
    year=IntegerSchema(None, description="发表年份 (如 2024)，留空表示未知", nullable=True),
    venue=StringSchema("出处/会议/期刊 (如 NeurIPS, ICML)，留空表示未知"),
    source=StringSchema("来源标识 (如 arxiv, pubmed, manual)，默认 manual"),
    required=["title", "content"],
))
class RagIngestTool(Tool):
    """将文献入库到 RAG 知识库，自动建立 BM25 + 向量索引。"""

    def __init__(self, unified: UnifiedRAG | None = None) -> None:
        self._unified = unified

    @property
    def name(self) -> str:
        return "rag_ingest"

    @property
    def description(self) -> str:
        return (
            "将一篇论文或文献入库到 RAG 知识库，自动索引 (BM25 + 向量)。"
            "入库后可通过 rag_search 检索。如果 content 是 URL，请先用 web_fetch 获取全文。"
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, title: str, content: str, abstract: str = "",
                      authors: str = "", tags: str = "",
                      year: int | None = None, venue: str = "", source: str = "manual",
                      **kwargs: Any) -> str:
        if not self._unified or not self._unified._rag:
            return "Error: ResearchRAG not configured"

        author_list = [a.strip() for a in authors.split(",") if a.strip()] if authors else []
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

        # 归一化：空串/0 → None，避免把空值写进索引元数据
        y = year or None
        v = (venue or "").strip() or None
        src = (source or "").strip() or "manual"

        # Auto-tag from content if enabled
        config = self._unified._config
        if config and config.ingest_auto_tag and not tag_list:
            found = _TECH_KEYWORD_RE.findall(content)
            tag_list = list(dict.fromkeys(t.lower() for t in found))[:10]

        try:
            result = await self._unified._rag.add_paper(
                title=title,
                abstract=abstract,
                content=content,
                authors=author_list,
                tags=tag_list,
                year=y,
                venue=v,
                source=src,
            )
            return json.dumps({
                "status": "ingested",
                "paper_id": result.get("paper_id", ""),
                "title": title,
                "chunk_count": result.get("chunk_count", 0),
                "tags": tag_list,
                "year": y,
                "venue": v,
                "source": src,
                "message": f"已入库: {title} ({result.get('chunk_count', 0)} chunks, tags: {', '.join(tag_list) or 'none'})",
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"入库失败: {e}"


@tool_parameters(tool_parameters_schema(
    query=StringSchema("检索查询（用于查找相关文献和已分析论文）"),
    paper_ids=StringSchema("要对比的已分析论文 ID 列表（逗号/分号/空白分隔），留空则基于检索结果自动发现"),
    synthesize=BooleanSchema(description="是否用 LLM 对匹配与关系生成综合对比结论", default=False),
    required=["query"],
))
class RagCompareTool(Tool):
    """对比检索结果与已分析论文之间的关联。"""

    def __init__(self, unified: UnifiedRAG | None = None,
                 provider: "LLMProvider | None" = None) -> None:
        self._unified = unified
        self._provider = provider

    @staticmethod
    def _parse_paper_ids(raw: str) -> list[str] | None:
        """按逗号/分号（含全角）/空白/换行切分去重，空输入返回 None。"""
        if not raw or not raw.strip():
            return None
        tokens = re.split(r"[,;，；\s]+", raw.strip())
        seen: list[str] = []
        for tok in tokens:
            tok = tok.strip()
            if tok and tok not in seen:
                seen.append(tok)
        return seen or None

    @property
    def name(self) -> str:
        return "rag_compare"

    @property
    def description(self) -> str:
        return (
            "发现检索结果与已分析论文之间的关联关系（引用、方法相似、扩展）。"
            "输入一个研究主题，返回哪些已入库文献和已分析论文相关，以及它们之间的关系。"
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, query: str, paper_ids: str = "", synthesize: bool = False,
                      **kwargs: Any) -> str:
        if not self._unified:
            return "Error: RAG unified search not configured"

        pids = self._parse_paper_ids(paper_ids)
        comparison = await self._unified.compare(query, paper_ids=pids)

        parts: list[str] = []

        matches = comparison.get("matches", [])
        if matches:
            parts.append("## 检索匹配")
            for i, m in enumerate(matches, 1):
                parts.append(f"{i}. **{m.get('title', '?')}** (score: {m.get('score', 0):.3f})")

        relations = comparison.get("relations", [])
        if relations:
            parts.append("\n## 论文关系")
            shown: set[tuple[str, str]] = set()
            for rel in relations:
                pair = tuple(sorted([rel["from"], rel["to"]]))
                if pair in shown:
                    continue
                shown.add(pair)
                rtype = rel.get("type", "unknown")
                label = {"cites": "引用", "similar_method": "方法相似", "extends": "扩展"}.get(rtype, rtype)
                parts.append(f"- `{rel['from']}` → {label} → `{rel['to']}`")

        if not matches and not relations:
            parts.append("未找到相关匹配或关系。")

        if synthesize:
            if self._provider:
                parts.append("\n## 综合对比结论\n" + await self._synthesize(query, comparison))
            else:
                parts.append("\n## 综合对比结论\n(未配置 LLM provider，无法生成综合结论)")

        return "\n".join(parts)

    async def _synthesize(self, query: str, comparison: dict[str, Any]) -> str:
        """用 LLM 对 matches + relations 生成综合对比结论（补 compare 无 LLM 增强的缺口）。"""
        payload = json.dumps({
            "query": query,
            "matches": comparison.get("matches", []),
            "relations": comparison.get("relations", []),
        }, ensure_ascii=False)
        prompt = (
            "基于以下检索匹配与论文关系，生成一段简明的中文对比结论"
            "（相似点、差异、演进脉络），不超过 500 字，不要列编号。\n\n" + payload
        )
        try:
            resp = await self._provider.chat_with_retry(
                messages=[{"role": "user", "content": prompt}],
                tools=None, max_tokens=500, temperature=0.0,
            )
            return (resp.content or "").strip() or "(LLM 返回空)"
        except Exception:
            logger.exception("RagCompareTool._synthesize failed")
            return "(LLM 合成失败)"
