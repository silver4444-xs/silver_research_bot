"""引用归因：解析答案里的 [N] 标记并反查检索来源。

**轻量 grounding** —— 不做逐句语义归因（成本高、需另一轮 LLM），只做三件
可程序化验证的事：

1. 标记是否悬空（引用了不存在的编号 = 幻觉标记）
2. 来源是否被用上（检索回来却没进答案 = 召回过宽或生成没吃进去）
3. 哪些句子完全无引用支撑（无据断言）

这三项足以判断「引用环节是否符合预期」，且结论可复现、零额外 LLM 成本。

前提：`sources` 的**位置即编号**（`[N]` ↔ `sources[N-1]`）。调用方必须用同一批
检索结果既构造给 LLM 看的编号 context、又构造这里的 sources，否则归因本身
就是假的。

契约见 docs/superpowers/specs/2026-08-25-rag-lab-page-design.md §5.2。
"""

from __future__ import annotations

import re
from typing import Any

# 引用标记 [N]。限制位数，避免把 [2026] 这类年份误当引用
_MARKER_RE = re.compile(r"\[(\d{1,3})\]")

# 断句：中英文终止符 + 换行。保留终止符本身，以便判断标记归属于前句还是后句
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?;；])\s*|(?<=\.)\s+|\n+")

# 行首的 markdown 结构标记（标题/列表/引用/分隔线）——不是断言句
_STRUCTURE_RE = re.compile(r"^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>\s|[-*_]{3,}\s*$)")

# 剥掉标记与空白后短于此长度的片段不计入断言句，
# 避免把孤立的「。」或「[1]」当成一整句稀释分母
_MIN_SENTENCE_CHARS = 4


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(text) if s and s.strip()]


def _is_assertion(fragment: str) -> bool:
    """是否算一句「断言」——结构行与纯标记片段不算。"""
    stripped = fragment.strip()
    if _STRUCTURE_RE.match(stripped):
        return False
    content = _MARKER_RE.sub("", stripped).strip()
    return len(content) >= _MIN_SENTENCE_CHARS


def analyze_grounding(answer: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    """解析 answer 内的 [N] 并反查 sources，返回引用归因与统计。

    只有**有效**标记才算支撑：悬空标记是幻觉，不构成 grounding。
    """
    n_sources = len(sources)

    def _lookup(idx: int) -> dict[str, Any] | None:
        # 编号 1-based，位置即编号
        if 1 <= idx <= n_sources:
            return sources[idx - 1]
        return None

    def _has_valid_marker(text: str) -> bool:
        return any(_lookup(int(g)) is not None for g in _MARKER_RE.findall(text))

    # ── 标记：按首次出现排序去重，统计按出现次数 ──────────────
    citations: list[dict[str, Any]] = []
    seen: dict[int, dict[str, Any]] = {}
    markers_total = markers_valid = 0

    for m in _MARKER_RE.finditer(answer):
        idx = int(m.group(1))
        src = _lookup(idx)
        markers_total += 1
        if src is not None:
            markers_valid += 1
        if idx in seen:
            seen[idx]["count"] += 1
            continue
        entry = {
            "marker": f"[{idx}]",
            "index": idx,
            "count": 1,
            "chunk_id": src.get("chunk_id") if src else None,
            "paper_id": src.get("paper_id") if src else None,
            "title": src.get("title") if src else None,
            "valid": src is not None,
        }
        seen[idx] = entry
        citations.append(entry)

    cited_indices = {c["index"] for c in citations if c["valid"]}

    # ── 句子：判断哪些断言完全没有有效标记支撑 ────────────────
    fragments = _split_sentences(answer)
    is_assertion = [_is_assertion(f) for f in fragments]
    has_support = [_has_valid_marker(f) for f in fragments]

    # 「。[N] 文字。」这个局部模式有歧义：
    #   后置引用风格「断言。[1]」里，[1] 是**前**句的尾注；
    #   前置引用风格「[1] 断言。」里，[1] 是**后**句的引子。
    # 局部分辨不了，只能看全局——答案是否以标记开头。
    #
    # 判错方向要紧：把尾注误当引子，只会少算支撑（指标偏保守，促使人去核对）；
    # 把引子误当尾注，会让无据句被隔壁的标记白白撑起来，virtue 是假的——
    # answer_groundedness 虚报满分，恰好掩盖本模块要暴露的问题。
    leading_style = bool(re.match(r"^\s*\[\d{1,3}\]", answer))

    for i, frag in enumerate(fragments):
        if i == 0:
            continue
        lead = re.match(r"^\s*((?:\[\d{1,3}\]\s*)+)", frag)
        if not lead or not _has_valid_marker(lead.group(1)):
            continue
        # 片段除了标记别无内容（「断言。[1]」被切出的独立「[1]」）→ 无歧义的尾注，
        # 无论全局风格都回挂。若片段还有实质内容且全文是前置引用风格，
        # 标记属于它自己那句，不能回挂。
        if leading_style and is_assertion[i]:
            continue
        for j in range(i - 1, -1, -1):  # 回溯到最近的一个断言句
            if is_assertion[j]:
                has_support[j] = True
                break

    uncited = [
        fragments[i].strip()
        for i in range(len(fragments))
        if is_assertion[i] and not has_support[i]
    ]
    sentences_total = sum(1 for x in is_assertion if x)

    stats: dict[str, Any] = {
        "markers_total": markers_total,
        "markers_valid": markers_valid,
        "markers_dangling": markers_total - markers_valid,
        "sources_total": n_sources,
        "sources_cited": len(cited_indices),
        "sources_unused": n_sources - len(cited_indices),
        "sentences_total": sentences_total,
        "sentences_uncited": len(uncited),
        # 分母为 0 时给 None 而非 1.0：「一个标记都没有」不是「引用 100% 准确」，
        # 「一句话都没有」也不是「100% 有据」。页面据此显示「不适用」而非假满分。
        "citation_precision": (markers_valid / markers_total) if markers_total else None,
        "answer_groundedness": (
            1.0 - len(uncited) / sentences_total if sentences_total else None
        ),
    }

    return {"citations": citations, "citation_stats": stats, "uncited_sentences": uncited}
