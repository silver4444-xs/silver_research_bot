"""ResearchRAG.search(debug=) 链路可观测性测试 — 全离线（provider=None）。

对应 docs/superpowers/specs/2026-08-25-rag-lab-page-design.md 的 §7.2。

核心约束：
- A10 回归保护：debug=False 的响应体必须与改造前逐字段一致
- debug=True 才暴露中间态，且各字段自洽（守恒关系、rank 一致性）
- D1：score 被归一化（top-1 恒为 1.0），rrf_raw 保留归一化前的可比量
- D2：重排跳过必须可辨（status + reason），不能与"重排了但没效果"混淆
"""

from __future__ import annotations

import asyncio

from silver_research_bot.rag import ResearchRAG
from silver_research_bot.rag import research_rag as rr


# 改造前 search() 的响应契约，A10 回归的基准
_BASE_TOP_KEYS = {"query", "top_k", "tag", "modality", "results"}
_BASE_RESULT_KEYS = {
    "score", "chunk_id", "paper_id", "title", "section", "text",
    "chunk_type", "metadata",
}


async def _seed(root, count: int = 6):
    """建一个多篇小库，保证 fused 里有足够候选可被过滤/截断。"""
    rag = ResearchRAG(root=root)
    for i in range(count):
        await rag.add_paper(
            title=f"Paper {i} on retrieval",
            abstract="retrieval augmented generation",
            content=f"Document {i} about retrieval augmented generation and ranking.",
            authors=[f"Author{i}"], tags=["rag"], year=2020 + i, venue="NeurIPS",
        )
    return rag


# ── A10 回归保护 ──────────────────────────────────────────────

def test_search_debug_false_response_shape_unchanged(tmp_path):
    """debug=False（默认）时响应体逐字段与改造前一致，不夹带任何新 key。"""
    async def scenario():
        rag = await _seed(tmp_path / "rag")

        # 默认调用（不传 debug）
        sr = await rag.search("retrieval", top_k=3)
        assert set(sr.keys()) == _BASE_TOP_KEYS
        assert sr["results"]
        for r in sr["results"]:
            assert set(r.keys()) == _BASE_RESULT_KEYS

        # 显式 debug=False 与默认等价
        sr2 = await rag.search("retrieval", top_k=3, debug=False)
        assert set(sr2.keys()) == _BASE_TOP_KEYS

    asyncio.run(scenario())


# ── debug=True 暴露中间态 ────────────────────────────────────

def test_search_debug_true_adds_observability_fields(tmp_path):
    async def scenario():
        rag = await _seed(tmp_path / "rag")
        sr = await rag.search("retrieval", top_k=3, debug=True)

        for key in (
            "run_id", "params_effective", "expanded_queries", "expansion_source",
            "stages", "timings_ms", "dropped", "dropped_truncated",
        ):
            assert key in sr, f"debug=True 缺少字段 {key}"

        # 原有字段一个不少
        assert _BASE_TOP_KEYS.issubset(set(sr.keys()))
        for r in sr["results"]:
            assert _BASE_RESULT_KEYS.issubset(set(r.keys()))
            for key in ("rank", "rank_before_rerank", "rerank_score", "rrf_raw", "channels"):
                assert key in r, f"debug=True 结果项缺少字段 {key}"

        # 六个阶段齐备且顺序固定
        assert [s["name"] for s in sr["stages"]] == [
            "expand", "bm25", "vector", "fuse", "filter", "rerank",
        ]
        for s in sr["stages"]:
            assert s["ms"] >= 0
            assert s["status"] in ("ok", "skipped", "failed")
            if s["status"] != "ok":
                assert s["reason"], f"阶段 {s['name']} 非 ok 必须给出 reason"

    asyncio.run(scenario())


def test_search_debug_filter_conservation(tmp_path):
    """守恒：进入 filter 的候选 = 留下的 + 被丢弃的。"""
    async def scenario():
        rag = await _seed(tmp_path / "rag")
        sr = await rag.search("retrieval", top_k=2, debug=True)

        stages = {s["name"]: s for s in sr["stages"]}
        assert sr["dropped_truncated"] is False
        assert stages["filter"]["in"] - stages["filter"]["out"] == len(sr["dropped"])

        # results 是 filter 输出再按 final_k 截断，不能超过 filter 的 out
        assert len(sr["results"]) <= stages["filter"]["out"]
        assert len(sr["results"]) <= sr["params_effective"]["top_k"]

    asyncio.run(scenario())


