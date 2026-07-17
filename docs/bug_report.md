# Bug Report: PDF Formula Extraction Pipeline

> 分析日期: 2026-07-08 | Python 3.11+ target | 基于 working tree

---

## 1. Formula Detector — 是否读取了正文

### Bug 1-1: `_looks_like_formula` 将 "min-max" 类英文复合词误判为公式

| 项目 | 内容 |
|------|------|
| **所在文件** | `silver_research_bot/paper_analyzer/extractor.py` |
| **所在函数** | `_looks_like_formula` (line 146-191) |
| **原因** | FORMULA_MARKERS[0] 匹配 "min" → score+1; FORMULA_MARKERS[3] 匹配 "-" → score+1。score=2 ≥ 阈值 → 返回 True。后续 `_is_valid_formula` 的 math_signals=2 绕过了 line 233 的英文复合词拦截（该拦截要求 math_signals==1）。 |
| **影响** | "min-max"、"AoI-Aware"、"state-of-the-art" 等英文复合词被写入 `extracted.json` formulas[]，污染公式计数和后续 LLM 解读输入。 |
| **修复建议** | 在 `_looks_like_formula` 中增加连字符专项检查: 若连字符两侧均为 ≥3 字母的纯英文词，拒绝。或提升非希腊/非 LaTeX 的 score 阈值为 ≥3。 |

---

### Bug 1-2: 非数学字体 Unicode 数学符号不转换为 LaTeX

| 项目 | 内容 |
|------|------|
| **所在文件** | `silver_research_bot/paper_analyzer/extractor.py` |
| **所在函数** | `extract_pdf_text` span loop (line 446-467) |
| **原因** | `_convert_formula_text()` 仅在 `_is_math_font(font) or _looks_like_formula(text)` 为 True 时调用。若 ℝ(U+211D) 在 Times 字体中且不触发 `_looks_like_formula`（单个 ℝ 不匹配任何 FORMULA_MARKER），则保持为 Unicode 原样输出到 full_text，不转为 `\mathbb{R}`。 |
| **影响** | full_text 包含原始 Unicode 数学字符。LLM 可能不识别的 Unicode 字符被丢弃或误译。 |
| **修复建议** | 在所有 span 上执行 `_convert_formula_text()`，或将 code-point→LaTeX 的映射独立于 math-font/math-looks-like 检测。任何命中 SYMBOL_TO_LATEX 的字符都应无条件转换。 |

---

## 2. Formula Filter — 是否误删所有公式

### Bug 2-1 [致命]: `_COMPLETE_FORMULA_RE` 全部 LaTeX 命令匹配因正则转义错误而失效

