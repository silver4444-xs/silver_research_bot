# PDF 公式提取流水线完整分析

> 分析日期: 2026-07-08 | 基于当前 working tree (uncommitted)

---

## 流水线全景图

```
PDF 文件
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│ M1: PDF 文本提取 + 公式检测                                  │
│     extractor.py :: extract_pdf_text()                       │
│     工具: PyMuPDF (fitz) — 非 OCR, 直接从 PDF 字体/编码读取   │
│     输出: extracted.json {formulas[], full_text, figures...}  │
└─────────────────────────────────────────────────────────────┘
  │
  ├─ 英文论文 ──────────────────────────────────────────────┐
  │                                                         │
  ▼                                                         │
┌──────────────────────────────────────────────────────────┐│
│ M2: LLM 全文翻译 (公式→LaTeX Markdown 保护)               ││
│     translator.py :: translate_paper()                    ││
│     输入: extracted full_text (+ figures, tables)         ││
│     输出: translation.md (中文 + $...$/$$...$$ 公式)       ││
└──────────────────────────────────────────────────────────┘│
  │                                                         │
  ▼                                                         │
┌──────────────────────────────────────────────────────────┐│
│ M3: 翻译中公式提取 (display math only)                    ││
│     formula_explainer.py :: extract_formulas_from_translation()│
│     步骤: dollar_merge → equation_fragment_merge           ││
│           → promote_display_math → extract $$...$$         ││
│           → filter prose/English                           ││
│     输出: list[{latex, equation_number, context}]          ││
└──────────────────────────────────────────────────────────┘│
  │                                                         │
  ├─ 中文论文 / 回退路径 ───────────────────────────────────┘│
  │                                                         │
  ▼                                                         │
┌──────────────────────────────────────────────────────────┐│
│ M4: LLM 公式四级解读 (符号→数学→领域→关联)                 ││
│     formula_explainer.py :: explain_formulas()             ││
│     输入: formulas[] + full_text + provider                ││
│     输出: formula_explanations.md (HTML .frow 卡片)         ││
└──────────────────────────────────────────────────────────┘│
  │                                                         │
  ▼                                                         │
┌──────────────────────────────────────────────────────────┐│
│ M5: 前端渲染 (MathJax + 公式交互)                         ││
│     web/src/App.vue :: renderFormula() + retypeset()       ││
│     输入: formula_explanations.md HTML                     ││
│     输出: MathJax 渲染的 DOM + 点击查看公式详情              ││
└──────────────────────────────────────────────────────────┘
```

**关键架构决策**: 本项目不使用 OCR。公式提取完全依赖 PDF 内嵌字体/编码信息（通过 PyMuPDF 读取 span 级别的 font name + Unicode text）。

---

## M1: PDF 文本提取 + 公式检测

### ① 代码位置

`silver_research_bot/paper_analyzer/extractor.py`

**核心函数调用链**:
```
extract_pdf_text(pdf_path)
  ├── fitz.open() → doc[page].get_text("dict")["blocks"]
  ├── 逐 block 处理:
  │   ├── type==1 (image) → 提取图片 (doc.extract_image)
  │   └── type==0 (text)  → 逐 span:
  │       ├── _is_math_font(font)  → 数学字体检测
  │       ├── _looks_like_formula(text) → 内容模式检测 (FORMULA_MARKERS 7条规则)
  │       ├── _convert_formula_text(text) → Unicode→LaTeX (SYMBOL_TO_LATEX, 80+映射)
  │       └── _merge_nearby_dollar_blocks(line) → 合并碎片化 $...$ 块
  │   └── block 级:
  │       ├── _is_valid_formula(fm_text) → 二次过滤 (去噪)
  │       └── SECTION_PATTERNS → 章节检测
  ├── page.find_tables() → 表格提取
  └── _filter_metadata_lines(full_text) → 元数据清洗
```

### ② 职责

PDF 文本层的"粗暴扫描器"。从 PDF 物理排版的 block/line/span 层级中：
1. **检测候选公式区域**: 字体匹配 (math fonts) + 内容模式 (正则)
2. **Unicode→LaTeX 转换**: 将 PDF 中的 Unicode 数学符号映射为 LaTeX 命令
3. **碎片合并**: 将因 PDF 编码分散在多个 span 中的公式文本重新聚合为 `$...$` 块
4. **噪音过滤**: 排除英文散文、URL、页码等非公式内容

