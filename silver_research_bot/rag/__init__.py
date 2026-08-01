"""RAG (Retrieval-Augmented Generation) subsystem.

Components:
- research_rag       — ResearchRAG orchestrator, PaperChunk
- research_bm25      — BM25 Okapi keyword search
- research_embedder  — Embedding generation
- research_vector_store — NumPy vector storage
- research_reranker  — LLM cross-encoder reranking
- research_multimodal — Multimodal filtering (text/formula/figure/table)
"""

from silver_research_bot.rag.research_bm25 import BM25Scorer
from silver_research_bot.rag.research_embedder import EmbeddingEngine
from silver_research_bot.rag.research_multimodal import MultimodalIndexer
from silver_research_bot.rag.research_rag import PaperChunk, ResearchRAG
from silver_research_bot.rag.research_reranker import LLMReranker
from silver_research_bot.rag.research_vector_store import VectorStore

__all__ = [
    "BM25Scorer",
    "EmbeddingEngine",
    "MultimodalIndexer",
    "PaperChunk",
    "ResearchRAG",
    "LLMReranker",
    "VectorStore",
]
