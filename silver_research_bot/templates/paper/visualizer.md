# Visualizer 模板结构

此目录包含 `visualizer.py` 中两个 ISCC 领域可视化模块的 Jinja2 提示模板：

| 模板 | 用途 | 调用位置 |
|------|------|----------|
| `visualizer_system.md` | ISCC 系统架构流程图 (7 种架构类型) | `_build_system_architecture_diagram()` |
| `visualizer_algorithm.md` | 算法流程图 (6 种算法类型) | `_build_algorithm_flow_diagram()` |

各模板通过 `render_template("paper/visualizer_*.md", ...)` 加载，传入对应的 Jinja2 变量。

> 注意：`visualizer.py` 中系统架构和算法流程图通过上述模板驱动。实验对比表格 (`_llm_experiment_table`) 的 prompt 直接写在代码中。
