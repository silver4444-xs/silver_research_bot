---
name: formula-extraction
description: Extract ALL mathematical formulas from academic PDFs with 4-stage pipeline (Formula Detector → Formula OCR → Python Validator → Coverage Checker). Use when the user asks to extract, identify, recognize, or restore formulas from PDF papers. Supports display equations, inline equations, formulas in algorithms, definitions, optimization constraints, appendices, figures, and tables. Outputs Markdown + LaTeX with equation numbers, positions, and coverage statistics. Triggers on: "extract formulas", "公式提取", "formula extraction", "OCR formulas", "公式识别", "公式恢复", "math formula recovery".
---

# Formula Extraction — 4-Stage Pipeline

Extract ALL formulas from academic PDFs with a rigorous 4-stage pipeline that ensures completeness, correctness, and verifiable coverage.

## Pipeline Overview

```
PDF → [1. Formula Detector] → [2. Formula OCR] → [3. Python Validator] → [4. Coverage Checker] → Markdown
```

## Stage 1: Formula Detector (Sub-Agent)

**Goal**: Locate ALL formula regions in the PDF. Do NOT output LaTeX.

### Instructions

Dispatch a sub-agent with the detector reference. The agent MUST:

1. Use PyMuPDF (`fitz`) to open the PDF
2. For each page, scan for formula regions using:
   - **Math fonts**: CMMI, CMSY, CMEX, Math, Cambria Math, XITS, STIX
   - **Math Unicode**: Greek (α-ω, Α-Ω), operators (∈, ≤, Σ, ∫, ∇, ∂), arrows (→, ⇒)
   - **LaTeX commands**: `\frac`, `\sum`, `\int`, `\mathbb`, \mathbf, etc.
   - **Equation numbers**: (1), (15a), (15b), (5.3) — capture separately
   - **Math formatting**: superscript/subscript font sizes, centered blocks
3. For each detected region, record:
   - `page`: page number (1-based)
   - `bbox`: [x0, y0, x1, y1] bounding box coordinates
   - `eq_number`: detected equation number (e.g., "5", "15a"), or null
   - `context`: surrounding text (~60 chars before/after)
   - `type`: "display" | "inline" | "definition" | "algorithm" | "constraint"
   - `confidence`: "high" | "medium" | "low"

### Output Format
```json
{
  "paper_title": "...",
  "total_pages": 10,
  "total_regions": 45,
  "regions": [
    {
      "index": 1,
      "page": 3,
      "eq_number": "5",
      "bbox": [120.5, 340.2, 480.8, 380.6],
      "type": "display",
      "context": "... defining the utility function ...",
      "confidence": "high"
    }
  ]
}
```

### Scanning Checklist
- [ ] Main body text — display equations
- [ ] Main body text — inline math ($...$)
- [ ] Section "System Model" / "系统模型" — all formulas
- [ ] Section "Problem Formulation" / "问题表述" — optimization objectives & constraints
- [ ] Section "Algorithm" / "算法" — pseudo-code formulas
- [ ] Appendix / "附录" — all formulas
- [ ] Figure captions — any formula references
- [ ] Table cells — any formula content
- [ ] Footnotes / margin notes
- [ ] Numbered sub-equations: (15a), (15b), (15c)

## Stage 2: Formula OCR (Sub-Agent)

**Goal**: Convert each formula region image to standard LaTeX.

### Instructions

Dispatch a sub-agent for each batch of formula regions (batch by page or by groups of 10–15). The agent MUST:

1. For each region, extract the high-resolution image from the PDF using:
   ```python
   import fitz; doc = fitz.open(pdf_path)
   page = doc[page_num - 1]
   pix = page.get_pixmap(clip=bbox, dpi=200)
   pix.save(f"formula_{index}.png")
   ```
2. Use vision-capable LLM to read the formula image and output LaTeX
3. Follow these LaTeX rules:
   - Use `$$...$$` for display formulas, `$...$` for inline
   - Greek: `\alpha, \beta, \gamma, ...` (NOT Unicode α, β, γ)
   - Operators: `\sum, \prod, \int, \partial, \nabla`
   - Subscripts/superscripts: `x_i, x^{2}, x_{i,j}` — use braces for multi-char
   - Fractions: `\frac{num}{den}` — NEVER use `/`
   - Bold: `\mathbf{x}`, blackboard: `\mathbb{R}`, calligraphic: `\mathcal{F}`
   - Vectors: `\vec{x}`, matrices: `\begin{bmatrix} ... \end{bmatrix}`
   - Piecewise: `\begin{cases} ... \end{cases}`
   - Limits: `\lim_{x \to \infty}`, expectations: `\mathbb{E}[X]`
   - Keep equation number as `\tag{N}` or `\tag{15a}`
   - NEVER simplify or shorten — restore ALL symbols

