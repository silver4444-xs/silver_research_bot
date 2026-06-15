"""
运行时路径辅助模块，负责根据当前激活的配置上下文来生成和管理各类运行时目录的路径
（如数据目录、日志、媒体文件、cron存储、工作区等）。
所有路径都会自动确保目录存在（通过 ensure_dir）
"""

from __future__ import annotations

from pathlib import Path

from silver_research_bot.config.loader import get_config_path
from silver_research_bot.utils.helpers import ensure_dir


def get_data_dir() -> Path:
    """返回当前实例的运行时数据根目录"""
    return ensure_dir(get_config_path().parent)


def get_runtime_subdir(name: str) -> Path:
    """在数据根目录下创建一个指定名称的子目录并返回其路径"""
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    """在数据根目录下创建一个指定名称的子目录并返回其路径"""
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_dir() -> Path:
    """返回 cron 任务存储目录（例如 Dream 记忆巩固的调度记录、定时任务的状态文件等）"""
    return get_runtime_subdir("cron")


def get_logs_dir() -> Path:
    """返回日志文件存储目录"""
    return get_runtime_subdir("logs")


def get_workspace_path(workspace: str | None = None) -> Path:
    """返回 Agent 的工作区目录（用于文件操作、代码执行等）"""
    path = Path(workspace).expanduser() if workspace else Path.home() / ".silver_research_bot" / "workspace"
    return ensure_dir(path)


def is_default_workspace(workspace: str | Path | None) -> bool:
    """判断给定的工作区路径是否为 silver_research_bot 默认的工作区路径（即 ~/.silver_research_bot/workspace）"""
    current = Path(workspace).expanduser() if workspace is not None else Path.home() / ".silver_research_bot" / "workspace"
    default = Path.home() / ".silver_research_bot" / "workspace"
    return current.resolve(strict=False) == default.resolve(strict=False)


def get_cli_history_path() -> Path:
    """返回命令行界面（CLI）的历史记录文件路径"""
    return Path.home() / ".silver_research_bot" / "history" / "cli_history"


def get_bridge_install_dir() -> Path:
    """返回 WhatsApp bridge（或其他第三方桥接服务）的安装目录."""
    return Path.home() / ".silver_research_bot" / "bridge"


def get_legacy_sessions_dir() -> Path:
    """返回传统的全局会话目录，用于配置迁移或回退场景"""
    return Path.home() / ".silver_research_bot" / "sessions"