### ③ 输入/输出

| 项目 | 类型 | 说明 |
|------|------|------|
| **输入** | `pdf_path: str\|Path` | PDF 文件路径 |
| **输出** | `dict` | `{pages, sections, formulas, figures, tables, full_text, page_count, formula_count, figure_count, table_count}` |
| **formulas[]** | `list[dict]` | `{index, latex, context, page}` — 仅包含通过 `_is_valid_formula` 的项 |
| **持久化** | `extracted.json` | 写入 `workspace/papers/{paper_id}/` |

### ④ Bug 分析

#### B1. `_looks_like_formula` 阈值变更导致行内短公式全面漏检 (回归 Bug)

**代码位置**: `extractor.py:146-191`

**问题**: 旧版 committed 代码 `return score >= 1 or any(c in text for c in "∑∏∫∂∇")` — 任一标志即判定为公式。当前 working tree 将其替换为复杂多层逻辑:
- `score >= 2` → 直接返回 True (需要至少两种不同类型的数学标志)
- `has_greek` → 返回 True (希腊字母单独成立)
- `score == 1` → 分情况: 纯运算符字符串通过、连字符英文拒绝、单非连字符标志需 `len >= 2`

**影响**: 以下类型的行内公式片段在 span 级别会被**漏检**:
- 单个数字+单位: `10`, `100ms` (无数学标志)
- 单个变量+下标碎片: 若 span 只含 `t` 或 `n` (无任何 FORMULA_MARKER 匹配)，不会触发数学检测
- 单操作符 span: `=`, `+` 在 `_looks_like_formula` 中的行为取决于 `len(stripped) >= 2` — 单字符 `=` 被拒绝

**严重性**: 中等。大多数数学公式跨多个 span，部分 span 会在 `_merge_nearby_dollar_blocks` 中被合并。但遇到孤立数学 span (PDF 中仅此一个 span 含公式内容) 时会漏检。

#### B2. `_merge_nearby_dollar_blocks` 仅行内调用，跨行公式碎片永不合并 (结构性 Bug)

**代码位置**: `extractor.py:264-313` (函数定义) + `extractor.py:472` (调用点)

**问题**: 
```python
# Line 472 — 每行独立调用
merged = _merge_nearby_dollar_blocks(line_text.strip())
block_lines.append(merged)

# Line 475 — 行合并为 block，但不再调用 _merge_nearby_dollar_blocks
block_text = " ".join(block_lines).strip()
```

PDF 中的多行公式 (display math) 在 PyMuPDF 中通常分割为多个 "line" 对象。第 1 行的 `$...$` 和第 2 行的 `$...$` 属于不同 `line_text`，**永不合并**。

**影响**: 多行公式被分割为多个独立的 `$...$` 块，每个块可能因过短被 `_is_valid_formula` 拒绝 (阈值 `len < 3`)，导致整个公式碎片未进入 `extracted.json` 的 `formulas[]` 数组。

**严重性**: 高。直接影响 display math 的提取质量。中文论文路径依赖 `formulas[]` 作为唯一公式数据源，此 Bug 导致中文论文的公式丢失率显著升高。

#### B3. `_is_valid_formula` 的 `math_signals` 计数器可能存在误计数

**代码位置**: `extractor.py:194-251`

**问题**: 
```python
ops = re.findall(r'[-=+*/<>≤≥≠≈≡∈⊂⊆∪∩∫∑∏∮∇∂⋅×±−]', stripped)
math_signals = sum([has_sub_sup, has_latex, has_greek, has_math_kw, len(ops) >= 1])
```
`ops` 正则包含 `-` 和 `−` (U+2212 MINUS SIGN)。英文文本中 `-` 极常见 (连字符、破折号语境)。虽然 `_is_valid_formula` 有英文连字符检测 (line 220-234)，但逻辑复杂且有边界情况:

- 如果 `math_signals == 2` (两个独立信号)，连字符检测**不执行** (仅检查 `math_signals == 1` and `not has_sub_sup`)
- 这可能导致 "max-min" 类英文文本在同时含有另一数学标志时被误判为公式

