对论文在 **单一维度** 上进行对比评分。直接输出 JSON，禁止包含问候语、角色介绍、思考过程或 Markdown 代码围栏。

## 当前维度

{{ dimension_name }}

### 维度描述

{{ dimension_description }}

## 评分标准 (1-10)

- 8-10: 在该维度有突出创新或严谨完整的理论/实验支撑，方法新颖，表述清晰
- 5-7: 表述清晰，方法合理，但无明显突破或创新
- 1-4: 该维度论述不足、方法有缺陷或缺少关键细节

## 输出 JSON Schema

严格按以下格式输出：

{
  "paper_score": {"paper_id_1": 8.5, "paper_id_2": 7.0},
  "score_reasons": {"paper_id_1": "评分理由≤200字", "paper_id_2": "评分理由≤200字"},
  "key_items": {
    "paper_id_1": [
      {"name": "关键点名称≤80字", "description": "具体描述≤300字", "comparative_note": "与其他论文的对比≤200字"}
    ],
    "paper_id_2": [
      {"name": "关键点名称≤80字", "description": "具体描述≤300字", "comparative_note": "与其他论文的对比≤200字"}
    ]
  }
}

## 严格规则

1. 每篇论文至少提取 2 个 key_items，每个含 comparative_note 跨论文对比
2. 评分必须有区分度（标准差 ≥ 1.0），禁止所有论文得相同分数
3. paper_id 必须使用用户消息中提供的完整 paper_id，不得修改或截断
4. 直接输出 JSON 对象，禁止用 ```json 包裹，禁止加任何前言后语
5. 评分基于论文在该维度的实际内容，不要根据论文整体质量打分
