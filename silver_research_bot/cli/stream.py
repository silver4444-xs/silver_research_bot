"""用于 CLI 输出的流式渲染器。

使用 Rich Live 并设置 auto_refresh=False，以实现流式传输过程中稳定、无闪烁的
Markdown 渲染。省略号模式用于处理内容溢出。
"""

from __future__ import annotations

import sys
import time

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from silver_research_bot import __logo__


def _make_console() -> Console:
    """当 stdout 不是 TTY 时，创建一个输出纯文本的控制台。

    Rich 的加载转圈图、实时渲染以及光标可见性转义码均
    基于 ``Console.is_terminal`` 进行判断。强制设置 ``force_terminal=True`` 会覆盖
    对 ``isatty()`` 的检查，导致控制序列（如 ``\\x1b[?25l``、盲文加载转圈图帧）污染程序化调用者，例如
    ``docker exec -i`` 或管道等程序化调用中，即使设置了 ``NO_COLOR`` 或 ``TERM=dumb`` 亦然。
    改用 ``isatty()`` 可确保 Rich 的输出在交互式终端中保持原样，
    而在其他所有场景下均显示为纯文本 (#3265)。
    """
    return Console(file=sys.stdout, force_terminal=sys.stdout.isatty())


class ThinkingSpinner:
    """显示“silver_research_bot 正在思考……”并支持暂停功能的加载图标。"""

    def __init__(self, console: Console | None = None):
        c = console or _make_console()
        self._spinner = c.status("[dim]silver_research_bot is thinking...[/dim]", spinner="dots")
        self._active = False

    def __enter__(self):
        self._spinner.start()
        self._active = True
        return self

    def __exit__(self, *exc):
        self._active = False
        self._spinner.stop()
        return False

    def pause(self):
        """上下文管理器：暂时停止加载动画，以确保输出清晰。"""
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            if self._spinner and self._active:
                self._spinner.stop()
            try:
                yield
            finally:
                if self._spinner and self._active:
                    self._spinner.start()

        return _ctx()


class StreamRenderer:
    """使用 Markdown 实现丰富的实时渲染。设置 auto_refresh=False 可避免渲染竞争。

    来自代理循环的增量数据在到达时已预先过滤（不含 <think> 标签）。

    每轮流程：
      加载图标 -> 首个可见增量 -> 标题 + 实时渲染 ->
      on_end -> 实时渲染停止（内容仍保留在屏幕上）
    """

    def __init__(self, render_markdown: bool = True, show_spinner: bool = True):
        self._md = render_markdown
        self._show_spinner = show_spinner
        self._buf = ""
        self._live: Live | None = None
        self._t = 0.0
        self.streamed = False
        self._spinner: ThinkingSpinner | None = None
        self._start_spinner()

    def _render(self):
        return Markdown(self._buf) if self._md and self._buf else Text(self._buf or "")

    def _start_spinner(self) -> None:
        if self._show_spinner:
            self._spinner = ThinkingSpinner()
            self._spinner.__enter__()

    def _stop_spinner(self) -> None:
        if self._spinner:
            self._spinner.__exit__(None, None, None)
            self._spinner = None

    async def on_delta(self, delta: str) -> None:
        self.streamed = True
        self._buf += delta
        if self._live is None:
            if not self._buf.strip():
                return
            self._stop_spinner()
            c = _make_console()
            c.print()
            c.print(f"[cyan]{__logo__} silver_research_bot[/cyan]")
            self._live = Live(self._render(), console=c, auto_refresh=False)
            self._live.start()
        now = time.monotonic()
        if (now - self._t) > 0.15:
            self._live.update(self._render())
            self._live.refresh()
            self._t = now

    async def on_end(self, *, resuming: bool = False) -> None:
        if self._live:
            self._live.update(self._render())
            self._live.refresh()
            self._live.stop()
            self._live = None
        self._stop_spinner()
        if resuming:
            self._buf = ""
            self._start_spinner()
        else:
            _make_console().print()

    def stop_for_input(self) -> None:
        """在用户输入前停止旋转器，以避免与 prompt_toolkit 发生冲突。"""
        self._stop_spinner()

    async def close(self) -> None:
        """停止旋转/退出，且不渲染最后一轮流式数据。"""
        if self._live:
            self._live.stop()
            self._live = None
        self._stop_spinner()
