# Visualizer 模板（参考用）

此模板供 `visualizer.py` 中的 `_llm_experiment_table()` 使用：

从实验分析文本中提取性能对比数据，输出 HTML `<table class="cmp-table">`。
表结构：方法名 | 指标1 | 指标2 | 指标3（至少2行数据）。
只输出 `<table>` 标签，不含任何解释。

> 注意：系统概述图、Mermaid 流程图和公式依赖图现已由 `visualizer.py` 程序化生成，
> 不再依赖本模板。
