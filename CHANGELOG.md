# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

各版本详细技术变更归档于 [docs/changelog/CHANGELOG.md](docs/changelog/CHANGELOG.md)。

## [v0.7.0] - 2026-07-16

### Added

- Markdown 语法扩展：有序列表与链接渲染支持

### Fixed

- 前端渲染修复：MathJax rAF→setTimeout、HTML 标签转义恢复、sanitizeLatex 统一化
- 翻译流水线修复：图表占位符对齐、行内公式分块边界保护、裸露 LaTeX 包裹、翻译质量微调

[详细变更](docs/changelog/CHANGELOG.md#v070-变更记录-2026-07-16)

## [v0.6.1] - 2026-06-26

### Fixed

- 公式提取全链路加固（8 项修复）：修复「PDF 编码损坏 → 误包裹 → 跨空格吞噬英文词 → 词计数 Bug → 裸 `=` 放行 → `#`/`%` 未转义 → MathJax 报错」的完整根因链

[详细变更](docs/changelog/CHANGELOG.md#v061-变更记录-2026-06-26)

## [v0.6.0] - 2026-06-25

### Changed

- `_is_valid_formula` 去重：formula_explainer 改为从 extractor 导入单一源，消除双重维护

### Fixed

- `_COMPLETE_FORMULA_RE` 正则转义 Bug（阻塞级）：50+ LaTeX 命令匹配全部失效
- 公式提取重构：`_promote_display_math` 桥接独立行 `$...$` → `$$...$$`，翻译路径从死代码到正常工作

[详细变更](docs/changelog/CHANGELOG.md#v060-变更记录-2026-06-25)

## [v0.5.0] - 2026-06-24

### Added

- 公式解读重构：`extract_formulas_from_translation()` 从翻译中提取完整公式，新增四级解读模板

### Changed

- 引用图谱完全重写：`_js_escape()` 防 XSS、JSON 直接嵌入、提取范围 8000→16000 字、LLM 降级韧性

### Fixed

- 公式截断修复：`_expand_formula_boundaries()` 边界扩展、词计数修正、sanitizeLatex 合并双下标与控制字符移除

[详细变更](docs/changelog/CHANGELOG.md#v050-变更记录-2026-06-24)

## [v0.4.0] - 2026-06-22

### Changed

- 图片提取完全重写：`doc.extract_image()` 原图提取 + 先提取后编号，图片 API 动态 media_type
- 公式检测重写：5 条有效性规则 + block 级装配 + `/` 从数学运算符字符类移除
- 可视化卡片增强：行内 Markdown 渲染、Markdown 表格转 HTML、条目数与字数提升
- 审计报告可视化仪表板 + 原文阅读全屏双栏模式

### Fixed

- 翻译中 `<img>` 标签被转义为文本的根因 bug（renderMd 占位符保护机制）

[详细变更](docs/changelog/CHANGELOG.md#v040-变更记录-2026-06-22)

---

见 [Releases 页面](https://github.com/silver4444-xs/silver_research_bot/releases) 查看已发布版本。
