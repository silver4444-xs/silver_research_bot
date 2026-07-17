{# UNUSED: Designed for single-shot structured comparison. Current implementation uses per-dimension parallel scoring via comparison_dimension.md for better quality. Kept for future fast-mode or as a reference. #}
对论文进行多维度结构化对比分析。直接输出 JSON，禁止包含问候语、角色介绍、思考过程或 Markdown 代码围栏。

## 输出要求

严格按以下 JSON Schema 输出。每篇论文用其 paper_id 作为键。

```json
{
  "dimensions": {
    "系统模型": {
      "paper_score": {"paper_id_1": 8.5, "paper_id_2": 7.0},
      "score_reasons": {"paper_id_1": "评分理由≤200字", "paper_id_2": "评分理由≤200字"},
      "key_items": {
        "paper_id_1": [{"name": "条目名≤80字", "description": "描述≤300字", "category": "分类", "comparative_note": "跨论文对比备注"}],
        "paper_id_2": [{"name": "条目名≤80字", "description": "描述≤300字", "category": "分类", "comparative_note": "跨论文对比备注"}]
      }
    },
    "问题建模": { "...": "同上结构" },
    "算法方案": { "...": "同上结构" },
    "实验设计": { "...": "同上结构" },
    "贡献与局限": { "...": "同上结构" }
  },
  "metrics": [
    {
      "metric_name": "准确率",
      "paper_values": {"paper_id_1": 0.953, "paper_id_2": 0.941},
      "unit": "%",
      "dataset": "测试集名称",
      "higher_is_better": true
    }
  ],
  "cross_paper_insights": {
    "method_spectrum": "方法谱系描述（各论文方法的关系：递进/互补/竞争）≤500字",
    "trend_timeline": "技术趋势时间线描述 ≤300字",
    "key_differences": ["差异点1", "差异点2", "差异点3"],
    "common_limitations": ["共同局限1", "共同局限2"]
  }
}
```

## 评分标准 (1-10)

- 8-10: 在该维度有突出创新或严谨完整的理论/实验支撑
- 5-7: 表述清晰，方法合理，但无明显突破
- 1-4: 该维度论述不足或存在明显缺陷

## 维度要求

1. **系统模型**: 实体组成、关键假设、模型复杂度、数学工具
2. **问题建模**: 目标函数、约束条件、优化框架
3. **算法方案**: 算法类型、计算复杂度、收敛性/理论保证
4. **实验设计**: 数据集规模与多样性、基线数量、消融实验充分性、性能指标
5. **贡献与局限**: 核心贡献、局限性、对未来研究的启发

## 指标提取要求

- 从实验设计文本中提取有数值的性能指标（如准确率、F1、BLEU、推理时间等）
- 每个指标给出 paper_id → 数值的映射
- 标注单位、数据集名称、是否越高越好
- 若某论文无该指标，不包含其 paper_id

## 严格规则

1. 必须覆盖所有 5 个维度
2. 每篇论文每个维度至少提取 2 个 key_items
3. key_items 的 comparative_note 必须跨论文比较，不能只描述单篇
4. metrics 至少提取 3 个共同指标
5. 评分必须有区分度（标准差 ≥ 0.5）
6. 直接输出 JSON，禁止 Markdown 包裹，禁止前言后语
