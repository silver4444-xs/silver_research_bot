"""Stage 1b: 四维度系统性分析 — 并行 LLM 调用"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from silver_research_bot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

DIMENSIONS = [
    {"key": "system_model", "template": "paper/analyzer_system_model.md", "label": "系统模型分析"},
    {"key": "problem_formulation", "template": "paper/analyzer_problem.md", "label": "问题表述分析"},
    {"key": "optimization_algorithm", "template": "paper/analyzer_algorithm.md", "label": "优化算法分析"},
    {"key": "experiment_design", "template": "paper/analyzer_experiment.md", "label": "实验设计分析"},
]


async def analyze_dimensions(
    full_text: str,
    provider: "LLMProvider",
    model: str,
    language: str = "en",
) -> dict[str, str]:
    """并行执行四维系统性分析，返回 {key: analysis_text}。"""
    text = _prepare_text(full_text, language)

    async def analyze_one(dim: dict) -> tuple[str, str]:
        system_prompt = render_template(dim["template"], strip=True)
        lang_hint = "英文论文" if language == "en" else "中文论文"
        user_msg = (
            f"## {dim['label']}\n\n"
            f"以下是一篇{lang_hint}内容。请仅从 **{dim['label']}** 维度深入分析。\n"
            f"对较难部分着重详细说明，给出直观物理含义和数学推导。\n"
            f"直接输出分析结果，不要添加问候语、开场白或角色介绍。\n\n---\n\n{text}"
        )
        response = await provider.chat_with_retry(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            tools=None,
        )
        return dim["key"], response.content or ""

    results = await asyncio.gather(*(analyze_one(d) for d in DIMENSIONS), return_exceptions=True)

    output: dict[str, str] = {}
    unassigned = [d["key"] for d in DIMENSIONS]
    for result in results:
        if isinstance(result, Exception):
            key = unassigned.pop(0)
            output[key] = f"分析失败: {result}"
        else:
            key, content = result
            output[key] = content
            unassigned.remove(key)
    return output


def _prepare_text(full_text: str, language: str) -> str:
    max_chars = 15000 if language == "en" else 20000
    if len(full_text) <= max_chars:
        return full_text
    priority = [
        "abstract", "introduction", "system model", "problem",
        "proposed", "method", "algorithm", "experiment", "conclusion",
        "摘要", "引言", "系统模型", "问题", "算法", "实验", "结论",
    ]
    lower = full_text.lower()
    best_end = max_chars
    for kw in priority:
        pos = lower.rfind(kw, max_chars // 2, len(lower))
        if pos > 0 and pos < max_chars * 2:
            best_end = min(pos + 1000, len(full_text))
            break
    return full_text[:best_end]