def test_search_debug_dropped_records_min_score_reason(tmp_path):
    """min_score 截断掉的候选必须留痕，并标明 reason。"""
    async def scenario():
        rag = await _seed(tmp_path / "rag")
        # 阈值拉到接近 1.0：归一化后只有 top-1 能活下来（D1 的直接体现）
        sr = await rag.search("retrieval", top_k=10, min_score=0.99, debug=True)

        assert sr["dropped"], "高阈值下应有候选被截断并记录"
        assert any(d["reason"] == "min_score" for d in sr["dropped"])
        for d in sr["dropped"]:
            assert {"chunk_id", "reason", "stage"}.issubset(set(d.keys()))

    asyncio.run(scenario())


def test_search_debug_dropped_records_metadata_filter_reason(tmp_path):
    """被元数据过滤掉的候选，reason 要指出是哪一维过滤的。"""
    async def scenario():
        rag = await _seed(tmp_path / "rag")
        sr = await rag.search("retrieval", top_k=10, year=1999, debug=True)

        assert sr["results"] == []
        assert sr["dropped"], "全部被 year 过滤掉时应有 dropped 记录"
        assert any(d["reason"] == "filter:year" for d in sr["dropped"])

    asyncio.run(scenario())


def test_search_debug_rrf_raw_survives_normalization(tmp_path):
    """D1：score 被归一化使 top-1 恒为 1.0；rrf_raw 保留归一化前的可比量。"""
    async def scenario():
        rag = await _seed(tmp_path / "rag")
        sr = await rag.search("retrieval", top_k=3, debug=True)

        assert sr["results"]
        top = sr["results"][0]
        assert top["score"] == 1.0, "归一化后 top-1 必然是 1.0（这正是 score 不可跨 query 比较的原因）"
        assert 0.0 < top["rrf_raw"] < 1.0, "rrf_raw 是归一化前的 RRF 累加和，不应等于 1.0"
        # 归一化是等比缩放，两者排序必须一致
        assert [r["score"] for r in sr["results"]] == sorted(
            [r["score"] for r in sr["results"]], reverse=True
        )
        assert [r["rrf_raw"] for r in sr["results"]] == sorted(
            [r["rrf_raw"] for r in sr["results"]], reverse=True
        )

    asyncio.run(scenario())


def test_search_debug_channels_expose_raw_recall_scores(tmp_path):
    """BM25 与向量各自的原始 rank+score 必须可见（改造前被 `_` 丢弃）。"""
    async def scenario():
        rag = await _seed(tmp_path / "rag")
        sr = await rag.search("retrieval", top_k=3, debug=True)

        assert sr["results"]
        traced = False
        for r in sr["results"]:
            ch = r["channels"]
            assert set(ch.keys()) == {"bm25", "vector"}
            for route in ("bm25", "vector"):
                for hit in ch[route]:
                    assert {"q_idx", "rank", "score"}.issubset(set(hit.keys()))
                    assert hit["rank"] >= 0
                    traced = True
            assert ch["bm25"] or ch["vector"], "进入结果集的 chunk 至少被一路召回"
        assert traced

    asyncio.run(scenario())


def test_search_debug_rerank_skip_is_distinguishable(tmp_path):
    """D2：无 provider 时重排不可用，必须显式 skipped + reason，且名次不失真。"""
    async def scenario():
        rag = await _seed(tmp_path / "rag")
        sr = await rag.search("retrieval", top_k=3, debug=True)

        rerank_stage = {s["name"]: s for s in sr["stages"]}["rerank"]
        assert rerank_stage["status"] == "skipped"
        assert rerank_stage["reason"], "跳过必须给出原因，否则与'重排了但没效果'无法区分"

        for r in sr["results"]:
            assert r["rank_before_rerank"] == r["rank"], "未重排时前后名次应一致"
            assert r["rerank_score"] is None
        assert [r["rank"] for r in sr["results"]] == list(range(1, len(sr["results"]) + 1))

    asyncio.run(scenario())


class _TailReranker:
    """把融合序整个倒过来，保证融合 top-K 里必有条目被挤出最终 top-K。

    重排把候选挤出 top-K 是 rerank_gain 唯一有意义的场景——若重排只在
    top-K 内部换位，前后两个 P@K 算的是同一个集合，增益恒为 0。
    """

    def __init__(self):
        self.seen = None

    async def rerank(self, query, candidates, top_k):
        self.seen = [cid for cid, _ in candidates]
        picked = list(reversed(candidates))[:top_k]
        return [(cid, float(10 - i)) for i, (cid, _) in enumerate(picked)]


