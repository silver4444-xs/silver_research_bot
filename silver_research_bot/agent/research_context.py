"""研究上下文追踪器 — 跨对话轮次追踪当前研究焦点，无 LLM 调用。

追踪内容：
- active_papers: 当前讨论涉及的论文 ID 列表
- focus_topics: 当前关注的研究主题关键词
- recent_findings: 最近几轮的关键发现摘要

全部通过规则匹配 + 会话 metadata 存储，不产生额外 LLM 成本。
"""

from __future__ import annotations

import json
import re
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from silver_research_bot.session.manager import Session

# 关键发现信号词（中英文）
_FINDING_SIGNALS = re.compile(
    r"(关键发现|主要贡献|核心结论|实验表明|结果表明|研究发现|"
    r"key finding|main contribution|result shows?|experiment demonstrates?|"
    r"we find that|we observe that|the paper proposes|论文提出)",
    re.IGNORECASE,
)

# 主题关键词提取：匹配中英文技术术语
_TOPIC_RE = re.compile(
    r"(transformer|attention|diffusion|reinforcement learning|"
    r"fine.tun|pretrain|embedding|tokenization|decoder|encoder|"
    r"注意力机制|扩散模型|强化学习|微调|预训练|嵌入|解码器|编码器|"
    r"CNN|RNN|LSTM|GAN|BERT|GPT|LLM|ViT|MoE|LoRA|RLHF|DPO)",
    re.IGNORECASE,
)

_MAX_FINDINGS = 5
'最多保留的关键发现数'
_MAX_TOPICS = 8
'最多保留的主题关键词数'


