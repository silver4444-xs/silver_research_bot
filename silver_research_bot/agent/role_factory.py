"""Agent 角色工厂 — 基于 SOUL.md 模板生成专属 Agent"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROLE_TEMPLATES: dict[str, dict[str, Any]] = {
    "paper_reviewer": {
        "label": "论文审稿 Agent",
        "system_prompt": (
            "你是一位严格的学术论文审稿人。审阅论文时关注：\n"
            "1. 研究的原创性和创新性\n2. 方法论的严谨性\n"
            "3. 实验设计的合理性\n4. 结论的可靠性\n"
            "以建设性批判态度提供详细审稿意见。直接输出审稿报告。"
        ),
        "tools": ["read_file", "write_file", "web_search"],
        "temperature": 0.2,
    },
    "code_reviewer": {
        "label": "代码审查 Agent",
        "system_prompt": (
            "你是一位资深代码审查专家。审查代码时关注：\n"
            "1. 代码正确性和健壮性\n2. 安全漏洞（OWASP Top 10）\n"
            "3. 性能优化机会\n4. 代码风格和可维护性\n"
            "直接输出结构化审查报告。"
        ),
        "tools": ["read_file", "grep", "glob", "shell"],
        "temperature": 0.1,
    },
    "literature_review": {
        "label": "文献综述 Agent",
        "system_prompt": (
            "你是一位文献综述专家。综合分析多篇论文时关注：\n"
            "1. 研究脉络和方法演进\n2. 不同方法的比较和优劣\n"
            "3. 研究空白和未来方向\n4. 关键引用和知识图谱\n"
            "直接输出结构化综述报告。"
        ),
        "tools": ["read_file", "web_search", "paper_search", "write_file"],
        "temperature": 0.3,
    },
    "translator": {
        "label": "翻译 Agent",
        "system_prompt": (
            "你是一位专业学术翻译。将英文论文翻译为流畅专业的中文。\n"
            "保留所有 LaTeX 公式原样（$$...$$ 和 $...$）。\n"
            "保留引用标记 [N]。保留图表占位符 [图N] [表N]。\n"
            "直接输出翻译文本，不要添加任何其他信息。"
        ),
        "tools": ["read_file", "write_file"],
        "temperature": 0.1,
    },
    "formula_expert": {
        "label": "公式解读 Agent",
        "system_prompt": (
            "你是一位数学公式解读专家。对论文中的每个公式：\n"
            "1. 解释每个符号的含义\n2. 推导过程的物理/数学直觉\n"
            "3. 与其他公式的依赖关系\n4. 计算复杂度估计\n"
            "输出清晰的结构化解读。"
        ),
        "tools": ["read_file", "write_file"],
        "temperature": 0.1,
    },
}


@dataclass
class RoleSpec:
    name: str
    label: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    temperature: float = 0.1


class RoleFactory:
    """从预定义角色模板生成 AgentRole 规格。支持从 SOUL.md 文件自定义角色。"""

    @classmethod
    def list_roles(cls) -> list[str]:
        return list(ROLE_TEMPLATES.keys())

    @classmethod
    def build(cls, role_key: str) -> RoleSpec:
        if role_key not in ROLE_TEMPLATES:
            raise ValueError(f"Unknown role: {role_key}. Available: {cls.list_roles()}")
        tpl = ROLE_TEMPLATES[role_key]
        return RoleSpec(
            name=role_key,
            label=tpl["label"],
            system_prompt=tpl["system_prompt"],
            tools=tpl.get("tools", []),
            temperature=tpl.get("temperature", 0.1),
        )

    @classmethod
    def build_from_soul(cls, workspace: Path) -> RoleSpec | None:
        """从工作区的 SOUL.md 文件构建自定义角色。"""
        soul_file = workspace / "SOUL.md"
        if not soul_file.exists():
            return None
        content = soul_file.read_text(encoding="utf-8")[:2000]
        return RoleSpec(
            name="custom",
            label="自定义 Agent",
            system_prompt=content,
            tools=["read_file", "write_file", "web_search", "shell"],
            temperature=0.2,
        )