#### B4. `_COMPLETE_FORMULA_RE` 的 `\\b` 词边界问题

**代码位置**: `extractor.py:116-143`

**问题**: 模式如 `r"|\\min\\b|\\max\\b"` 中的 `\\b` 在 raw string 中表示两个字面字符 `\b`，传递给 re 后是**退格符** (backspace, `\x08`)，不是词边界 `\b`。

这是正则转义 bug 的延续 (CLAUDE.md v0.6.0 已修复部分类似问题，但 `\b` 问题依然存在):
- `\\min\\b` 匹配的是 `\min` 后跟退格符，极不可能实际匹配
- `\\sin\\b`, `\\log\\b`, `\\lim\\b` 等同理

**影响**: `_COMPLETE_FORMULA_RE` 中所有带 `\b` 的 LaTeX 函数名匹配全部失效。但由于 `_COMPLETE_FORMULA_RE` 仅用于 `_is_valid_formula` 的 `has_latex` 判断 (检测文本是否含 LaTeX 命令)，而 `\sin`, `\log` 等也可被 `FORMULA_MARKERS[2]` (`\\[a-zA-Z]+`) 捕获，因此影响相对有限——`has_latex` 仍可能通过其他命令匹配为 True。**实际影响**: 当公式的**唯一** LaTeX 命令是 `\sin`, `\log`, `\lim` 等函数名时，`has_latex` 为 False。

#### B5. `SYMBOL_TO_LATEX` 映射不完整

**代码位置**: `extractor.py:12-65`

**缺失的重要映射**:
- `U+03C3` (σ) → 已映射为 `\sigma` ✓
- `U+03C2` (ς, final sigma) → **缺失**
- `U+2126` (Ω, Ohm sign) → **缺失** (仅映射了 `U+03A9`)
- `U+220F` (∏) → 已映射 `\prod` ✓
- `U+2264` / `U+2265` → 已映射 ✓

**影响**: 轻微。较罕见的 Unicode 数学符号不会被转换为 LaTeX。

### ⑤ 失效模式风险评估

| 失效模式 | 风险等级 | 相关 Bug | 说明 |
|----------|---------|----------|------|
| 所有公式被过滤 | **中** | B1 | `_looks_like_formula` 更严格的阈值可能漏检，但 _is_math_font 路径提供第二道防线 |
| 正文和公式混在一起 | **高** | B2, B3 | 跨行公式碎片不合并导致上下文混入；连字符混淆正文与公式 |
| x_t^n 变成 xt n | **高** | B2 | PDF 上下标通常分割为独立 span/line，跨行不合并直接破坏二维结构 |
| Σ 变成 NX | **低** | — | `SYMBOL_TO_LATEX` 映射 `U+03A3→\Sigma`，`_convert_formula_text` 逐字符转换。故障仅在 PDF 编码损坏且无 Unicode→LaTeX 映射时发生 |
| 行内公式遗漏 | **高** | B1, B2 | 单标志行内公式在 `_looks_like_formula` 中被拒绝；短 span 文本不触发数学检测 |
| Bounding Box 过大 | **N/A** | — | 本项目不使用 bounding box 提取公式，无此问题 |
| OCR 后没有恢复二维数学结构 | **N/A** | — | 本项目不使用 OCR，不存在 OCR→结构恢复流程。但**等价问题**存在: PDF 中将二维数学编码为线性 span 序列后，`_merge_nearby_dollar_blocks` 仅做一维合并（行内），不做二维 (上下标) 结构恢复 |

---

## M2: LLM 全文翻译 (公式保护)

### ① 代码位置

`silver_research_bot/paper_analyzer/translator.py`

### ② 职责

将英文论文全文翻译为中文，同时保护数学公式。核心原则：**LLM 仅翻译自然语言部分，LaTeX 公式原样保留**。

分块策略: 2000 字符/块 + 重叠段落 + 前文摘要。截断检测: 2 级降级重试 (1/2, 1/4)。

后处理:
- `_validate_formulas()`: 修复常见 LaTeX 语法错误 (brace balance, `\boldsymbol→\mathbf`, 多字符下标 unbrace)
- `_embed_figures_tables()`: 替换 ◈FIG_N◈/◈TBL_N◈ 占位符为 Markdown 引用
- `_validate_translation_length()`: 输出/输入比 < 40% 时警告

