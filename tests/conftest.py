"""pytest configuration and shared fixtures."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ── Formula evaluation fixtures ────────────────────────────────────────

@pytest.fixture(scope="module")
def parsed_dataset():
    """Parse 公式汇总.md once per test run — expensive, so module-scoped."""
    from tests.parse_formula_md import parse_formulas_md

    return parse_formulas_md()


@pytest.fixture(scope="module")
def ground_truth_formulas(parsed_dataset) -> list[str]:
    """All display formula LaTeX strings — these MUST pass validation."""
    return [f.latex for f in parsed_dataset.display_formulas]


@pytest.fixture(scope="module")
def known_bad_fragments() -> list[str]:
    """Fragments that MUST be rejected by _is_valid_formula."""
    # User-reported fragments from real usage
    user_reported = [
        r"\varphi  t n,m   =   1",
        r"\varphi t n,m =",
        r"\in [0, 2 \pi",
        r"\varphi t",
        r"\varphi  t n,m   =   0",
        r"= \emptyset",
        r"\leq R max",
        r"\varphi t n,mdt n,m n (1)",
        r"\varpi t = P N n = 1 D U",
        r"\varpi t 0 , \varpi t 1 , . . . , \varpi t n)",
    ]
    # Synthesized: strip subscripts from good formulas
    synthesized = [
        r"x i 2",                       # x_i^2 stripped
        r"\varphi t n,m = 1",           # \varphi_t^{n,m} stripped
        r"w n 2",                       # w_n^2 stripped
        r"R k t = B log 2 1 + SNR",    # formula without LaTeX structure
    ]
    # Edge cases
    edge = [
        r"=",                           # bare operator
        r"the quick brown fox",         # English prose
        r"",                            # empty
        r"x",                           # single letter
        r"\varphi",                     # lone LaTeX symbol
        r"\varphi = 1 表示通信",         # CJK contamination
    ]
    return user_reported + synthesized + edge


@pytest.fixture
def sample_paper_en() -> str:
    return (
        "The dominant sequence transduction models are based on complex recurrent or "
        "convolutional neural networks that include an encoder and a decoder. The best "
        "performing models also connect the encoder and decoder through an attention "
        "mechanism. We propose a new simple network architecture, the Transformer, "
        "based solely on attention mechanisms, dispensing with recurrence and convolutions "
        "entirely. Experiments on two machine translation tasks show these models to be "
        "superior in quality while being more parallelizable and requiring significantly "
        "less time to train. Our model achieves 28.4 BLEU on WMT 2014 English-to-German."
    )


@pytest.fixture
def sample_formulas() -> list[str]:
    return [
        r"Attention(Q, K, V) = \text{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V",
        r"\text{MultiHead}(Q, K, V) = \text{Concat}(\text{head}_1, ..., \text{head}_h)W^O",
    ]


@pytest.fixture
def sample_extracted() -> dict:
    return {
        "formulas": [
            {"index": 0, "latex": r"E = mc^2", "context": "Energy", "page": 1},
            {"index": 1, "latex": r"F = ma", "context": "Newton", "page": 1},
        ],
        "figures": [{"index": 0, "page": 1, "caption": "Architecture"}],
        "tables": [{"index": 0, "page": 1, "rows": 4, "cols": 3, "markdown": "|A|B|C|"}],
        "full_text": "Test paper.",
        "page_count": 1,
        "formula_count": 2,
        "figure_count": 1,
        "table_count": 1,
    }
