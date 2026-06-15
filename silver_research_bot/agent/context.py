"""负责构建发送给 LLM 的完整消息列表"""

import base64
import mimetypes
import platform
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import Any

from silver_research_bot.agent.memory import MemoryStore
from silver_research_bot.agent.skills import SkillsLoader
from silver_research_bot.utils.helpers import build_assistant_message, current_time_str, detect_image_mime
from silver_research_bot.utils.prompt_templates import render_template


class ContextBuilder:
    """
        构建发送给 LLM 的消息列表。
        包括 system prompt、历史消息和运行时上下文。
    """

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
    '''每次对话都会自动加载的工作区根目录下的文件名列表（AGENTS.md, SOUL.md, USER.md, TOOLS.md），用于注入 Agent 的身份、规则、工具说明等内容'''
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"
    '''运行时上下文块的起始标记，用于区分临时元数据和用户真实输入'''
    _MAX_RECENT_HISTORY = 50
    '''从 MemoryStore 中读取“最近历史”时最多取多少条记录'''
    _RUNTIME_CONTEXT_END = "[/Runtime Context]"
    '''运行时上下文块的结束标记'''

    def __init__(self, workspace: Path, timezone: str | None = None, disabled_skills: list[str] | None = None):
        self.workspace = workspace
        '工作区目录'
        self.timezone = timezone
        '时区（用于运行时时间显示）'
        self.memory = MemoryStore(workspace)
        '长期记忆存储（dream 相关）'
        self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills) if disabled_skills else None)
        '技能加载器，管理可动态激活的提示模块'

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
    ) -> str:
        """
        构建系统提示
        组装顺序（最终通过 "\n\n---\n\n".join(parts) 连接）：
        1.身份标识：_get_identity 生成包含工作路径、操作系统版本、运行时的基本信息。
        2.引导文件：读取 BOOTSTRAP_FILES 中存在的文件内容并嵌入。
        3.长期记忆上下文：若 memory.get_memory_context() 有内容，且用户尚未修改默认的 MEMORY.md，则添加 # Memory 块。
        4.常驻技能：always_skills 总是生效的技能，直接加载其内容。
        5.技能概览：构建一个可用技能的摘要列表（方便 LLM 了解能调用哪些技能）。
        6.近期历史：从 memory 读取未被 “dream” 处理过的历史条目，最多 _MAX_RECENT_HISTORY 条，按时间列出。
        """
        parts = [self._get_identity(channel=channel)]

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        memory = self.memory.get_memory_context()
        if memory and not self._is_template_content(self.memory.read_memory(), "memory/MEMORY.md"):
            parts.append(f"# Memory\n\n{memory}")

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
        if skills_summary:
            parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

        entries = self.memory.read_unprocessed_history(since_cursor=self.memory.get_last_dream_cursor())
        if entries:
            capped = entries[-self._MAX_RECENT_HISTORY:]
            parts.append("# Recent History\n\n" + "\n".join(
                f"- [{e['timestamp']}] {e['content']}" for e in capped
            ))

        return "\n\n---\n\n".join(parts)

    def _get_identity(self, channel: str | None = None) -> str:
        """
        身份信息模板
        读取 agent/identity.md 模板，填充：
            -workspace_path：工作区绝对路径。
            -runtime：操作系统 + Python 版本。
            -platform_policy：加载 agent/platform_policy.md（设定平台特定的安全策略）。
            -channel：当前渠道（cli / feishu 等）
        """
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            runtime=runtime,
            platform_policy=render_template("agent/platform_policy.md", system=system),
            channel=channel or "",
        )

    @staticmethod
    def _build_runtime_context(
        channel: str | None, chat_id: str | None, timezone: str | None = None,
        session_summary: str | None = None,
    ) -> str:
        """
        运行时上下文块 在用户消息之前构建用于注入的不可信运行时元数据块。
        返回一个被 _RUNTIME_CONTEXT_TAG 和 _RUNTIME_CONTEXT_END 包裹的文本块，包含：
        -当前时间（根据时区格式化）
        -渠道信息（channel / chat_id）
        -可选的会话摘要（session_summary，通常由自动压缩产生）
        这块内容不会被持久化到会话历史（_save_turn 时会剥离），只用于本次 LLM 请求
        """
        lines = [f"Current Time: {current_time_str(timezone)}"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if session_summary:
            lines += ["", "[Resumed Session]", session_summary]
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines) + "\n" + ContextBuilder._RUNTIME_CONTEXT_END

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        """合并两个消息内容"""
        if isinstance(left, str) and isinstance(right, str):
            return f"{left}\n\n{right}" if left else right

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [item if isinstance(item, dict) else {"type": "text", "text": str(item)} for item in value]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    def _load_bootstrap_files(self) -> str:
        """
        加载引导文件
        依次尝试读取 workspace/AGENTS.md、SOUL.md 等文件，每个文件以 ## 文件名 为标题，内容紧随其后。
        适用于让 Agent 了解项目约定、用户偏好或自定义工具说明
        """
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _is_template_content(content: str, template_path: str) -> bool:
        """判断内容是否仍为默认模板"""
        try:
            tpl = pkg_files("nanobot") / "templates" / template_path
            if tpl.is_file():
                return content.strip() == tpl.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        return False

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        current_role: str = "user",
        session_summary: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        核心消息组装:回一个完整的消息列表 list[dict]，可直接发送给 LLM
        步骤：
        1.构建运行时上下文块 runtime_ctx。
        2.构建用户内容 user_content（通过 _build_user_content 将文本和图片转为多模态块）。
        3.将 runtime_ctx 与 user_content 合并成一个单条用户消息（避免连续两条 user 消息）。
        4.初始化消息列表：system（系统提示） + 历史记录 history。
        5.如果最后一条消息的角色与将要追加的角色 current_role 相同，则合并内容（调用 _merge_message_content），否则追加新消息。
        """
        runtime_ctx = self._build_runtime_context(channel, chat_id, self.timezone, session_summary=session_summary)
        user_content = self._build_user_content(current_message, media)

        # Merge runtime context and user content into a single user message
        # to avoid consecutive same-role messages that some providers reject.
        if isinstance(user_content, str):
            merged = f"{runtime_ctx}\n\n{user_content}"
        else:
            merged = [{"type": "text", "text": runtime_ctx}] + user_content
        messages = [
            {"role": "system", "content": self.build_system_prompt(skill_names, channel=channel)},
            *history,
        ]
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), merged)
            messages[-1] = last
            return messages
        messages.append({"role": current_role, "content": merged})
        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """构建用户消息内容，可选包含 Base64 编码的图片。"""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            images.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
                "_meta": {"path": str(p)},
            })

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: Any,
    ) -> list[dict[str, Any]]:
        """向消息列表中添加tool调用结果"""
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """向消息列表中添加assistant结果"""
        messages.append(build_assistant_message(
            content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
        ))
        return messages
