# SILVER RESEARCH BOT — 变更记录

> 从 CLAUDE.md 提取，减少主文件 token 消耗

---

## v0.4.0 变更记录 (2026-06-22)

### 图片提取 (extractor.py) — 完全重写

**Strategy**: `_try_extract_figure()` — 先提取再编号，仅成功时分配 figure_idx + 占位符。

| 改动 | 说明 |
|------|------|
| `page.get_pixmap(clip=bbox)` → `doc.extract_image(xref)` | 原图提取, 非截图; xref 来自 block.number |
| `got_pixmap` fallback | xref 非图片时回退到截图 |
| **先提取后编号** | 提取失败不分配图号、不插入 `◈FIG_N◈` 占位符 |
| bbox 裁剪到 page.rect | 防止 "Invalid bandwriter header" 错误 |
| 静默跳过非图片块 | 矢量图形/蒙版不产生 "图片未导出" 噪音 |

**图片嵌入 (translator.py)**:

| 改动 | 说明 |
|------|------|
| URL 使用 `image_rel_path` 可变扩展名 | 不再硬编码 `.png` |
| `onerror` 从 `display:none` 改为可见提示 | 加载失败时显示橙色边框提示框 |
| "图片未导出" 样式化 | 左侧橙色边框卡片 |

**图片 API (research_app.py)**:
- `paper_figure` 端点 `media_type` 动态检测: 支持 `.png/.jpg/.jpeg/.gif/.webp/.bmp/.svg/.tiff`

### 公式检测 (extractor.py + formula_explainer.py) — 重写过滤逻辑

| 改动 | 说明 |
|------|------|
| `/` 从所有数学运算符字符类移除 | 匹配 URL/DOI/path 的概率远超数学分数 |
| `^` `_` 加入 `FORMULA_MARKERS[3]` | 超/下标是内联数学的通用标记 |
| `_is_valid_formula()` 5 条规则重写 | 孤立 LaTeX 命令(如 `\phi`)→拒绝; 运算符必须伴随变量; 希腊字母必须有其他内容; 显式拒绝 URL/DOI |
| 公式装配: span → block 级合并 | 扫描 block_text 中 `$...$` 区域, 间距≤8 字符的合并 |

### 前端 renderMd() (App.vue) — 保留安全 HTML 标签

- 新增 placeholder 保护模式: `◈HTML_N◈` → 转义后还原
- 修复翻译中 `<img>` 标签被转义为 `&lt;img&gt;` 的根因 bug

### 可视化 (visualizer.py)

| 改动 | 说明 |
|------|------|
| `_render_md_inline()` | 卡片中 `**text**`→`<strong>`, `*text*`→`<em>`, `` `code` ``→`<code>` |
| `_truncate_at_sentence()` | 在 `。！？.!?` 处截断, 不再硬切 100 字符 |
| `_is_table_row()` | 过滤 Markdown 表格行(含 `|`), 不显示为卡片条目 |
| 行内 `$...$` 保留 | MathJax 可渲染, 不再替换为 `[公式]` 文字 |
| 条目数 3→5, 字符 100→150 | 更丰富的内容展示 |
| `_llm_experiment_table` fallback | LLM 返回 Markdown 管道表格时转为 HTML `<table>` |

### 审计报告 (auditor.py + App.vue) — 可视化仪表板

- `renderAudit()`: JSON → HTML 仪表板 (通过/未通过横幅 + 严重程度分组 + LLM 审计内容)

### 原文阅读 (App.vue + style.css) — 全屏双栏 + 段落对齐

| 改动 | 说明 |
|------|------|
| 全屏模式 | position:fixed 覆盖视口 |
| 三种模式 | PDF 原文 / 段落对照 / 提取文本 |
| 同步滚动 | 段落模式: 左右同步; PDF 模式: 比例同步 |
| 悬停高亮 | 段落对 hover 时双方同时高亮 |

### 关键架构决策 (v0.4.0)

1. 图片先提取后编号: `_try_extract_figure()` 成功后 figure_idx 才递增
2. 公式5规则过滤: 运算符+变量 / 带参LaTeX / 希腊+其他 / 数字+运算符 / 已知函数名
3. 公式block级装配: `$...$` 区域间距≤8合并且不加前后填充
4. renderMd HTML保护: `◈HTML_N◈` 占位符在 `<>` 转义前后保护安全标签
5. 翻译块结构: 2000字符/块 + 动态max_tokens + 2级截断重试 + chunk重叠
6. 层叠上下文: `.app` 和 `.main` 不能有 `z-index`

---

## v0.5.0 变更记录 (2026-06-24)

### 公式解读重构 — 从翻译提取完整公式

| 文件 | 变更 |
|------|------|
| `formula_explainer.py` | 新增 `extract_formulas_from_translation()`; 公式解读数据源优先级: 翻译公式 → PDF碎片 → 全文回退 |
| `orchestrator.py` | Stage 2: `lang=="en"` 时传入 `translation_text=analysis.translation` |
| `templates/paper/formula_explainer.md` | 新增四级解读层次(符号定义/数学含义/领域含义/关联关系) |

### 引用图谱重写 (6项修复)

| 文件 | 变更 |
|------|------|
| `citation_graph.py` | 完全重写: `_js_escape()` 防XSS; JSON直接嵌入; 提取范围 8000→16000字; `related_to` 字段; LLM降级韧性 |

### 公式截断修复

| 文件 | 变更 |
|------|------|
| `extractor.py` | 新增 `_expand_formula_boundaries()`; `_is_valid_formula()` 增强; 页面级合并 gap 8→4 |
| `formula_explainer.py` | `_is_valid_formula()` 同步所有规则 |
| `web/src/App.vue` | `renderFormula()` 跳过非数学 $ 包裹; `sanitizeLatex()` 合并双下标+控制字符移除 |

