"""动态工具管理的工具注册表"""

from typing import Any

from silver_research_bot.agent.tools.base import Tool


class ToolRegistry:
    """
    agent工具注册表。
    负责工具的注册、注销、查找、定义生成、参数预处理和执行，
    并为 LLM 提供统一、稳定排序的工具定义，以提升缓存效果和决策稳定性
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None
        '缓存 get_definitions() 的返回值，避免重复计算。当注册或注销工具时，缓存被清空'

    def register(self, tool: Tool) -> None:
        """注册工具."""
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        """按名称移除工具"""
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        """根据名称返回工具实例"""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """判断工具是否已注册"""
        return name in self._tools

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        """从 OpenAI 或扁平化模式中提取标准化后的工具名称。"""
        fn = schema.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str):
                return name
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    def get_definitions(self) -> list[dict[str, Any]]:
        """
        用于生成发送给 LLM 的函数调用 schema 列表。

        设计要点：
        1.缓存机制：若 _cached_definitions 非空，直接返回缓存，避免每轮 LLM 调用都重新构建。
        2.工具分类：遍历所有工具，根据名称前缀 mcp_ 区分两类：
            -builtins：内置工具（如文件读写、Shell 执行）
            -mcp_tools：通过 MCP 协议动态加载的外部工具
        3.稳定排序：分别对两类工具按名称排序（通过辅助函数 _schema_name 提取标准化名称），
        然后 builtins + mcp_tools 拼接。这种顺序保证了每次生成的列表顺序一致，有利于 LLM 的 prompt 缓存（相同序列的 function 定义可以复用缓存键）。
        4.辅助函数 _schema_name：兼容 OpenAI 标准格式（{"function": {"name": ...}}）和扁平格式（{"name": ...}），提取工具名称。

        返回格式：每个元素是 tool.to_schema() 的结果，即符合 OpenAI 函数调用规范的字典
        """
        if self._cached_definitions is not None:
            return self._cached_definitions

        definitions = [tool.to_schema() for tool in self._tools.values()]
        builtins: list[dict[str, Any]] = []
        mcp_tools: list[dict[str, Any]] = []
        for schema in definitions:
            name = self._schema_name(schema)
            if name.startswith("mcp_"):
                mcp_tools.append(schema)
            else:
                builtins.append(schema)

        builtins.sort(key=self._schema_name)
        mcp_tools.sort(key=self._schema_name)
        self._cached_definitions = builtins + mcp_tools
        return self._cached_definitions

    def prepare_call(
        self,
        name: str,
        params: dict[str, Any],
    ) -> tuple[Tool | None, dict[str, Any], str | None]:
        """在正式执行工具前，对参数进行类型转换和验证，返回三元组 (tool, cast_params, error)"""
        # Guard against invalid parameter types (e.g., list instead of dict)
        if not isinstance(params, dict) and name in ('write_file', 'read_file'):
            return None, params, (
                f"Error: Tool '{name}' parameters must be a JSON object, got {type(params).__name__}. "
                "Use named parameters: tool_name(param1=\"value1\", param2=\"value2\")"
            )

        tool = self._tools.get(name)
        if not tool:
            return None, params, (
                f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"
            )

        cast_params = tool.cast_params(params)
        errors = tool.validate_params(cast_params)
        if errors:
            return tool, cast_params, (
                f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            )
        return tool, cast_params, None

    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        """根据名称并使用给定的参数执行工具。"""
        _HINT = "\n\n[Analyze the error above and try a different approach.]"
        tool, params, error = self.prepare_call(name, params)
        if error:
            return error + _HINT

        try:
            assert tool is not None  # guarded by prepare_call()
            result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                return result + _HINT
            return result
        except Exception as e:
            return f"Error executing {name}: {str(e)}" + _HINT

    @property
    def tool_names(self) -> list[str]:
        """返回所有已注册工具的名称列表"""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
