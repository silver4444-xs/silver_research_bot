"""多 Agent 论文分析协作团队 — Translator + Analyzer + Auditor 通过 MessageBus 协同"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider
    from silver_research_bot.bus.queue import MessageBus
    from silver_research_bot.agent.runner import AgentRunner


@dataclass
class TeamRole:
    name: str
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    temperature: float = 0.1

    def build_messages(self, task: str) -> list[dict]:
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]


# Pre-defined roles for paper analysis team
TRANSLATOR_ROLE = TeamRole(
    name="Translator",
    system_prompt="你是一位专业的学术论文翻译。将英文论文翻译为专业中文，保留 LaTeX 公式原样（$$...$$）。直接输出翻译内容，不要添加任何其他信息。",
    allowed_tools=["read_file", "write_file"],
    temperature=0.1,
)

ANALYZER_ROLE = TeamRole(
    name="Analyzer",
    system_prompt="你是一位论文分析专家。对论文进行四维系统性深入分析：系统模型、问题表述、优化算法、实验设计。每个维度给出详细数学推导和物理直觉。直接输出分析结果。",
    allowed_tools=["read_file", "write_file", "web_search"],
    temperature=0.2,
)

AUDITOR_ROLE = TeamRole(
    name="Auditor",
    system_prompt="你是一位严格的论文质量审计专家。检查分析结果的完整性、一致性和正确性。列出所有发现的问题及其严重程度（严重/一般/建议）。直接输出审计报告。",
    allowed_tools=["read_file"],
    temperature=0.0,
)


class PaperAnalysisTeam:
    """三 Agent 论文分析协作团队。通过 MessageBus 异步协同分析同一篇论文。"""

    def __init__(
        self,
        provider: "LLMProvider",
        model: str,
        message_bus: "MessageBus | None" = None,
    ):
        self.provider = provider
        self.model = model
        self.bus = message_bus

    async def analyze(
        self, paper_text: str, title: str, language: str = "en"
    ) -> dict[str, str]:
        """执行完整的三 Agent 协作分析流程。"""
        results: dict[str, str] = {}

        # Phase 1: Translate (if needed)
        if language == "en":
            translation = await self._run_role(
                TRANSLATOR_ROLE, f"翻译以下论文：\n\n{paper_text[:10000]}"
            )
            results["translation"] = translation
            text_for_analysis = translation
        else:
            text_for_analysis = paper_text

        # Phase 2: Four-dimension analysis (parallel)
        dims = [
            ("system_model", "分析论文的系统模型架构"),
            ("problem_formulation", "分析论文的问题表述和数学定义"),
            ("optimization_algorithm", "分析论文的优化算法设计"),
            ("experiment_design", "分析论文的实验设计和方法"),
        ]
        analysis_tasks = [
            self._run_role(ANALYZER_ROLE, f"{desc}:\n\n{text_for_analysis[:8000]}")
            for _, desc in dims
        ]
        analysis_results = await asyncio.gather(*analysis_tasks, return_exceptions=True)
        for (key, _), result in zip(dims, analysis_results):
            results[key] = str(result) if not isinstance(result, Exception) else f"Error: {result}"

        # Phase 3: Audit
        audit_input = f"审计以下论文分析结果:\n论文: {title}\n"
        for key in ["system_model", "problem_formulation", "optimization_algorithm", "experiment_design"]:
            audit_input += f"\n## {key}\n{results.get(key, '')[:2000]}"
        audit = await self._run_role(AUDITOR_ROLE, audit_input)
        results["audit"] = audit

        return results

    async def _run_role(self, role: TeamRole, task: str) -> str:
        """运行单个角色 Agent 并返回结果。"""
        try:
            messages = role.build_messages(task)
            response = await self.provider.chat_with_retry(
                model=self.model, messages=messages,
                tools=None, max_tokens=3000, temperature=role.temperature,
            )
            return response.content or ""
        except Exception as exc:
            return f"[{role.name} error: {exc}]"
