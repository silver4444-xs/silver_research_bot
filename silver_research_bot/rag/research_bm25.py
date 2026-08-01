"""纯 Python Okapi BM25 关键词评分器 — 零外部依赖，中英文兼容"""

from __future__ import annotations

import math
import re
from typing import Any

_TOKEN_RE = re.compile(r"[\w一-鿿]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class BM25Scorer:
    """Okapi BM25 实现 (k1=1.5, b=0.75)。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._docs: list[dict[str, Any]] = []  # [{id, tokens, doc_len}]
        self._doc_len_sum: float = 0.0
        self._tf: dict[str, dict[str, int]] = {}  # term -> {doc_id -> freq}
        self._df: dict[str, int] = {}  # document frequency per term

    @property
    def doc_count(self) -> int:
        return len(self._docs)

    @property
    def avg_doc_len(self) -> float:
        return self._doc_len_sum / max(1, len(self._docs))

    def fit(self, corpus: list[tuple[str, str]]) -> None:
        """全量构建索引。corpus: [(doc_id, text), ...]"""
        self._docs.clear()
        self._doc_len_sum = 0.0
        self._tf.clear()
        self._df.clear()
        for doc_id, text in corpus:
            self.add(doc_id, text)

    def add(self, doc_id: str, text: str) -> None:
        tokens = _tokenize(text)
        self._docs.append({"id": doc_id, "tokens": tokens, "doc_len": len(tokens)})
        self._doc_len_sum += len(tokens)
        seen: set[str] = set()
        for term in tokens:
            if term not in self._tf:
                self._tf[term] = {}
            self._tf[term][doc_id] = self._tf[term].get(doc_id, 0) + 1
            if term not in seen:
                seen.add(term)
                self._df[term] = self._df.get(term, 0) + 1

    def remove(self, doc_id: str) -> None:
        self._docs = [d for d in self._docs if d["id"] != doc_id]
        for term in list(self._tf):
            if doc_id in self._tf[term]:
                self._df[term] -= 1
                del self._tf[term][doc_id]
                if self._df[term] <= 0:
                    del self._df[term]
                    del self._tf[term]
        self._doc_len_sum = sum(d["doc_len"] for d in self._docs)

    def score(self, query: str) -> list[tuple[str, float]]:
        """返回 [(doc_id, score), ...] 按分数降序排列。"""
        qtokens = _tokenize(query)
        if not qtokens or not self._docs:
            return []
        avg = self.avg_doc_len
        N = len(self._docs)
        scores: dict[str, float] = {}
        for term in set(qtokens):
            qtf = qtokens.count(term)
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
            for doc_id, tf in self._tf.get(term, {}).items():
                doc_entry = next((d for d in self._docs if d["id"] == doc_id), None)
                if doc_entry is None:
                    continue
                dl = doc_entry["doc_len"]
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * dl / avg)
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * qtf * numerator / denominator
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        return self.score(query)[:top_k]