### ③ 输入/输出

| 项目 | 类型 | 说明 |
|------|------|------|
| **输入** | `full_text: str` | 提取的英文全文 |
| **输出** | `str` | 中文翻译, 公式用 `$...$`/`$$...$$` 包裹 |
| **模板** | `templates/paper/translator_system.md` | LLM 系统提示 (翻译指令 + 公式保护规则) |

### ④ Bug 分析

#### B6. LLM 翻译可静默损坏/丢弃公式 (系统性风险)

**无 round-trip 验证**: 翻译后没有将输出中的公式与 `extracted.json` 的 `formulas[]` 做对比校验。LLM 可能:
- 跳过公式不翻译 (仍保留在输出中，理想情况)
- 修改 LaTeX (如 `\sum_{i=1}^{n}` → `\sum_i=1^n`)
- 完全删除公式块
- 将公式文本翻译为中文 (如 `where x is` → `其中 x 是`，正确；但 `x = 5` → `x 等于 5`，错误)

**模板指令不可强制执行**: system prompt 中 "公式内部的 LaTeX 代码绝对不可触碰" 是软约束，LLM 没有强制执行机制。

#### B7. `_validate_formulas` 的 `\boldsymbol→\mathbf` 替换是破坏性的

**代码位置**: `translator.py:244`

```python
fixed = re.sub(r"(?<!\$)\\boldsymbol\{(.+?)\}", r"\\mathbf{\1}", fixed)
```

`\boldsymbol` 和 `\mathbf` 在 LaTeX 中语义不同:
- `\boldsymbol{\alpha}` — 对希腊字母等非粗体字符加粗 (需要 amsmath)
- `\mathbf{\alpha}` — 对希腊字母**无效果** (仅对拉丁字母有效)

这个替换会**静默破坏**包含希腊字母粗体的公式，使 `\boldsymbol{\alpha}` 变成无效果的 `\mathbf{\alpha}`。

#### B8. 分块边界可能切断公式

**代码位置**: `translator.py:135-161`

`_build_chunks` 在 `cur_size + ps > max_size` 处切分。如果公式块 (特别是多行 `$$...$$`) 恰好跨越切分边界:
- 前半块: `$$\n\begin{aligned}` (无闭合 `$$`)
- 后半块: `x &= y \\ z &= w\n\end{aligned}$$` (无开头 `$$`)

两个片段在各自块中都是未闭合公式，`_validate_formulas` 虽会发出警告，但**无法自动修复**。

### ⑤ 失效模式风险评估

| 失效模式 | 风险等级 | 说明 |
|----------|---------|------|
| 所有公式被过滤 | **低** | 翻译路径下公式提取在 M3 阶段，不依赖 M1 的 formulas[] |
| 正文和公式混在一起 | **中** | LLM 可能将相邻正文合并到公式块中，尤其是公式后的 "where" 从句 |
| x_t^n 变成 xt n | **中** | LLM 可能损坏 LaTeX。`_validate_formulas` 的 brace 修复仅处理 `_{2+char}` 模式，`x_t^n` 中的 `_t` 因单字符而不被修复 |
| Σ 变成 NX | **低** | 若翻译输入中的 `\Sigma` 已是正确 LaTeX，LLM 通常保留。故障仅发生在翻译前阶段 |
| 行内公式遗漏 | **中** | LLM 可能将行内 `$...$` 视为正文翻译，输出中文替代 |

---

## M3: 翻译中公式提取

### ① 代码位置

`silver_research_bot/paper_analyzer/formula_explainer.py`

**核心函数**: `extract_formulas_from_translation(translation_text)`

**处理步骤**:
```
translation.md
  → _merge_nearby_dollar_blocks()        # Step 0a: 合并碎片 $...$
  → _merge_equation_fragments()          # Step 0b: 合并 $lhs$ = $rhs$
  → _promote_display_math()             # Step 1: 独立行 $→$$
  → 正则提取 $$...$$ 和 \[...\]         # Step 2: 提取 display math
  → 正则提取行内 $...$                  # Step 3: 提取 substantial inline math
  → _is_substantial_math() 过滤         # Step 4: 拒绝单符号/英文
  → 英文散文黑名单过滤                    # Step 5: 拒绝 LLM 误输出的英文
  → _balance_braces()                   # Step 6: 括号平衡
  → 输出 formulas[]
```

