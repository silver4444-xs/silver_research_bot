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
        formula_req = _dim_formula_requirements(dim["key"])
        user_msg = (
            f"## {dim['label']}\n\n"
            f"以下是一篇{lang_hint}内容。请仅从 **{dim['label']}** 维度深入分析。\n"
            f"对较难部分着重详细说明，给出直观物理含义和数学推导。\n"
            f"直接输出分析结果，不要添加问候语、开场白或角色介绍。\n\n"
            f"## 数学公式完整性要求（严格遵守，违反将导致分析不合格）\n"
            f"- 所有数学符号、变量、表达式必须用 $...$ 包裹（行内）或 $$...$$ 独占段落（独立公式）\n"
            f"- 单字母变量也必须用 $ 包裹：写成 $I$、$M$、$N$，禁止写成裸字母 I、M、N\n"
            f"- 带上下标的变量必须用 LaTeX：写成 $a_i$、$H_i^m$、$\\mu_i^t$、$p^{{max}}$，禁止用 Unicode 字符代替\n"
            f"- 所有公式必须保留原文编号并标注：如 $$...$$ (1)，$$...$$ (7)，$$...$$ (20a)\n"
            f"- 示例正确写法：$x \\in \\mathbb{{R}}^N$、$$\\min_{{x}} \\; f(x) \\; \\text{{s.t.}} \\; g(x) \\leq 0$$\n"
            f"- 示例错误写法（禁止）：\\frac{{1}}{{2}}、x^2 + y^2、a_i 缺少 $ 定界符、约束条件仅用文字描述不给公式\n"
            f"- 每个数学变量、每个公式、每个符号都必须完整保留并正确包裹 $，无一例外\n"
            f"- 论文中若有信息缺失（如仿真平台未指定、参数未披露），必须在分析中明确标注\n\n"
            f"{formula_req}\n\n---\n\n{text}"
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


def _dim_formula_requirements(dim_key: str) -> str:
    """每个分析维度的专属公式完整性要求。"""
    reqs = {
        "system_model": (
            "**系统模型专属要求**：\n"
            "- 信道模型必须给出完整 LaTeX：路径损耗 $PL(d) = ...$、小尺度衰落分布、LoS 概率 $P_{\\text{LoS}} = ...$\n"
            "- 通信模型必须包含 SNR/SINR 表达式、可达速率 $R_{i,m} = B \\log_2(1 + \\gamma_{i,m})$\n"
            "- 计算模型必须给出本地和卸载的时延、能耗完整公式\n"
            "- 所有公式标注原文编号 (1), (2), ... (N)\n"
            '- 禁止仅写模型名称不写公式，如“采用莱斯衰落信道”而不给出概率密度函数'
        ),
        "problem_formulation": (
            "**问题表述专属要求**：\n"
            "- 目标函数必须给出完整 LaTeX，包含所有求和项和权重\n"
            "- 每条约束条件必须写出完整 LaTeX 并标注编号 (C1), (C2), ... 或 (20a), (20b), ...\n"
            "- 约束必须逐条写，1 条都不能省略（含边界约束、整数约束、非负约束）\n"
            '- 禁止用"(20a)-(20c)给出约束"而不写出约束本身，必须先写公式再解释\n'
            "- 若约束较多，用编号列表逐条呈现：\n"
            "  $$\\text{C1: } \\sum_{m} a_{i,m} \\leq 1 \\quad \\forall i$$ (20a)\n"
            "  $$\\text{C2: } \\sum_{i} a_{i,m} f_i \\leq F_m \\quad \\forall m$$ (20b)"
        ),
        "optimization_algorithm": (
            "**优化算法专属要求**：\n"
            '- 必须在"算法详细拆解"节给出完整伪代码（Algorithm 环境或编号步骤）\n'
            "- 关键迭代更新公式必须给出 LaTeX 源码，标注原文编号\n"
            "  例：$$\\mathbf{w}^{(t+1)} = \\mathbf{w}^{(t)} - \\eta_t \\nabla L(\\mathbf{w}^{(t)})$$ (15)\n"
            "  例：$$\\lambda_k^{(t+1)} = \\left[\\lambda_k^{(t)} + \\rho \\cdot g_k(\\mathbf{x}^{(t)})\\right]^+$$ (16)\n"
            "- 若涉及 Lagrange 对偶、闭式解、投影操作，都必须给出数学表达式\n"
            '- 禁止仅用文字描述"然后更新变量"，必须写出更新公式'
        ),
        "experiment_design": (
            "**实验设计专属要求**：\n"
            "- 评价指标必须给出数学定义，不能仅写名称\n"
            "  例：$R_{\\text{sum}} = \\sum_{i=1}^{N} \\sum_{m=1}^{M} a_{i,m} B \\log_2(1 + \\gamma_{i,m})$\n"
            '- 若论文未披露仿真平台/编程框架/硬件配置，必须在该子节明确标注"论文未指定"\n'
            "- 对比方法必须逐一列出并说明核心差异\n"
            "- 必须包含收敛性分析检查（是否有收敛曲线图？），若无则标注为不足\n"
            "- 必须包含算法复杂度对比检查（是否有运行时间/复杂度比较？），若无则标注为不足"
        ),
    }
    return reqs.get(dim_key, "")


def _prepare_text(full_text: str, language: str) -> str:
    max_chars = 20000 if language == "en" else 25000
    if len(full_text) <= max_chars:
        return full_text
    priority = [
        "abstract", "introduction", "system model", "channel model",
        "communication model", "computation model", "problem",
        "proposed", "method", "algorithm", "convergence",
        "experiment", "evaluation", "conclusion",
        "摘要", "引言", "系统模型", "信道模型", "计算模型", "问题", "算法", "实验", "结论",
    ]
    lower = full_text.lower()
    best_end = max_chars
    for kw in priority:
        pos = lower.rfind(kw, max_chars // 2, len(lower))
        if pos > 0 and pos < max_chars * 2:
            best_end = min(pos + 1000, len(full_text))
            break
    return full_text[:best_end]
