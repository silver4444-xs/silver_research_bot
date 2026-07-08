#!/usr/bin/env python3
"""Stage 3: Formula Validator — auto-detect OCR errors in extracted LaTeX formulas.

Detects:
  - Garbled token soup (N X, xt n, Gt t) — broken subscript/superscript chains
  - Missing subscript/superscript notation (_ ^) where context demands it
  - Unbalanced braces {} [] $$
  - Invalid LaTeX commands
  - Naked operators without surrounding variables
  - English prose inside math mode
  - Equation numbering gaps

Usage:
  python validate_formulas.py formulas.json   # single file
  python validate_formulas.py --stdin          # read JSON from stdin
  python validate_formulas.py --stats-only f.json  # summary only
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


# ── Validation patterns ──────────────────────────────────────────────────

SUSPECT_ENGLISH_RE = re.compile(
    r"\b(?:the|and|for|are|was|not|but|all|has|had|have|can|may|our|their|"
    r"this|that|with|from|they|were|been|will|would|using|based|given|"
    r"shown|found|used|made|taken|seen|said|proposed|defined|described|"
    r"obtained|derived|computed|method|system|model|result|paper|figure|"
    r"table|section|problem|approach|algorithm|scheme|strategy|technique|"
    r"performance|simulation|experiment|analysis|scenario|respectively|"
    r"therefore|however|moreover|furthermore|denotes|represents|indicates|"
    r"corresponds|follows|satisfies|line|lines|where|which|each|such)\b",
    re.IGNORECASE,
)

KNOWN_LATEX_CMDS = {
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma",
    "tau", "upsilon", "phi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Phi", "Psi", "Omega",
    "varepsilon", "vartheta", "varpi", "varrho", "varsigma", "varphi",
    "leq", "geq", "neq", "approx", "equiv", "propto", "sim", "simeq",
    "triangleq", "doteq", "mapsto", "implies", "iff", "colon",
    "times", "cdot", "pm", "mp", "div", "ast", "star", "circ",
    "oplus", "ominus", "otimes", "odot", "oslash",
    "leftarrow", "rightarrow", "uparrow", "downarrow",
    "Leftarrow", "Rightarrow", "Uparrow", "Downarrow",
    "leftrightarrow", "Leftrightarrow",
    "sum", "prod", "coprod", "int", "iint", "iiint", "oint",
    "bigcap", "bigcup", "bigwedge", "bigvee",
    "sin", "cos", "tan", "cot", "sec", "csc",
    "arcsin", "arccos", "arctan",
    "log", "ln", "exp", "lim", "det", "gcd", "lcm",
    "min", "max", "argmin", "argmax", "sup", "inf",
    "dim", "ker", "deg", "arg", "mod",
    "frac", "dfrac", "tfrac", "sqrt", "binom",
    "mathbf", "mathcal", "mathbb", "boldsymbol", "mathit",
    "mathrm", "mathsf", "mathtt", "text", "textrm",
    "partial", "nabla", "infty", "emptyset", "forall", "exists",
    "in", "notin", "subset", "supset", "subseteq", "supseteq",
    "cap", "cup", "wedge", "vee", "langle", "rangle",
    "Pr", "mathbb{E}",
    "left", "right", "big", "Big", "bigg", "Bigg",
    "hat", "tilde", "bar", "vec", "dot", "ddot",
    "begin", "end", "tag", "label", "notag",
    "quad", "qquad", "forall", "exists", "ni",
    "mid", "parallel", "bot", "top", "triangle",
    "Box", "square", "diamond", "clubsuit", "spadesuit",
    "heartsuit", "diamondsuit",
    "ell", "wp", "aleph", "hbar", "imath", "jmath",
    "nabla", "triangleleft", "triangleright",
    "setminus", "smallsetminus",
    "lfloor", "rfloor", "lceil", "rceil",
    "textstyle", "displaystyle", "scriptstyle", "scriptscriptstyle",
}

VALID_CMD_RE = re.compile(r"\\([a-zA-Z]+|\{E\})")
SUB_SUP_RE = re.compile(r"[_^]")


def validate_braces(latex: str) -> list[str]:
    errors = []
    depth, i = 0, 0
    while i < len(latex):
        if latex[i] == '\\' and i + 1 < len(latex):
            i += 2; continue
        if latex[i] == '{': depth += 1
        elif latex[i] == '}': depth -= 1
        if depth < 0:
            errors.append(f"Unexpected closing brace at pos {i}")
            depth = 0
        i += 1
    if depth > 0:
        errors.append(f"Unclosed braces: {depth} missing")
    return errors


def validate_commands(latex: str) -> list[str]:
    errors = []
    for m in VALID_CMD_RE.finditer(latex):
        cmd = m.group(1)
        if cmd in KNOWN_LATEX_CMDS or len(cmd) == 1:
            continue
        errors.append(f"Suspicious LaTeX command: \\{cmd}")
    return errors


def validate_garbled(latex: str) -> list[str]:
    """Detect garbled token soup from broken subscript/superscript spans."""
    # If formula has explicit _ or ^ notation, it's structurally sound —
    # short tokens are part of well-formed subscripts/superscripts
    if SUB_SUP_RE.search(latex):
        return []
    errors = []
    cleaned = re.sub(r"\\[a-zA-Z]+(\{[^}]*\})*", " ", latex)
    cleaned = re.sub(r"\\[^a-zA-Z]", " ", cleaned)
    cleaned = re.sub(r'[\[\]{}()|,.;:+\-*/=<>≤≥≠≈≡∈⊂⊆∪∩∫∑∏∇∂⋅×±−]', ' ', cleaned)
    tokens = cleaned.split()
    current_run = []
    for t in tokens:
        t_clean = re.sub(r'[^a-zA-Z]', '', t)
        if len(t_clean) == 0:
            continue
        if 1 <= len(t_clean) <= 2:
            current_run.append(t_clean)
        else:
            if len(current_run) >= 3:
                errors.append(
                    f"Garbled token chain: '{' '.join(current_run)}' "
                    f"— likely broken subscript/superscript spans"
                )
            current_run = []
    if len(current_run) >= 3:
        errors.append(
            f"Garbled token chain: '{' '.join(current_run)}' "
            f"— likely broken subscript/superscript spans"
        )
    return errors


def validate_english_prose(latex: str) -> list[str]:
    errors = []
    for m in SUSPECT_ENGLISH_RE.finditer(latex):
        word = m.group(0)
        start = max(0, m.start() - 3)
        end = min(len(latex), m.end() + 3)
        ctx = latex[start:end]
        if re.search(r'[_^{}\\]', ctx):
            continue
        errors.append(f"English word in formula: '{word}' — likely prose contamination")
    return errors


def validate_naked_operators(latex: str) -> list[str]:
    errors = []
    cleaned = re.sub(r"\\[a-zA-Z]+(\{[^}]*\})*", " ", latex)
    ops = re.findall(r'[=+\-*/<>≤≥≠≈≡∈⊂⊆∪∩]', cleaned)
    alpha_chars = len(re.findall(r'[a-zA-Z]', cleaned))
    digit_chars = len(re.findall(r'\d', cleaned))
    if ops and alpha_chars + digit_chars < 2 and len(ops) == 1:
        errors.append(
            f"Naked operator with insufficient context: "
            f"ops={ops}, alpha={alpha_chars}"
        )
    return errors


def validate_sub_superscript(latex: str) -> list[str]:
    if SUB_SUP_RE.search(latex):
        return []
    cleaned = re.sub(r"\\[a-zA-Z]+(\{[^}]*\})*", " ", latex)
    cleaned = re.sub(r'[\[\]{}()|,.;:+\-*/=<>≤≥≠≈≡∈⊂⊆∪∩∫∑∏∇∂⋅×±−]', ' ', cleaned)
    tokens = [t for t in cleaned.split() if t]
    short_tokens = [t for t in tokens
                    if 1 <= len(re.sub(r'[^a-zA-Z0-9]', '', t)) <= 2]
    if len(short_tokens) >= 2 and len(tokens) >= 3:
        return [
            f"Possible missing subscript/superscript: "
            f"{len(short_tokens)} short tokens ({short_tokens[:5]}) "
            f"without _ or ^ notation"
        ]
    return []


def validate_equation_numbering(formulas: list[dict]) -> list[str]:
    errors = []
    numbers = []
    for f in formulas:
        eq_num = f.get("equation_number")
        if eq_num:
            try:
                base = re.sub(r'[a-z]', '', eq_num)
                numbers.append(int(base))
            except (ValueError, TypeError):
                pass
    if not numbers:
        return errors
    numbers.sort()
    expected = set(range(min(numbers), max(numbers) + 1))
    missing = expected - set(numbers)
    if missing:
        errors.append(
            f"Equation numbering gaps: missing {sorted(missing)} — "
            f"may indicate missed formulas"
        )
    return errors


def validate_formula(formula: dict, index: int) -> dict[str, Any]:
    latex = formula.get("latex", "")
    all_errors = []
    all_errors.extend(validate_braces(latex))
    all_errors.extend(validate_commands(latex))
    all_errors.extend(validate_garbled(latex))
    all_errors.extend(validate_english_prose(latex))
    all_errors.extend(validate_naked_operators(latex))
    all_errors.extend(validate_sub_superscript(latex))
    return {
        "index": index + 1,
        "equation_number": formula.get("equation_number"),
        "latex": latex,
        "passed": len(all_errors) == 0,
        "errors": all_errors,
    }


def validate_all(formulas: list[dict], stats_only: bool = False) -> dict[str, Any]:
    results = [validate_formula(f, i) for i, f in enumerate(formulas)]
    coverage_errors = validate_equation_numbering(formulas)
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    return {
        "total_formulas": total,
        "passed": passed,
        "failed": total - passed,
        "coverage_errors": coverage_errors,
        "formulas": results if not stats_only else [],
    }


def load_formulas(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("formulas", [])
    return []


def print_report(report: dict, verbose: bool = True) -> None:
    sep = "=" * 60
    print(f"\n{sep}\nFormula Validation Report\n{sep}")
    print(f"Total: {report['total_formulas']}  "
          f"Passed: {report['passed']}  "
          f"Failed: {report['failed']}")
    print(f"Pass rate: {report['passed'] / max(1, report['total_formulas']) * 100:.1f}%")

    if report["coverage_errors"]:
        print(f"\n── Coverage Issues ──")
        for e in report["coverage_errors"]:
            print(f"  !  {e}")

    if verbose and report.get("formulas"):
        failed = [f for f in report["formulas"] if not f["passed"]]
        if failed:
            print(f"\n── Failed Formulas ({len(failed)}) ──")
            for f in failed:
                eq_str = f" (#{f.get('equation_number')})" if f.get("equation_number") else ""
                print(f"\n  Formula #{f['index']}{eq_str}")
                print(f"  LaTeX: {f['latex'][:120]}")
                for e in f["errors"]:
                    print(f"  x  {e}")

    print(f"\n{sep}")
    if report["failed"] > 0:
        print("VALIDATION FAILED — fix errors above and re-run")
        print(sep)
    else:
        print("VALIDATION PASSED — all formulas are valid")
        print(sep)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Validate extracted LaTeX formulas for OCR errors"
    )
    parser.add_argument("input", nargs="?", help="JSON file containing formulas")
    parser.add_argument("--stdin", action="store_true", help="Read formulas from stdin")
    parser.add_argument("--stats-only", action="store_true", help="Summary statistics only")
    parser.add_argument("--json", action="store_true", help="Output report as JSON")

    args = parser.parse_args()

    if args.stdin:
        formulas = json.loads(sys.stdin.read())
        if isinstance(formulas, dict):
            formulas = formulas.get("formulas", [])
    elif args.input:
        formulas = load_formulas(args.input)
    else:
        parser.print_help()
        sys.exit(1)

    report = validate_all(formulas, stats_only=args.stats_only)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report, verbose=not args.stats_only)

    sys.exit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
