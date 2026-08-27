"""引用归因纯函数测试 — 无 LLM、无 I/O。

对应 docs/superpowers/specs/2026-08-25-rag-lab-page-design.md §5.2。

这是缺陷 D3 的解药：prompt 让 LLM 写 [1][2]，但全仓库此前没有任何代码
解析这些标记或反查 chunk，sources 只是 top-k 列表。这里做**可程序化验证的
三件事**：标记是否悬空、来源是否被用上、哪些句子完全无引用支撑。
"""

from __future__ import annotations

from silver_research_bot.rag.research_grounding import analyze_grounding

_SOURCES = [
    {"idx": 1, "chunk_id": "a1b2c3d4", "paper_id": "rp_0001", "title": "Paper 0 on retrieval"},
    {"idx": 2, "chunk_id": "e5f6a7b8", "paper_id": "rp_0002", "title": "Paper 1 on ranking"},
    {"idx": 3, "chunk_id": "c9d0e1f2", "paper_id": "rp_0003", "title": "Paper 2 on rerank"},
]


def test_valid_markers_map_to_sources():
    g = analyze_grounding("检索增强结合了参数化与非参数化记忆[1]。重排提升了精度[2]。", _SOURCES)

    by_marker = {c["marker"]: c for c in g["citations"]}
    assert by_marker["[1]"]["chunk_id"] == "a1b2c3d4"
    assert by_marker["[1]"]["valid"] is True
    assert by_marker["[2]"]["paper_id"] == "rp_0002"
    assert g["citation_stats"]["markers_valid"] == 2
    assert g["citation_stats"]["markers_dangling"] == 0


def test_dangling_marker_is_flagged():
    """引用了不存在的编号 = 幻觉标记，必须能被程序识别出来。"""
    g = analyze_grounding("结论如此[5]。", _SOURCES)

    dangling = [c for c in g["citations"] if not c["valid"]]
    assert len(dangling) == 1
    assert dangling[0]["marker"] == "[5]"
    assert dangling[0]["chunk_id"] is None
    assert g["citation_stats"]["markers_dangling"] == 1
    assert g["citation_stats"]["markers_valid"] == 0


def test_marker_zero_is_dangling_because_numbering_is_one_based():
    g = analyze_grounding("见前文[0]。", _SOURCES)
    assert g["citations"][0]["valid"] is False
    assert g["citation_stats"]["markers_dangling"] == 1


def test_repeated_marker_deduped_in_citations_but_counted_in_stats():
    g = analyze_grounding("首先[1]。其次仍据此[1]。", _SOURCES)

    assert len([c for c in g["citations"] if c["marker"] == "[1]"]) == 1
    assert g["citations"][0]["count"] == 2
    assert g["citation_stats"]["markers_total"] == 2
    assert g["citation_stats"]["sources_cited"] == 1


def test_unused_sources_are_counted():
    """检索回来但答案根本没用上的来源——召回过宽或生成没吃进去。"""
    g = analyze_grounding("只引用了第一条[1]。", _SOURCES)

    st = g["citation_stats"]
    assert st["sources_total"] == 3
    assert st["sources_cited"] == 1
    assert st["sources_unused"] == 2


def test_no_markers_means_nothing_grounded():
    g = analyze_grounding("这是一段完全没有引用标记的断言。第二句也没有。", _SOURCES)

    st = g["citation_stats"]
    assert st["markers_total"] == 0
    assert g["citations"] == []
    assert st["sources_cited"] == 0
    assert st["sources_unused"] == 3
    assert st["sentences_uncited"] == st["sentences_total"] == 2


def test_marker_after_terminator_attaches_to_preceding_sentence():
    """LLM 常写「……结论。[1]」——标记落在句号之后。

    若按朴素切分，这个标记会被算进下一句，导致 sentences_uncited 被系统性高估，
    而这恰恰是测试人员要读的 groundedness 指标。
    """
    g = analyze_grounding("检索增强提升了事实性。[1] 但延迟也上升了。[2]", _SOURCES)

    assert g["citation_stats"]["sentences_uncited"] == 0
    assert g["uncited_sentences"] == []


def test_leading_citation_style_does_not_steal_support_from_middle_sentence():
    """前置引用风格下，句首标记属于它自己那句，不能回挂前一句。

    否则「无引用的中间句」会被后面那句的 [2] 白白撑起来，groundedness 虚报满分——
    指标被系统性地往好看的方向偏，比不报更糟，因为它恰好掩盖本模块要暴露的问题。
    """
    g = analyze_grounding("[1] 研究表明X成立。无引用的中间句。[2] 此外Y也成立。", _SOURCES)

    assert g["citation_stats"]["sentences_total"] == 3
    assert g["citation_stats"]["sentences_uncited"] == 1
    assert "无引用的中间句" in g["uncited_sentences"][0]


def test_marker_only_fragment_reattaches_even_in_leading_style():
    """片段只有标记、别无内容时是无歧义的尾注，全局风格不影响它回挂前句。"""
    g = analyze_grounding("[1] 开头就引用的断言。中间这句其实有据。[2]", _SOURCES)

    assert g["citation_stats"]["sentences_total"] == 2
    assert g["citation_stats"]["sentences_uncited"] == 0


def test_uncited_sentences_are_listed_verbatim():
    g = analyze_grounding("有据的结论[1]。这一句是凭空断言的。", _SOURCES)

    assert g["citation_stats"]["sentences_total"] == 2
    assert g["citation_stats"]["sentences_uncited"] == 1
    assert len(g["uncited_sentences"]) == 1
    assert "凭空断言" in g["uncited_sentences"][0]


def test_english_sentences_are_split_too():
    g = analyze_grounding(
        "Retrieval helps factuality [1]. Latency increases as a result.", _SOURCES
    )
    assert g["citation_stats"]["sentences_total"] == 2
    assert g["citation_stats"]["sentences_uncited"] == 1


def test_empty_answer_is_not_a_crash_and_not_fake_perfect():
    g = analyze_grounding("", _SOURCES)

    st = g["citation_stats"]
    assert st["markers_total"] == 0
    assert st["sentences_total"] == 0
    assert st["sources_unused"] == 3
    assert g["uncited_sentences"] == []


def test_no_sources_makes_every_marker_dangling():
    g = analyze_grounding("凭空引用[1][2]。", [])

    st = g["citation_stats"]
    assert st["sources_total"] == 0
    assert st["markers_total"] == 2
    assert st["markers_valid"] == 0
    assert st["markers_dangling"] == 2


def test_headings_and_bullets_are_not_counted_as_assertions():
    """markdown 结构行不是断言句，计进分母会稀释 groundedness。"""
    g = analyze_grounding("## 结论\n\n检索增强有效[1]。", _SOURCES)

    assert g["citation_stats"]["sentences_total"] == 1
    assert g["citation_stats"]["sentences_uncited"] == 0
