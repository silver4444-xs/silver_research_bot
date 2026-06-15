"""SLash 命令路由和内置处理程序。"""

from silver_research_bot.command.builtin import register_builtin_commands
from silver_research_bot.command.router import CommandContext, CommandRouter

__all__ = ["CommandContext", "CommandRouter", "register_builtin_commands"]
