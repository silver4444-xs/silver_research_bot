"""Formula extraction evaluation against 公式汇总.md ground-truth dataset.

Tests all validation gates: _is_valid_formula, _is_substantial_math,
extract_formulas_from_translation, and _is_fragment.

Run: pytest tests/test_formula_eval.py -v --tb=short
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

# ── Load modules directly (avoid package __init__.py → tomllib on <3.11) ──

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_module(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, str(_PROJECT_ROOT / relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Set up module hierarchy for intra-package imports
_extractor_mod = _load_module("extractor", "silver_research_bot/paper_analyzer/extractor.py")
sys.modules["silver_research_bot.paper_analyzer.extractor"] = _extractor_mod
sys.modules.setdefault("silver_research_bot.paper_analyzer", types.ModuleType("pa"))
sys.modules.setdefault("silver_research_bot", types.ModuleType("srb"))
sys.modules.setdefault("silver_research_bot.utils", types.ModuleType("utils"))
sys.modules.setdefault("silver_research_bot.utils.prompt_templates", types.ModuleType("pt"))
sys.modules["silver_research_bot.utils.prompt_templates"].render_template = (
    lambda t, strip=False: ""
)
sys.modules.setdefault("silver_research_bot.providers", types.ModuleType("providers"))
sys.modules.setdefault("silver_research_bot.providers.base", types.ModuleType("base"))

_formula_mod = _load_module("fe", "silver_research_bot/paper_analyzer/formula_explainer.py")

# Shortcuts
_is_valid_formula = _extractor_mod._is_valid_formula
_is_substantial_math = _formula_mod._is_substantial_math
extract_from_translation = _formula_mod.extract_formulas_from_translation
_COMPLETE_FORMULA_RE = _extractor_mod._COMPLETE_FORMULA_RE


# ── Helpers ──────────────────────────────────────────────────────────────

def _is_fragment_fn(c: str) -> bool:
    """Reconstructed _is_fragment closure from formula_explainer._strip_fragment_cards."""
    c = c.strip()
    if not c:
        return True
    if "◈" in c:
        return True
    if "\\" in c:
        return False
    if re.search(r'[_^=+<>≤≥≠≈≡∈⊂⊆∪∩∫∑∏∇∂]', c):
        return False
    alpha_only = re.sub(r'[\s,.;:()\[\]{}|0-9+\-*/×⋅±]', '', c)
    if not alpha_only or len(alpha_only) <= 1:
        return True
    if len(alpha_only) <= 3 and re.match(r'^[a-zA-Z]+$', alpha_only):
        return True
    words = c.split()
    if len(words) >= 3:
        alpha_words = [w for w in words if re.match(r'^[a-zA-Z]{2,}$', w)]
        if len(alpha_words) >= 3:
            return True
    return False


# Commands NOT in _COMPLETE_FORMULA_RE (vulnerable to false negatives)
_VULNERABLE_COMMANDS = [
    r"\varphi", r"\varpi", r"\emptyset", r"\varnothing", r"\theta",
    r"\pi", r"\alpha", r"\beta", r"\gamma", r"\delta", r"\epsilon",
    r"\sigma", r"\mu", r"\lambda", r"\in",
]


# ── Test Class 1: Core Validator ─────────────────────────────────────────

class TestFormulaValidator:
    """Test _is_valid_formula — the central validation gate."""

    def test_all_display_formulas_pass(self, ground_truth_formulas, parsed_dataset):
        """Every ground-truth display formula should pass _is_valid_formula."""
        failures: list[tuple[str, int, str]] = []
        for f in parsed_dataset.display_formulas:
            if not _is_valid_formula(f.latex):
                failures.append((f.latex[:100], f.paper_id, f.category))

        total = len(parsed_dataset.display_formulas)
        fail_count = len(failures)
        recall = (total - fail_count) / total * 100 if total else 0

        print(f"\n    Ground-truth recall: {total - fail_count}/{total} = {recall:.1f}%")
        if failures:
            by_cat: dict[str, list[str]] = {}
            for latex, pid, cat in failures:
                by_cat.setdefault(cat, []).append(latex)
            print(f"    --- {fail_count} FALSE NEGATIVES ---")
            for cat, items in sorted(by_cat.items()):
                print(f"    [{cat}] ({len(items)} rejected):")
                for item in items[:5]:
                    print(f"      - {item}")
                if len(items) > 5:
                    print(f"      ... and {len(items) - 5} more")

        assert recall >= 98.0, (
            f"Ground-truth recall {recall:.1f}% below 98% threshold. "
            f"{fail_count} false negatives. First: {failures[0] if failures else 'N/A'}"
        )

    def test_all_bad_fragments_rejected(self, known_bad_fragments):
        """All known-bad and synthesized fragments must be REJECTED."""
        accepted = [f for f in known_bad_fragments if _is_valid_formula(f)]
        if accepted:
            print(f"\n    {len(accepted)} bad fragments WRONGLY ACCEPTED:")
            for f in accepted:
                print(f"      - {f[:80]}")
        assert len(accepted) == 0, f"{len(accepted)} bad fragments passed"

    def test_by_category_recall(self, parsed_dataset):
        """Per-category acceptance rate — must be ≥ 95%."""
        by_cat: dict[str, list[str]] = {}
        for f in parsed_dataset.display_formulas:
            by_cat.setdefault(f.category, []).append(f.latex)

        low_cats = []
        for cat, formulas in sorted(by_cat.items()):
            accepted = sum(1 for l in formulas if _is_valid_formula(l))
            rate = accepted / len(formulas) * 100
            if rate < 95:
                low_cats.append((cat, accepted, len(formulas), rate))
            print(f"    [{cat}] {accepted}/{len(formulas)} = {rate:.1f}%")

        assert not low_cats, (
            f"Categories below 95% recall: "
            + "; ".join(f"{c}: {a}/{t}={r:.1f}%" for c, a, t, r in low_cats)
        )

    def test_vulnerable_commands_analysis(self, parsed_dataset):
        """Identify formulas using LaTeX commands not in _COMPLETE_FORMULA_RE."""
        found_cmds: set[str] = set()
        rejected_count = 0
        cmd_reject_counts: dict[str, int] = {}

        for f in parsed_dataset.display_formulas:
            for cmd in _VULNERABLE_COMMANDS:
                if cmd in f.latex:
                    found_cmds.add(cmd)
                    if not _is_valid_formula(f.latex):
                        rejected_count += 1
                        cmd_reject_counts[cmd] = cmd_reject_counts.get(cmd, 0) + 1
                        break  # count each formula once

        print(f"\n    Vulnerable commands in dataset: {sorted(found_cmds)}")
        print(f"    Formulas using vulnerable commands that were rejected: {rejected_count}")
        if cmd_reject_counts:
            print(f"    By command: {cmd_reject_counts}")

    def test_COMPLETE_FORMULA_RE_known_commands(self):
        """Verify specific LaTeX commands are recognized."""
        should_match = [
            r"\leq", r"\geq", r"\neq", r"\approx", r"\equiv",
            r"\frac", r"\sum", r"\prod", r"\int", r"\sqrt",
            r"\sin", r"\cos", r"\log", r"\ln", r"\exp",
            r"\min", r"\max", r"\partial", r"\nabla", r"\mathbb{E}",
            r"\times", r"\cdot", r"\pm", r"\propto", r"\sim",
        ]
        missing = [c for c in should_match if not _COMPLETE_FORMULA_RE.search(c)]
        assert missing == [], f"Commands missing from _COMPLETE_FORMULA_RE: {missing}"


# ── Test Class 2: Substantial Math ───────────────────────────────────────

class TestSubstantialMath:
    """Test _is_substantial_math — inline $...$ filter in translation path."""

    def test_inline_formulas_pass(self, parsed_dataset):
        """Inline formulas with operators/subscripts should pass."""
        passed = 0
        failed: list[str] = []
        for f in parsed_dataset.inline_formulas:
            if _is_substantial_math(f.latex):
                passed += 1
            else:
                failed.append(f.latex[:80])

        total = len(parsed_dataset.inline_formulas)
        if total:
            print(f"\n    Inline: {passed}/{total} = {passed / total * 100:.1f}%")
        if failed:
            print(f"    {len(failed)} rejected:")
            for f in failed[:5]:
                print(f"      - {f}")

    def test_bare_symbols_rejected(self):
        """Lone symbols should be rejected."""
        for sym in [r"\varphi", r"\pi", r"\theta", r"x", r"N", r"\alpha"]:
            assert not _is_substantial_math(sym), f"'{sym}' should be rejected"


# ── Test Class 3: Translation Path ───────────────────────────────────────

class TestExtractFromTranslation:
    """Test extract_formulas_from_translation — full pipeline integration."""

    def test_display_formulas_extracted(self):
        """Translation with $$...$$ blocks should extract them."""
        t = (
            "The rate is:\n\n"
            "$$R = B \\log_2(1 + \\text{SNR})$$\n\n"
            "The optimization is:\n\n"
            "$$\\min_{x \\in \\mathcal{X}} f(x)$$\n\n"
            "subject to constraints."
        )
        formulas = extract_from_translation(t)
        assert len(formulas) >= 2, f"Expected >=2, got {len(formulas)}"

    def test_inline_garbage_not_extracted(self):
        """Prose fragments in $...$ should NOT produce formulas."""
        t = (
            "无人机通信模型：$\\varphi$ t n,m $=$ 1 表示无人机 n 与 UE m "
            "之间可以实现通信，$\\varphi$ t n,m $=$ 0 则相反。\n"
            "其中 $\\theta$ \\in [0, 2$\\pi$ 定义了覆盖范围。"
        )
        formulas = extract_from_translation(t)
        garbage_latex = {f["latex"] for f in formulas}
        for pat in ["\\varphi t", "\\in [0, 2"]:
            found = [l for l in garbage_latex if pat in l]
            assert len(found) == 0, f"Pattern '{pat}' extracted: {found}"

    def test_mixed_content(self):
        """Both real formulas and prose — only real extracted."""
        t = (
            "Channel gain:\n\n"
            "$$g = \\frac{\\beta_0}{d^2}$$\n\n"
            "坏的模式：$\\varphi$ t n,m $=$ 1 表示通信。\n\n"
            "The SINR:\n\n"
            "$$\\gamma = \\frac{P g}{\\sigma^2 + I}$$\n"
        )
        formulas = extract_from_translation(t)
        latex_set = {f["latex"] for f in formulas}
        assert any("g =" in l for l in latex_set), f"No good formulas found: {latex_set}"


# ── Test Class 4: Fragment Filter ────────────────────────────────────────

class TestFragmentFilter:
    """Test _is_fragment — post-LLM HTML card filter."""

    def test_fragment_detection(self):
        """Short alpha-only / numeric / prose content → fragment; LaTeX/ops → keep."""
        # Must reject
        for fexpr in ["x", "t n", "ab", "  ", "1", "12", "a", "◈FIG_3◈",
                       "the quick brown fox", "this is prose text"]:
            assert _is_fragment_fn(fexpr), f"'{fexpr}' should be fragment"

        # Must keep
        for fexpr in [
            r"\varphi_{t}^{n,m} = 1",
            r"x_i^2 + y_i^2",
            r"E = mc^2",
            r"\alpha + \beta",
            r"SNR \geq 10",
        ]:
            assert not _is_fragment_fn(fexpr), f"'{fexpr}' should NOT be fragment"


# ── Test Class 5: Edge Cases ─────────────────────────────────────────────

class TestEdgeCases:
    """Specific edge cases from pipeline_analysis.md / bug_report.md."""

    def test_subscript_vs_prose(self):
        """Proper subscripts accepted; stripped ones rejected."""
        assert _is_valid_formula(r"\varphi_{t}^{n,m} = 1")
        assert _is_valid_formula(r"x_i^2 + y_i^2")
        assert not _is_valid_formula(r"\varphi t n,m = 1")
        assert not _is_valid_formula(r"x i 2 + y i 2")

    def test_trailing_operator(self):
        assert not _is_valid_formula(r"\varphi t n,m =")
        assert not _is_valid_formula(r"x +")
        assert _is_valid_formula(r"x + y")

    def test_leading_operator(self):
        assert not _is_valid_formula(r"= \emptyset")
        assert not _is_valid_formula(r"+ x")
        assert not _is_valid_formula(r"\leq R max")
        assert _is_valid_formula(r"\min_x f(x)")

    def test_CJK_contamination(self):
        assert not _is_valid_formula(r"\varphi = 1 表示通信")
        assert not _is_valid_formula(r"x + y の最適化")
        assert _is_valid_formula(r"\varphi = 1")

    def test_cjk_in_text_blocks_pass(self):
        """Formulas with CJK inside \\text{} MUST pass. Regression test."""
        must_pass = [
            r"a_k(t) = \begin{cases} 1, & \text{节点在时隙 t 决定进行任务卸载} \\ 0, & \text{otherwise} \end{cases}",
            r"\theta = \arctan\left(\frac{z}{r}\right) \quad \text{与} \quad \arcsin\left(\frac{z}{d}\right)",
            r"\min_{x} f(x) \quad \text{s.t.} \quad g(x) \leq 0",
        ]
        for f in must_pass:
            assert _is_valid_formula(f), f"Formula with CJK in \\text{{}} rejected: {f[:60]}..."

    def test_cjk_outside_text_rejected(self):
        """CJK OUTSIDE \\text{} must still be rejected."""
        must_fail = [
            r"\varphi = 1 表示通信",
            r"x + y の最適化",
            r"\sum_{i=1}^n x_i 其中 n 是样本数",
        ]
        for f in must_fail:
            assert not _is_valid_formula(f), f"CJK in body should be rejected: {f[:60]}..."

    def test_balanced_brackets(self):
        assert not _is_valid_formula(r"\in [0, 2\pi")
        assert not _is_valid_formula(r"f(x = y")
        assert _is_valid_formula(r"x \in [0, 2\pi]")
        assert _is_valid_formula(r"\frac{a}{b}")

    def test_dollar_sign_artifact(self):
        assert not _is_valid_formula(r"\varphi t$ n,mdt")
        assert not _is_valid_formula(r"x $ y")
        assert _is_valid_formula(r"x + y")