### ② 职责

从 LLM 翻译产物中**精准提取 display math 公式**。这是英文论文路径的公式主数据源 (替代 M1 extracted.json 的碎片化公式)。

### ③ 输入/输出

| 项目 | 类型 | 说明 |
|------|------|------|
| **输入** | `translation_text: str` | M2 翻译产物 |
| **输出** | `list[dict]` | `[{latex, equation_number, context, index}]` |

### ④ Bug 分析

#### B9. `_promote_display_math` 仅提升独立行公式，不处理嵌入式 display math

**代码位置**: `formula_explainer.py:129-145`

**问题**: 仅当 `$...$` 独占一行或后跟编号 `(N)` 时才提升为 `$$...$$`。PDF 中的 display formula 有多种表现形式:
- 公式独占一行 ✓ (被 promote)
- 公式后跟编号 ✓
- 公式嵌入段落中 ✗ (不被 promote)
- 公式为列表项 ✗ (不被 promote)

后两类在 M1 中也不被检测 (extracted.json formulas[] 为空)，导致这些公式**在两个路径中都丢失**。

#### B10. 英文散文黑名单可能误杀合法公式

**代码位置**: `formula_explainer.py:218-219`

```python
if _re.search(r"\b(?:the|and|for|...|terms|order)\b", latex, _re.IGNORECASE): continue
```

黑名单包含 72 个英文单词。以下情况可能误杀:
- `\text{for all } i \in \mathcal{N}` — 被拒绝 (含 `for`)
- `\text{with } n \to \infty` — 被拒绝 (含 `with`)
- `\text{and } x > 0` — 被拒绝 (含 `and`)

这些是 LaTeX 中合法的 `\text{}` 用法，需要加 `\b` 保护。虽然目前 `\text{for}` 中的 `for` 前后是 `{` 和 `}`，它们在 `\b` 语义下是词边界，所以确实会被拒绝。

**但实际上**: M3 的输入是 LLM 翻译产物，LLM 不应在 `$$...$$` 块中输出 `\text{for all}` 而是用中文。如果 LLM 在 `$$...$$` 中保留了英文，本身就说明该块不是公式而是 LLM 错误输出的英文段落。所以这个黑名单主要防范的是 **LLM 输出错误**，误杀真公式的概率较低。

#### B11. `_merge_equation_fragments` 可能将上下文 prose 合并进公式

**代码位置**: `formula_explainer.py:105-126`

**问题**: 正则捕获 `[^$\n]{0,40}?` (LHS 前最多 40 个非 `$` 字符) 和 `[^$\n]{0,30}?` (等号前最多 30 个字符)。如果 LHS 前有中文翻译文字:
```
根据公式，$\varpi$ t = $\frac{a}{b}$
```
捕获的 `m.group(1)` = "根据公式，" → 合并进公式 → `$根据公式， \varpi t = \frac{a}{b}$`

这个合并后的公式在后续的黑名单检查中大概率会被拒绝 (含中文)，但 LHS 英文 context 如 `acct (` 也会被合并进去。

#### B12. 缺失 `_is_complete_formula` 二次过滤

