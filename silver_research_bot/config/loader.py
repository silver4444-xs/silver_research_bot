"""配置加载工具模块，负责从 JSON 文件中加载应用配置、处理配置迁移、解析环境变量占位符，并将配置中的 SSRF 白名单应用到网络安全模块"""

import json
import os
import re
from pathlib import Path

import pydantic
from loguru import logger

from silver_research_bot.config.schema import Config

# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None
'存储当前使用的配置文件路径，支持多实例运行（每个实例可以有不同的配置路径）'

def set_config_path(path: Path) -> None:
    """设置全局配置路径，供应用启动时指定"""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """
    返回当前配置路径。
    若已通过 set_config_path 设置过，则使用该路径；
    否则返回默认路径 ~/.silver_research_bot/config.json
    """
    if _current_config_path:
        return _current_config_path
    return Path.home() / ".silver_research_bot" / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """
    主加载函数
    流程：
        1.确定最终配置文件路径（参数传入或使用默认/全局路径）。
        2.创建一个默认的 Config 实例（所有字段都是默认值）。
        3.如果配置文件存在：
            - 读取 JSON 文件。
            - 调用 _migrate_config 进行旧格式迁移（向后兼容）。
            - 使用 Pydantic 的 model_validate(data) 验证并转换为 Config 对象。如果 JSON 解析失败、值错误或验证失败，记录警告并使用默认配置。
        4.调用 _apply_ssrf_whitelist(config) 将配置中的 SSRF 白名单应用到网络安全模块。
        5.返回最终配置对象。
    """
    path = config_path or get_config_path()

    # pydantic-settings looks for .env in CWD. If not found there, check
    # the config directory (~/.silver_research_bot/.env) as a fallback so
    # production deployments work regardless of working directory.
    _cwd_env = Path(".env")
    _cfg_env = path.parent / ".env"
    if not _cwd_env.exists() and _cfg_env.exists():
        import os as _os
        _os.chdir(str(path.parent))

    config = Config()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data = _migrate_config(data)
            config = Config.model_validate(data)
        except (json.JSONDecodeError, ValueError, pydantic.ValidationError) as e:
            logger.warning(f"Failed to load config from {path}: {e}")
            logger.warning("Using default configuration.")

    _apply_ssrf_whitelist(config)
    return config


def _apply_ssrf_whitelist(config: Config) -> None:
    """从配置中的 tools.ssrf_whitelist 获取 CIDR 列表，调用安全模块的函数配置白名单（用于绕过 SSRF 黑名单检查，例如允许 Tailscale 网段）"""
    from silver_research_bot.security.network import configure_ssrf_whitelist

    configure_ssrf_whitelist(config.tools.ssrf_whitelist)


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    保存配置

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(mode="json", by_alias=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def resolve_config_env_vars(config: Config) -> Config:
    """
    环境变量解析
    返回一个已解析 ``${VAR}`` 环境变量引用的 *config* 副本。

    仅字符串值会受到影响；其他类型保持不变。
    如果被引用的变量未设置，则引发 :class:`ValueError`.
    """
    data = config.model_dump(mode="json", by_alias=True)
    data = _resolve_env_vars(data)
    return Config.model_validate(data)


def _resolve_env_vars(obj: object) -> object:
    """辅助递归函数"""
    if isinstance(obj, str):
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", _env_replace, obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    return obj


def _env_replace(match: re.Match[str]) -> str:
    name = match.group(1)
    value = os.environ.get(name)
    if value is None:
        raise ValueError(
            f"Environment variable '{name}' referenced in config is not set"
        )
    return value


def _migrate_config(data: dict) -> dict:
    """
    配置迁移
    1.tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    之前 restrictToWorkspace 错误地放在 exec 子配置下，现在提升到 tools 顶层。迁移时：若旧位置存在而新位置没有，则移动该值并删除旧键。

    2.tools.myEnabled / tools.mySet → tools.my.enable / tools.my.allowSet
    最初 my 工具的开关和写权限是平铺在 tools 下的，后来为了对称性（与 web、exec 一致）改成了 tools.my 子对象。迁移时：若存在旧键且新子配置中尚未设置对应的键，则将其移动到 tools.my 中，并删除旧键
    """
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    # Move tools.myEnabled / tools.mySet → tools.my.{enable, allowSet}.
    # The old flat keys shipped in the initial MyTool landing; wrapping them in a
    # sub-config keeps `web` / `exec` / `my` symmetric and gives room to grow.
    if "myEnabled" in tools or "mySet" in tools:
        my_cfg = tools.setdefault("my", {})
        if "myEnabled" in tools and "enable" not in my_cfg:
            my_cfg["enable"] = tools.pop("myEnabled")
        else:
            tools.pop("myEnabled", None)
        if "mySet" in tools and "allowSet" not in my_cfg:
            my_cfg["allowSet"] = tools.pop("mySet")
        else:
            tools.pop("mySet", None)

    return data