---

## v0.6.0 变更记录 (2026-06-25)

### `_is_valid_formula` 去重 (消除双重维护)

| 文件 | 变更 |
|------|------|
| `formula_explainer.py` | 删除本地 `_is_valid_formula`(130行), 改为 `from extractor import _is_valid_formula` |
| `extractor.py` | 重复词列表去重; `_FORMULA_EXPAND_STOP_RE` 新增 `re.IGNORECASE` |

### `_COMPLETE_FORMULA_RE` 正则转义 Bug 修复 (阻塞级)

**根因**: raw string `r"\\leq"` → `\l` 是未知转义(消费为 `l`), 匹配 `leq` 而非 `\leq`. `\\frac`→`\f`=换页符, `\\neq`→`\n`=换行. 50+ LaTeX 命令匹配全部失效.

**修复**: 全部 `\\cmd` → `\\\\cmd` (raw string 4个`\`→Python`\\`→正则`\`=字面反斜杠).

### 公式提取重构 (翻译路径从死代码到正常工作)

**根因**: `extract_formulas_from_translation` 只匹配 `$$...$$`, 但 PDF 提取器只产 `$...$`, 翻译从不含 `$$` → 永远返回空.

**修复**: 新增 `_promote_display_math()` 预处理, 将独立成行的 `$...$` 升级为 `$$...$$`.

### 关键架构决策 (v0.6.0)

1. 公式提取只从 `$$...$$` (显示公式块), `$...$` 行内数学不提取
2. `_promote_display_math` 桥接: 翻译中独立行/有编号者 → `$$` 升级
3. `_is_complete_formula` 二次过滤: `$$...$$` 中也需要运算符检查
4. `_is_valid_formula` 单一源: extractor.py 维护, formula_explainer.py 导入

---

## v0.6.1 变更记录 (2026-06-26)

### 公式提取全链路加固 (8 项修复, 3 文件)

**背景**: 公式解读页面产生 `+ It n`、`= D t nyt n,n` 等乱码碎片, MathJax `#`/`%` 特殊字符报错。

**根因链**: PDF编码损坏 → `_looks_like_formula` 误包裹 → `_expand_formula_boundaries` 跨空格吞噬英文词 → `.isalpha()` 词计数Bug(逗号否决)绕过4词阈值 → `_is_complete_formula` 裸 `=` 放行 → `sanitizeLatex` 不处理 `#` `%` → MathJax 报错

| # | 文件 | 函数/区域 | 修改 |
|---|------|----------|------|
| 1 | `extractor.py` | `_is_valid_formula` | `.isalpha()`→`re.findall(r'[a-zA-Z]{2,}')`; 阈值4→3; 新增长词白名单 |
| 2 | `extractor.py` | `_is_valid_formula` | 多token英文检测: ≥1个纯英文token + 单操作符 + 无强数学→拒绝 |
| 3 | `extractor.py` | `_expand_formula_boundaries` | 扩展上限10→6; 连续字母上限5→3; **禁止跨空格扩展** |
| 4 | `formula_explainer.py` | `_is_complete_formula` | 裸 `=` + ≥2个3+字母序列 → 拒绝 |
| 5 | `formula_explainer.py` | `_promote_display_math` | 移除 `re.DOTALL`; 新增5词安全阈值; 移除跨行promotion |
| 6 | `formula_explainer.py` | `extract_formulas_from_translation` | 词计数同步修复 |
| 7 | `formula_explainer.py` | `_explain_translation_formulas` | 新增 `_is_valid_formula` 二次过滤安全网 |
| 8 | `web/src/App.vue` | `sanitizeLatex` | `#`→`\#`, `%`→`\%`, `~`→`\textasciitilde{}`, Unicode引号→ASCII |

---

## v0.7.0 变更记录 (2026-07-16)

### 前端渲染修复 (App.vue)

| 修复 | 说明 |
|------|------|
| MathJax rAF→setTimeout | `requestAnimationFrame` 在后台标签页被暂停/节流, 改回 `setTimeout(retypeset, 0)` |
| HTML 标签转义修复 | 恢复 `◈HTML_N◈` 占位符保护机制 (v0.4.0 引入后在重构中丢失) |
| sanitizeLatex 统一化 | 从仅公式 tab → 所有内容 tab 统一在 `renderMd()` 还原点应用 |

### Markdown 语法扩展

| 新增 | 说明 |
|------|------|
| 有序列表 | `1. item` → `<ol><li>item</li></ol>`, `<!--OLI-->` 区分有序/无序 |
| 链接 | `[text](url)` → `<a target="_blank" rel="noopener noreferrer">` |

### 翻译流水线修复 (translator.py)

| 修复 | 说明 |
|------|------|
| 图表占位符格式对齐 | 新增 `_normalize_placeholders()`: `[Fig N]` / `[Table N]` → `◈FIG_N◈` / `◈TBL_N◈` |
| 行内公式分块边界保护 | 新增 `_count_inline_dollars()` + `_safe_para_split()`; `$...$` 不被切分 |
| 裸露 LaTeX 包裹 | 新增 `_wrap_bare_latex_commands()`: `\mathbf{X}` → `$\mathbf{X}$` |
| 翻译质量微调 | overlap 1→2段, summary 300→500字符, docstring chunk_size 2000→3000 |

### renderMd 完整生命周期 (v0.7.0)

```
输入 → 保护$$/$ → 保护Mermaid → 保护HTML标签 → 转换链接 → 转义<>&
→ Markdown转换 → 保护表格 → 段落包裹 → 还原Mermaid → 还原表格
→ 还原HTML → 还原数学(经sanitizeLatex) → 输出
```
