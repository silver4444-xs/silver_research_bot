"""Agent RAG 工具 — UnifiedRAG 统一检索适配器 + 4 个 Agent Tool

UnifiedRAG 融合 ResearchRAG (文献库) 和 PaperKnowledgeIndex (分析产物)，
提供统一检索入口。工具层通过 @tool_parameters 装饰器注册为 Agent 可调用工具。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any

from loguru import logger

from silver_research_bot.agent.tools.base import Tool, tool_parameters
from silver_research_bot.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
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
    """统一检索适配器 — 融合 ResearchRAG + PaperKnowledgeIndex。"""

    def __init__(
        self,
        research_rag: "ResearchRAG | None" = None,
        paper_knowledge: "PaperKnowledgeIndex | None" = None,
        config: "RAGToolConfig | None" = None,
    ) -> None:
        self._rag = research_rag
        self._pk = paper_knowledge
        self._config = config
        self._analysis_weight = config.analysis_source_weight if config else 1.5
        self._fallback_score = config.fallback_score_threshold if config else 0.5
        self._fallback_count = config.fallback_count_threshold if config else 3

    async def search(
        self,
        query: str,
        top_k: int = 5,
        modality: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """并行检索两个索引，去重合并，analysis 结果加权。

        返回: [{chunk_id, paper_id, title, text, score, source, chunk_type, section, metadata}, ...]
        """
        lit_results: list[dict[str, Any]] = []
        ana_results: list[dict[str, Any]] = []

        async def _search_lit() -> None:
            if self._rag:
                try:
                    sr = await self._rag.search(
                        query, top_k=top_k, tag=tag, modality=modality, rerank=True,
                    )
                    for r in sr.get("results", []):
                        r["source"] = "literature"
                        lit_results.append(r)
                except Exception:
                    logger.exception("UnifiedRAG: ResearchRAG search failed")

        async def _search_ana() -> None:
            if self._pk:
                try:
                    pk_results = await self._pk.search(query, top_k=top_k)
                    for r in pk_results:
                        ana_results.append({
                            "chunk_id": r.get("chunk_id", ""),
                            "paper_id": r.get("paper_id", ""),
                            "title": r.get("title", ""),
                            "text": r.get("text_snippet", ""),
                            "score": r.get("score", 0.0),
                            "source": "analysis",
                            "chunk_type": "text",
                            "section": r.get("artifact_kind", ""),
                            "metadata": {},
                        })
                except Exception:
                    logger.exception("UnifiedRAG: PaperKnowledgeIndex search failed")

        await asyncio.gather(_search_lit(), _search_ana())

        return self._merge_results(lit_results, ana_results, top_k)

    def _merge_results(
        self,
        lit_results: list[dict[str, Any]],
        ana_results: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """去重合并：同 paper_id 保留最高分，analysis 加权。"""
        # Apply source weight
        for r in ana_results:
            r["score"] = r.get("score", 0.0) * self._analysis_weight

        # Deduplicate by paper_id: keep highest-scoring chunk per paper
        seen: dict[str, dict[str, Any]] = {}
        for r in lit_results + ana_results:
            pid = r.get("paper_id", r.get("chunk_id", ""))
            if pid not in seen or r.get("score", 0) > seen[pid].get("score", 0):
                seen[pid] = r

        merged = sorted(seen.values(), key=lambda x: x.get("score", 0), reverse=True)
        return merged[:top_k]

    async def build_context(self, query: str, top_k: int = 5) -> str:
        """构建可注入 LLM 的上下文块。"""
        results = await self.search(query, top_k=top_k)
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
    ) -> dict[str, Any]:
        """基于检索结果用 LLM 合成学术回答 (带引用)。

        返回: {"answer": str, "sources": [{chunk_id, paper_id, title}], "context": str}
        """
        results = await self.search(question, top_k=top_k)
        if not results:
            return {
                "answer": "未在知识库中找到相关信息。",
                "sources": [],
                "context": "",
            }

        context = await self.build_context(question, top_k=top_k)
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