### Output Format
```json
{
  "formulas": [
    {
      "index": 1,
      "page": 3,
      "eq_number": "5",
      "latex": "\\varpi_t = \\frac{\\sum_{n=1}^{N} D_{U,n}^t \\varpi_n^t}{\\sum_{n=1}^{N} D_{U,n}^t}",
      "bbox": [120.5, 340.2, 480.8, 380.6],
      "type": "display",
      "context": "aggregate utility weight"
    }
  ]
}
```

## Stage 3: Python Validator

**Goal**: Run automated validation to detect OCR errors. Failures MUST be fixed before proceeding.

### Execute

```bash
python scripts/validate_formulas.py formulas.json
```

Or for JSON output:
```bash
python scripts/validate_formulas.py --json formulas.json
```

### Validator Checks

| Check | What it detects |
|-------|----------------|
| Garbled tokens | `N X`, `xt n`, `Gt t` — broken subscript/superscript chains |
| Missing sub/sup | Short token sequences without `_` or `^` notation |
| Unbalanced braces | Mismatched `{}`, `$$` |
| Invalid commands | Unknown LaTeX commands (typos) |
| English prose | "the", "and", "method" inside math mode |
| Naked operators | Bare `=` without variable context |
| Numbering gaps | Missing equation numbers suggesting coverage gaps |

### Validation Rules
- If validation FAILS: fix the identified formulas, then re-run Stage 2 OCR for those specific regions
- If validation PASSES: proceed to Stage 4
- Maximum 3 retry cycles; if still failing, flag formulas as `[存在识别风险]` and note the alternative

## Stage 4: Coverage Checker (Sub-Agent)

**Goal**: Re-scan the PDF to verify NO formulas were missed.

### Instructions

Dispatch a sub-agent that:

1. Reads the paper and checks:
   - Count of `(N)` equation numbers in PDF vs. extracted formulas — should match
   - Check for sub-numbering: (15a), (15b), (15c) — all sub-numbers extracted?
   - Check appendices: any formulas in Appendix sections?
   - Check algorithm pseudo-code: any math notation within algorithms?
   - Check definitions: formulas in Definition/Theorem/Lemma environments?

2. For each potential missed formula:
   - Record page number and context
   - Send back to Stage 2 OCR

3. Produce final coverage report:
```
Coverage Report
  Total expected:  42  (from equation numbering: (1)–(42))
  Stage 1 detected: 40
  Stage 4 found:    2  (appendix formulas)
  Total extracted:  42
  Coverage: 100%
```

## Final Output Format

Produce a Markdown file with ALL extracted formulas:

```markdown
# Formula Extraction Report — [Paper Title]

**PDF**: `paper.pdf`
**Total formulas**: 42
**Coverage**: 100%
**Detection method**: 4-stage pipeline (Detector → OCR → Validator → Coverage Check)

---

## Formula (1) — Page 2

$$
\mathcal{P}: \min_{\mathbf{x}} \sum_{n=1}^{N} c_n x_n
$$

**类型**: display | **位置**: page 2, bbox [120, 340, 480, 380]
**含义**: Optimization objective minimizing total cost

---

## Formula (5) — Page 3

$$
\varpi_t = \frac{\sum_{n=1}^{N} D_{U,n}^t \varpi_n^t}{\sum_{n=1}^{N} D_{U,n}^t} \tag{5}
$$

**类型**: display | **位置**: page 3, bbox [120, 340, 480, 380]
**含义**: Aggregate utility weight calculation

---

## Coverage Statistics

| Metric | Value |
|--------|-------|
| Total formulas | 42 |
| Display equations | 28 |
| Inline equations | 10 |
| Algorithm formulas | 2 |
| Appendix formulas | 2 |
| Sub-numbered (a/b/c) | 3 pairs |
| Recognition risk flags | 1 |

## Recognition Risk Notes

- Formula (38): `\mathcal{F}(x) = \int_{-\infty}^{\infty} ...`
  【存在识别风险】Integrand partially obscured in original PDF.
  Alternative: `\mathcal{F}(x) = \int_{0}^{\infty} f(t) e^{-ixt} dt`

## Validation Log

\```
Stage 3 Python Validator: 2 retry cycles
  - Formula (12): fixed garbled subscript chain
  - Formula (28): fixed missing \mathbb{} notation
Final: PASSED (42/42 formulas valid)
\```
```

## Critical Rules

1. **NEVER skip formulas** — if unsure, flag with `[存在识别风险]` rather than omit
2. **Keep equation numbers** — (1), (15a), (15b) — exactly as in the paper
3. **Use standard LaTeX** — `\frac` not `/`, `\begin{bmatrix}` not plain text
4. **One formula per section** — separate `---` divider between formulas
5. **Include positions** — page number and bbox for traceability
6. **Run validator until passing** — do not output formulas that fail OCR checks
