"""pytest 配置和共享夹具"""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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
