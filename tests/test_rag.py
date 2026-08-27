"""文献 RAG 子系统单元测试 — 全离线（provider=None → TF-IDF fallback）。

覆盖本次升级的关键行为：
- BM25 中文分词（单字 + 重叠 bigram）
- VectorStore tombstone 重载后不复活
- EmbeddingEngine TF-IDF 稳定哈希（跨实例确定性）
- LLMReranker 分数解析（1-10 clamp）
- ResearchRAG CRUD 持久化回归（delete 后重启不复活）
- search 元数据过滤（tag/author/year/venue/modality）+ score 归一化 [0,1]
"""

from __future__ import annotations

import asyncio
import json

from silver_research_bot.rag import (
    LLMReranker, ResearchRAG, VectorStore, read_analysis_artifacts,
)
from silver_research_bot.agent.paper_knowledge import PaperKnowledgeIndex
from silver_research_bot.agent.tools.rag_tools import UnifiedRAG
from silver_research_bot.rag.research_bm25 import BM25Scorer, _tokenize
from silver_research_bot.rag.research_embedder import EmbeddingEngine


# ── BM25 中文分词 ────────────────────────────────────────────

def test_bm25_cjk_tokenize_single_char_plus_bigram():
    tokens = _tokenize("深度学习")
    for t in ("深", "学", "习"):
        assert t in tokens
    assert "深度" in tokens and "学习" in tokens


def test_bm25_latin_word_level():
    tokens = _tokenize("Attention is all you need")
    assert "attention" in tokens and "need" in tokens


def test_bm25_score_orders_relevant_doc_first_and_remove():
    bm = BM25Scorer()
    bm.add("d1", "transformer attention mechanism")
    bm.add("d2", "convolutional neural network")
    ids = [cid for cid, _ in bm.score("transformer attention")]
    assert ids[0] == "d1"          # d1 更相关排第一
    assert "d2" not in ids         # d2 与查询无重叠词，不计分
    bm.remove("d1")
    assert all(cid != "d1" for cid, _ in bm.score("transformer attention"))


# ── VectorStore tombstone 持久化 ─────────────────────────────

def test_vector_store_tombstone_survives_reload(tmp_path):
    store = VectorStore(tmp_path / "vec", dim=4)
    store.add([[1.0, 0, 0, 0], [0.0, 1, 0, 0]], ["a", "b"])
    assert store.search([1.0, 0, 0, 0], top_k=2)[0][0] == "a"
    store.remove(["a"])
    # 重新实例化（模拟重启）后 tombstone 仍生效
    store2 = VectorStore(tmp_path / "vec", dim=4)
    assert all(cid != "a" for cid, _ in store2.search([1.0, 0, 0, 0], top_k=2))


# ── EmbeddingEngine TF-IDF 确定性 ────────────────────────────

def test_embedder_tfidf_deterministic_across_instances():
    e1 = EmbeddingEngine(provider=None, dim=256)
    e2 = EmbeddingEngine(provider=None, dim=256)
    text = "中文检索与 transformer 注意力机制"
    assert e1._tfidf_vector(text) == e2._tfidf_vector(text)


# ── LLMReranker 分数解析 ─────────────────────────────────────

def test_reranker_parse_scores_clamps_to_1_10():
    scores = LLMReranker._parse_scores("<0>: 9\n1：3.5\n2: 15\n3: 0", 4)
    assert scores[0] == 9.0
    assert scores[1] == 3.5
    assert scores[2] == 10.0  # 上限 clamp
    assert scores[3] == 1.0   # 下限 clamp


# ── ResearchRAG CRUD 持久化回归（锁定软删除持久化）────────────

def test_rag_delete_persists_after_restart(tmp_path):
    async def scenario():
        root = tmp_path / "rag"
        rag = ResearchRAG(root=root)
        rec = await rag.add_paper(
            title="Transformer", abstract="attention mechanism",
            content="The Transformer uses self-attention.",
            authors=["Vaswani"], tags=["nlp"], year=2017, venue="NeurIPS",
        )
        pid = rec["paper_id"]

        sr = await rag.search("attention", top_k=5)
        assert any(r["paper_id"] == pid for r in sr["results"])

        assert await rag.delete_paper(pid) is True

        # 新实例（模拟重启）：旧 chunk 不得复活
        rag2 = ResearchRAG(root=root)
        sr2 = await rag2.search("attention", top_k=5)
        assert all(r["paper_id"] != pid for r in sr2["results"])

        # 不存在的 paper 删除返回 False（404 语义）
        assert await rag2.delete_paper("nonexistent") is False

    asyncio.run(scenario())


