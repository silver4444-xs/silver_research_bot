"""RAG REST 层测试 — 参数透传、debug 直通、配置只读端点。

对应 docs/superpowers/specs/2026-08-25-rag-lab-page-design.md §7.2 / §7.3。

核心约束：
- 默认响应体不变（A10）
- min_score / coarse_top_k / debug / bypass_expansion_cache 必须真的到达后端，
  而不是被 Pydantic 静默吞掉
- GET /api/rag/config 回显的是**当前进程真正生效的值**，不是重新 load_config()
  的结果——配置文件改了但没重启时，这两者会不一致，而验收页要看的是前者
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from silver_research_bot import research_app as app_mod
from silver_research_bot.config.schema import RAGConfig
from silver_research_bot.rag import ResearchRAG

# 改造前 search() 的响应契约，A10 回归基准
_BASE_TOP_KEYS = {"query", "top_k", "tag", "modality", "results"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """把 app 的 RAG 单例换成临时库，避开真实 workspace 与 provider。

    不用 `with TestClient(...)`：那会触发 @app.on_event("startup")，拉起
    channels/cron 等与本测试无关的子系统。
    """
    # 必须先备好当前事件循环再构造：research_rag.py:157 的 asyncio.Lock() 在
    # Py3.9 上会于构造期调 get_event_loop() 绑定循环，而前序测试模块跑完
    # asyncio.run() 会把当前循环置空，届时构造直接抛 RuntimeError。
    # （Py3.10+ Lock 不再在构造期绑定循环，本项目声明的 3.11 上无此问题）
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    rag = ResearchRAG(root=tmp_path / "rag")

    async def _seed():
        for i in range(6):
            await rag.add_paper(
                title=f"Paper {i} on retrieval",
                abstract="retrieval augmented generation",
                content=f"Document {i} about retrieval augmented generation and ranking.",
                authors=[f"Author{i}"], tags=["rag"], year=2020 + i, venue="NeurIPS",
            )

    # 用 run_until_complete 而非 asyncio.run：后者收尾会把当前循环置空，
    # 而 TestClient 的 anyio portal 之后还要 get_event_loop()。
    loop.run_until_complete(_seed())

    monkeypatch.setattr(app_mod, "rag", rag, raising=False)
    monkeypatch.setattr(app_mod, "_rag_initialized", True, raising=False)
    return TestClient(app_mod.app)


# ── A10 回归保护 ──────────────────────────────────────────────

def test_rag_search_default_response_shape_unchanged(client):
    r = client.post("/api/rag/search", json={"query": "retrieval", "top_k": 3})
    assert r.status_code == 200
    assert set(r.json().keys()) == _BASE_TOP_KEYS


# ── 新参数确实到达后端 ────────────────────────────────────────

def test_rag_search_debug_flag_reaches_backend(client):
    r = client.post("/api/rag/search", json={"query": "retrieval", "top_k": 3, "debug": True})
    assert r.status_code == 200
    body = r.json()
    for key in ("run_id", "params_effective", "stages", "dropped", "expanded_queries"):
        assert key in body, f"debug=true 未透传到后端，缺少 {key}"


def test_rag_search_min_score_is_wired_through(client):
    """min_score 此前完全没暴露到 REST 层，只存在于 Python 签名与 Agent 工具里。"""
    loose = client.post("/api/rag/search", json={"query": "retrieval", "top_k": 10}).json()
    strict = client.post(
        "/api/rag/search", json={"query": "retrieval", "top_k": 10, "min_score": 0.99}
    ).json()

    assert len(strict["results"]) < len(loose["results"]), "min_score 没有生效"
    # D1：阈值作用在归一化之后，top-1 恒为 1.0，因此永远过不掉
    assert len(strict["results"]) >= 1


def test_rag_search_coarse_top_k_is_wired_through(client):
    body = client.post("/api/rag/search", json={
        "query": "retrieval", "top_k": 3, "coarse_top_k": 7, "debug": True,
    }).json()
    assert body["params_effective"]["coarse_top_k"] == 7


def test_rag_search_bypass_expansion_cache(client):
    """同一 query 二次检索会命中扩展缓存——这是 A/B 对比时的混淆变量，必须可绕过。"""
    payload = {"query": "retrieval augmented", "top_k": 3, "debug": True}
    first = client.post("/api/rag/search", json=payload).json()
    second = client.post("/api/rag/search", json=payload).json()
    assert second["expansion_source"] == "cached", "第二次应命中缓存，否则这个开关没有存在意义"

    third = client.post("/api/rag/search", json={**payload, "bypass_expansion_cache": True}).json()
    assert third["expansion_source"] != "cached"
    # 离线扩展是确定性的：绕过缓存拿到的结果应与首次一致
    assert third["expanded_queries"] == first["expanded_queries"]


# ── GET /api/rag/config ──────────────────────────────────────

def test_rag_config_covers_every_rag_config_field(client):
    """字段集从 RAGConfig.model_fields 推导，schema 增删时测试自动跟上。"""
    r = client.get("/api/rag/config")
    assert r.status_code == 200
    body = r.json()

    expected = set(RAGConfig.model_fields.keys())
    assert set(body["config"].keys()) == expected
    assert set(body["mutability"].keys()) == expected


def test_rag_config_marks_reindex_and_request_scopes(client):
    body = client.get("/api/rag/config").json()
    m = body["mutability"]

    for field in ("embedding_model", "embedding_dimensions", "chunk_size", "chunk_overlap"):
        assert m[field]["scope"] == "reindex", f"{field} 改动会变更 chunk_id/向量，必须标 reindex"
    for field in ("coarse_top_k", "final_top_k", "min_score"):
        assert m[field]["scope"] == "request", f"{field} 可按请求覆盖"
    assert m["query_expansion_cache_ttl_seconds"]["scope"] == "restart"
    assert all(v["scope"] in ("request", "restart", "reindex") for v in m.values())


def test_rag_config_flags_rerank_enable_asymmetry(client):
    """rerank 请求参数只能单向关闭：rerank_enabled=false 时请求传 true 也开不起来。"""
    m = client.get("/api/rag/config").json()["mutability"]
    assert m["rerank_enabled"]["scope"] == "restart"
    assert m["rerank_enabled"]["note"], "非对称行为必须写明，否则会被误判成'参数没生效'"


def test_rag_config_reflects_live_instance_not_config_file(client, monkeypatch):
    """回显当前进程真正生效的值——配置改了但没重启时，文件与内存并不一致。"""
    monkeypatch.setattr(app_mod.rag, "_coarse_top_k", 33, raising=False)
    body = client.get("/api/rag/config").json()
    assert body["config"]["coarse_top_k"] == 33


def test_rag_config_lists_request_overridable_params(client):
    body = client.get("/api/rag/config").json()
    assert set(body["request_overridable"]) >= {
        "top_k", "coarse_top_k", "min_score", "rerank", "bypass_expansion_cache",
    }


# ── POST /api/rag/ask ────────────────────────────────────────

class _AnswerProvider:
    """返回固定答案的假 provider，沿用 tests/test_rag.py 的 FakeProvider 惯例。"""

    def __init__(self, answer: str):
        self.answer = answer
        self.calls = 0
        self.prompts: list[str] = []

    async def chat_with_retry(self, **kw):
        self.calls += 1
        self.prompts.append(kw.get("messages", [{}])[0].get("content", ""))

        class Resp:
            content = self.answer

        return Resp()


def test_rag_ask_without_provider_is_reported_not_disguised_as_generation(client):
    """无 provider 时返回的是检索原文，绝不能让人误以为 LLM 生成过。"""
    body = client.post("/api/rag/ask", json={"question": "retrieval augmented generation", "top_k": 3}).json()

    assert body["generation_status"] == "no_provider"
    assert body["generation_reason"]
    assert body["citation_stats"]["markers_total"] == 0
    # 没有标记时不能假装引用 100% 准确
    assert body["citation_stats"]["citation_precision"] is None


def test_rag_ask_markers_map_to_the_exact_chunks_the_llm_saw(client):
    """[N] 必须指向 LLM 实际看到的那一条。

    这是本端点存在的理由：既有 UnifiedRAG.ask 做了两次检索——一次产出
    sources、一次产出给 LLM 的编号 context——两者无任何对齐保证。
    """
    app_mod.rag._provider = _AnswerProvider("结论一[1]。结论二[2]。")

    body = client.post("/api/rag/ask", json={"question": "retrieval augmented generation", "top_k": 3}).json()

    assert body["generation_status"] == "ok"
    srcs = body["sources"]
    cites = {c["index"]: c for c in body["citations"]}
    assert cites[1]["chunk_id"] == srcs[0]["chunk_id"]
    assert cites[2]["chunk_id"] == srcs[1]["chunk_id"]
    # 同一批检索结果贯穿 context / sources / retrieval，位置即编号
    assert srcs[0]["chunk_id"] == body["retrieval"]["results"][0]["chunk_id"]
    assert [s["idx"] for s in srcs] == list(range(1, len(srcs) + 1))


def test_rag_ask_flags_dangling_marker(client):
    app_mod.rag._provider = _AnswerProvider("凭空断言[99]。")

    body = client.post("/api/rag/ask", json={"question": "retrieval augmented generation", "top_k": 3}).json()

    assert body["citation_stats"]["markers_dangling"] == 1
    assert body["citation_stats"]["markers_valid"] == 0
    assert body["citations"][0]["valid"] is False


def test_rag_ask_reports_uncited_sentences(client):
    app_mod.rag._provider = _AnswerProvider("有据的结论[1]。这一句没有任何出处。")

    body = client.post("/api/rag/ask", json={"question": "retrieval augmented generation", "top_k": 3}).json()

    assert body["citation_stats"]["sentences_uncited"] == 1
    assert len(body["uncited_sentences"]) == 1


def test_rag_ask_carries_full_retrieval_debug_block(client):
    app_mod.rag._provider = _AnswerProvider("答案[1]。")

    body = client.post("/api/rag/ask", json={"question": "retrieval augmented generation", "top_k": 3}).json()

    retrieval = body["retrieval"]
    for key in ("stages", "params_effective", "dropped", "expanded_queries"):
        assert key in retrieval
    assert body["run_id"] == retrieval["run_id"]


def test_rag_ask_llm_failure_is_reported_not_swallowed(client):
    class BoomProvider:
        async def chat_with_retry(self, **kw):
            raise RuntimeError("boom")

    app_mod.rag._provider = BoomProvider()

    body = client.post("/api/rag/ask", json={"question": "retrieval augmented generation", "top_k": 3}).json()

    assert body["generation_status"] == "llm_failed"
    assert body["generation_reason"]


def test_rag_ask_empty_retrieval_does_not_call_llm(client):
    provider = _AnswerProvider("不该被调用[1]。")
    app_mod.rag._provider = provider

    body = client.post("/api/rag/ask", json={
        "question": "retrieval augmented generation", "top_k": 3, "year": 1999,
    }).json()

    assert body["generation_status"] == "no_results"
    assert body["sources"] == []
    # 不能断言 provider.calls == 0：查询扩展本身就会正当地调用 provider。
    # 要断言的是"没有发出生成请求"，所以看 prompt 而不是调用次数。
    assert not any("学术研究助手" in p for p in provider.prompts), \
        "检索为空时不该浪费一次生成调用"
