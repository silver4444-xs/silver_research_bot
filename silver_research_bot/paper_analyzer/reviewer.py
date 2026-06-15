"""A/B 审稿 — 多视角独立审稿（理论家 / 工程派 / 领域专家）"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

_PERSONAS = [
    {
        "key": "theory", "label": "理论家审稿",
        "system": "你是一位理论计算机科学/数学审稿人。关注数学推导严密性、定理证明完整性、理论创新程度、符号一致性。以批判但建设性态度审稿。直接输出审稿意见。",
    },
    {
        "key": "engineering", "label": "工程派审稿",
        "system": "你是一位工程实践审稿人。关注可实现性、计算资源需求、超参数敏感性、代码开源复现难度、实际部署问题。以务实落地态度审稿。直接输出审稿意见。",
    },
    {
        "key": "domain", "label": "领域专家审稿",
        "system": "你是一位该领域资深专家审稿人。关注与SOTA对比、问题动机合理性、实验说服力、潜在影响力、未来方向启发性。以领域发展视角审稿。直接输出审稿意见。",
    },
]


async def generate_reviews(
    paper_text: str, title: str, provider: "LLMProvider", model: str
) -> dict[str, str]:
    async def _review_one(p: dict) -> tuple[str, str]:
        prompt = f"""请从你的审稿视角审阅以下论文。

论文标题: {title}
论文内容: {paper_text[:8000]}

按以下结构输出审稿意见：
## 总体评价 (1-10分)
## 主要优点
## 主要问题
## 具体建议
## 是否建议接收 (Accept / Minor Revision / Major Revision / Reject)"""
        try:
            response = await provider.chat_with_retry(
                model=model,
                messages=[
                    {"role": "system", "content": p["system"]},
                    {"role": "user", "content": prompt},
                ],
                tools=None, max_tokens=2000, temperature=0.3,
            )
            return p["key"], response.content or ""
        except Exception as exc:
            return p["key"], f"审稿出错: {exc}"

    tasks = [_review_one(p) for p in _PERSONAS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    output: dict[str, str] = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        key, text = result
        output[key] = text
    return output
