"""Verify Bug 2-1 fix: _COMPLETE_FORMULA_RE with BS+BS concatenation"""
import re
import importlib.util
import sys
import os

# Clear any cached .pyc
pyc = "silver_research_bot/paper_analyzer/__pycache__/extractor.cpython-39.pyc"
if os.path.exists(pyc):
    os.remove(pyc)

BS = chr(92)

spec = importlib.util.spec_from_file_location(
    "extractor", "silver_research_bot/paper_analyzer/extractor.py"
)
m = importlib.util.module_from_spec(spec)
sys.modules["silver_research_bot.paper_analyzer.extractor"] = m
spec.loader.exec_module(m)

cre = m._COMPLETE_FORMULA_RE

print("=== _COMPLETE_FORMULA_RE tests ===")
tests = [
    (BS + "frac{a}{b}", "frac"),
    (BS + "sin(x)", "sin"),
    (BS + "sum_{i=1}^n x_i", "sum"),
    (BS + "int_0^\\infty", "int"),
    (BS + "partial f", "partial"),
    (BS + "log P(x)", "log"),
    (BS + "mathbb{E}[X]", "mathbb"),
    (BS + "leq 5", "leq"),
    (BS + "times 3", "times"),
    (BS + "begin{aligned}", "begin"),
    (BS + "min_x f(x)", "min"),
    (BS + "nabla f", "nabla"),
    (BS + "cdot 3", "cdot"),
    (BS + "neq 0", "neq"),
]

all_ok = True
for text, label in tests:
    result = bool(cre.search(text))
    status = "OK" if result else "FAIL"
    if not result:
        all_ok = False
    print(f"  {status}: {label:12s} match={result}")

print()
print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
