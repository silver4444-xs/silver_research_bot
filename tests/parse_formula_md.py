"""Parse 公式汇总.md into a structured evaluation dataset.

Usage:
    from tests.parse_formula_md import parse_formulas_md, ParsedDataset
    dataset = parse_formulas_md()
    print(f"Loaded {len(dataset.display_formulas)} display formulas from {dataset.paper_count} papers")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ── Data classes ──────────────────────────────────────────────────────────

@dataclass
class DisplayFormula:
    """A $$...$$ display math formula extracted from 公式汇总.md."""
    latex: str
    paper_id: int           # 1-20
    paper_name: str         # short paper title
    section: str            # e.g. "1.2 通信模型"
    category: str           # 通信模型/计算模型/能耗模型/控制模型/AoI/RL/优化问题/感知模型/其他
    line_number: int        # approximate line in source file


@dataclass
class InlineFormula:
    """A $...$ inline math formula extracted from 公式汇总.md."""
    latex: str
    paper_id: int
    paper_name: str
    section: str
    category: str
    context: str            # surrounding text (~60 chars)


@dataclass
class ParsedDataset:
    """Complete parsed dataset from 公式汇总.md."""
    display_formulas: list[DisplayFormula] = field(default_factory=list)
    inline_formulas: list[InlineFormula] = field(default_factory=list)
    paper_count: int = 0
    papers_with_formulas: int = 0
    papers_png_only: list[int] = field(default_factory=list)

    @property
    def category_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.display_formulas:
            counts[f.category] = counts.get(f.category, 0) + 1
        return counts


# ── Category detection ────────────────────────────────────────────────────

_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("通信模型|信道模型|SINR|SNR|香农|NOMA|路径损耗|LoS|传输速率|衰落|Nakagami", "通信模型"),
    ("计算模型|计算时延|计算能耗|本地计算|边缘计算|卸载|MEC|任务处理|队列", "计算模型"),
    ("能耗模型|推进功率|飞行能耗|悬停能耗|能量模型|电池|DVFS", "能耗模型"),
    ("AoI|AoT|信息年龄|Age of|AoI模型|AoT模型", "AoI"),
    ("DQN|DDPG|A3C|MAPPO|MADDPG|MATD3|MASAC|PPO|Actor|Critic|RL|强化学习|状态.*动作.*奖励|策略|观测", "RL模型"),
    ("问题表述|P1|优化问题|目标函数|min.*\\\\max|约束", "优化问题"),
    ("控制模型|移动模型|轨迹|位置更新|速度|运动学|动力学", "控制模型"),
    ("感知模型|雷达|感知数据|覆盖半径|FOV|探测", "感知模型"),
    ("DT模型|数字孪生", "数字孪生"),
    ("覆盖模型|覆盖评分|覆盖判据|公平指数", "覆盖模型"),
    ("Cobb.*Douglas|效用函数|经济成本", "效用模型"),
]


def _detect_category(section_title: str) -> str:
    """Classify a section by its title keywords."""
    for pattern, cat in _CATEGORY_KEYWORDS:
        if re.search(pattern, section_title):
            return cat
    return "其他"


# ── PNG-only papers (no extractable LaTeX) ────────────────────────────────

_PNG_ONLY_PAPERS = {2, 6, 7, 10, 16}


# ── Main parser ───────────────────────────────────────────────────────────

def parse_formulas_md(filepath: str | Path | None = None) -> ParsedDataset:
    """Parse 公式汇总.md and return a structured ParsedDataset.

    Args:
        filepath: Path to 公式汇总.md. If None, auto-discovers relative to project root.

    Returns:
        ParsedDataset with all extracted formulas, categorized by paper and type.
    """
    if filepath is None:
        filepath = Path(__file__).resolve().parent.parent / "公式汇总.md"
    else:
        filepath = Path(filepath)

    if not filepath.exists():
        raise FileNotFoundError(f"公式汇总.md not found at {filepath}")

    text = filepath.read_text(encoding="utf-8")
    lines = text.split("\n")

    dataset = ParsedDataset()
    dataset.papers_png_only = sorted(_PNG_ONLY_PAPERS)

    # State machine: track current paper and section
    current_paper_id = 0
    current_paper_name = ""
    current_section = ""
    skip_paper = False

    # Regex patterns
    paper_header_re = re.compile(r"^##\s+(\d+)\.\s+(.+)$")
    section_re = re.compile(r"^###\s+(\d+\.\d+)\s+(.+)$")
    display_math_re = re.compile(r"\$\$(.+?)\$\$")
    # Inline math: $...$ but not $$...$$, handle escaped dollars
    inline_math_re = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")

    line_idx = 0
    while line_idx < len(lines):
        line = lines[line_idx]

        # Detect paper header: "## N. Paper Name"
        pm = paper_header_re.match(line)
        if pm:
            current_paper_id = int(pm.group(1))
            current_paper_name = pm.group(2).strip()
            skip_paper = current_paper_id in _PNG_ONLY_PAPERS
            if not skip_paper:
                dataset.papers_with_formulas += 1
            dataset.paper_count = max(dataset.paper_count, current_paper_id)
            current_section = ""
            line_idx += 1
            continue

        # Detect section header: "### N.M Section Name"
        sm = section_re.match(line)
        if sm:
            current_section = f"{sm.group(1)} {sm.group(2)}"
            line_idx += 1
            continue

        # Detect subsection via bold: "**Title:**"
        bold_re = re.match(r"^\*\*(.+?)[：:]\*\*\s*(.*)", line)
        if bold_re:
            subsection_title = bold_re.group(1).strip()
            rest = bold_re.group(2).strip()

            # The line after the bold title might contain a formula
            if not skip_paper and current_paper_id > 0:
                _extract_inline_from_text(rest, current_paper_id, current_paper_name,
                                          f"{current_section} / {subsection_title}", dataset, line_idx)

            # Also check if the formula continues on this line
            if not skip_paper and current_paper_id > 0:
                _extract_display_from_text(rest, current_paper_id, current_paper_name,
                                           f"{current_section} / {subsection_title}", dataset, line_idx)

            line_idx += 1
            continue

        # Skip PNG-only papers
        if skip_paper or current_paper_id == 0:
            line_idx += 1
            continue

        # Extract display formulas ($$...$$) — can span multiple lines
        for dm in display_math_re.finditer(line):
            latex = dm.group(1).strip()
            if len(latex) >= 5 and "◈" not in latex:
                category = _detect_category(current_section)
                dataset.display_formulas.append(DisplayFormula(
                    latex=latex,
                    paper_id=current_paper_id,
                    paper_name=current_paper_name,
                    section=current_section,
                    category=category,
                    line_number=line_idx + 1,
                ))

        # Extract inline formulas ($...$)
        _extract_inline_from_text(line, current_paper_id, current_paper_name,
                                  current_section, dataset, line_idx)

        line_idx += 1

    return dataset


def _extract_display_from_text(text: str, paper_id: int, paper_name: str,
                                section: str, dataset: ParsedDataset, line_no: int) -> None:
    """Extract $$...$$ from a single line of text."""
    for dm in re.finditer(r"\$\$(.+?)\$\$", text):
        latex = dm.group(1).strip()
        if len(latex) >= 5 and "◈" not in latex:
            dataset.display_formulas.append(DisplayFormula(
                latex=latex,
                paper_id=paper_id,
                paper_name=paper_name,
                section=section,
                category=_detect_category(section),
                line_number=line_no,
            ))


def _extract_inline_from_text(text: str, paper_id: int, paper_name: str,
                               section: str, dataset: ParsedDataset, line_no: int) -> None:
    """Extract $...$ inline math from a single line."""
    for im in re.finditer(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", text):
        latex = im.group(1).strip()
        if len(latex) < 3:
            continue
        if "◈" in latex:
            continue
        # Skip pure text descriptions (not real LaTeX)
        if re.search(r"[一-鿿]", latex):
            continue
        # Build context from surrounding text
        ctx_start = max(0, im.start() - 60)
        ctx_end = min(len(text), im.end() + 60)
        context = text[ctx_start:ctx_end].strip()
        dataset.inline_formulas.append(InlineFormula(
            latex=latex,
            paper_id=paper_id,
            paper_name=paper_name,
            section=section,
            category=_detect_category(section),
            context=context,
        ))
