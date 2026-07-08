# Formula OCR — Detailed Reference

## Core Task
Convert formula region images to standard LaTeX. Each region is a high-res PNG (200 DPI) from the PDF at the specified bbox.

## LaTeX Standards

### MUST USE:
- Fractions: \frac{num}{den} — NEVER a / b
- Subscripts: x_i, x_{i,j}, x_{n}^{t} — braces for multi-char
- Greek: \alpha \beta \gamma \delta \epsilon \theta \lambda \mu \nu \pi \rho \sigma \tau \phi \chi \psi \omega
- Greek variants: \varepsilon \vartheta \varpi \varrho \varphi
- Uppercase Greek: \Gamma \Delta \Theta \Lambda \Xi \Pi \Sigma \Phi \Psi \Omega
- Math fonts: \mathbf{x} (bold), \mathbb{R} (blackboard), \mathcal{F} (calligraphic), \boldsymbol{\theta}
- Operators: \sum \prod \int \iint \iiint \oint \partial \nabla
- Relations: \leq \geq \neq \approx \equiv \propto \sim \in \subset \subseteq \notin
- Arrows: \rightarrow \Rightarrow \leftarrow \mapsto \implies \iff
- Functions: \sin \cos \tan \log \ln \exp \lim \min \max \argmin \argmax \sup \inf \det
- Brackets: \left( \right) \left[ \right] \left\{ \right\}
- Matrices: \begin{bmatrix} a & b \\ c & d \end{bmatrix}
- Cases: \begin{cases} x & \text{if } y \\ z & \text{otherwise} \end{cases}
- Vectors: \vec{x}, \overrightarrow{AB}
- Accents: \hat{x} \tilde{x} \bar{x} \dot{x} \ddot{x}
- Spacing: \quad \qquad \; \,
- Text in math: \text{subject to}, \text{s.t.}
- Equation numbers: \tag{5} or \tag{15a}

### MUST NOT:
- Use Unicode math symbols (write \alpha not alpha)
- Use / for fractions (write \frac{}{})
- Simplify or abbreviate LaTeX
- Omit symbols even if partially obscured
- Convert display to inline or vice versa
- Add commentary in the LaTeX output

## Vision LLM Prompt
```
You are a LaTeX OCR expert for academic papers.
Convert this formula image to standard LaTeX:
- Use \frac{}{} for fractions, \sum \int for operators
- Use _{...} for subscripts, ^{...} for superscripts
- Use \mathbf{}, \mathbb{}, \mathcal{} for special fonts
- Use \begin{bmatrix} for matrices, \begin{cases} for piecewise
- Preserve ALL symbols exactly — no simplification
- Include equation number as \tag{N}
- Output ONLY the LaTeX code
```

## Image Extraction
```python
import fitz
doc = fitz.open(pdf_path)
page = doc[page_num - 1]
pix = page.get_pixmap(clip=bbox, dpi=200)
pix.save(f"formula_{index}.png")
```

## Output JSON
```json
[{ "index": 1, "page": 3, "eq_number": "5",
   "latex": "\\varpi_t = \\frac{\\sum D_{U,n}^t \\varpi_n^t}{\\sum D_{U,n}^t}",
   "bbox": [120.5, 340.2, 480.8, 380.6],
   "type": "display", "context": "aggregate utility weight" }]
```