def test_search_debug_exposes_pre_rerank_topk(tmp_path):
    """重排把候选挤出 top-K 后，被挤出者仍须可见。

    否则 rerank_gain 无从算起：results 只剩重排后的 top-K，拿它按
    rank_before_rerank 重排序，集合不变、P@K 不变、增益恒为 0——
    一个永远输出 0 的指标比没有更糟，它看起来像"重排没用"。
    """
    async def scenario():
        rag = await _seed(tmp_path / "rag", count=8)
        rag._reranker = _TailReranker()   # _seed 已触发 _ensure_init，不会被覆盖
        sr = await rag.search("retrieval", top_k=3, rerank=True, debug=True)

        assert {s["name"]: s for s in sr["stages"]}["rerank"]["status"] == "ok"
        assert "rerank_before" in sr, "debug=True 必须给出重排前的 top-K 快照"

        before = sr["rerank_before"]
        assert len(before) == len(sr["results"])
        assert [e["rank"] for e in before] == list(range(1, len(before) + 1))
        for e in before:
            assert {"rank", "chunk_id", "title", "section", "chunk_type",
                    "score", "rrf_raw", "text"}.issubset(set(e.keys()))

        after_ids = {r["chunk_id"] for r in sr["results"]}
        before_ids = {e["chunk_id"] for e in before}
        assert before_ids - after_ids, "本场景重排必然挤出条目，快照里应能看到它们"

        # 快照就是融合序的前 K 个：与 results 里各项的 rank_before_rerank 对得上
        pos = {e["chunk_id"]: e["rank"] for e in before}
        for r in sr["results"]:
            if r["chunk_id"] in pos:
                assert pos[r["chunk_id"]] == r["rank_before_rerank"]

    asyncio.run(scenario())


def test_search_debug_false_hides_pre_rerank_snapshot(tmp_path):
    """A10：日常路径不得因这个新字段而变形。"""
    async def scenario():
        rag = await _seed(tmp_path / "rag", count=8)
        rag._reranker = _TailReranker()
        sr = await rag.search("retrieval", top_k=3, rerank=True)
        assert set(sr.keys()) == _BASE_TOP_KEYS

    asyncio.run(scenario())


def test_search_debug_pre_rerank_snapshot_present_when_skipped(tmp_path):
    """重排跳过时快照仍在，且等于最终序——形状稳定，前端无需分支。"""
    async def scenario():
        rag = await _seed(tmp_path / "rag")
        sr = await rag.search("retrieval", top_k=3, debug=True)

        assert {s["name"]: s for s in sr["stages"]}["rerank"]["status"] == "skipped"
        assert [e["chunk_id"] for e in sr["rerank_before"]] == [
            r["chunk_id"] for r in sr["results"]
        ]

    asyncio.run(scenario())


def test_search_debug_params_effective_reports_fallback_not_input(tmp_path):
    """非法 top_k 会被后端静默回落，params_effective 必须回显真实生效值。"""
    async def scenario():
        rag = await _seed(tmp_path / "rag")
        sr = await rag.search("retrieval", top_k=0, debug=True)

        pe = sr["params_effective"]
        assert pe["top_k"] == sr["top_k"] != 0, "回显的是生效值，不是用户输入的 0"
        for key in (
            "coarse_top_k", "min_score", "rerank", "tag", "modality",
            "author", "year", "venue", "rrf_k",
            "query_expansion_enabled", "query_expansion_count",
            "embedding_model", "rerank_model",
        ):
            assert key in pe, f"params_effective 缺少 {key}"
        assert pe["rrf_k"] == 60

    asyncio.run(scenario())


def test_search_debug_expanded_queries_keep_original_first(tmp_path):
    async def scenario():
        rag = await _seed(tmp_path / "rag")
        sr = await rag.search("retrieval augmented", top_k=3, debug=True)

        assert sr["expanded_queries"][0] == "retrieval augmented"
        # 无 provider → 离线同义词表扩展
        assert sr["expansion_source"] in ("offline", "cached", "disabled")

        expand_stage = {s["name"]: s for s in sr["stages"]}["expand"]
        assert expand_stage["in"] == 1
        assert expand_stage["out"] == len(sr["expanded_queries"])

    asyncio.run(scenario())


def test_search_debug_dropped_is_capped(tmp_path, monkeypatch):
    """dropped 必须有上限，且截断时如实置位，不能假装收全了。"""
    async def scenario():
        rag = await _seed(tmp_path / "rag", count=8)
        monkeypatch.setattr(rr, "_DEBUG_DROPPED_LIMIT", 2)
        # 全部被 year 过滤掉，制造远多于上限的 dropped
        sr = await rag.search("retrieval", top_k=10, year=1999, debug=True)

        assert len(sr["dropped"]) <= 2
        assert sr["dropped_truncated"] is True

    asyncio.run(scenario())
