"""本地向量存储 — numpy 数组 + pickle 持久化 + tombstone 支持"""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np


class VectorStore:
    """numpy 向量存储。维度固定，按 chunk_id 索引。"""

    def __init__(self, store_dir: Path, dim: int = 1536):
        self.dir = store_dir
        self.dim = dim
        self.dir.mkdir(parents=True, exist_ok=True)
        self._np_path = self.dir / "embeddings.npy"
        self._ids_path = self.dir / "chunk_ids.pkl"
        self._meta_path = self.dir / "metadata.pkl"
        self._tomb_path = self.dir / "tombstones.json"
        self._ids: list[str] = []
        self._meta: dict[str, dict[str, Any]] = {}
        self._embeddings: np.ndarray | None = None
        self._tombstones: set[str] = set()
        self._load()

    def _load(self) -> None:
        if self._np_path.exists() and self._ids_path.exists():
            self._embeddings = np.load(str(self._np_path))
            with open(self._ids_path, "rb") as f:
                self._ids = pickle.load(f)
        else:
            self._embeddings = np.empty((0, self.dim), dtype=np.float32)
        if self._meta_path.exists():
            with open(self._meta_path, "rb") as f:
                self._meta = pickle.load(f)
        if self._tomb_path.exists():
            self._tombstones = set(json.loads(self._tomb_path.read_text(encoding="utf-8")))

    def _save(self) -> None:
        np.save(str(self._np_path), self._embeddings)
        with open(self._ids_path, "wb") as f:
            pickle.dump(self._ids, f)
        with open(self._meta_path, "wb") as f:
            pickle.dump(self._meta, f)

    def _save_tombstones(self) -> None:
        self._tomb_path.write_text(json.dumps(list(self._tombstones), ensure_ascii=False), encoding="utf-8")

    def add(self, vectors: list[list[float]], chunk_ids: list[str], metas: list[dict[str, Any]] | None = None) -> None:
        """批量添加向量。跳过已在 tombstones 中的 ID。"""
        new_vecs = []
        new_ids = []
        for vec, cid in zip(vectors, chunk_ids):
            if cid in self._tombstones:
                continue
            new_vecs.append(vec)
            new_ids.append(cid)
        if not new_vecs:
            return
        arr = np.array(new_vecs, dtype=np.float32)
        if self._embeddings is not None and self._embeddings.size > 0:
            self._embeddings = np.vstack([self._embeddings, arr])
        else:
            self._embeddings = arr
        self._ids.extend(new_ids)
        if metas:
            for cid, meta in zip(new_ids, metas):
                self._meta[cid] = meta
        self._save()

    def remove(self, chunk_ids: list[str]) -> None:
        """逻辑删除：写入 tombstones，重建数组。"""
        self._tombstones.update(chunk_ids)
        self._save_tombstones()
        keep_idx = [i for i, cid in enumerate(self._ids) if cid not in self._tombstones]
        if not keep_idx:
            self._embeddings = np.empty((0, self.dim), dtype=np.float32)
            self._ids = []
        else:
            self._embeddings = self._embeddings[keep_idx]
            self._ids = [self._ids[i] for i in keep_idx]
        for cid in chunk_ids:
            self._meta.pop(cid, None)
        self._save()

    def search(self, query_vec: list[float], top_k: int = 20) -> list[tuple[str, float]]:
        """余弦相似度搜索，返回 [(chunk_id, score), ...]"""
        if self._embeddings is None or self._embeddings.size == 0:
            return []
        q = np.array(query_vec, dtype=np.float32)
        q_norm = q / (np.linalg.norm(q) + 1e-10)
        norms = np.linalg.norm(self._embeddings, axis=1) + 1e-10
        scores = np.dot(self._embeddings, q_norm) / norms
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(self._ids[int(i)], float(scores[int(i)])) for i in top_indices if scores[int(i)] > 0]

    def get_meta(self, chunk_id: str) -> dict[str, Any] | None:
        return self._meta.get(chunk_id)

    def __len__(self) -> int:
        return len(self._ids)
