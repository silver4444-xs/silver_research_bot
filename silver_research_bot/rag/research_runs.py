"""RAG 验收台的 run 留存、人工标注与指标计算。

存储仿 `paper_analyzer/manager.py` 的 `comparisons/` 模式——每个结果一个
`{run_id}.json` + 一个 `index.json` 摘要索引——但**写入方式不照抄**：那边是
无锁的普通 `write_text`，并发下 index.json 的读-改-写会静默丢条目。这里一律
走 `threading.RLock` + `FileLock` + 临时文件 `os.replace` 原子替换。

两把锁各司其职，缺一不可：
- `FileLock` 管跨进程（REST 与 Agent 是两个实例，共享同一目录）
- `RLock` 管进程内多线程（FastAPI 的同步 handler 跑在线程池里），
  同时保证任一时刻只有一个线程去碰文件锁，绕开 filelock 在各平台上
  同进程重入行为的差异

契约见 docs/superpowers/specs/2026-08-25-rag-lab-page-design.md §5.3 / §5.4。
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from filelock import FileLock
from loguru import logger

from silver_research_bot.config.paths import get_workspace_path

# 相关性三档 → 增益。未标注的条目不出现在 annotations 里，与标为 0 是两回事
RELEVANCE_GAIN = {0: 0.0, 1: 0.5, 2: 1.0}

# run_id 直接参与文件名拼接，必须挡住路径穿越
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 留存时正文的截断长度：run 文件是留档不是语料，不必存全文
_STORED_TEXT_CHARS = 600
_STORED_NOTE_CHARS = 500

_UNSET: Any = object()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID_RE.match(run_id):
        raise ValueError(f"非法 run_id：{run_id!r}（只允许字母数字下划线连字符，1-64 字符）")
    return run_id


def _is_relevance(value: Any) -> bool:
    # bool 是 int 的子类，True 会被当成 relevance=1 混进来
    return isinstance(value, int) and not isinstance(value, bool) and value in RELEVANCE_GAIN


def validate_annotations(annotations: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """校验并规整标注。脏值进了存储，指标就会算出没人能解释的数字。"""
    if not isinstance(annotations, dict):
        raise ValueError("annotations 必须是 {chunk_id: {relevance, note}} 结构")
    clean: dict[str, dict[str, Any]] = {}
    for cid, entry in annotations.items():
        if not isinstance(cid, str) or not cid:
            raise ValueError(f"非法 chunk_id：{cid!r}")
        if not isinstance(entry, dict):
            raise ValueError(f"{cid} 的标注必须是对象，实得 {type(entry).__name__}")
        rel = entry.get("relevance")
        if not _is_relevance(rel):
            raise ValueError(f"{cid} 的 relevance 必须是 0/1/2 之一，实得 {rel!r}")
        note = entry.get("note") or ""
        clean[cid] = {"relevance": rel, "note": str(note)[:_STORED_NOTE_CHARS]}
    return clean


def valid_annotations_only(annotations: dict[str, Any] | None) -> dict[str, Any]:
    """只挑出合法条目，不抛异常——供指标计算容忍历史脏数据。"""
    return {
        cid: entry for cid, entry in (annotations or {}).items()
        if isinstance(entry, dict) and _is_relevance(entry.get("relevance"))
    }


# ── 指标 §5.4 ─────────────────────────────────────────────────

def _annotated_gains(chunk_ids: Iterable[str],
                     annotations: dict[str, Any]) -> list[float]:
    """只取已标注且档位合法的条目的增益。未标注 ≠ 不相关，不能拿 0 充数。"""
    gains = []
    for cid in chunk_ids:
        entry = annotations.get(cid)
        if not isinstance(entry, dict):
            continue
        rel = entry.get("relevance")
        if _is_relevance(rel):
            gains.append(RELEVANCE_GAIN[rel])
    return gains


def _p_at_k(chunk_ids: Iterable[str],
            annotations: dict[str, Any]) -> tuple[float | None, int]:
    gains = _annotated_gains(chunk_ids, annotations)
    return ((sum(gains) / len(gains)) if gains else None), len(gains)


def _hit_rate(chunk_ids: Iterable[str], annotations: dict[str, Any]) -> float | None:
    seen = False
    for cid in chunk_ids:
        entry = annotations.get(cid)
        if not isinstance(entry, dict):
            continue
        rel = entry.get("relevance")
        if not _is_relevance(rel):
            continue
        seen = True
        if rel == 2:
            return 1.0
    return 0.0 if seen else None   # 一条都没标时是「未知」，不是「没命中」


def _channel_contribution(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"both": 0, "bm25_only": 0, "vector_only": 0, "neither": 0}
    for r in results:
        channels = r.get("channels") or {}
        bm25 = bool(channels.get("bm25"))
        vector = bool(channels.get("vector"))
        if bm25 and vector:
            counts["both"] += 1
        elif bm25:
            counts["bm25_only"] += 1
        elif vector:
            counts["vector_only"] += 1
        else:
            counts["neither"] += 1
    total = len(results)
    out: dict[str, Any] = {
        key: (value / total if total else None) for key, value in counts.items()
    }
    out["counts"] = counts
    return out


def compute_metrics(
    *,
    results: list[dict[str, Any]] | None = None,
    rerank_before: list[dict[str, Any]] | None = None,
    dropped: list[dict[str, Any]] | None = None,
    annotations: dict[str, Any] | None = None,
    stages: list[dict[str, Any]] | None = None,
    generation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """由一次检索 + 一批人工标注算出 §5.4 七项指标。

    纯函数，不碰存储——前端预览路径可以直接调，无需先落盘。

    通篇纪律：**分母为 0 一律 None，不给假满分**。「一条都没标」不是「全对」，
    「一个引用标记都没有」不是「引用 100% 准确」。
    """
    results = results or []
    rerank_before = rerank_before or []
    dropped = dropped or []
    annotations = annotations or {}
    stages = stages or []

    after_ids = [r.get("chunk_id") for r in results]
    before_ids = [e.get("chunk_id") for e in rerank_before]

    p_after, n_after = _p_at_k(after_ids, annotations)
    p_before, n_before = _p_at_k(before_ids, annotations)

    rerank_stage = next((s for s in stages if s.get("name") == "rerank"), {}) or {}
    rr_status = rerank_stage.get("status", "skipped")
    rr_reason = rerank_stage.get("reason")

    # 重排没跑就没有「增益」可言。给 0 会被读成「重排了但没用」——正是 D2 要消除的歧义
    gain = None
    if rr_status == "ok" and p_after is not None and p_before is not None:
        gain = p_after - p_before

    # 被丢弃却被人工判定有关的条数：阈值与过滤条件误伤的直接证据
    false_drop = 0
    drop_annotated = 0
    for entry in dropped:
        ann = annotations.get(entry.get("chunk_id"))
        if not isinstance(ann, dict):
            continue
        rel = ann.get("relevance")
        if not _is_relevance(rel):
            continue
        drop_annotated += 1
        if rel >= 1:
            false_drop += 1

    # 只认真正生成出来的答案。回退时 answer 是检索原文，其编号头会被当成引用标记，
    # 算出一份漂亮的假指标（§7.3 实施期修正 2）
    citation_precision = None
    groundedness = None
    if isinstance(generation, dict) and generation.get("generation_status") == "ok":
        stats = generation.get("citation_stats") or {}
        citation_precision = stats.get("citation_precision")
        groundedness = stats.get("answer_groundedness")

    return {
        "k": len(results),
        "p_at_k": p_after,
        "p_at_k_annotated": n_after,
        "hit_rate": _hit_rate(after_ids, annotations),
        "rerank_gain": gain,
        "rerank_gain_basis": {
            "p_at_k_after": p_after,
            "p_at_k_before": p_before,
            "annotated_after": n_after,
            "annotated_before": n_before,
            "status": rr_status,
            "reason": rr_reason,
        },
        "channel_contribution": _channel_contribution(results),
        "filter_false_drop": false_drop,
        "filter_false_drop_annotated": drop_annotated,
        "citation_precision": citation_precision,
        "answer_groundedness": groundedness,
        "annotated_total": len(valid_annotations_only(annotations)),
    }


# ── 存储 §5.3 ─────────────────────────────────────────────────

class RunStore:
    """run 的留存与标注。所有写入原子且带双锁。"""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else get_workspace_path() / "rag_eval" / "runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self._file_lock = FileLock(str(self.root / ".runs.lock"))
        self._thread_lock = threading.RLock()

    # ── 锁与原子写 ────────────────────────────────────────────

    @contextmanager
    def _guard(self) -> Iterator[None]:
        # 先线程锁再文件锁，全类统一顺序，避免交叉持锁死锁
        with self._thread_lock, self._file_lock:
            yield

    def _atomic_write_json(self, path: Path, data: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            logger.warning("RAG runs：index.json 损坏，按空索引继续")
            return []
        return data if isinstance(data, list) else []

    def _read_run(self, run_id: str) -> dict[str, Any] | None:
        path = self.root / f"{run_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            logger.warning("RAG runs：{} 损坏，视为不存在", run_id)
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _summary(run: dict[str, Any]) -> dict[str, Any]:
        """索引里的轻量摘要——列表页要的是名字和指标，不是整份 results。"""
        return {
            "run_id": run["run_id"],
            "created_at": run["created_at"],
            "updated_at": run.get("updated_at"),
            "label": run.get("label", ""),
            "tags": run.get("tags", []),
            "query": run.get("query", ""),
            "result_count": len(run.get("results") or []),
            "annotated_count": len(valid_annotations_only(run.get("annotations"))),
            "rerank_status": (
                (run.get("metrics") or {}).get("rerank_gain_basis") or {}
            ).get("status"),
            "metrics": run.get("metrics") or {},
        }

    def _write_run_and_index(self, run: dict[str, Any]) -> None:
        """调用方必须已持有 _guard()。"""
        self._atomic_write_json(self.root / f"{run['run_id']}.json", run)
        index = [e for e in self._read_index() if e.get("run_id") != run["run_id"]]
        index.insert(0, self._summary(run))   # 插队首 = 最新在前，不依赖时间戳排序
        self._atomic_write_json(self.index_path, index)

    @staticmethod
    def _trim_result(item: dict[str, Any]) -> dict[str, Any]:
        out = dict(item)
        text = out.get("text")
        if isinstance(text, str):
            out["text"] = text[:_STORED_TEXT_CHARS]
        return out

    # ── CRUD ──────────────────────────────────────────────────

    def create(
        self,
        *,
        retrieval: dict[str, Any],
        generation: dict[str, Any] | None = None,
        label: str = "",
        tags: list[str] | None = None,
        labeler: str = "",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """从一次 search(debug=True) 的响应留存一个 run。"""
        retrieval = retrieval or {}
        rid = _safe_run_id(
            run_id or retrieval.get("run_id") or f"r_{uuid.uuid4().hex[:12]}"
        )

        results = [self._trim_result(r) for r in (retrieval.get("results") or [])]
        rerank_before = [
            self._trim_result(e) for e in (retrieval.get("rerank_before") or [])
        ]
        dropped = list(retrieval.get("dropped") or [])
        stages = list(retrieval.get("stages") or [])

        run: dict[str, Any] = {
            "run_id": rid,
            "created_at": _now(),
            "updated_at": None,
            "label": label or "",
            "tags": list(tags or []),
            "labeler": labeler or "",
            "query": retrieval.get("query", ""),
            "params_effective": retrieval.get("params_effective") or {},
            "expanded_queries": retrieval.get("expanded_queries") or [],
            "expansion_source": retrieval.get("expansion_source"),
            "stages": stages,
            "timings_ms": retrieval.get("timings_ms") or {},
            "results": results,
            "dropped": dropped,
            "dropped_truncated": bool(retrieval.get("dropped_truncated", False)),
            "rerank_before": rerank_before,
            "generation": generation,
            "annotations": {},
        }
        run["metrics"] = compute_metrics(
            results=results, rerank_before=rerank_before, dropped=dropped,
            annotations={}, stages=stages, generation=generation,
        )

        with self._guard():
            self._write_run_and_index(run)
        return run

    def get(self, run_id: str) -> dict[str, Any] | None:
        rid = _safe_run_id(run_id)
        with self._guard():
            return self._read_run(rid)

    def list_runs(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        limit = max(1, int(limit))
        offset = max(0, int(offset))
        with self._guard():
            index = self._read_index()
        return {
            "total": len(index),          # 全量数，不随分页变
            "limit": limit,
            "offset": offset,
            "runs": index[offset:offset + limit],
        }

    def update(
        self,
        run_id: str,
        *,
        label: Any = _UNSET,
        tags: Any = _UNSET,
        labeler: Any = _UNSET,
        annotations: dict[str, Any] | None = None,
        replace_annotations: bool = False,
    ) -> dict[str, Any] | None:
        """改名/打标/标注。标注默认**合并**——标注是一条条打的，第二次提交不能抹掉第一次。"""
        rid = _safe_run_id(run_id)
        # 校验放在锁外：脏数据不该占着锁，也不该让别的写入排队等它抛异常
        clean = validate_annotations(annotations) if annotations is not None else None

        with self._guard():
            run = self._read_run(rid)
            if run is None:
                return None

            if label is not _UNSET:
                run["label"] = label or ""
            if tags is not _UNSET:
                run["tags"] = list(tags or [])
            if labeler is not _UNSET:
                run["labeler"] = labeler or ""
            if clean is not None:
                run["annotations"] = clean if replace_annotations else {
                    **(run.get("annotations") or {}), **clean,
                }

            run["updated_at"] = _now()
            run["metrics"] = compute_metrics(
                results=run.get("results"), rerank_before=run.get("rerank_before"),
                dropped=run.get("dropped"), annotations=run.get("annotations"),
                stages=run.get("stages"), generation=run.get("generation"),
            )
            self._write_run_and_index(run)
        return run

    def delete(self, run_id: str) -> bool:
        rid = _safe_run_id(run_id)
        with self._guard():
            path = self.root / f"{rid}.json"
            existed = path.exists()
            if existed:
                path.unlink()
            index = self._read_index()
            pruned = [e for e in index if e.get("run_id") != rid]
            if len(pruned) != len(index):
                self._atomic_write_json(self.index_path, pruned)
        return existed