CLAUDE.md 多次提到 `_is_complete_formula()` 函数，但在**当前 working tree 的 formula_explainer.py 中该函数不存在**。`extract_formulas_from_translation` 中的过滤完全依赖:
1. `_is_substantial_math()` — 仅用于行内 $...$ 路径
2. 英文散文黑名单 — 仅用于无 `\` 的 LaTeX
3. 5+ 双字母词序列检测

**缺失的过滤**: 裸 `=` 号公式 (如 `$$= D t nyt n,n$$` 类乱码) 没有专门的 `_is_complete_formula` 来拒绝。除非被黑名单中的英文词捕获或长度过短 (< 5)，否则会进入 LLM 解读阶段。

### ⑤ 失效模式风险评估

| 失效模式 | 风险等级 | 说明 |
|----------|---------|------|
| 所有公式被过滤 | **低** | 多级提取 (display + inline) + 多级过滤仅拒绝对非数学内容 |
| 正文和公式混在一起 | **中** | B11: 合并方程片段可能带入 context; LLM 在 `$$...$$` 中输出英文段落 |
| x_t^n 变成 xt n | **中** | 若 LLM 翻译时保留了 LaTeX (`x_t^n`)，M3 原样提取。若 LLM 损坏了 LaTeX (`x_t n`)，M3 无法修复 |
| 行内公式遗漏 | **高** | B9: 嵌入式 display math 不被 promote。行内 substantial math (如 `$\frac{a}{b}$`) 被 `_is_substantial_math` 的结构性检测捕获，但 `$x_i$` 类短行内公式被拒绝 (`len(latex) < 5`) |

---

## M4: LLM 公式四级解读

### ① 代码位置

`silver_research_bot/paper_analyzer/formula_explainer.py`

**核心函数**: `explain_formulas()` + `_explain_translation_formulas()` + `_explain_from_text()`

### ② 职责

将提取到的公式 LaTeX 发送给 LLM，生成结构化 HTML 卡片解读。每条公式覆盖四个层次:
1. 符号定义
2. 数学含义
3. 物理/领域含义
4. 关联关系

### ③ 输入/输出

| 项目 | 类型 | 说明 |
|------|------|------|
| **输入** | `formulas[]`, `full_text`, `provider`, `model`, `translation_text?` | 公式列表 + 提供商 |
| **输出** | `str` (HTML) | `.frow` 卡片格式的完整 HTML |
| **模板** | `templates/paper/formula_explainer.md` | LLM 系统提示 (四级解读 + 卡片格式规范) |

### ④ Bug 分析

#### B13. `_strip_fragment_cards` 可能移除合法短公式

**代码位置**: `formula_explainer.py:40-72`

**问题**: 后处理中用 `_is_valid_formula` (从 extractor 导入) 检查每个 `.fexpr` 内容，不通过者移除整个 `.frow` 卡片。但 `_is_valid_formula` 是为 **PDF span 级公式碎片**设计的过滤器，对 LLM 输出的**完整 LaTeX 公式**可能存在误判:
- `\sin(x)` 通过 `_is_valid_formula`? → `has_latex=True` (`\sin` 经过 _COMPLETE_FORMULA_RE 可匹配？不，因为 `\b` bug, `\\sin\\b` 匹配失效。但 `\\sin` 本身可以被 FORMULA_MARKERS[2] 匹配… 不对，_COMPLETE_FORMULA_RE 用于判断 has_latex…)

  实际上 `has_latex = bool(_COMPLETE_FORMULA_RE.search(stripped))`。由于 B4 的 `\b` bug，`\sin\b` 匹配失效。但 `\sin(x)` 中有括号 `(x)`，不是 `\b` 要求的词边界... 让我再想想。

  `_COMPLETE_FORMULA_RE` 中的 `\\sin\\b` 在 raw string 中是 `\sin\b`，传给 re 后 `\b` 是退格符。所以它匹配的是 `\sin` + 退格符(0x08)，这在正常文本中不存在。所以 `has_latex` 对于 `\sin(x)` 是 **False**。

  那 `\sin(x)` 的其他 signals: `has_greek=False`, `has_math_kw=False`, `has_sub_sup=False`, `len(ops) >= 1`? `ops = re.findall(r'[-=+*/<>≤≥≠≈≡∈⊂⊆∪∩∫∑∏∮∇∂⋅×±−]', '\sin(x)')` → 没有运算符匹配。所以 `math_signals = 0` → **被拒绝**。

  这意味着 LLM 正确输出的 `$f(x) = \sin(x) + \cos(x)$` 中的 `\sin(x) + \cos(x)` 作为 fexpr 内容 → `_is_valid_formula` 可能返回 False → 卡片被 `_strip_fragment_cards` 移除！

  **这是一个严重的误杀问题**。`_is_valid_formula` 是为 PDF 碎片设计的，不应直接用于 LLM 输出的完整公式。

#### B14. LLM 无法保证 ".fexpr 逐字复制" 指令

模板 (formula_explainer.md line 42-43) 要求:
> "fexpr 中的 LaTeX 表达式必须与下方用户消息中提供的公式原文逐字相同，禁止截断、改写、省略或增删任何符号"

但 user message 中也用了 `⚠️必须完整复制到fexpr，禁止截断` 标记 — 这是**双重软约束**，LLM 仍可能截断长公式。

#### B15. 未使用 `_is_valid_formula` 对翻译路径的二次过滤

CLAUDE.md v0.6.1 提到: "formula_explainer.py:437-448: `_explain_translation_formulas` 新增 `_is_valid_formula` 二次过滤安全网"。

但在当前 working tree 的 `_explain_translation_formulas` (line 256-269) 中，**没有此二次过滤**。提取到的公式直接送入 LLM，没有经过 `_is_valid_formula` 验证。

### ⑤ 失效模式风险评估

| 失效模式 | 风险等级 | 说明 |
|----------|---------|------|
| 所有公式被过滤 | **低** | B13 可能导致部分短公式卡片被移除，但不会是全部 |
| 正文被当作公式解读 | **中** | LLM 可能在 `$$...$$` 中输出非公式内容，M3 的黑名单防护不足 |

---

## M5: 前端渲染 (MathJax)

### ① 代码位置

`web/src/App.vue`

**核心函数**: `renderFormula()` (line 226), `renderMd()` (line 307-321), `retypeset()` (line 225)

### ② 职责

将后端产出的公式解释 HTML 渲染到浏览器:
1. `.fexpr` 内容包裹 `$$...$$` 触发 MathJax display math 渲染
2. `.fmean` 中的 `$...$` 由 MathJax 行内渲染
3. `renderMd()` 处理翻译等 Markdown 产物的通用渲染

### ③ 输入/输出

| 项目 | 类型 | 说明 |
|------|------|------|
| **输入** | `formula_explanations.md` (HTML) | 后端产出的公式卡片 HTML |
| **输出** | 浏览器 DOM | MathJax 渲染后的公式 |

### ④ Bug 分析

#### B16. `renderFormula` 缺少 `.fmean` LaTeX 自动包裹

**代码位置**: `App.vue:226`

**当前代码**:
```javascript
function renderFormula(t){
  if(!t)return'';
  if(t.indexOf('<div class="frow"')>-1||t.indexOf('<style>')>-1){
    t=t.replace(/(<div class="fexpr">)([\s\S]*?)(<\/div>)/g,function(_,o,c,e){
      if(/^\s*\$/.test(c))return o+c+e;
      c=c.replace(/^\s+|\s+$/g,'');
      return o+'$$'+c+'$$'+e
    });
    setTimeout(retypeset,100);
    return t
  }
  return renderAll(t)
}
```

仅处理 `.fexpr`，不对 `.fmean` 做任何 LaTeX 检测/包裹。CLAUDE.md v0.6.0 声称添加的 `.fmean` 后处理**在代码中不存在**。

**影响**: 若 LLM 在 `.fmean` 中忘记用 `$...$` 包裹数学符号 (如写 `v_i^R(t)` 而非 `$v_i^R(t)$`)，这些符号不会触发 MathJax 渲染，显示为纯文本。

#### B17. 缺失 `sanitizeLatex` 导致 TeX 特殊字符报错

CLAUDE.md v0.6.1 声称添加了 `sanitizeLatex` 函数处理 `#`, `%`, `~` 等 TeX 特殊字符，但**该函数在 App.vue 中不存在**。

