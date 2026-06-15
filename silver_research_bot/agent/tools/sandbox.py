"""用于执行 shell 命令的沙箱后端。

要添加一个新的后端，请实现一个具有以下签名的函数：
    _wrap_<name>(command: str, workspace: str, cwd: str) -> str
并在下方的 _BACKENDS 中注册该函数。
"""

import shlex
from pathlib import Path

from silver_research_bot.config.paths import get_media_dir


def _bwrap(command: str, workspace: str, cwd: str) -> str:
    """将命令封装在气泡膜沙箱中（容器内需安装 bwrap）。

    仅工作区被以读写方式绑定挂载；其父目录（包含
    config.json）被隐藏在新的 tmpfs 之后。媒体目录
    以只读方式绑定挂载，以便 exec 命令能够读取上传的附件。
    """
    ws = Path(workspace).resolve()
    media = get_media_dir().resolve()

    try:
        sandbox_cwd = str(ws / Path(cwd).resolve().relative_to(ws))
    except ValueError:
        sandbox_cwd = str(ws)

    required  = ["/usr"]
    optional  = ["/bin", "/lib", "/lib64", "/etc/alternatives",
                 "/etc/ssl/certs", "/etc/resolv.conf", "/etc/ld.so.cache"]

    args = ["bwrap", "--new-session", "--die-with-parent"]
    for p in required: args += ["--ro-bind",     p, p]
    for p in optional: args += ["--ro-bind-try", p, p]
    args += [
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--tmpfs", str(ws.parent),        # mask config dir
        "--dir", str(ws),                 # recreate workspace mount point
        "--bind", str(ws), str(ws),
        "--ro-bind-try", str(media), str(media),  # read-only access to media
        "--chdir", sandbox_cwd,
        "--", "sh", "-c", command,
    ]
    return shlex.join(args)


_BACKENDS = {"bwrap": _bwrap}


def wrap_command(sandbox: str, command: str, workspace: str, cwd: str) -> str:
    """使用指定的沙箱后端封装 *command*。"""
    if backend := _BACKENDS.get(sandbox):
        return backend(command, workspace, cwd)
    raise ValueError(f"Unknown sandbox backend {sandbox!r}. Available: {list(_BACKENDS)}")