def test_rag_search_metadata_filters_and_score_normalization(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        rec = await rag.add_paper(
            title="GPT", abstract="language model",
            content="Generative pretrained transformer.",
            authors=["Radford"], tags=["llm"], year=2018, venue="OpenAI",
        )
        pid = rec["paper_id"]

        async def hit(**kw):
            sr = await rag.search("transformer", top_k=10, **kw)
            return any(r["paper_id"] == pid for r in sr["results"]), sr

        assert (await hit(tag="llm"))[0] is True
        assert (await hit(tag="vision"))[0] is False
        assert (await hit(author="radford"))[0] is True
        assert (await hit(year=2018))[0] is True
        assert (await hit(year=2020))[0] is False
        ok, sr = await hit(venue="openai")
        assert ok is True
        # 融合/重排后的 score 均在 [0,1]，杜绝 850% 尺度假象
        assert sr["results"]
        assert all(0.0 <= r["score"] <= 1.0 for r in sr["results"])

    asyncio.run(scenario())


def test_rag_list_tags_and_authors(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(
            title="A", abstract="", content="content A",
            authors=["Alice", "Bob"], tags=["nlp", "cv"],
        )
        tags = rag.list_tags()
        authors = rag.list_authors()
        assert "nlp" in tags and "cv" in tags
        assert "Alice" in authors and "Bob" in authors

    asyncio.run(scenario())


def test_rag_ingest_analysis_idempotent(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        artifacts = {
            "system_model": "# 系统模型\n\n基于 Transformer 的编码器-解码器架构。",
            "translation": "# 翻译\n\n本文提出了一种新的注意力机制。",
            "summary": "- 关键发现一：提出稀疏注意力\n- 关键发现二：降低计算复杂度",
        }
        r1 = await rag.ingest_analysis("p1", "Test Paper", artifacts)
        assert r1["status"] == "ok"
        assert r1["count"] >= 3

        # 幂等：第二次 skipped
        r2 = await rag.ingest_analysis("p1", "Test Paper", artifacts)
        assert r2["status"] == "skipped"

        sr = await rag.search("Transformer", top_k=10, modality="analysis")
        assert any(r["chunk_type"] == "analysis" for r in sr["results"])
        sections = {r["section"] for r in sr["results"]}
        assert "system_model" in sections

    asyncio.run(scenario())


def test_rag_read_analysis_artifacts(tmp_path):
    paper_dir = tmp_path / "p1"
    paper_dir.mkdir()
    (paper_dir / "analysis_system_model.md").write_text("系统模型内容", encoding="utf-8")
    (paper_dir / "translation.md").write_text("翻译内容", encoding="utf-8")
    # 缺失 analysis_problem.md / formula_explanations.md 应被跳过
    (paper_dir / "analysis_summary.json").write_text(
        json.dumps({"key_findings": ["发现一", "发现二"], "paper_id": "p1"}),
        encoding="utf-8",
    )

    artifacts = read_analysis_artifacts(paper_dir)
    assert artifacts["system_model"] == "系统模型内容"
    assert artifacts["translation"] == "翻译内容"
    assert "summary" in artifacts and "发现一" in artifacts["summary"]
    assert "problem" not in artifacts          # 缺失文件被跳过
    assert "formula" not in artifacts          # 缺失文件被跳过


def test_rag_reindex_backfills_analysis(tmp_path):
    """reindex 从论文目录回填分析产物（幂等补缺，无参调用向后兼容）。"""
    async def scenario():
        papers_dir = tmp_path / "papers"
        paper_dir = papers_dir / "p1"
        paper_dir.mkdir(parents=True)
        (paper_dir / "extracted.json").write_text(
            json.dumps({"title": "Test Paper", "formulas": [], "figures": [], "tables": []}),
            encoding="utf-8",
        )
        (paper_dir / "analysis_system_model.md").write_text(
            "# 系统模型\n\n基于 Transformer 的编码器-解码器架构。", encoding="utf-8",
        )
        (paper_dir / "analysis_summary.json").write_text(
            json.dumps({"key_findings": ["提出稀疏注意力"]}), encoding="utf-8",
        )

        rag = ResearchRAG(root=tmp_path / "rag")
        result = await rag.reindex(papers_dir=papers_dir)
        assert result["status"] == "ok"

        sr = await rag.search("Transformer", top_k=10, modality="analysis")
        assert any(r["chunk_type"] == "analysis" for r in sr["results"])
        sections = {r["section"] for r in sr["results"]}
        assert "system_model" in sections

        # 无参 reindex 向后兼容，不抛错
        await rag.reindex()

    asyncio.run(scenario())


# ── P0-2 跨实例一致性（_maybe_sync 联动重建 BM25/向量）─────────

def test_rag_cross_instance_sync_after_add_and_delete(tmp_path):
    async def scenario():
        root = tmp_path / "rag"
        # 实例 B 先初始化（空库），此后 A 的写入需靠 _maybe_sync 联动重载才能被 B 看到
        b = ResearchRAG(root=root)
        assert await b.search("attention", top_k=5) is not None

        # 实例 A 入库
        a = ResearchRAG(root=root)
        rec = await a.add_paper(
            title="Transformer", abstract="attention mechanism",
            content="The Transformer uses self-attention.",
            authors=["Vaswani"],
        )
        pid = rec["paper_id"]

        # B 检索应命中（_maybe_sync 检测 corpus 变化并重建 BM25 / 重载向量）
        sr = await b.search("attention", top_k=5)
        assert any(r["paper_id"] == pid for r in sr["results"])

        # A 删除后，B 检索不再命中
        assert await a.delete_paper(pid) is True
        sr2 = await b.search("attention", top_k=5)
        assert all(r["paper_id"] != pid for r in sr2["results"])

    asyncio.run(scenario())


# ── P0-3 跨实例并发写（文件锁防丢更新）─────────────────────────

def test_rag_concurrent_add_across_instances_no_lost_update(tmp_path):
    async def scenario():
        root = tmp_path / "rag"
        a = ResearchRAG(root=root)
        b = ResearchRAG(root=root)

        async def add(rag, i):
            return await rag.add_paper(
                title=f"Paper {i}", abstract=f"abs {i}",
                content=f"content number {i}", authors=[f"author{i}"],
            )

        recs = await asyncio.gather(*(add(a if i % 2 == 0 else b, i) for i in range(8)))
        assert len({r["paper_id"] for r in recs}) == 8

        # index.json 不丢记录（FileLock 包裹 read-modify-write）
        idx = json.loads((root / "index.json").read_text(encoding="utf-8"))
        assert len(idx) == 8

        # corpus.jsonl 行数正确（每篇至少 1 个 text chunk）
        lines = [l for l in (root / "corpus.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) >= 8

    asyncio.run(scenario())


# ── P0-1 向量维度不匹配不崩溃（embedder 层回退 TF-IDF）──────────

def test_rag_dimension_mismatch_falls_back_no_crash(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        rag._rerank_enabled = False  # 隔离维度测试，避免坏 provider 触发 reranker

        class BadProvider:
            """返回 3072 维，模拟换 embedding_model 却未同步 embedding_dimensions。"""
            async def embed(self, text, model=None):
                return [0.0] * 3072
            async def embed_batch(self, texts, model=None):
                return [[0.0] * 3072 for _ in texts]

        rag._provider = BadProvider()
        rag._embedding_model = "text-embedding-3-large"

        # 不应崩：embed_batch 维度不符 → 记日志并回退 TF-IDF（1536 维）
        rec = await rag.add_paper(
            title="T", abstract="a", content="hello world content", authors=["x"],
        )
        assert rec["paper_id"]

        # search 不应崩，且回退后仍有结果
        sr = await rag.search("hello", top_k=5)
        assert sr["results"]

    asyncio.run(scenario())


# ── P1-3 RRF 融合单例不退化（唯一结果不再被 min-max 归一到 0）───

def test_rag_rrf_single_result_non_degenerate(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(title="Transformer", abstract="", content="self-attention mechanism")
        sr = await rag.search("attention", top_k=5)
        assert sr["results"]
        # 旧 min-max 归一化在单例时把唯一结果压到 0；RRF 后应恒为正分
        assert all(r["score"] > 0.0 for r in sr["results"])

    asyncio.run(scenario())


# ── P1-2 切分保护公式/代码块（占位符保护，不在块内切断）──────────

def test_rag_chunk_split_protects_formula_and_code(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        content = (
            "## 方法\n\n"
            + "本段内容较长，用于触发 token 预算切分。" * 300
            + "\n\n公式如下：\n$$\nE = mc^2\n$$\n\n"
            + "代码：\n```python\nprint('x')\n```\n"
        )
        chunks = rag._split_chunks(content)
        assert chunks
        for _, body in chunks:
            assert body.count("$$") % 2 == 0, f"公式被切断: {body!r}"
            assert body.count("```") % 2 == 0, f"代码块被切断: {body!r}"

    asyncio.run(scenario())


# ── P1-7 chunk_id 内容寻址稳定（跨会话可去重）───────────────────

def test_rag_content_addressed_chunk_id_stable(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        # 静态内容寻址确定性：同 (paper, section, text) → 同 id；text 变 → id 变
        assert rag._content_chunk_id("p1", "全文", "相同") == rag._content_chunk_id("p1", "全文", "相同")
        assert rag._content_chunk_id("p1", "全文", "相同") != rag._content_chunk_id("p1", "全文", "不同")

        await rag.add_paper(paper_id="p1", title="T", abstract="", content="hello world")
        ids1 = {c.chunk_id for c in rag._get_chunks().values() if c.paper_id == "p1"}
        # 相同内容更新 → chunk_id 稳定（不再每次 uuid 随机）
        await rag.update_paper("p1", title="T", abstract="", content="hello world")
        ids2 = {c.chunk_id for c in rag._get_chunks().values() if c.paper_id == "p1"}
        assert ids1 == ids2

    asyncio.run(scenario())


# ── P1-6 min_score 阈值截断低分噪声 ─────────────────────────────

def test_rag_min_score_threshold_filters_noise(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(title="T", abstract="", content="attention transformer model")
        sr = await rag.search("attention", top_k=5)
        assert sr["results"]  # 默认阈值 0，有结果

        rag._min_score = 1.5  # 高于任何 RRF 归一化分数（max 1.0）
        sr2 = await rag.search("attention", top_k=5)
        assert sr2["results"] == []  # 全部被阈值截断

    asyncio.run(scenario())


# ── P1-4 reranker 失败回退 None（调用方维持融合序）──────────────

def test_reranker_returns_none_on_provider_failure():
    class FailingProvider:
        async def chat_with_retry(self, **kw):
            raise RuntimeError("boom")

    r = LLMReranker(provider=FailingProvider())

    async def scenario():
        candidates = [(f"c{i}", f"text {i}") for i in range(10)]
        out = await r.rerank("query", candidates, top_k=5)
        assert out is None  # 失败回退：返回 None，而非全 10 分静默不重排

    asyncio.run(scenario())


def test_reranker_skips_when_candidates_not_exceed_topk():
    r = LLMReranker(provider=object())  # 无需真正 provider：候选 ≤ top_k 时早退

    async def scenario():
        candidates = [(f"c{i}", f"text {i}") for i in range(3)]
        assert await r.rerank("query", candidates, top_k=5) is None

    asyncio.run(scenario())


# ── P2-5 分页 / 损坏恢复 / top_k 校验 ─────────────────────────

def test_rag_list_papers_pagination(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        for i in range(3):
            await rag.add_paper(title=f"P{i}", abstract="", content=f"content {i}", paper_id=f"p{i}")
        all_papers = rag.list_papers()
        assert len(all_papers) == 3
        assert rag.list_papers(limit=2) == all_papers[:2]
        assert rag.list_papers(offset=1) == all_papers[1:]
        assert rag.list_papers(limit=1, offset=1) == all_papers[1:2]

    asyncio.run(scenario())


def test_rag_corrupt_index_recovers_to_empty(tmp_path):
    async def scenario():
        root = tmp_path / "rag"
        root.mkdir(parents=True)
        (root / "index.json").write_text("{not valid json", encoding="utf-8")
        rag = ResearchRAG(root=root)
        assert rag._index == []                      # 不崩，重建空索引
        assert (root / "index.json.bak").exists()    # 坏文件已备份

    asyncio.run(scenario())


def test_rag_corrupt_corpus_line_skipped(tmp_path):
    async def scenario():
        root = tmp_path / "rag"
        root.mkdir(parents=True)
        good = {"chunk_id": "c1", "paper_id": "p1", "title": "T", "section": "s",
                "text": "hello", "source": "manual", "chunk_type": "text", "metadata": {}}
        (root / "corpus.jsonl").write_text(
            "not json\n" + json.dumps(good, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        rag = ResearchRAG(root=root)
        chunks = rag._get_chunks()
        assert "c1" in chunks and len(chunks) == 1  # 坏行被跳过，好行保留

    asyncio.run(scenario())


def test_rag_top_k_invalid_falls_back_to_default(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(title="T", abstract="", content="attention mechanism")
        # top_k=0 / 负值 不应触发 filter[:负数]，回退默认 final_top_k
        sr0 = await rag.search("attention", top_k=0)
        sr_neg = await rag.search("attention", top_k=-3)
        assert sr0["results"]
        assert sr_neg["results"]

    asyncio.run(scenario())


# ── P2-1a 分析产物集合补全（review_* 纳入 ResearchRAG）───────────

def test_rag_read_analysis_artifacts_includes_reviews(tmp_path):
    paper_dir = tmp_path / "p1"
    paper_dir.mkdir()
    (paper_dir / "review_theory.md").write_text("理论审稿", encoding="utf-8")
    (paper_dir / "review_engineering.md").write_text("工程审稿", encoding="utf-8")
    (paper_dir / "review_domain.md").write_text("领域审稿", encoding="utf-8")

    artifacts = read_analysis_artifacts(paper_dir)
    assert artifacts["review_theory"] == "理论审稿"
    assert artifacts["review_engineering"] == "工程审稿"
    assert artifacts["review_domain"] == "领域审稿"


# ── P2-1b PaperKnowledgeIndex 退化为关系图 + 标题解析 ─────────────

def test_paper_knowledge_index_reduced_no_vector_search(tmp_path):
    async def scenario():
        papers_dir = tmp_path / "papers"
        paper_dir = papers_dir / "p1"
        paper_dir.mkdir(parents=True)
        (papers_dir / "index.json").write_text(
            json.dumps({"p1": {"status": "completed", "title": "Transformer"}}),
            encoding="utf-8",
        )
        (paper_dir / "analysis_algorithm.md").write_text(
            "# 算法\n\ntransformer attention mechanism", encoding="utf-8",
        )
        (paper_dir / "analysis_system_model.md").write_text(
            "# 模型\n\nencoder decoder", encoding="utf-8",
        )

        pk = PaperKnowledgeIndex(papers_dir, tmp_path / "cache")
        await pk.build()
        # 关系图 + 标题解析仍可用
        assert pk._chunks
        assert all("keywords" in c and "text" not in c for c in pk._chunks)  # P2-2: 不存全文
        assert pk._resolve_title("p1") == "Transformer"
        assert pk.get_related_papers("p1") == []  # 单篇无关系，但不报错
        # 精简后不再暴露向量检索机制
        assert not hasattr(pk, "search")
        assert not hasattr(pk, "build_context")
        assert not hasattr(pk, "_vectors")
        assert not hasattr(pk, "embedder")

    asyncio.run(scenario())


def test_paper_knowledge_index_keyword_based_relations(tmp_path):
    async def scenario():
        papers_dir = tmp_path / "papers"
        papers_dir.mkdir(parents=True)
        (papers_dir / "index.json").write_text(json.dumps({
            "p1": {"status": "completed", "title": "Transformer Paper"},
            "p2": {"status": "completed", "title": "BERT Paper"},
        }), encoding="utf-8")
        for pid, body in [("p1", "transformer attention mechanism encoder decoder"),
                          ("p2", "transformer attention encoder BERT")]:
            d = papers_dir / pid
            d.mkdir()
            (d / "analysis_algorithm.md").write_text(f"# 算法\n\n{body}", encoding="utf-8")

        pk = PaperKnowledgeIndex(papers_dir, tmp_path / "cache")
        await pk.build()
        # 共享 transformer/attention/encoder 关键词 → similar_method 双向关系
        rels_p1 = pk.get_related_papers("p1")
        assert any(r["target"] == "p2" and r["type"] == "similar_method" for r in rels_p1)
        rels_p2 = pk.get_related_papers("p2")
        assert any(r["target"] == "p1" and r["type"] == "similar_method" for r in rels_p2)

    asyncio.run(scenario())


# ── P2-1c UnifiedRAG 单一检索源 ─────────────────────────────────

def test_unified_rag_search_single_source(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(title="Lit", abstract="", content="attention mechanism literature")
        await rag.ingest_analysis("p1", "Test Paper", {
            "system_model": "# 系统模型\n\n基于 Transformer 的编码器-解码器架构。",
        })

        unified = UnifiedRAG(research_rag=rag, paper_knowledge=None)
        # 文献 chunk → source="literature"
        lit = await unified.search("attention", top_k=10)
        assert any(r["source"] == "literature" for r in lit)
        # analysis chunk → source="analysis"
        ana = await unified.search("Transformer", top_k=10, modality="analysis")
        assert ana and all(r["source"] == "analysis" for r in ana)
        assert all(r["chunk_type"] == "analysis" for r in ana)

    asyncio.run(scenario())


# ── P2-3 查询扩展 ───────────────────────────────────────────────

def test_rag_expand_queries_offline_rule_based(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        qs = await rag._expand_queries("transformer attention")
        assert qs[0] == "transformer attention"  # 原始 query 恒为首位
        assert len(qs) >= 2                       # 无 provider → 规则型同义词变体
        assert len(qs) == len(set(qs))            # 无重复
        assert all(isinstance(q, str) for q in qs)

    asyncio.run(scenario())


def test_rag_expand_queries_cached(tmp_path):
    class FakeProvider:
        def __init__(self):
            self.calls = 0

        async def chat_with_retry(self, **kw):
            self.calls += 1
            class Resp:
                content = "1. self-attention mechanism\n2. 注意力机制\n"
            return Resp()

    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        rag._provider = FakeProvider()
        rag._query_expansion_enabled = True
        rag._query_expansion_count = 3

        q1 = await rag._expand_queries("transformer attention")
        q2 = await rag._expand_queries("transformer attention")
        assert q1 == q2
        assert rag._provider.calls == 1  # 第二次命中查询级缓存

    asyncio.run(scenario())


def test_rag_expand_queries_cache_ttl(tmp_path):
    class FakeProvider:
        def __init__(self):
            self.calls = 0

        async def chat_with_retry(self, **kw):
            self.calls += 1
            class Resp:
                content = "1. self-attention mechanism\n"
            return Resp()

    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        rag._provider = FakeProvider()
        rag._query_expansion_enabled = True
        rag._query_expansion_count = 3
        rag._query_expansion_cache_ttl = 0  # 禁用缓存 → 每次重查

        await rag._expand_queries("transformer attention")
        await rag._expand_queries("transformer attention")
        assert rag._provider.calls == 2

    asyncio.run(scenario())


def test_rag_expand_queries_fake_provider_multi_query(tmp_path):
    class FakeProvider:
        async def chat_with_retry(self, **kw):
            class Resp:
                content = "1. self-attention mechanism\n2. 注意力机制\n3. attention mechanism\n"
            return Resp()

    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        rag._provider = FakeProvider()
        rag._query_expansion_enabled = True
        rag._query_expansion_count = 3

        qs = await rag._expand_queries("transformer attention")
        assert qs[0] == "transformer attention"  # 原始 query 恒为首位
        assert len(qs) >= 2                       # 至少原始 + 1 变体
        assert len(qs) == len(set(qs))            # 无重复
        assert all(isinstance(q, str) for q in qs)

    asyncio.run(scenario())


# ── P2-1f build_context(modality) 透传 ──────────────────────────

def test_rag_build_context_modality_analysis_only(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(title="Lit", abstract="", content="attention mechanism literature")
        await rag.ingest_analysis("p1", "Test Paper", {
            "system_model": "# 系统模型\n\n基于 Transformer 的编码器-解码器架构。",
        })

        ctx = await rag.build_context("Transformer", top_k=10, modality="analysis")
        assert ctx["context"]
        # context 内全部为 analysis chunk
        assert all(r["chunk_type"] == "analysis" for r in ctx["results"])

    asyncio.run(scenario())


# ── Group A-1.1 标题/摘要纳入检索 ─────────────────────────────

def test_rag_title_abstract_searchable(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(
            title="AlphaFold Protein Structure Prediction",
            abstract="Deep learning achieves high accuracy in protein folding.",
            content="Residue co-evolution constraints guide the model.",
        )
        # 摘要关键词唯一 → 命中 abstract chunk
        sr_abs = await rag.search("accuracy", top_k=10)
        assert any(r["section"] == "abstract" for r in sr_abs["results"])
        # 标题词唯一 → 命中 title chunk
        sr_title = await rag.search("AlphaFold", top_k=10)
        assert any(r["section"] == "title" for r in sr_title["results"])

    asyncio.run(scenario())


def test_rag_empty_abstract_no_extra_chunk(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(title="T", abstract="", content="attention mechanism")
        sections = {c.section for c in rag._get_chunks().values()}
        assert "title" in sections
        assert "abstract" not in sections  # 空摘要不产生额外 chunk

    asyncio.run(scenario())


def test_rag_title_chunk_id_stable_on_reingest(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(title="T", abstract="A", content="body text", paper_id="p1")

        def title_abstract_ids():
            return {c.section: c.chunk_id for c in rag._get_chunks().values()
                    if c.section in ("title", "abstract")}

        first = title_abstract_ids()
        # 同 paper_id 重入库 → 内容寻址 chunk_id 不变（去重幂等）
        await rag.add_paper(title="T", abstract="A", content="body text", paper_id="p1")
        assert title_abstract_ids() == first

    asyncio.run(scenario())


# ── Group A-1.2 analysis chunk 元数据继承 ──────────────────────

def test_rag_ingest_analysis_inherits_paper_metadata(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(
            title="Lit", abstract="", content="attention mechanism",
            authors=["Radford"], tags=["llm"], year=2018, venue="OpenAI", paper_id="p1",
        )
        await rag.ingest_analysis("p1", "Lit", {
            "system_model": "# 系统模型\n\n基于 Transformer 的编码器-解码器架构。",
        })
        # analysis chunk 从 index 兜底继承 year/venue/tag → 过滤命中
        assert any(r["chunk_type"] == "analysis"
                   for r in (await rag.search("Transformer", top_k=10, modality="analysis", year=2018))["results"])
        assert not any(r["chunk_type"] == "analysis"
                       for r in (await rag.search("Transformer", top_k=10, modality="analysis", year=2020))["results"])
        assert any(r["chunk_type"] == "analysis"
                   for r in (await rag.search("Transformer", top_k=10, modality="analysis", venue="openai"))["results"])
        assert any(r["chunk_type"] == "analysis"
                   for r in (await rag.search("Transformer", top_k=10, modality="analysis", tag="llm"))["results"])

    asyncio.run(scenario())


def test_rag_ingest_analysis_explicit_metadata_override(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(title="Lit", abstract="", content="attention",
                            paper_id="p1", year=2018, tags=["llm"], authors=["Radford"])
        await rag.ingest_analysis("p1", "Lit", {"system_model": "# 系统模型\n\n基于 Transformer。"},
                                  metadata={"year": 2020, "tags": ["cv"], "authors": ["He"], "venue": "CVPR"})
        # 显式 metadata 覆盖 index 兜底
        assert any(r["chunk_type"] == "analysis"
                   for r in (await rag.search("Transformer", top_k=10, modality="analysis", year=2020))["results"])
        assert not any(r["chunk_type"] == "analysis"
                       for r in (await rag.search("Transformer", top_k=10, modality="analysis", year=2018))["results"])
        assert any(r["chunk_type"] == "analysis"
                   for r in (await rag.search("Transformer", top_k=10, modality="analysis", venue="cvpr"))["results"])

    asyncio.run(scenario())


def test_ingest_paper_analysis_passes_metadata(tmp_path):
    from silver_research_bot.rag.research_analysis import ingest_paper_analysis

    async def scenario():
        papers_dir = tmp_path / "papers"
        paper_dir = papers_dir / "p1"
        paper_dir.mkdir(parents=True)
        (paper_dir / "extracted.json").write_text(json.dumps({
            "title": "Test Paper", "authors": ["Alice"], "tags": ["nlp"],
            "year": 2021, "venue": "ACL", "abstract": "A transformer study.",
        }), encoding="utf-8")
        (paper_dir / "analysis_system_model.md").write_text(
            "# 系统模型\n\n基于 Transformer 的编码器。", encoding="utf-8",
        )

        rag = ResearchRAG(root=tmp_path / "rag")
        await ingest_paper_analysis(rag, papers_dir, "p1")

        # extracted.json 的 year/venue 透传到 analysis chunk → 过滤命中
        assert any(r["chunk_type"] == "analysis"
                   for r in (await rag.search("Transformer", top_k=10, modality="analysis", year=2021))["results"])
        assert any(r["chunk_type"] == "analysis"
                   for r in (await rag.search("Transformer", top_k=10, modality="analysis", venue="acl"))["results"])

    asyncio.run(scenario())


# ── Group B-2.3 嵌入磁盘缓存 ──────────────────────────────────

def test_embedder_disk_cache_persists_across_instances(tmp_path):
    class FakeProvider:
        def __init__(self, dim=4):
            self.dim = dim
            self.calls = 0

        async def embed_batch(self, texts, model=None):
            self.calls += 1
            return [[float(len(t))] * self.dim for t in texts]

    async def scenario():
        cache = tmp_path / "embed_cache.json"
        p1 = FakeProvider(dim=4)
        e1 = EmbeddingEngine(provider=p1, model="m", dim=4, cache_path=cache)
        await e1.embed_batch(["alpha", "beta"])
        assert p1.calls == 1

        # 新实例（模拟重启）命中磁盘缓存，provider 不再调用
        p2 = FakeProvider(dim=4)
        e2 = EmbeddingEngine(provider=p2, model="m", dim=4, cache_path=cache)
        await e2.embed_batch(["alpha", "beta"])
        assert p2.calls == 0

    asyncio.run(scenario())


def test_embedder_disk_cache_dim_mismatch_discards(tmp_path):
    cache = tmp_path / "embed_cache.json"
    cache.write_text(json.dumps({"model": "m", "dim": 4, "vectors": {"a": [1, 2, 3, 4]}}), encoding="utf-8")
    e = EmbeddingEngine(provider=None, model="m", dim=256, cache_path=cache)
    assert e._cache == {}
    assert cache.with_suffix(cache.suffix + ".bak").exists()


def test_embedder_disk_cache_corrupt_recovers(tmp_path):
    cache = tmp_path / "embed_cache.json"
    cache.write_text("not json", encoding="utf-8")
    e = EmbeddingEngine(provider=None, model="m", dim=4, cache_path=cache)
    assert e._cache == {}
    assert cache.with_suffix(cache.suffix + ".bak").exists()


# ── Group B-2.1 reranker 缓存 + 排序修复 ──────────────────────

def test_reranker_cache_hits_same_query():
    class FakeProvider:
        def __init__(self):
            self.calls = 0

        async def chat_with_retry(self, **kw):
            self.calls += 1
            class Resp:
                content = "".join(f"<{i}>: {10 - i}\n" for i in range(10))
            return Resp()

    async def scenario():
        r = LLMReranker(provider=FakeProvider())
        candidates = [(f"c{i}", f"text {i}") for i in range(10)]
        out1 = await r.rerank("query", candidates, top_k=5)
        out2 = await r.rerank("query", candidates, top_k=5)
        assert out1 == out2
        assert r.provider.calls == 1  # 第二次命中查询级缓存

    asyncio.run(scenario())


def test_reranker_cache_key_varies_with_candidates():
    k1 = LLMReranker._cache_key("q", ["a", "b", "c"])
    k2 = LLMReranker._cache_key("q", ["a", "b", "d"])  # 候选不同 → key 不同
    k3 = LLMReranker._cache_key("q", ["c", "b", "a"])  # 候选顺序不同 → 同 key
    assert k1 != k2
    assert k1 == k3


def test_reranker_cache_ttl_expiry():
    class FakeProvider:
        def __init__(self):
            self.calls = 0

        async def chat_with_retry(self, **kw):
            self.calls += 1
            class Resp:
                content = "".join(f"<{i}>: {10 - i}\n" for i in range(10))
            return Resp()

    async def scenario():
        r = LLMReranker(provider=FakeProvider(), ttl_seconds=0)  # 禁用缓存
        candidates = [(f"c{i}", f"text {i}") for i in range(10)]
        await r.rerank("query", candidates, top_k=5)
        await r.rerank("query", candidates, top_k=5)
        assert r.provider.calls == 2

    asyncio.run(scenario())


def test_reranker_missing_scores_sink_bottom():
    class FakeProvider:
        async def chat_with_retry(self, **kw):
            class Resp:
                content = "<0>: 9\n<1>: 8\n"  # 只给前两个候选打分
            return Resp()

    async def scenario():
        r = LLMReranker(provider=FakeProvider())
        candidates = [(f"c{i}", f"text {i}") for i in range(6)]
        out = await r.rerank("query", candidates, top_k=3)
        # 有分候选 (9, 8) 排前，缺分候选记 0.0 沉底，绝不压过真实 1-10 分
        assert [cid for cid, _ in out] == ["c0", "c1", "c2"]
        scores = dict(out)
        assert scores["c0"] == 9.0
        assert scores["c1"] == 8.0
        assert scores["c2"] == 0.0

    asyncio.run(scenario())


# ── Group C-3.2 RagIngestTool 透传 year/venue/source ──────────

def test_rag_ingest_tool_passes_year_venue():
    from silver_research_bot.agent.tools.rag_tools import RagIngestTool

    class FakeRAG:
        def __init__(self):
            self.captured = None

        async def add_paper(self, **kwargs):
            self.captured = kwargs
            return {"paper_id": "p1", "chunk_count": 3}

    class FakeUnified:
        def __init__(self, rag):
            self._rag = rag
            self._config = None

    async def scenario():
        rag = FakeRAG()
        tool = RagIngestTool(unified=FakeUnified(rag))
        out = await tool.execute(
            title="T", content="body", authors="Alice, Bob", tags="nlp",
            year=2021, venue="NeurIPS", source="arxiv",
        )
        assert rag.captured["year"] == 2021
        assert rag.captured["venue"] == "NeurIPS"
        assert rag.captured["source"] == "arxiv"
        assert rag.captured["authors"] == ["Alice", "Bob"]
        assert rag.captured["tags"] == ["nlp"]
        assert "NeurIPS" in out and "arxiv" in out

        # 归一化：year=0 / venue 空白 / source 空 → None / None / manual
        await tool.execute(title="T", content="body", year=0, venue="   ", source="")
        assert rag.captured["year"] is None
        assert rag.captured["venue"] is None
        assert rag.captured["source"] == "manual"

    asyncio.run(scenario())


# ── Group C-3.1 Agent 工具暴露检索参数 ─────────────────────────

def test_unified_search_rerank_flag_off():
    class FakeRAG:
        def __init__(self):
            self.captured = None

        async def search(self, query, **kwargs):
            self.captured = kwargs
            return {"results": []}

    async def scenario():
        rag = FakeRAG()
        unified = UnifiedRAG(research_rag=rag, paper_knowledge=None)
        await unified.search("q", rerank=False, min_score=0.5)
        assert rag.captured["rerank"] is False
        assert rag.captured["min_score"] == 0.5
        # 默认 rerank=True / min_score=None（向后兼容）
        await unified.search("q")
        assert rag.captured["rerank"] is True
        assert rag.captured["min_score"] is None

    asyncio.run(scenario())


def test_rag_search_min_score_param(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(title="T", abstract="A", content="attention mechanism transformer",
                            paper_id="p1")
        base = await rag.search("attention transformer", top_k=10)
        assert base["results"]
        # min_score 高于归一化 top(=1.0) → 全部截断
        strict = await rag.search("attention transformer", top_k=10, min_score=1.5)
        assert strict["results"] == []
        # min_score=0.0 → 不截断
        loose = await rag.search("attention transformer", top_k=10, min_score=0.0)
        assert loose["results"]

    asyncio.run(scenario())


def test_rag_compare_parse_paper_ids():
    from silver_research_bot.agent.tools.rag_tools import RagCompareTool

    assert RagCompareTool._parse_paper_ids("a,b,c") == ["a", "b", "c"]
    assert RagCompareTool._parse_paper_ids("a; b，c；d") == ["a", "b", "c", "d"]
    assert RagCompareTool._parse_paper_ids("a\nb  c") == ["a", "b", "c"]
    assert RagCompareTool._parse_paper_ids("a,a,b") == ["a", "b"]
    assert RagCompareTool._parse_paper_ids("") is None
    assert RagCompareTool._parse_paper_ids("   ") is None


# ── Group C-3.3 suggest_research modality 透传 ─────────────────

def test_rag_suggest_research_modality(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        await rag.add_paper(title="Lit", abstract="", content="attention mechanism",
                            authors=["A"], year=2020, paper_id="p1")
        await rag.ingest_analysis("p1", "Lit", {"system_model": "# 模型\n\nTransformer 编码器。"})

        # modality="analysis" → 只返回分析产物（context 块标记 [analysis]，无 [text]）
        sug = await rag.suggest_research("Transformer", top_k=10, modality="analysis")
        assert sug["key_papers"]
        assert "[analysis]" in sug["context"]
        assert "[text]" not in sug["context"]

        # year 过滤：analysis 产物继承 paper year=2020，2021 命中为空
        sug2 = await rag.suggest_research("Transformer", top_k=10, modality="analysis", year=2021)
        assert not sug2["key_papers"]

    asyncio.run(scenario())


# ── Group 1.3 chunk 重叠 ──────────────────────────────────────

def test_rag_split_chunks_overlap_normal_path(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        rag._chunk_overlap = 3  # 3 token 重叠（配置值即 token 数）
        body = "token one two three\n\ntoken four five six"
        chunks = rag._split_by_tokens(body, size_tokens=7)
        assert len(chunks) == 2
        # 第二块以第一块末尾 3 个 token 开头（重叠尾）
        assert chunks[1].startswith("one two three")
        assert "four five six" in chunks[1]

    asyncio.run(scenario())


def test_rag_split_chunks_no_overlap_when_zero(tmp_path):
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        rag._chunk_overlap = 0
        body = "token one two three\n\ntoken four five six"
        chunks = rag._split_by_tokens(body, size_tokens=7)
        assert len(chunks) == 2
        # 无重叠：第二块以其自身首个 token 开头
        assert chunks[1].startswith("token four five six")

    asyncio.run(scenario())


def test_rag_hard_split_honours_token_budget(tmp_path):
    """无句末标点的超长单句落到 _hard_split 兜底，按 token 预算换算字符窗口切分。"""
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        rag._chunk_overlap = 5
        body = "word " * 400  # 无 。！？；.!?; → 句切分切不动，必然进 _hard_split
        chunks = rag._split_by_tokens(body, size_tokens=50)
        assert len(chunks) > 1
        # 每块受 token 预算约束（重叠尾会让非首块略微超出，留 2x 余量）
        for c in chunks:
            assert rag._num_tokens(c) <= 100
        # 原文未丢字：去掉空白后拼接应覆盖原文全部字符
        assert "".join(chunks).count("word") >= 400

    asyncio.run(scenario())


def test_rag_hard_split_clamps_overlap_to_avoid_chunk_explosion(tmp_path):
    """overlap 远大于窗口时重叠截到窗口一半，step 不退化为 1（否则逐字符产出）。"""
    async def scenario():
        rag = ResearchRAG(root=tmp_path / "rag")
        rag._chunk_overlap = 2000  # 远大于 size_tokens=10 换算出的窗口
        text = "x" * 2000
        chunks = rag._hard_split(text, size_tokens=10)
        # 未截断时 step=1 会产出 2000 块；截断后 step ≥ 窗口一半
        assert len(chunks) < 200
        assert all(c for c in chunks)

    asyncio.run(scenario())
