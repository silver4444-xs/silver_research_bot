"""RAG 验收台的 run 留存、标注与指标 —— 全离线。

对应 docs/superpowers/specs/2026-08-25-rag-lab-page-design.md 的 §5.3 / §5.4。

核心约束：
- 写入必须原子且带锁：并发写不能丢 run、不能丢标注（§5.3 明确点名不要照抄
  reading_history 的非原子 write_text）
- 指标分母为 0 时返回 None 而非满分（§7.3 实施期修正 4）
- rerank_gain 必须建立在**重排前 top-K 快照**上，且重排没跑时为 None（D2）
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from silver_research_bot import research_app as app_mod
from silver_research_bot.rag.research_runs import RunStore, compute_metrics


def _res(cid, rank, rank_before, ch_bm25=True, ch_vec=False):
    return {
        "score": 1.0 / rank, "chunk_id": cid, "paper_id": "p_" + cid,
        "title": "Doc " + cid.upper(), "section": "intro",
        "text": "body of " + cid, "chunk_type": "text", "metadata": {},
        "rank": rank, "rank_before_rerank": rank_before, "rerank_score": 8,
        "rrf_raw": 0.03 / rank,
        "channels": {
            "bm25": [{"q_idx": 0, "rank": rank - 1, "score": 5.0}] if ch_bm25 else [],
            "vector": [{"q_idx": 0, "rank": rank - 1, "score": 0.8}] if ch_vec else [],
        },
    }


def _before(cid, rank):
    return {"rank": rank, "chunk_id": cid, "title": "Doc " + cid.upper(),
            "section": "intro", "chunk_type": "text", "score": 1.0 / rank,
            "rrf_raw": 0.03 / rank, "text": "body of " + cid}


def _retrieval(results=None, rerank_before=None, dropped=None,
               rerank_status="ok", run_id="r_test01"):
    """造一份 search(debug=True) 形状的检索载荷。"""
    return {
        "run_id": run_id,
        "query": "retrieval augmented",
        "top_k": 3,
        "params_effective": {"top_k": 3, "coarse_top_k": 20, "min_score": 0.0,
                             "rerank": True, "rrf_k": 60},
        "stages": [
            {"name": "expand", "in": 1, "out": 3, "ms": 1.0, "status": "ok"},
            {"name": "bm25", "in": 3, "out": 9, "ms": 1.0, "status": "ok"},
            {"name": "vector", "in": 3, "out": 9, "ms": 1.0, "status": "ok"},
            {"name": "fuse", "in": 18, "out": 12, "ms": 1.0, "status": "ok"},
            {"name": "filter", "in": 12, "out": 8, "ms": 1.0, "status": "ok"},
            {"name": "rerank", "in": 8, "out": 3, "ms": 1.0, "status": rerank_status,
             "reason": None if rerank_status == "ok" else "候选数未超过 top_k"},
        ],
        "results": results if results is not None else [
            _res("a1", 1, 1, ch_bm25=True, ch_vec=True),
            _res("b1", 2, 2, ch_bm25=True, ch_vec=False),
            _res("c1", 3, 3, ch_bm25=False, ch_vec=True),
        ],
        "dropped": dropped or [],
        "rerank_before": rerank_before if rerank_before is not None else [
            _before("a1", 1), _before("b1", 2), _before("c1", 3),
        ],
    }


# ── CRUD ──────────────────────────────────────────────────────

def test_create_and_get_roundtrip(tmp_path):
    store = RunStore(root=tmp_path / "runs")
    run = store.create(retrieval=_retrieval(), label="基线", tags=["回归"], labeler="silver")

    assert run["run_id"] == "r_test01", "search 给的 run_id 是关联键，必须沿用而不是另生成"
    assert run["query"] == "retrieval augmented"
    assert run["label"] == "基线" and run["tags"] == ["回归"] and run["labeler"] == "silver"
    assert run["annotations"] == {}
    assert run["created_at"].endswith("+00:00"), "created_at 必须是 ISO 8601 UTC"

    again = store.get("r_test01")
    assert again["run_id"] == run["run_id"] and again["query"] == run["query"]
    assert (tmp_path / "runs" / "r_test01.json").exists()


def test_create_generates_run_id_when_absent(tmp_path):
    store = RunStore(root=tmp_path / "runs")
    payload = _retrieval()
    payload.pop("run_id")
    run = store.create(retrieval=payload)
    assert run["run_id"].startswith("r_")
    assert store.get(run["run_id"]) is not None


def test_get_missing_returns_none(tmp_path):
    assert RunStore(root=tmp_path / "runs").get("r_nope") is None


def test_list_is_newest_first_and_paginates(tmp_path):
    store = RunStore(root=tmp_path / "runs")
    for i in range(5):
        store.create(retrieval=_retrieval(run_id=f"r_{i}"), label=f"L{i}")

    page = store.list_runs()
    assert page["total"] == 5
    assert [r["run_id"] for r in page["runs"]] == ["r_4", "r_3", "r_2", "r_1", "r_0"]
    # 索引条目是轻量摘要，不该把整个 results 塞进去
    assert "results" not in page["runs"][0]
    assert page["runs"][0]["result_count"] == 3

    assert [r["run_id"] for r in store.list_runs(limit=2)["runs"]] == ["r_4", "r_3"]
    assert [r["run_id"] for r in store.list_runs(limit=2, offset=2)["runs"]] == ["r_2", "r_1"]
    assert store.list_runs(limit=2, offset=2)["total"] == 5, "total 是全量数，不随分页变"


def test_update_label_and_tags(tmp_path):
    store = RunStore(root=tmp_path / "runs")
    store.create(retrieval=_retrieval())
    updated = store.update("r_test01", label="重排关闭对照组", tags=["回归", "v0.7.0"])

    assert updated["label"] == "重排关闭对照组"
    assert updated["tags"] == ["回归", "v0.7.0"]
    assert store.get("r_test01")["label"] == "重排关闭对照组"
    # 索引摘要要跟着变，否则列表页显示的还是旧名字
    assert store.list_runs()["runs"][0]["label"] == "重排关闭对照组"


def test_update_annotations_merges_by_default(tmp_path):
    """标注是一条条打的，第二次提交不能把第一次的抹掉。"""
    store = RunStore(root=tmp_path / "runs")
    store.create(retrieval=_retrieval())

    store.update("r_test01", annotations={"a1": {"relevance": 2, "note": "正是要找的"}})
    run = store.update("r_test01", annotations={"b1": {"relevance": 0}})

    assert set(run["annotations"]) == {"a1", "b1"}
    assert run["annotations"]["a1"]["note"] == "正是要找的"


def test_update_annotations_can_replace_wholesale(tmp_path):
    store = RunStore(root=tmp_path / "runs")
    store.create(retrieval=_retrieval())
    store.update("r_test01", annotations={"a1": {"relevance": 2}})
    run = store.update("r_test01", annotations={"b1": {"relevance": 1}},
                       replace_annotations=True)
    assert set(run["annotations"]) == {"b1"}


def test_update_rejects_invalid_relevance(tmp_path):
    """relevance 只有 0/1/2 三档。脏值进了存储，指标就会算出鬼数字。"""
    store = RunStore(root=tmp_path / "runs")
    store.create(retrieval=_retrieval())
    for bad in (3, -1, "yes", None):
        try:
            store.update("r_test01", annotations={"a1": {"relevance": bad}})
        except ValueError:
            continue
        raise AssertionError(f"relevance={bad!r} 应被拒绝")


def test_update_missing_returns_none(tmp_path):
    assert RunStore(root=tmp_path / "runs").update("r_nope", label="x") is None


def test_delete_removes_file_and_index_entry(tmp_path):
    store = RunStore(root=tmp_path / "runs")
    store.create(retrieval=_retrieval())
    assert store.delete("r_test01") is True
    assert store.get("r_test01") is None
    assert not (tmp_path / "runs" / "r_test01.json").exists()
    assert store.list_runs()["total"] == 0
    assert store.delete("r_test01") is False


def test_corrupt_index_recovers_without_crashing(tmp_path):
    root = tmp_path / "runs"
    root.mkdir(parents=True)
    (root / "index.json").write_text("{not json", encoding="utf-8")
    store = RunStore(root=root)
    assert store.list_runs()["total"] == 0
    store.create(retrieval=_retrieval())          # 仍可继续写
    assert store.list_runs()["total"] == 1


# ── 指标 §5.4 ─────────────────────────────────────────────────

def test_p_at_k_counts_only_annotated(tmp_path):
    """相关=1、部分=0.5、不相关=0，分母是**已标注**条数。"""
    store = RunStore(root=tmp_path / "runs")
    store.create(retrieval=_retrieval())
    run = store.update("r_test01", annotations={
        "a1": {"relevance": 2}, "b1": {"relevance": 1},   # c1 未标注
    })
    m = run["metrics"]
    assert m["p_at_k"] == 0.75                     # (1 + 0.5) / 2
    assert m["p_at_k_annotated"] == 2
    assert m["k"] == 3, "k 与已标注数分开报，部分标注的事实不能被藏起来"
    assert m["hit_rate"] == 1.0


def test_metrics_are_none_when_nothing_annotated(tmp_path):
    """一条都没标 ≠ 全对。分母为 0 一律 None。"""
    store = RunStore(root=tmp_path / "runs")
    run = store.create(retrieval=_retrieval())
    m = run["metrics"]
    assert m["p_at_k"] is None
    assert m["hit_rate"] is None
    assert m["p_at_k_annotated"] == 0


def test_hit_rate_zero_when_annotated_but_none_relevant(tmp_path):
    store = RunStore(root=tmp_path / "runs")
    store.create(retrieval=_retrieval())
    run = store.update("r_test01", annotations={
        "a1": {"relevance": 1}, "b1": {"relevance": 0},
    })
    assert run["metrics"]["hit_rate"] == 0.0, "只有部分相关，没有一条 relevance==2"


def test_rerank_gain_uses_pre_rerank_snapshot(tmp_path):
    """重排把两条好的挤出去、换进三条差的 —— 增益必须为负。

    这是 rerank_gain 存在的全部理由：单次检索 + 一批标注即可量化重排价值。
    若只拿 results 按 rank_before_rerank 重排序，集合不变、增益恒为 0。
    """
    store = RunStore(root=tmp_path / "runs")
    store.create(retrieval=_retrieval(
        results=[_res("c1", 1, 3), _res("d1", 2, 4), _res("e1", 3, 5)],
        rerank_before=[_before("a1", 1), _before("b1", 2), _before("c1", 3)],
    ))
    run = store.update("r_test01", annotations={
        "a1": {"relevance": 2}, "b1": {"relevance": 2}, "c1": {"relevance": 0},
        "d1": {"relevance": 0}, "e1": {"relevance": 0},
    })
    m = run["metrics"]
    assert m["p_at_k"] == 0.0                             # 重排后 c1/d1/e1 全不相关
    assert round(m["rerank_gain_basis"]["p_at_k_before"], 4) == 0.6667   # (1+1+0)/3
    assert round(m["rerank_gain"], 4) == -0.6667
    assert m["rerank_gain_basis"]["status"] == "ok"


def test_rerank_gain_none_when_rerank_did_not_run(tmp_path):
    """D2：重排没跑，增益无从谈起 —— 是 None，不是 0。0 会被读成「重排没用」。"""
    store = RunStore(root=tmp_path / "runs")
    store.create(retrieval=_retrieval(rerank_status="skipped"))
    run = store.update("r_test01", annotations={"a1": {"relevance": 2}})
    m = run["metrics"]
    assert m["rerank_gain"] is None
    assert m["rerank_gain_basis"]["status"] == "skipped"
    assert m["rerank_gain_basis"]["reason"]


def test_channel_contribution_splits_two_routes(tmp_path):
    store = RunStore(root=tmp_path / "runs")
    run = store.create(retrieval=_retrieval())   # a1=both, b1=bm25 only, c1=vector only
    cc = run["metrics"]["channel_contribution"]
    assert cc["counts"] == {"both": 1, "bm25_only": 1, "vector_only": 1, "neither": 0}
    assert round(cc["both"], 4) == round(1 / 3, 4)


def test_filter_false_drop_counts_annotated_drops(tmp_path):
    """被阈值/过滤器丢掉、但人工判定有关的条数 —— 误伤的直接证据。"""
    store = RunStore(root=tmp_path / "runs")
    store.create(retrieval=_retrieval(dropped=[
        {"chunk_id": "x1", "title": "Dropped X", "reason": "min_score", "stage": "filter"},
        {"chunk_id": "y1", "title": "Dropped Y", "reason": "filter:year", "stage": "filter"},
        {"chunk_id": "z1", "title": "Dropped Z", "reason": "min_score", "stage": "filter"},
    ]))
    run = store.update("r_test01", annotations={
        "x1": {"relevance": 2}, "y1": {"relevance": 1}, "z1": {"relevance": 0},
    })
    assert run["metrics"]["filter_false_drop"] == 2       # x1 相关 + y1 部分相关
    assert run["metrics"]["filter_false_drop_annotated"] == 3


def test_generation_metrics_read_from_citation_stats(tmp_path):
    store = RunStore(root=tmp_path / "runs")
    run = store.create(retrieval=_retrieval(), generation={
        "generation_status": "ok",
        "citation_stats": {"markers_total": 4, "markers_valid": 3,
                           "sentences_total": 12, "sentences_uncited": 5,
                           "citation_precision": 0.75, "answer_groundedness": 0.5833},
    })
    m = run["metrics"]
    assert m["citation_precision"] == 0.75
    assert m["answer_groundedness"] == 0.5833


def test_generation_metrics_none_when_not_generated(tmp_path):
    """回退路径下 answer 是检索原文，对它归因会算出一份漂亮的假指标。"""
    store = RunStore(root=tmp_path / "runs")
    run = store.create(retrieval=_retrieval(), generation={
        "generation_status": "no_provider",
        "citation_stats": {"markers_total": 3, "markers_valid": 3,
                           "citation_precision": 1.0, "answer_groundedness": 1.0},
    })
    assert run["metrics"]["citation_precision"] is None
    assert run["metrics"]["answer_groundedness"] is None


def test_metrics_none_when_no_generation_at_all(tmp_path):
    store = RunStore(root=tmp_path / "runs")
    run = store.create(retrieval=_retrieval())
    assert run["metrics"]["citation_precision"] is None
    assert run["metrics"]["answer_groundedness"] is None


def test_compute_metrics_is_pure():
    """指标函数不依赖存储，可被前端预览路径直接调用。"""
    m = compute_metrics(
        results=[_res("a1", 1, 1)], rerank_before=[_before("a1", 1)],
        dropped=[], annotations={"a1": {"relevance": 2}},
        stages=[{"name": "rerank", "status": "ok", "reason": None}], generation=None,
    )
    assert m["p_at_k"] == 1.0 and m["hit_rate"] == 1.0


# ── 并发写 ────────────────────────────────────────────────────

def test_concurrent_creates_do_not_lose_runs(tmp_path):
    """index.json 是读-改-写，无锁并发必丢条目。

    §5.3 点名不要照抄 reading_history 的 `_save_history`（非原子、无锁）。
    这条测试就是钉死那个教训。
    """
    store = RunStore(root=tmp_path / "runs")
    n = 24

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda i: store.create(retrieval=_retrieval(run_id=f"r_c{i:03d}")),
            range(n),
        ))

    page = store.list_runs(limit=1000)
    assert page["total"] == n, f"并发写丢了 run：期望 {n}，实得 {page['total']}"
    assert len({r["run_id"] for r in page["runs"]}) == n
    for i in range(n):
        assert (tmp_path / "runs" / f"r_c{i:03d}.json").exists()

    # 索引本身必须是完好 JSON，不能是两次交错写出的半截文件
    idx = json.loads((tmp_path / "runs" / "index.json").read_text(encoding="utf-8"))
    assert len(idx) == n


def test_concurrent_annotations_on_same_run_do_not_lose_writes(tmp_path):
    """同一 run 上并发打标：读-改-写窗口里丢标注是最隐蔽的数据损失。"""
    store = RunStore(root=tmp_path / "runs")
    store.create(retrieval=_retrieval())
    n = 24

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda i: store.update(
                "r_test01", annotations={f"chunk{i:03d}": {"relevance": i % 3}},
            ),
            range(n),
        ))

    run = store.get("r_test01")
    assert len(run["annotations"]) == n, (
        f"并发标注丢了：期望 {n}，实得 {len(run['annotations'])}"
    )


def test_concurrent_create_and_delete_keep_index_consistent(tmp_path):
    """索引里不该留下指向已删文件的孤儿条目。"""
    store = RunStore(root=tmp_path / "runs")
    for i in range(12):
        store.create(retrieval=_retrieval(run_id=f"r_m{i:03d}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: store.delete(f"r_m{i:03d}"), range(0, 12, 2)))

    page = store.list_runs(limit=1000)
    assert page["total"] == 6
    for entry in page["runs"]:
        assert (tmp_path / "runs" / f"{entry['run_id']}.json").exists()


# ── REST 层 §7.3 ──────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """把 app 的 RunStore 单例换成临时目录，避开真实 workspace。

    不用 `with TestClient(...)`：那会触发 startup 事件，拉起 channels/cron 等
    与本测试无关的子系统。
    """
    monkeypatch.setattr(app_mod, "_run_store", RunStore(root=tmp_path / "runs"),
                        raising=False)
    return TestClient(app_mod.app)


def test_rest_run_crud_roundtrip(client):
    created = client.post("/api/rag/runs", json={
        "retrieval": _retrieval(), "label": "基线", "tags": ["回归"], "labeler": "silver",
    })
    assert created.status_code == 200
    assert created.json()["run_id"] == "r_test01"

    assert client.get("/api/rag/runs").json()["total"] == 1
    assert client.get("/api/rag/runs/r_test01").json()["label"] == "基线"

    updated = client.put("/api/rag/runs/r_test01", json={
        "annotations": {"a1": {"relevance": 2, "note": "命中"}},
    })
    assert updated.status_code == 200
    assert updated.json()["metrics"]["p_at_k"] == 1.0

    assert client.delete("/api/rag/runs/r_test01").status_code == 200
    assert client.get("/api/rag/runs/r_test01").status_code == 404


def test_rest_partial_update_does_not_wipe_untouched_fields(client):
    """PUT 里没提到的字段不能被当成「改成空」。"""
    client.post("/api/rag/runs", json={
        "retrieval": _retrieval(), "label": "基线", "tags": ["回归"],
    })
    body = client.put("/api/rag/runs/r_test01", json={
        "annotations": {"a1": {"relevance": 1}},
    }).json()
    assert body["label"] == "基线" and body["tags"] == ["回归"]

    # 显式传空才是清空
    assert client.put("/api/rag/runs/r_test01", json={"label": ""}).json()["label"] == ""


def test_rest_invalid_relevance_is_400_not_500(client):
    client.post("/api/rag/runs", json={"retrieval": _retrieval()})
    r = client.put("/api/rag/runs/r_test01", json={"annotations": {"a1": {"relevance": 7}}})
    assert r.status_code == 400
    assert "relevance" in r.json()["detail"]


def test_rest_rejects_path_traversal_run_id(client, tmp_path):
    """run_id 直接拼文件名，穿越绝不能落到 runs 目录之外。

    断言的是「没写出去」这个性质本身，不是某个具体状态码——不同的畸形路径
    会在路由层、校验层不同位置被挡下（404/405/400 都可能），但共同的硬要求
    是：目录之外不产生任何文件。
    """
    outside = tmp_path / "pwned.json"
    for bad in ("..", "a/../../b", "a b", "..%2F..%2Fpwned", "%2e%2e%2fpwned"):
        assert client.get(f"/api/rag/runs/{bad}").status_code >= 400
        assert client.delete(f"/api/rag/runs/{bad}").status_code >= 400
    assert not outside.exists()
    assert list((tmp_path / "runs").glob("*.json")) == []


def test_store_rejects_traversal_run_id_directly(tmp_path):
    """真正的防线在 store 层，不依赖 HTTP 路由恰好把畸形路径挡掉。"""
    store = RunStore(root=tmp_path / "runs")
    # 空串在查改删里是「非法 id」；在 create 里是「没给 id」，见下一条测试
    for bad in ("../escape", "a/b", "..", "", "x" * 65, "a\\b"):
        for name, call in (
            ("get", lambda: store.get(bad)),
            ("delete", lambda: store.delete(bad)),
            ("update", lambda: store.update(bad, label="x")),
        ):
            try:
                call()
            except ValueError:
                continue
            raise AssertionError(f"{name}(run_id={bad!r}) 应被拒绝")

    for bad in ("../escape", "a/b", "..", "x" * 65, "a\\b"):
        try:
            store.create(retrieval=_retrieval(run_id=bad))
        except ValueError:
            continue
        raise AssertionError(f"create(run_id={bad!r}) 应被拒绝")

    assert list((tmp_path / "runs").glob("*.json")) == []
    assert not (tmp_path / "escape.json").exists()


def test_create_with_blank_run_id_generates_one(tmp_path):
    """空 run_id 是「没给」不是「非法」——生成一个，而不是抛错。"""
    store = RunStore(root=tmp_path / "runs")
    run = store.create(retrieval=_retrieval(run_id=""))
    assert run["run_id"].startswith("r_") and len(run["run_id"]) > 2


def test_rest_missing_run_is_404(client):
    assert client.get("/api/rag/runs/r_nope").status_code == 404
    assert client.put("/api/rag/runs/r_nope", json={"label": "x"}).status_code == 404
    assert client.delete("/api/rag/runs/r_nope").status_code == 404