class ResearchContextTracker:
    """轻量研究上下文追踪，跨轮次维护研究焦点状态。

    用法::

        tracker = ResearchContextTracker(paper_knowledge)
        tracker.update(session, user_msg, assistant_response)
        ctx_block = tracker.build_context_block(session)
        # → "## Research Context\n- 当前论文: ...\n- 关注主题: ..."
    """

    _META_KEY = "_research_context"
    '会话 metadata 中存储研究上下文的键'

    _MAX_AGE_DAYS = 7  # 超过7天未更新的磁盘状态视为过期

    def __init__(self, paper_knowledge=None, persist_dir: Path | None = None):
        self.paper_knowledge = paper_knowledge
        'PaperKnowledgeIndex 实例，用于解析论文标题'
        self._persist_dir = persist_dir
        '磁盘持久化目录（跨会话恢复）'
        if self._persist_dir:
            self._persist_dir.mkdir(parents=True, exist_ok=True)

    # -- public API ----------------------------------------------------------

    def update(
        self,
        session: "Session",
        user_message: str,
        assistant_response: str = "",
        paper_ids: list[str] | None = None,
    ) -> None:
        """根据最新一轮对话更新研究上下文。

        参数:
            session: 当前会话
            user_message: 用户消息文本
            assistant_response: 助手回复文本
            paper_ids: 本轮涉及的论文 ID（可从 PaperKnowledgeIndex 搜索结果获取）
        """
        ctx = self._load(session)

        # 1) 更新活跃论文
        if paper_ids:
            existing_ids = {p["id"] for p in ctx.get("active_papers", [])}
            for pid in paper_ids:
                if pid not in existing_ids:
                    title = self._resolve_title(pid)
                    ctx.setdefault("active_papers", []).append({
                        "id": pid,
                        "title": title,
                    })
            # 只保留最近 5 篇
            ctx["active_papers"] = ctx["active_papers"][-5:]

        # 2) 更新关注主题
        topics = _TOPIC_RE.findall(user_message)
        if topics:
            existing_topics = set(ctx.get("focus_topics", []))
            for t in topics:
                t_lower = t.lower()
                if t_lower not in {x.lower() for x in existing_topics}:
                    existing_topics.add(t)
            ctx["focus_topics"] = list(existing_topics)[-_MAX_TOPICS:]

        # 3) 提取关键发现
        if assistant_response and _FINDING_SIGNALS.search(assistant_response):
            findings = ctx.get("recent_findings", [])
            sentences = re.split(r"[。.!！?\n]", assistant_response)
            for s in sentences:
                s = s.strip()
                if _FINDING_SIGNALS.search(s) and len(s) > 20:
                    if not any(self._overlap(s, f, 0.6) for f in findings):
                        findings.append(s[:300])
            ctx["recent_findings"] = findings[-_MAX_FINDINGS:]

        self._save(session, ctx)

    def get_context(self, session: "Session") -> dict[str, Any]:
        """获取当前研究上下文（原始 dict）。"""
        return self._load(session)

    def build_context_block(self, session: "Session") -> str:
        """构建可注入系统提示的研究上下文块。"""
        ctx = self._load(session)
        if not any(ctx.values()):
            return ""

        lines = ["## Research Context（当前研究上下文）"]

        papers = ctx.get("active_papers", [])
        if papers:
            lines.append("\n**当前讨论的论文:**")
            for p in papers:
                title = p.get("title", p["id"])
                lines.append(f"- {title} (`{p['id']}`)")

        topics = ctx.get("focus_topics", [])
        if topics:
            lines.append(f"\n**关注主题:** {', '.join(topics)}")

        findings = ctx.get("recent_findings", [])
        if findings:
            lines.append("\n**最近关键发现:**")
            for i, f in enumerate(findings, 1):
                lines.append(f"{i}. {f}")

        return "\n".join(lines) + "\n"

    def save_to_disk(self, session: "Session") -> None:
        """持久化研究上下文到磁盘（跨会话恢复）。"""
        if not self._persist_dir:
            return
        ctx = self._load(session)
        if not any(ctx.values()):
            return
        safe_key = re.sub(r'[^a-zA-Z0-9_-]', '_', session.key)
        path = self._persist_dir / f"research_state_{safe_key}.json"
        ctx["_saved_at"] = _time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime())
        try:
            path.write_text(json.dumps(ctx, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        except Exception:
            pass

    def load_from_disk(self, session: "Session") -> None:
        """从磁盘恢复研究上下文到会话。优先使用会话中的状态。"""
        if not self._persist_dir or self._META_KEY in session.metadata:
            return
        safe_key = re.sub(r'[^a-zA-Z0-9_-]', '_', session.key)
        path = self._persist_dir / f"research_state_{safe_key}.json"
        if not path.exists():
            return
        try:
            ctx = json.loads(path.read_text(encoding="utf-8"))
            # 检查新鲜度
            age_days = (_time.time() - path.stat().st_mtime) / 86400
            if age_days > self._MAX_AGE_DAYS:
                path.unlink(missing_ok=True)
                return
            ctx.pop("_saved_at", None)
            session.metadata[self._META_KEY] = ctx
        except Exception:
            pass

    def clear(self, session: "Session") -> None:
        """重置研究上下文（/reset 时调用）。"""
        session.metadata.pop(self._META_KEY, None)
        if self._persist_dir:
            safe_key = re.sub(r'[^a-zA-Z0-9_-]', '_', session.key)
            path = self._persist_dir / f"research_state_{safe_key}.json"
            path.unlink(missing_ok=True)

    # -- internal ------------------------------------------------------------

    def _load(self, session: "Session") -> dict[str, Any]:
        meta = session.metadata.get(self._META_KEY)
        if isinstance(meta, dict):
            return meta
        return {}

    def _save(self, session: "Session", ctx: dict[str, Any]) -> None:
        session.metadata[self._META_KEY] = ctx

    def _resolve_title(self, paper_id: str) -> str:
        """从 PaperKnowledgeIndex 解析论文标题。"""
        if self.paper_knowledge and self.paper_knowledge._built:
            for chunk in self.paper_knowledge._chunks:
                if chunk["paper_id"] == paper_id:
                    return chunk.get("title", paper_id)
        return paper_id

    @staticmethod
    def _overlap(text1: str, text2: str, threshold: float = 0.6) -> bool:
        """简单的词级 Jaccard 去重。"""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2:
            return False
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return (intersection / union) > threshold if union > 0 else False
