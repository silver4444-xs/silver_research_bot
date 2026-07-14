"""Stage 4: 质量审计器"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from silver_research_bot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider


@dataclass(slots=True)
class AuditReport:
    paper_id: str
    passed: bool = True
    issues: list[dict] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    overall_grade: str = ""
    overall_score: int = 0
    dimension_scores: dict = field(default_factory=dict)
    summary: str = ""


async def audit_analysis(
    paper_id: str,
    translation: str | None,
    analysis: dict[str, str],
    formulas_text: str,
    visualization_html: str,
    formula_count: int,
    provider: "LLMProvider",
    model: str,
) -> AuditReport:
    """执行质量审计，返回审计报告。"""
    report = AuditReport(paper_id=paper_id)

    # 1. 结构完整性检查
    _check_structure(report, analysis, formulas_text, visualization_html)

    # 2. 公式数量检查
    if translation is not None and formula_count > 0:
        _check_formula_count(report, translation, formula_count)

    # 3. LLM 深度审计
    await _llm_audit(report, translation, analysis, provider, model)

    return report


def _check_structure(
    report: AuditReport,
    analysis: dict[str, str],
    formulas_text: str,
    vis_html: str,
) -> None:
    """检查各输出文档的结构完整性。"""
    required = {
        "system_model": "系统模型分析",
        "problem_formulation": "问题表述分析",
        "optimization_algorithm": "优化算法分析",
        "experiment_design": "实验设计分析",
    }
    for key, label in required.items():
        content = analysis.get(key, "")
        if not content or len(content) < 200:
            report.issues.append({
                "severity": "严重", "dimension": label,
                "detail": f"分析内容为空或过短 ({len(content)} 字符)",
                "fix": "需重新运行该维度分析",
            })
            report.passed = False

    if not formulas_text or len(formulas_text) < 50:
        report.issues.append({
            "severity": "一般", "dimension": "公式解读",
            "detail": "公式解释内容过短",
        })

    if not vis_html or len(vis_html) < 100:
        report.issues.append({
            "severity": "一般", "dimension": "可视化",
            "detail": "可视化 HTML 为空",
        })


def _check_formula_count(
    report: AuditReport, translation: str, expected: int
) -> None:
    """检查翻译中 LaTeX 公式数量。"""
    display = len(re.findall(r'\$\$', translation)) // 2
    inline = len(re.findall(r'(?<!\$)\$(?!\$)[^$]+\$(?!\$)', translation))
    total = display + inline

    if total == 0 and expected > 0:
        report.issues.append({
            "severity": "严重", "dimension": "翻译",
            "detail": f"翻译文档中未检测到 LaTeX 公式（预期 ~{expected} 个）",
            "fix": "公式可能在翻译中丢失，需加强 Prompt 对公式保留的强调",
        })
        report.passed = False
    elif total < expected * 0.5:
        report.issues.append({
            "severity": "一般", "dimension": "翻译",
            "detail": f"翻译中公式数量 ({total}) 远少于预期 ({expected})",
            "fix": "部分公式可能在翻译中丢失",
        })


def _parse_llm_audit_json(content: str) -> dict | None:
    """Parse LLM response JSON, handling common formatting issues."""
    import json as _json

    text = content.strip()

    # Strip ```json / ``` code fences anywhere in the response
    m = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
    if m:
        text = m.group(1).strip()

    # Try direct parse first
    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        pass

    # Extract from first { to last } and attempt repair
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        # Remove trailing commas before ] or } (common LLM mistake)
        candidate = re.sub(r",\s*([]}])", r"\1", candidate)
        try:
            return _json.loads(candidate)
        except _json.JSONDecodeError:
            pass

    return None


async def _llm_audit(
    report: AuditReport,
    translation: str | None,
    analysis: dict[str, str],
    provider: "LLMProvider",
    model: str,
) -> None:
    """使用 LLM 进行深度审计，解析结构化 JSON 响应。"""
    system_prompt = render_template("paper/auditor.md", strip=True)
    context_parts = []
    if translation:
        context_parts.append(f"## 翻译文档\n{translation[:3000]}\n")
    for key, label in [
        ("system_model", "系统模型"),
        ("problem_formulation", "问题表述"),
        ("optimization_algorithm", "优化算法"),
        ("experiment_design", "实验设计"),
    ]:
        text = analysis.get(key, "")
        context_parts.append(f"## {label}\n{text[:2000]}\n")

    try:
        response = await provider.chat_with_retry(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "\n\n".join(context_parts)},
            ],
            tools=None,
        )
    except Exception as e:
        report.passed = False
        report.issues.append({
            "severity": "严重",
            "dimension": "LLM审计",
            "detail": f"LLM 深度审计调用失败: {e}",
            "fix": "检查 Provider 配置和网络连接后重新运行审计",
        })
        return

    if not response.content:
        report.issues.append({
            "severity": "一般",
            "dimension": "LLM审计",
            "detail": "LLM 审计返回空内容",
            "fix": "重新运行审计或更换模型",
        })
        return

    parsed = _parse_llm_audit_json(response.content)
    if parsed is None:
        report.passed = False
        report.issues.append({
            "severity": "建议",
            "dimension": "LLM审计",
            "detail": response.content[:2000],
            "fix": "LLM 未返回有效 JSON，需检查 Prompt 模板",
        })
        return

    # 填充报告字段
    report.overall_grade = str(parsed.get("overall_grade", ""))
    report.overall_score = int(parsed.get("overall_score", 0))
    report.dimension_scores = parsed.get("dimension_scores", {})
    if not isinstance(report.dimension_scores, dict):
        report.dimension_scores = {}

    # 提取问题列表
    llm_issues = parsed.get("issues", [])
    if isinstance(llm_issues, list):
        for iss in llm_issues:
            if not isinstance(iss, dict):
                continue
            severity = str(iss.get("severity", "建议"))
            if severity not in ("严重", "一般", "建议"):
                severity = "建议"
            issue = {
                "severity": severity,
                "dimension": str(iss.get("dimension", "LLM审计")),
                "detail": str(iss.get("detail", "")),
            }
            fix = iss.get("fix")
            if fix:
                issue["fix"] = str(fix)
            report.issues.append(issue)
            if severity == "严重":
                report.passed = False

    # 填充 checks 字典
    ds = report.dimension_scores
    for dim in ["translation_completeness", "formula_completeness",
                "analysis_coverage", "consistency", "overall_quality"]:
        report.checks[dim] = ds.get(dim, 0) >= 60

    # 综合得分过低视为不通过
    if report.overall_score < 60:
        report.passed = False

    report.summary = str(parsed.get("summary", ""))
