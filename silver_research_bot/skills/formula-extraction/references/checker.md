# Coverage Checker — Detailed Reference

## Core Task
Re-scan the PDF to verify zero missed formulas. Compare detected vs. expected count, find gaps, produce coverage report.

## Check Procedure

### 1. Count Equation Numbers
- Scan ALL pages for (N), (Na), (Nb) patterns
- Count unique equation numbers: (1)-(42) means 42 expected
- Compare: expected total vs. Stage 1+2 extracted total
- If expected > extracted: find missing formulas by scanning pages near the gap

### 2. Check Sub-Numbering
- Look for (15a), (15b), (15c) patterns
- Common issue: (15a) extracted but (15b) missed
- Each sub-number is a separate formula to extract

### 3. Check Appendix Sections
- Search for "Appendix", "Appendices", "Supplementary"
- Scan appendix pages for any math content
- Appendix numbering may differ: (A.1), (B.2), etc.

### 4. Check Algorithm Blocks
- Look for "Algorithm", "Procedure", pseudo-code environments
- Math in algorithms: assignment (:=), conditionals, loop bounds, return expressions
- Each math-containing line in an algorithm is a formula target

### 5. Check Definition/Theorem Environments
- Search for "Definition", "Theorem", "Lemma", "Corollary", "Proposition", "Proof"
- Mathematical expressions within these are often missed by region detectors

### 6. Check Edge Cases
- Figure captions with formula references
- Table cells with math content
- Footnotes with math notation
- Margin notes
- Continued equations (split across pages)
- Inline formulas in dense technical paragraphs

## Coverage Report Format
```
Coverage Report
  PDF pages: 10
  Equation numbers found: (1)-(42)
  Stage 1 regions detected: 40
  Stage 4 additional found: 2
    - Formula A.1 (page 12, appendix): Lyapunov stability condition
    - Formula (28b) (page 7): sub-equation of (28)
  Total extracted: 42
  Coverage: 100%
  Risk flags: 1
    - Formula (38): partially obscured — may need manual correction
```

## Verification Thresholds
- Coverage >= 98%: PASS — note minor gaps
- Coverage 90-97%: WARN — flag specific missing sections
- Coverage < 90%: FAIL — re-run Stage 1 with wider parameters

## Gap Resolution
For each gap found:
1. Navigate to the page before/after the nearby extracted formulas
2. Scan visually for math content
3. Extract region bbox manually if needed
4. Send back to Stage 2 OCR
5. Add to final formula list with note "Stage 4 recovery"