**影响**: 若 LLM 输出的 LaTeX 包含 `#` (如在 `\#` 参数中) 或 `%` (注释)，MathJax 会报错并停止渲染该公式块。

#### B18. `renderMd` 不保护 `$...$` 在 `<>` 转义中

**代码位置**: `App.vue:309`

```javascript
return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
```

`$` 不转义 — 这是正确的 (MathJax 需要原始 `$`)。但 `_` 和 `^` 也不转义 — 它们在 HTML 中不是特殊字符，但在 Markdown 到 HTML 的转换中会被 `renderMd` 保留。这是正确的行为。

**问题**: `renderMd` 中 `<` 和 `>` 被转义为 `&lt;` 和 `&gt;`。如果公式中包含 `\langle` 和 `\rangle` (左右尖括号)，在正文段落的 `$...$` 中，`renderMd` 的 `<>` 转义在 `$` 处理**之前**执行吗？

是的！查看代码: `renderMd` 先做 `<>` 转义 (line 309)，然后才做各种 Markdown 替换。`$...$` 没有被特殊保护。但 `\langle` 在 `$...$` 内部，`<` 字符会被转义为 `&lt;`，MathJax 收到的是 `$\langle$` → `$\lang&lt;$` — **这会破坏 MathJax 渲染**。

