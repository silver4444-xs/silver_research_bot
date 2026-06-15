"""Shell 执行工具."""

import asyncio
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from silver_research_bot.agent.tools.base import Tool, tool_parameters
from silver_research_bot.agent.tools.sandbox import wrap_command
from silver_research_bot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from silver_research_bot.config.paths import get_media_dir

_IS_WINDOWS = sys.platform == "win32"
'判断当前操作系统是否为 Windows'

@tool_parameters(
    tool_parameters_schema(
        command=StringSchema("The shell command to execute"),
        working_dir=StringSchema("Optional working directory for the command"),
        timeout=IntegerSchema(
            60,
            description=(
                "Timeout in seconds. Increase for long-running commands "
                "like compilation or installation (default 60, max 600)."
            ),
            minimum=1,
            maximum=600,
        ),
        required=["command"],
    )
)
class ExecTool(Tool):
    """执行 shell 命令的工具"""

    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        sandbox: str = "",
        path_append: str = "",
        allowed_env_keys: list[str] | None = None,
    ):
        self.timeout = timeout
        '命令执行超时时间'
        self.working_dir = working_dir
        '命令执行的工作目录'
        self.sandbox = sandbox
        '沙箱环境配置'
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",  # 禁止：rm -r / rm -rf / rm -fr（递归删除文件/文件夹）
            r"\bdel\s+/[fq]\b",  # 禁止：del /f /q（Windows强制静默删除）
            r"\brmdir\s+/s\b",  # 禁止：rmdir /s（Windows递归删除目录）
            r"(?:^|[;&|]\s*)format\b",  # 禁止：format（磁盘格式化命令）
            r"\b(mkfs|diskpart)\b",  # 禁止：mkfs（创建文件系统）、diskpart（Windows磁盘分区）
            r"\bdd\s+if=",  # 禁止：dd if=（磁盘读写/镜像操作）
            r">\s*/dev/sd",  # 禁止：直接写入磁盘设备 /dev/sd…
            r"\b(shutdown|reboot|poweroff)\b",  # 禁止：关机、重启、断电
            r":\(\)\s*\{.*\};\s*:",  # 禁止：fork bomb（fork炸弹，耗尽系统资源崩溃）
            r">>?\s*\S*(?:history\.jsonl|\.dream_cursor)",  # 禁止：重定向写入历史记录文件
            r"\btee\b[^|;&<>]*(?:history\.jsonl|\.dream_cursor)",  # 禁止：tee 写入内部状态文件
            r"\b(?:cp|mv)\b(?:\s+[^\s|;&<>]+)+\s+\S*(?:history\.jsonl|\.dream_cursor)",  # 禁止：cp/mv 覆盖内部文件
            r"\bdd\b[^|;&<>]*\bof=\S*(?:history\.jsonl|\.dream_cursor)",  # 禁止：dd 写入内部文件
            r"\bsed\s+-i[^|;&<>]*(?:history\.jsonl|\.dream_cursor)",  # 禁止：sed -i 直接修改内部文件
        ]
        '禁止执行的危险命令正则列表，包含删除、格式化、磁盘、系统、炸弹攻击及内部文件修改操作'
        self.allow_patterns = allow_patterns or []
        '允许执行的命令白名单正则列表'
        self.restrict_to_workspace = restrict_to_workspace
        '是否限制操作仅在工作目录内执行'
        self.path_append = path_append
        '需要追加到环境变量PATH的路径'
        self.allowed_env_keys = allowed_env_keys or []
        '允许传递的环境变量键名列表'

    @property
    def name(self) -> str:
        return "exec"

    _MAX_TIMEOUT = 600
    _MAX_OUTPUT = 10_000

    @property
    def description(self) -> str:
        return (
            "Execute a shell command and return its output. "
            "Prefer read_file/write_file/edit_file over cat/echo/sed, "
            "and grep/glob over shell find/grep. "
            "Use -y or --yes flags to avoid interactive prompts. "
            "Output is truncated at 10 000 chars; timeout defaults to 60s."
        )

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(
        self, command: str, working_dir: str | None = None,
        timeout: int | None = None, **kwargs: Any,
    ) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()
        '''
        1.确定工作目录
        使用参数 working_dir，否则使用初始化时的 self.working_dir，否则使用 os.getcwd()。
        如果启用了 restrict_to_workspace 并且传入了不同的 working_dir，会检查该目录是否在配置的工作区之内，防止逃逸。
        '''
        # 当启用 restrict_to_workspace 时，防止由 LLM 提供的 working_dir 超出配置的
        # 工作区范围 (#2826)。如果不进行此限制，
        # 调用者可以传入 working_dir="/etc"，此时 /etc 下的所有绝对
        # 路径都将通过基于当前工作目录 (cwd) 的 _guard_command 检查。
        if self.restrict_to_workspace and self.working_dir:
            try:
                requested = Path(cwd).expanduser().resolve()
                workspace_root = Path(self.working_dir).expanduser().resolve()
            except Exception:
                return "Error: working_dir could not be resolved"
            if requested != workspace_root and workspace_root not in requested.parents:
                return "Error: working_dir is outside the configured workspace"

        '''
        2. 安全守卫（_guard_command）
        调用 _guard_command 检查命令是否匹配禁止模式、要求允许列表、包含内部 URL、路径遍历等。若违反则直接返回错误消息
        '''
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error

        '''
        3. 沙箱处理（非 Windows）
        如果配置了 self.sandbox 且不是 Windows，调用 wrap_command 将原始命令包装到沙箱环境中（例如使用 bubblewrap 限制文件系统访问）。
        Windows 下不支持沙箱，记录警告并继续
        '''
        if self.sandbox:
            if _IS_WINDOWS:
                logger.warning(
                    "Sandbox '{}' is not supported on Windows; running unsandboxed",
                    self.sandbox,
                )
            else:
                workspace = self.working_dir or cwd
                command = wrap_command(self.sandbox, command, workspace, cwd)
                cwd = str(Path(workspace).resolve())

        '''
        4. 构建环境变量（_build_env）
        在 Unix 上，默认只传递 HOME, LANG, TERM，然后附加 allowed_env_keys 中的变量。bash -l 会读取用户的 profile 设置 PATH 等。
        在 Windows 上，cmd.exe 没有登录配置，所以需要传递系统必要变量（SYSTEMROOT, PATH, TEMP 等）及允许的额外变量。
        '''
        effective_timeout = min(timeout or self.timeout, self._MAX_TIMEOUT)
        env = self._build_env()

        '''
        5. 处理 path_append
        Windows: 直接修改 env["PATH"]，追加 ; + path_append。
        Unix: 在命令前加上 export PATH="$PATH:{path_append}";。
        '''
        if self.path_append:
            if _IS_WINDOWS:
                env["PATH"] = env.get("PATH", "") + ";" + self.path_append
            else:
                command = f'export PATH="$PATH:{self.path_append}"; {command}'

        '''
        6. 启动子进程（_spawn）
        Unix: 使用 bash -l -c "command"（登录 shell，确保加载用户环境）。
        Windows: 使用 cmd.exe /c command。
        返回 asyncio.subprocess.Process 对象
        '''
        try:
            process = await self._spawn(command, cwd, env)

            '''
            7. 等待命令完成（带超时）
            asyncio.wait_for(process.communicate(), timeout=effective_timeout)。
            超时则调用 _kill_process 终止进程并返回错误
            '''
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=effective_timeout,
                )
            except asyncio.TimeoutError:
                await self._kill_process(process)
                return f"Error: Command timed out after {effective_timeout} seconds"
            except asyncio.CancelledError:
                await self._kill_process(process)
                raise

            output_parts = []

            '''
            8. 处理输出
            合并 stdout 和 stderr（如果 stderr 非空，加上 STDERR: 前缀）。
            附加退出码。
            若总长度超过 _MAX_OUTPUT = 10_000，则截断：保留首尾各一半，中间显示截断统计。
            '''
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))

            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")

            output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"

            max_len = self._MAX_OUTPUT
            if len(result) > max_len:
                half = max_len // 2
                result = (
                    result[:half]
                    + f"\n\n... ({len(result) - max_len:,} chars truncated) ...\n\n"
                    + result[-half:]
                )

            return result

        except Exception as e:
            return f"Error executing command: {str(e)}"

    @staticmethod
    async def _spawn(
        command: str, cwd: str, env: dict[str, str],
    ) -> asyncio.subprocess.Process:
        """根据平台选择 shell 并创建子进程"""
        if _IS_WINDOWS:
            comspec = env.get("COMSPEC", os.environ.get("COMSPEC", "cmd.exe"))
            return await asyncio.create_subprocess_exec(
                comspec, "/c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        bash = shutil.which("bash") or "/bin/bash"
        return await asyncio.create_subprocess_exec(
            bash, "-l", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

    @staticmethod
    async def _kill_process(process: asyncio.subprocess.Process) -> None:
        """终止子进程并回收其资源，以防止僵尸进程"""
        process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        finally:
            if not _IS_WINDOWS:
                try:
                    os.waitpid(process.pid, os.WNOHANG)
                except (ProcessLookupError, ChildProcessError) as e:
                    logger.debug("Process already reaped or not found: {}", e)

    def _build_env(self) -> dict[str, str]:
        """构建一个用于子进程执行的最小化环境。

        在 Unix 系统上，仅传递 HOME/LANG/TERM；``bash -l`` 会加载
        用户的配置文件，该文件会设置 PATH 及其他必要项。

        在 Windows 上，``cmd.exe`` 没有登录配置文件机制，因此会传递一组
        经过筛选的系统变量（包括 PATH）。API 密钥和其他机密信息仍被排除在外。
        """
        if _IS_WINDOWS:
            sr = os.environ.get("SYSTEMROOT", r"C:\Windows")
            env = {
                "SYSTEMROOT": sr,
                "COMSPEC": os.environ.get("COMSPEC", f"{sr}\\system32\\cmd.exe"),
                "USERPROFILE": os.environ.get("USERPROFILE", ""),
                "HOMEDRIVE": os.environ.get("HOMEDRIVE", "C:"),
                "HOMEPATH": os.environ.get("HOMEPATH", "\\"),
                "TEMP": os.environ.get("TEMP", f"{sr}\\Temp"),
                "TMP": os.environ.get("TMP", f"{sr}\\Temp"),
                "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
                "PATH": os.environ.get("PATH", f"{sr}\\system32;{sr}"),
                "APPDATA": os.environ.get("APPDATA", ""),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                "ProgramData": os.environ.get("ProgramData", ""),
                "ProgramFiles": os.environ.get("ProgramFiles", ""),
                "ProgramFiles(x86)": os.environ.get("ProgramFiles(x86)", ""),
                "ProgramW6432": os.environ.get("ProgramW6432", ""),
            }
            for key in self.allowed_env_keys:
                val = os.environ.get(key)
                if val is not None:
                    env[key] = val
            return env
        home = os.environ.get("HOME", "/tmp")
        env = {
            "HOME": home,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TERM": os.environ.get("TERM", "dumb"),
        }
        for key in self.allowed_env_keys:
            val = os.environ.get(key)
            if val is not None:
                env[key] = val
        return env

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """针对可能具有破坏性的命令的尽最大努力的安全防护措施."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        from silver_research_bot.security.network import contains_internal_url
        if contains_internal_url(cmd):
            return "Error: Command blocked by safety guard (internal/private URL detected)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            for raw in self._extract_absolute_paths(cmd):
                try:
                    expanded = os.path.expandvars(raw.strip())
                    p = Path(expanded).expanduser().resolve()
                except Exception:
                    continue

                media_path = get_media_dir().resolve()
                if (p.is_absolute() 
                    and cwd_path not in p.parents 
                    and p != cwd_path
                    and media_path not in p.parents
                    and p != media_path
                ):
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        """使用正则提取三种形式的绝对路径,返回这些路径的列表供 _guard_command 检查"""
        # Windows: match drive-root paths like `C:\` as well as `C:\path\to\file`
        # NOTE: `*` is required so `C:\` (nothing after the slash) is still extracted.
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]*", command)
        posix_paths = re.findall(r"(?:^|[\s|>'\"])(/[^\s\"'>;|<]+)", command) # POSIX: /absolute only
        home_paths = re.findall(r"(?:^|[\s|>'\"])(~[^\s\"'>;|<]*)", command) # POSIX/Windows home shortcut: ~
        return win_paths + posix_paths + home_paths
