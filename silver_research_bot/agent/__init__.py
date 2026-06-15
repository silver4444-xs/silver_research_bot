"""Agent core module."""

from silver_research_bot.agent.context import ContextBuilder
from silver_research_bot.agent.hook import AgentHook, AgentHookContext, CompositeHook
from silver_research_bot.agent.loop import AgentLoop
from silver_research_bot.agent.memory import Dream, MemoryStore
from silver_research_bot.agent.skills import SkillsLoader
from silver_research_bot.agent.subagent import SubagentManager

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentLoop",
    "CompositeHook",
    "ContextBuilder",
    "Dream",
    "MemoryStore",
    "SkillsLoader",
    "SubagentManager",
]