等一下，`\langle` 中的 `<` 会被转义为 `&lt;` 吗？是的，行 309 的 `replace(/</g, '&lt;')` 是全局替换，不判断是否在 `$...$` 内部。

**影响**: 翻译中所有含 `<` 或 `>` 的行内公式 (如 `$\langle x \rangle$`, `$x < y$`) 被 `renderMd` 破坏，MathJax 无法渲染。

---

## 跨模块系统性问题汇总

### S1. 双重公式数据源的不一致性

| 路径 | 数据源 | 适用论文 | 公式质量 |
|------|--------|---------|---------|
| **翻译路径** | M3: `extract_formulas_from_translation(translation)` | 英文 | 高 (LLM 翻译后的完整公式) |
| **中文/回退路径** | M1: `extracted.json` → `formulas[]` | 中文/其他 | 低 (PDF span 碎片, 受 B1-B2 影响) |

两个路径的公式质量差异巨大，但用户无感知。中文论文的公式解读质量显著低于英文论文。

### S2. 不存在 OCR 流程

整个流水线**不使用 OCR**。公式识别依赖:
1. PDF 内嵌字体名称 (通过 `_is_math_font`)
2. Unicode 文本内容模式匹配 (通过 `_looks_like_formula`)

对于**扫描版 PDF** (无文字层)，`extract_pdf_text()` 返回空文本，`_extract_fallback()` (pypdf) 同样无效。此类 PDF 完全无法处理。

### S3. 公式计数不反映实际解读数量

`extracted.json` 的 `formula_count` 来自 M1 的 `_is_valid_formula` 过滤。但实际展示给用户的是 M4 LLM 解读的结果。二者无关联 — M1 可能统计 50 个公式，M4 只解读了翻译中的 30 个完整公式。前端显示的 "? 公式" 基于 M1 计数，具有误导性。

### S4. CLAUDE.md 文档与代码不一致

多处 CLAUDE.md 描述的功能在当前 working tree 中不存在:
- `_is_complete_formula()` (formula_explainer.py)
- `sanitizeLatex()` (App.vue)
- `_expand_formula_boundaries()` (extractor.py)
- `.fmean` LaTeX 自动包裹 (App.vue renderFormula)
- 翻译路径 `_is_valid_formula` 二次过滤

CLAUDE.md 可能描述了计划中或历史版本中的功能，而非当前代码状态。

---

## 结论

### 按严重性排序的修复优先级

| 优先级 | Bug | 影响 |
|--------|-----|------|
| **P0** | B18: `renderMd` 转义破坏行内公式 `<>` | 翻译 Tab 中所有含 `\langle`, `<` 的公式无法渲染 |
| **P0** | B2: `_merge_nearby_dollar_blocks` 仅行内调用 | 跨行 display math 永久碎片化，中文论文公式全丢 |
| **P1** | B4: `_COMPLETE_FORMULA_RE` 的 `\b` 退格 Bug | 仅含 `\sin`, `\log` 等函数名的公式被 `_is_valid_formula` 拒绝 |
| **P1** | B13: `_strip_fragment_cards` 误用 `_is_valid_formula` 于 LLM 输出 | 合法短公式卡片被移除 |
| **P1** | B17: 缺失 `sanitizeLatex` | `#`, `%`, `~` 导致 MathJax 报错 |
| **P2** | B6: LLM 翻译无公式校验 | 公式可能被 LLM 静默修改 |
| **P2** | B9: `_promote_display_math` 覆盖率不足 | 嵌入段落的 display math 丢失 |
| **P2** | B16: `.fmean` 无 LaTeX 自动包裹 | `.fmean` 中未包裹的符号不渲染 |
| **P3** | B7: `\boldsymbol→\mathbf` 替换破坏性 | 希腊字母粗体公式显示错误 |
| **P3** | B8: 分块边界切分公式 | 极长公式可能被截断 |
