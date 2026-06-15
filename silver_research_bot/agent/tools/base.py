""" agent tools 的基类"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from copy import deepcopy
from typing import Any, TypeVar

_ToolT = TypeVar("_ToolT", bound="Tool")
'_ToolT 只能是 Tool 类型或其子类 TypeVar 是 Python 类型系统中用于定义泛型类型变量的工具。bound="Tool" 表示这个类型变量的上界是 Tool 类'

# Matches :meth:`Tool._cast_value` / :meth:`Schema.validate_json_schema_value` behavior
_JSON_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}
'从 JSON Schema 类型名到 Python 类型的映射表，用于将工具参数定义中的 type 字段转换为实际的 Python 类型'


class Schema(ABC):
    """用于描述工具参数的 JSON Schema 片段的抽象基类
    提供了一套静态方法用于验证参数值是否符合 JSON Schema 规范，并强制子类实现 to_json_schema 方法

    具体类型实现在 silver_research_bot.agent.tools.schema 模块中，
    所有子类必须实现 to_json_schema 和 validate_value 方法。
    类方法 validate_json_schema_value 和 fragment 是共享的验证和标准化入口点
    """

    @staticmethod
    def resolve_json_schema_type(t: Any) -> str | None:
        """
        从 JSON Schema 的 type 字段中提取非 null 的类型名
        处理逻辑：
        -若 t 是列表（例如 ["string", "null"]），则返回列表中第一个不是 "null" 的元素。
        -否则直接返回 t（假设 t 是字符串如 "string"）
        """
        if isinstance(t, list):
            return next((x for x in t if x != "null"), None)
        return t  # type: ignore[return-value]

    @staticmethod
    def subpath(path: str, key: str) -> str:
        """构建嵌套字段的错误路径字符串"""
        return f"{path}.{key}" if path else key

    @staticmethod
    def validate_json_schema_value(val: Any, schema: dict[str, Any], path: str = "") -> list[str]:
        """
        核心验证方法，检查值 val 是否符合给定的 JSON Schema 片段 schema
        """
        raw_type = schema.get("type")
        nullable = (isinstance(raw_type, list) and "null" in raw_type) or schema.get("nullable", False)
        t = Schema.resolve_json_schema_type(raw_type)
        label = path or "parameter"

        if nullable and val is None:
            return []
        if t == "integer" and (not isinstance(val, int) or isinstance(val, bool)):
            return [f"{label} should be integer"]
        if t == "number" and (
            not isinstance(val, _JSON_TYPE_MAP["number"]) or isinstance(val, bool)
        ):
            return [f"{label} should be number"]
        if t in _JSON_TYPE_MAP and t not in ("integer", "number") and not isinstance(val, _JSON_TYPE_MAP[t]):
            return [f"{label} should be {t}"]

        errors: list[str] = []
        if "enum" in schema and val not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")
        if t in ("integer", "number"):
            if "minimum" in schema and val < schema["minimum"]:
                errors.append(f"{label} must be >= {schema['minimum']}")
            if "maximum" in schema and val > schema["maximum"]:
                errors.append(f"{label} must be <= {schema['maximum']}")
        if t == "string":
            if "minLength" in schema and len(val) < schema["minLength"]:
                errors.append(f"{label} must be at least {schema['minLength']} chars")
            if "maxLength" in schema and len(val) > schema["maxLength"]:
                errors.append(f"{label} must be at most {schema['maxLength']} chars")
        if t == "object":
            props = schema.get("properties", {})
            for k in schema.get("required", []):
                if k not in val:
                    errors.append(f"missing required {Schema.subpath(path, k)}")
            for k, v in val.items():
                if k in props:
                    errors.extend(Schema.validate_json_schema_value(v, props[k], Schema.subpath(path, k)))
        if t == "array":
            if "minItems" in schema and len(val) < schema["minItems"]:
                errors.append(f"{label} must have at least {schema['minItems']} items")
            if "maxItems" in schema and len(val) > schema["maxItems"]:
                errors.append(f"{label} must be at most {schema['maxItems']} items")
            if "items" in schema:
                prefix = f"{path}[{{}}]" if path else "[{}]"
                for i, item in enumerate(val):
                    errors.extend(
                        Schema.validate_json_schema_value(item, schema["items"], prefix.format(i))
                    )
        return errors

    @staticmethod
    def fragment(value: Any) -> dict[str, Any]:
        """将 Schema 实例或现有的 JSON Schema 字典标准化为片段字典"""
        # Try to_json_schema first: Schema instances must be distinguished from dicts that are already JSON Schema
        to_js = getattr(value, "to_json_schema", None)
        if callable(to_js):
            return to_js()
        if isinstance(value, dict):
            return value
        raise TypeError(f"Expected schema object or dict, got {type(value).__name__}")

    @abstractmethod
    def to_json_schema(self) -> dict[str, Any]:
        """
        子类必须实现该方法，返回符合 JSON Schema 规范的字典（至少包含 "type" 字段，可能还包含 "properties", "required", "items" 等）。
        该方法被 validate_value 和外部工具注册逻辑调用。."""
        ...

    def validate_value(self, value: Any, path: str = "") -> list[str]:
        """验证单个值；返回错误消息（为空表示通过）。子类可重写该方法以添加额外规则。"""
        return Schema.validate_json_schema_value(value, self.to_json_schema(), path)


class Tool(ABC):
    """所有 Agent 工具能力的基类，例如文件读写、执行命令、网络请求等工具都继承自它"""

    _TYPE_MAP = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    '从 JSON Schema 类型名到 Python 类型的映射，用于参数值的强制转换'
    _BOOL_TRUE = frozenset(("true", "1", "yes"))
    '将字符串转换为布尔值时识别的真值集合'
    _BOOL_FALSE = frozenset(("false", "0", "no"))
    '将字符串转换为布尔值时识别的假值集合'

    @staticmethod
    def _resolve_type(t: Any) -> str | None:
        """从 JSON Schema 的 type 字段中提取非 null 的类型名（例如 ["string", "null"] → "string"）"""
        return Schema.resolve_json_schema_type(t)

    @property
    @abstractmethod
    def name(self) -> str:
        """工具的唯一标识符，用于 LLM 的函数调用"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具功能的自然语言描述，帮助 LLM 理解何时使用."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema 对象，描述工具的参数结构（包括类型、约束等）"""
        ...

    @property
    def read_only(self) -> bool:
        """工具是否无副作用且可安全并行。若为 True，表示只读操作"""
        return False

    @property
    def concurrency_safe(self) -> bool:
        """是否即使启用了并发执行，也要求该工具单独运行（例如写文件）"""
        return self.read_only and not self.exclusive

    @property
    def exclusive(self) -> bool:
        """是否可以与其他并发安全工具同时执行。只读且非独占的工具允许并发"""
        return False

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """子类必须实现的具体工具逻辑。接收经过类型转换和验证后的参数，返回结果（通常是字符串或内容块列表）"""
        ...

    def _cast_object(self, obj: Any, schema: dict[str, Any]) -> dict[str, Any]:
        """递归地将对象 obj 中的每个属性按照其对应的子 schema 进行类型转换"""
        if not isinstance(obj, dict):
            return obj
        props = schema.get("properties", {})
        return {k: self._cast_value(v, props[k]) if k in props else v for k, v in obj.items()}

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """对用户（即 LLM）传入的参数字典进行安全、基于 schema 的类型预转换"""
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            return params
        return self._cast_object(params, schema)

    def _cast_value(self, val: Any, schema: dict[str, Any]) -> Any:
        """根据 JSON Schema 尝试将 val 转换为期望的 Python 类型"""
        t = self._resolve_type(schema.get("type"))

        if t == "boolean" and isinstance(val, bool):
            return val
        if t == "integer" and isinstance(val, int) and not isinstance(val, bool):
            return val
        if t in self._TYPE_MAP and t not in ("boolean", "integer", "array", "object"):
            expected = self._TYPE_MAP[t]
            if isinstance(val, expected):
                return val

        if isinstance(val, str) and t in ("integer", "number"):
            try:
                return int(val) if t == "integer" else float(val)
            except ValueError:
                return val

        if t == "string":
            return val if val is None else str(val)

        if t == "boolean" and isinstance(val, str):
            low = val.lower()
            if low in self._BOOL_TRUE:
                return True
            if low in self._BOOL_FALSE:
                return False
            return val

        if t == "array" and isinstance(val, list):
            items = schema.get("items")
            return [self._cast_value(x, items) for x in val] if items else val

        if t == "object" and isinstance(val, dict):
            return self._cast_object(val, schema)

        return val

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """根据 JSON 模式进行验证；空列表表示有效."""
        if not isinstance(params, dict):
            return [f"parameters must be an object, got {type(params).__name__}"]
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            raise ValueError(f"Schema must be object type, got {schema.get('type')!r}")
        return Schema.validate_json_schema_value(params, {**schema, "type": "object"}, "")

    def to_schema(self) -> dict[str, Any]:
        """返回符合 OpenAI 函数调用规范的字典."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def tool_parameters(schema: dict[str, Any]) -> Callable[[type[_ToolT]], type[_ToolT]]:
    """类装饰器：附加 JSON 模式并注入具体的 ``parameters`` 属性。

    适用于 ``Tool`` 的子类，无需编写 ``@property def parameters``。该
    模式存储在类中，每次访问时都会返回一份新的副本。

    示例::

        @tool_parameters({
            “type”: “object”,
            “properties”: {“path”: {‘type’: “string”}},
            “required”: [“path”],
        })
        class ReadFileTool(Tool):
            ...
    """

    def decorator(cls: type[_ToolT]) -> type[_ToolT]:
        frozen = deepcopy(schema)

        @property
        def parameters(self: Any) -> dict[str, Any]:
            return deepcopy(frozen)

        cls._tool_parameters_schema = deepcopy(frozen)
        cls.parameters = parameters  # type: ignore[assignment]

        abstract = getattr(cls, "__abstractmethods__", None)
        if abstract is not None and "parameters" in abstract:
            cls.__abstractmethods__ = frozenset(abstract - {"parameters"})  # type: ignore[misc]

        return cls

    return decorator