| 项目 | 内容 |
|------|------|
| **所在文件** | `silver_research_bot/paper_analyzer/extractor.py` |
| **所在函数** | `_COMPLETE_FORMULA_RE` (line 116-143)，被 `_is_valid_formula` (line 194) 调用 |
| **严重程度** | **致命** |
| **原因** | Python raw string `r"\\frac"` → Python 字符串 `\frac`（`\\` 在 raw string 中仍被解释为转义反斜杠 → 单字符 `\`）。`re.compile("\frac")` 中 `\f` = 换页符(0x0C)，非字面反斜杠+f。**已验证**：`re.compile(r'\\frac').search(chr(92)+'frac{a}{b}')` 返回 `None`。正确写法应为 `r"\\\\frac"`（4 个反斜杠 → Python `\\frac` → 正则 `\frac` = 字面反斜杠+f+r+a+c）。 |

所有含 `\` 的 LaTeX 命令均受影响：

| 源码 raw | Python | 正则解释 | 匹配 |
|-----------|--------|----------|------|
| `r"\\frac"` | `\frac` | `\f`(换页)+rac | 换页符+rac |
| `r"\\sum"` | `\sum` | `\s`(空白)+um | 空白+um |
| `r"\\neq"` | `\neq` | `\n`(换行)+eq | 换行+eq |
| `r"\\sin\\b"` | `\sin\b` | `\s`(空白)+in+`\b` | 空白+in+边界 |
| `r"\\log\\b"` | `\log\b` | `\l`(字面l)+og+`\b` | log+边界 |
| `r"\\int"` | `\int` | `\i`(字面i)+nt | int |
| `r"\\partial"` | `\partial` | `\p`(字面p)+artial | partial |
| `r"\\times"` | `\times` | `\t`(制表)+imes | 制表+imes |

CLAUDE.md v0.6.0 第 71-73 行**声称**此 Bug 已修复（`\\cmd → \\\\cmd`），但**实际代码未实施**——所有模式仍用双反斜杠而非四反斜杠。

| **影响** | `_is_valid_formula` 中 `has_latex = bool(_COMPLETE_FORMULA_RE.search(text))` 在遇到**仅含 LaTeX 命令、无 ASCII 运算符**的公式时为 False。以下公式被拒绝: `\frac{a}{b}` (无=号), `\sin(\theta)` (无运算符), `\sum_{i=1}^n`, `\min_x f(x)`, `\mathbb{E}[X]`。 |
| **修复建议** | 全部 `\\cmd` → `\\\\cmd`。CLAUDE.md v0.6.0 第 71-89 行的修复方案本身正确，只需实际应用到代码中。 |

---

### Bug 2-2: `_strip_fragment_cards` 用 PDF 碎片过滤器误杀 LLM 合法公式卡片

| 项目 | 内容 |
|------|------|
| **所在文件** | `silver_research_bot/paper_analyzer/formula_explainer.py` |
| **所在函数** | `_strip_fragment_cards` → `_is_fragment` (line 40-72) |
| **原因** | `_is_fragment` 调用 `_is_valid_formula`（为 PDF span 级碎片设计的过滤器）检查 LLM 输出的 `.fexpr`。结合 Bug 2-1，仅含 LaTeX 命令无运算符的完整公式（`\sin(x)`, `\mathbb{E}[X]`, `\log P(x)`）被判定为 fragment → 卡片被静默移除。 |
| **影响** | 公式解读页面缺少部分卡片。LLM 已生成解释却被后处理移除。 |
| **修复建议** | 将 `_is_fragment` 的检查改为: 仅拒绝空内容和纯英文字母序列（`[a-zA-Z\s]{1,10}`），不拒绝含 `\` 的 LaTeX 命令。 |

---

### Bug 2-3: `block_formulas`（→ formulas[]）与 `line_text`（→ full_text）为两条独立通路

| 项目 | 内容 |
|------|------|
| **所在文件** | `silver_research_bot/paper_analyzer/extractor.py` |
| **所在函数** | `extract_pdf_text` (line 440-499) |
| **原因** | |

| 通路 | 组装方式 | 合并 | 用途 |
|------|---------|------|------|
| `line_text` | span 逐行，非数学追加，数学 `$...$` 包裹 | `_merge_nearby_dollar_blocks` 逐行合并 | → full_text → 翻译 |
| `block_formulas` | 仅数学 span，非数学中断拼接 | 无合并 | → formulas[] → extracted.json |

两通路无数据交换。PDF 中 `x_t^n` 由 3 个字体大小不同的数学 span 组成，若被非数学 span（空格）分隔 → `block_formulas = ["x", "t", "n"]` → 每个 ≤3 字符 → 被 line 494 `len > 3` 拒绝 → formulas[] 无此条目。但 `line_text` 通路中 `_merge_nearby_dollar_blocks` 正确合并为 `$x t n$` → full_text 正确。

| **影响** | **中文论文路径完全依赖 `extracted.json` formulas[] 作为公式数据源**。每个公式碎片被 `len > 3` 截断后，中文论文 formula_count → 0 → 触发 `_explain_from_text()` 回退路径，失去结构化公式。 |
| **修复建议** | 在 line 491-499 前，对合并后的 `block_text` 重新提取 `$...$` 包裹的内容作为 formulas[]，取代 span 级碎片的 `block_formulas`。 |

---

## 3. OCR / PDF Extraction — 是否输出普通文本而非 LaTeX

### Bug 3-1 [致命]: 上下标 `_` / `^` 在 PDF 提取中完全丢失

| 项目 | 内容 |
|------|------|
| **所在文件** | `silver_research_bot/paper_analyzer/extractor.py` |
| **所在函数** | `extract_pdf_text` span loop (line 446-471) |
| **严重程度** | **致命** |
| **原因** | 学术 PDF 中上下标通过字体大小（6pt vs 10pt）+ 基线偏移（y 坐标差异）**纯视觉呈现**，`_` 和 `^` 不编码在 PDF 文本中。PyMuPDF 正确报告 fontSize 差异但 extractor **完全忽略**。提取结果: `x` + `t`(下标) + `n`(上标) → `$x t n$`（合并后），无 `_`/`^`。Bug 2-3 使 `block_formulas` 甚至得不到合并结果 → formulas[] 为空。 |
| **影响** | `x_t^n → x t n` 的直接根因。所有 PDF 来源的公式上下标结构永久丢失。LLM 翻译是唯一可能部分恢复的途径（上下文推断），但不可靠。 |
| **修复建议** | 在 span 循环中检测相邻数学 span 的 fontSize 变化: 若比例 ≥ 1.3 且 y 偏移 < 0 → 插入 `_`；若比例 ≥ 1.3 且 y 偏移 > 0 → 插入 `^`。同时修复 Bug 2-3 使恢复的结构进入 formulas[]。 |

---

### Bug 3-2: Σ(大 sigma 字母) / ∑(求和运算符) code point 语义歧义

| 项目 | 内容 |
|------|------|
| **所在文件** | `silver_research_bot/paper_analyzer/extractor.py` |
| **所在函数** | `_convert_formula_text` (line 254-261) → `SYMBOL_TO_LATEX` (line 12-65) |
| **原因** | `0x03A3`(Σ) → `\Sigma`(大写字母), `0x2211`(∑) → `\sum`(运算符)。但 PDF 制作工具常混用 U+03A3 编码求和运算符。LaTeX 中 `\Sigma` 和 `\sum` 渲染不同（后者为可伸缩大运算符）。 |
| **影响** | `$$\Sigma_{i=1}^n$$` 而非 `$$\sum_{i=1}^n$$` → 求和号显示为不伸缩的普通大写字母。 |
| **修复建议** | 上下文启发式: 若 Σ/Π 后紧跟 `_{` 或 `^{` → 映射为 `\sum`/`\prod`；孤立使用 → `\Sigma`/`\Pi`。 |

---

### Bug 3-3: `_merge_nearby_dollar_blocks` 仅逐行调用，跨行碎片永不合并

| 项目 | 内容 |
|------|------|
| **所在文件** | `silver_research_bot/paper_analyzer/extractor.py` |
| **所在函数** | `extract_pdf_text` (line 472, 475) |
| **原因** | Line 472 `_merge_nearby_dollar_blocks(line_text)` 逐行调用。Line 475 `" ".join(block_lines)` 连接后不再调用合并。多行 display math 第一行 `$x_t^n$` 与第二行 `$= f(y)$` 永不合并为 `$x_t^n = f(y)$`。 |
| **影响** | 多行公式在 full_text 中保持碎片化，LLM 翻译时逐个处理碎片。 |
| **修复建议** | 在 line 475 之后增加 `block_text = _merge_nearby_dollar_blocks(block_text)` 调用。 |

---

## 4. Formula Parser — 是否恢复上下标

### Bug 4-1 [致命]: 不存在上下标结构恢复机制

| 项目 | 内容 |
|------|------|
| **所在文件** | 整个 `extractor.py` + `translator.py`（缺失功能） |
| **所在函数** | N/A — 功能缺失 |
| **严重程度** | **致命** |
| **原因** | Bug 3-1 丢失 `_`/`^` 后，流水线中**没有任何模块**从字体大小/基线偏移恢复这些符号。`_validate_formulas` (translator.py:252) 的 `_fix_subs` 仅给**已有的** `_text` 加花括号，不创建新的 `_`/`^`。 |
| **影响** | `x_t^n → x t n` 的全流程根因。只要 Bug 3-1 未修复，就没有机制能恢复上下标。 |
| **修复建议** | 与 Bug 3-1 合并修复: 在 PDF 提取 span 循环中利用 fontSize + 基线偏移信息插入 `_`/`^`。此修复同时解决 Bug 3-1 和 Bug 4-1。 |

---

### Bug 4-2: `\boldsymbol → \mathbf` 替换破坏粗体希腊字母

| 项目 | 内容 |
|------|------|
| **所在文件** | `silver_research_bot/paper_analyzer/translator.py` |
| **所在函数** | `_validate_formulas` (line 244) |
| **原因** | `\boldsymbol{\alpha}` 对希腊字母有效（需要 amsmath），`\mathbf{\alpha}` 对希腊字母无效（仅处理 A-Z, a-z）。替换后粗体希腊字母静默变为非粗体。 |
| **影响** | 翻译后粗体希腊字母渲染为普通字重。 |
| **修复建议** | 移除替换，或仅在 content 仅含 `[A-Za-z0-9]` 时替换。 |

---

## 5. Markdown Export — 是否把正文一起写入

### Bug 5-1 [致命]: `renderMd` 全局 `<>` 转义破坏 `$...$` 内 LaTeX 尖括号

| 项目 | 内容 |
|------|------|
| **所在文件** | `web/src/App.vue` |
| **所在函数** | `renderMd` (line 309) |
| **严重程度** | **致命** |
| **原因** | Line 309: `.replace(/</g,'&lt;').replace(/>/g,'&gt;')` 在**任何 `$...$` 保护前**全局执行。LaTeX 尖括号 `\langle`, `x < y`, `\left<` 被破坏: `$\langle x \rangle$` → `$&lt;langle x &gt;rangle$` → MathJax 报错。 |
| **影响** | **翻译 Tab** 中所有含 `<`/`>` 的行内公式无法渲染（用户直接可见）。 |
| **修复建议** | 在 `<>` 转义前插入占位符: ① `$...$` / `$$...$$` → `◈MATH_N◈`, ② 执行 `<>` 转义, ③ 还原占位符。 |

---

### Bug 5-2: `_merge_equation_fragments` 将中文正文合并入公式

| 项目 | 内容 |
|------|------|
| **所在文件** | `silver_research_bot/paper_analyzer/formula_explainer.py` |
| **所在函数** | `_merge_equation_fragments` → `_merge_line` (line 105-126) |
| **原因** | LHS 捕获 `[^$\n]{0,40}?`: 翻译后文本 `根据公式，$\varpi$ t = $\frac{a}{b}$` 中 "根据公式，" 被捕获 → 合并为 `$根据公式， \varpi t = \frac{a}{b}$` — 中文正文混入 LaTeX。 |
| **影响** | 中文正文混入公式。若无英文黑名单词且含 `\frac`，可能通过过滤进入 LLM 解读。 |
| **修复建议** | LHS context 清洗: `re.sub(r'[^\x00-\x7F\\_{}^$]', '', group(1))` 移除非 ASCII 字符后再合并。 |

---

### Bug 5-3: 英文散文黑名单误杀含 `\text{}` 的合法公式

| 项目 | 内容 |
|------|------|
| **所在文件** | `silver_research_bot/paper_analyzer/formula_explainer.py` |
| **所在函数** | `extract_formulas_from_translation` (line 218-219) |
| **原因** | 72 词黑名单 `\b(the|and|for|...)\b` 对全部 LaTeX 源码执行。`\min_x \sum_i f_i(x) \ \text{subject to} \ x \in \mathcal{X}` 含 "subject" "to" → 被拒绝。 |
| **影响** | 含 `\text{...}` 的优化问题公式被丢弃（常见于约束条件表述）。 |
| **修复建议** | 黑名单检查前剥离 `\text{...}` 块内容。 |

---

### Bug 5-4: `renderFormula` 缺失 `.fmean` LaTeX 包裹 + 缺失 `sanitizeLatex`

| 项目 | 内容 |
|------|------|
| **所在文件** | `web/src/App.vue` |
| **所在函数** | `renderFormula` (line 226) |
| **原因** | 仅处理 `.fexpr` 包裹 `$$...$$`，`.fmean` 不做处理。CLAUDE.md v0.6.1 声称的 `sanitizeLatex` (`#`→`\#`, `%`→`\%`) **代码中不存在**。 |
| **影响** | `.fmean` 中未 `$` 包裹的变量显示为纯文本。`#`/`%` 导致 MathJax 报错。 |
| **修复建议** | 实现 CLAUDE.md v0.6.0-v0.6.1 描述的功能: `.fmean` 正则包裹 + sanitizeLatex 特殊字符转义。 |

---

## 严重程度总览

| 级别 | ID | 简述 |
|------|-----|------|
| **致命** | Bug 2-1 | `_COMPLETE_FORMULA_RE` LaTeX 命令匹配全部失效（转义错误，声称已修复但未实施） |
| **致命** | Bug 3-1 | 上下标 `_`/`^` 丢失 — `x_t^n → x t n` |
| **致命** | Bug 4-1 | 缺失上下标结构恢复 — Bug 3-1 的全流程后果 |
| **致命** | Bug 5-1 | `renderMd` `<>` 转义破坏 `\langle`/`x < y` — MathJax 报错 |
| **高** | Bug 2-2 | `_strip_fragment_cards` 误杀 LLM 短公式卡片 |
| **高** | Bug 2-3 | `block_formulas` / `line_text` 双通路解耦 — 中文论文 formula_count → 0 |
| **中** | Bug 1-1 | 英文复合词误判为公式 |
| **中** | Bug 1-2 | 非数学字体 Unicode 不转 LaTeX |
| **中** | Bug 3-2 | Σ/∑ code point 语义歧义 |
| **中** | Bug 3-3 | 跨行公式碎片不合并 |
| **中** | Bug 5-2 | 中文正文混入公式 |
| **中** | Bug 5-3 | 英文黑名单误杀 `\text{}` |
| **低** | Bug 4-2 | `\boldsymbol→\mathbf` 破坏粗体希腊字母 |
| **低** | Bug 5-4 | `.fmean` 缺失 LaTeX 包裹 + 缺失 sanitizeLatex |
