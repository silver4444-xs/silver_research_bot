"""Agent core module."""

from silver_research_bot.agent.context import ContextBuilder
from silver_research_bot.agent.hook import AgentHook, AgentHookContext, CompositeHook
from silver_research_bot.agent.loop import AgentLoop
from silver_research_bot.agent.memory import Dream, MemoryStore
from silver_research_bot.agent.memory_keeper import MemoryKeeper, parse_conflict_decisions
from silver_research_bot.agent.paper_knowledge import PaperKnowledgeIndex
from silver_research_bot.agent.research_context import ResearchContextTracker
from silver_research_bot.agent.skills import SkillsLoader
from silver_research_bot.agent.tools.rag_tools import (
    RagAskTool,
    RagCompareTool,
    RagIngestTool,
    RagSearchTool,
    UnifiedRAG,
)
from silver_research_bot.agent.subagent import SubagentManager

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentLoop",
    "CompositeHook",
    "ContextBuilder",
    "Dream",
    "MemoryKeeper",
    "MemoryStore",
    "PaperKnowledgeIndex",
    "ResearchContextTracker",
    "SkillsLoader",
    "SubagentManager",
    "parse_conflict_decisions",
    "RagAskTool",
    "RagCompareTool",
    "RagIngestTool",
    "RagSearchTool",
    "UnifiedRAG",
]
