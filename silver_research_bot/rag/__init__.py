"""RAG (Retrieval-Augmented Generation) subsystem.

Components:
- research_rag       — ResearchRAG orchestrator, PaperChunk
- research_bm25      — BM25 Okapi keyword search
- research_embedder  — Embedding generation
- research_vector_store — NumPy vector storage
- research_reranker  — LLM cross-encoder reranking
"""

from silver_research_bot.rag.research_analysis import (
    ANALYSIS_ARTIFACTS,
    ingest_paper_analysis,
    read_analysis_artifacts,
)
from silver_research_bot.rag.research_bm25 import BM25Scorer
from silver_research_bot.rag.research_embedder import EmbeddingEngine
from silver_research_bot.rag.research_rag import PaperChunk, ResearchRAG
from silver_research_bot.rag.research_reranker import LLMReranker
from silver_research_bot.rag.research_runs import RunStore, compute_metrics
from silver_research_bot.rag.research_vector_store import VectorStore

__all__ = [
    "ANALYSIS_ARTIFACTS",
    "BM25Scorer",
    "compute_metrics",
    "EmbeddingEngine",
    "ingest_paper_analysis",
    "PaperChunk",
    "read_analysis_artifacts",
    "ResearchRAG",
    "LLMReranker",
    "RunStore",
    "VectorStore",
]
