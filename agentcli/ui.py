# agentcli/ui.py
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple, List

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.status import Status
from rich.text import Text
from rich.live import Live
from rich.markdown import Markdown

from agentcli.config import AgentState

# ---------------------------------
# Hardcoded palette
# ---------------------------------
# Borders
BORDER_AGENT = "cyan"
BORDER_COMMANDS = "blue"
BORDER_TOOLS_LIST = "magenta"
BORDER_TOOL = "yellow"
BORDER_DIFF = "magenta"
BORDER_ASSISTANT = "sky_blue1"

# Titles
TITLE_AGENT = "bold cyan"
TITLE_COMMANDS = "bold blue"
TITLE_TOOLS_LIST = "bold magenta"
TITLE_TOOL = "bold yellow"
TITLE_DIFF = "bold magenta"
TITLE_ASSISTANT = "red"

# Text roles
TXT_MUTED = "dim"
TXT_LABEL = "bold yellow"
TXT_VALUE = "green"
TXT_ACCENT = "bold cyan"
TXT_WHITE = "white"
TXT_RED = "red"

# Status
TXT_SUCCESS = "green"
TXT_WARN = "yellow"
TXT_ERROR = "bold red"

# Section headers (inside commands panel)
TXT_SECTION = "bold blue"
TXT_SECTION_ICON = "dim"

console = Console()


# -----------------------------
# Helpers
# -----------------------------
def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _now_ts() -> str:
    return datetime.now().strftime("%H:%M:%S %p")


def hr(title: str, style: str) -> None:
    console.print(Rule(title, style=style, characters="─"))


def format_usage(usage: Optional[Dict[str, int]]) -> str:
    if not usage:
        return ""
    pt = usage.get("prompt_tokens")
    ct = usage.get("completion_tokens")
    tt = usage.get("total_tokens")
    parts = []
    if pt is not None:
        parts.append(f"prompt {pt}")
    if ct is not None:
        parts.append(f"completion {ct}")
    if tt is not None:
        parts.append(f"total {tt}")
    return " | ".join(parts)


def _format_config_lines(state: AgentState) -> list[Text]:
    base_url = getattr(state, "base_url", None) or "(none)"
    api_key = "(set)" if getattr(state, "api_key", "") else "(missing)"
    truncate_lines = getattr(state, "truncate_lines", 10)
    verbose = getattr(state, "verbose", False)
    autosave = getattr(state, "autosave", True)
    session_name = getattr(state, "session_name", "default")
    sessions_dir = getattr(state, "sessions_dir", "./sessions")

    def row(k: str, v: str) -> Text:
        t = Text()
        t.append(f"{k:<16}", style=TXT_LABEL)
        t.append(": ", style=TXT_MUTED)
        t.append(str(v), style=TXT_WHITE)
        return t

    return [
        row("cwd", state.cwd),
        row("model", state.model),
        row("auto_approve", str(state.auto_approve)),
        row("base_url", base_url),
        row("timeout", f"{state.request_timeout}s"),
        row("api_key", api_key),
        row("session", session_name),
        row("autosave", str(autosave)),
        row("sessions_dir", sessions_dir),
        row("truncate_lines", f"{truncate_lines} (0 = no truncation)"),
        row("verbose", str(verbose)),
    ]


# -----------------------------
# Assistant block (streaming)
# -----------------------------
def print_assistant_header() -> None:
    hr(" Assistant ", style=TITLE_ASSISTANT)


def print_assistant_footer(usage: Optional[Dict[str, int]] = None) -> None:
    ts = _now_ts()
    if usage:
        u = format_usage(usage)
        hr(f" Assistant  ({u})  ·  {ts} ", style=TITLE_ASSISTANT)
    else:
        hr(f" Assistant  ·  {ts} ", style=TITLE_ASSISTANT)


# Commands that work WITHOUT slash
no_slash_ok = { "help", "tools", "clear", "paste", "exit", "quit", "reset", "config"}

def _cmd_line(cmd: str, desc: str) -> Text:
    """
    Adds a green (*) marker for commands that work without slashes.
    """
    t = Text()

    # Extract head (strip leading /)
    head = cmd.strip()
    if head.startswith("/") or head.startswith("\\"):
        head = head[1:]
    head = head.split()[0].lower()

    star = head in no_slash_ok

    t.append("• ", style=TXT_MUTED)
    t.append(cmd, style=TXT_ACCENT)

    if star:
        t.append(" ", style=TXT_MUTED)
        t.append("(*)", style=TXT_SUCCESS)

    t.append(" — ", style=TXT_MUTED)
    t.append(desc, style=TXT_MUTED)
    return t


def print_banner(state: AgentState) -> None:
    """
    Startup UI:
      - Full-width agentcli panel showing config (like /config)
      - Full-width Commands panel with grouped sections
      - Then a separate dim line: Type your message and press Enter.
    """
    from agentcli import __version__

    config_rows = _format_config_lines(state)
    agent_panel = Panel(
        Group(*config_rows),
        title=Text(f"agentcli ({__version__})", style=TITLE_AGENT),
        border_style=BORDER_AGENT,
        expand=True,
        box=box.ROUNDED,
    )

    def _section(title: str) -> Text:
        t = Text()
        t.append("— ", style=TXT_MUTED)
        t.append(title, style=TXT_RED)
        t.append(" —", style=TXT_MUTED)
        return t

    # Grouped commands (premium + readable)
    cmd_lines: list[Text] = []

    # Core
    cmd_lines.append(_section("Core"))
    for c, desc in [
        ("/help", "show help"),
        ("/tools", "list available tools"),
        ("/config", "show current config"),
        ("/clear", "clear screen"),
        ("/paste", "multi-line prompt (end with /end)"),
        ("/reset", "reset conversation context (same session)"),
        ("/exit or /quit", "quit"),
    ]:
        cmd_lines.append(_cmd_line(c, desc))

    cmd_lines.append(Text(""))  # spacer

    # Workspace & behavior
    cmd_lines.append(_section("Workspace & Behavior"))
    for c, desc in [
        ("/cwd <path>", "change workspace"),
        ("/model <name>", "change model"),
        ("/approve on|off", "toggle approvals"),
        ("/truncate <n>", "tool output line limit (0 = no truncation)"),
        ("/verbose on|off", "toggle verbose tool output"),
    ]:
        cmd_lines.append(_cmd_line(c, desc))

    cmd_lines.append(Text(""))  # spacer

    # Sessions
    cmd_lines.append(_section("Sessions"))
    for c, desc in [
        ("/session", "show current session info"),
        ("/sessions", "list sessions"),
        ("/new-session [name]", "create & switch to a new session"),
        ("/load <name>", "load a session"),
        ("/save [name]", "save current session (optionally as new name)"),
        ("/rename <old> <new>", "rename a session"),
        ("/delete <name>", "delete a session"),
        ("/autosave on|off", "toggle autosave to disk"),
    ]:
        cmd_lines.append(_cmd_line(c, desc))
    
    # Footer Hint
    cmd_lines.append(Text(""))
    hint = Text()
    hint.append("(*) ", style=TXT_SUCCESS)
    hint.append("works without slashes", style=TXT_MUTED)
    cmd_lines.append(hint)

    cmd_lines.append(Text(""))
    cmd_lines.append(_section("CLI Flags"))

    flags = [
        ("--cwd, -C", "workspace directory"),
        ("--model, -m", "LLM model name"),
        ("--auto-approve / --no-auto-approve, -y/-n", "toggle approvals"),
        ("--base-url", "optional provider base URL"),
        ("--request-timeout, -t", "LLM timeout (seconds)"),
        ("--truncate-lines", "tool output line limit (0 = no truncation)"),
        ("--verbose / --no-verbose", "verbose tool output"),
        ("--autosave / --no-autosave", "autosave sessions to disk"),
        ("--session, -s", "session name to load/create"),
    ]

    for flag, desc in flags:
        t = Text()
        t.append("• ", style=TXT_MUTED)
        t.append(flag, style=TXT_ACCENT)
        t.append(" — ", style=TXT_MUTED)
        t.append(desc, style=TXT_MUTED)
        cmd_lines.append(t)

    commands_panel = Panel(
        Group(*cmd_lines),
        title=Text("Commands", style=TITLE_COMMANDS),
        border_style=BORDER_COMMANDS,
        expand=True,
        box=box.ROUNDED,
    )

    console.print(agent_panel)
    console.print(commands_panel)
    console.print(Text("Type your message and press Enter.", style=TXT_MUTED))
    console.print()


def print_help(state: AgentState) -> None:
    print_banner(state)


def print_tools(state: AgentState) -> None:
    from agentcli.tools.registry import get_tools

    tools = get_tools()
    if not tools:
        console.print(
            Panel(
                Text("(no tools registered)", style=TXT_MUTED),
                title=Text("Available Tools", style=TITLE_TOOLS_LIST),
                border_style=BORDER_TOOLS_LIST,
                expand=True,
                box=box.ROUNDED,
            )
        )
        console.print()
        return

    body_lines: list[Text] = []
    for tinfo in tools:
        t = Text()
        t.append(tinfo.name, style=TXT_LABEL)
        t.append(" — ", style=TXT_MUTED)
        t.append(tinfo.description, style=TXT_WHITE)
        body_lines.append(t)

    console.print(
        Panel(
            Group(*body_lines),
            title=Text("Available Tools", style=TITLE_TOOLS_LIST),
            border_style=BORDER_TOOLS_LIST,
            expand=True,
            box=box.ROUNDED,
        )
    )
    console.print()


def print_config_panel(state: AgentState) -> None:
    rows = _format_config_lines(state)
    console.print(
        Panel(
            Group(*rows),
            title=Text("Current Config", style=TITLE_AGENT),
            border_style=BORDER_AGENT,
            expand=True,
            box=box.ROUNDED,
        )
    )
    console.print()


# -----------------------------
# Tool panel rendering
# -----------------------------
def _infer_tool_status(lines: list[str]) -> Tuple[str, str]:
    """
    Returns (status, icon):
      status in {"success","warn","error","info"}
    """
    text = "\n".join(lines).lower()

    if "user_disapproved" in text or "operation rejected" in text:
        return "warn", "✋"
    if "[error]" in text or "traceback" in text or "httpstatuserror" in text or "forbidden" in text:
        return "error", "✖"
    if lines:
        return "success", "✓"
    return "info", "•"


def print_tool_panel(title: str, lines: list[str], footer: str | None = None) -> None:
    """
    Tool panel with:
      - Status badge + icon in title
      - Dim timestamp
      - Optional footer
    """
    status, icon = _infer_tool_status(lines)

    badge = {
        "success": ("SUCCESS", TXT_SUCCESS),
        "warn": ("WARNING", TXT_WARN),
        "error": ("ERROR", TXT_ERROR),
        "info": ("INFO", TXT_MUTED),
    }.get(status, ("INFO", TXT_MUTED))

    title_text = Text()
    title_text.append(title, style=TITLE_TOOL)
    title_text.append("  ")
    title_text.append(icon + " ", style=badge[1])
    title_text.append(badge[0], style=badge[1])

    body = "\n".join(lines).rstrip() if lines else ""
    body_text = Text(body, style=TXT_VALUE) if body else Text("", style=TXT_VALUE)

    ts = _now_ts()
    footer_lines: list[Text] = [Text(ts, style=TXT_MUTED)]
    if footer:
        footer_lines.append(Text(footer, style=TXT_MUTED))

    console.print(
        Panel(
            Group(body_text, *footer_lines),
            title=title_text,
            border_style=BORDER_TOOL,
            expand=True,
            box=box.ROUNDED,
        )
    )
    console.print()


# -----------------------------
# Waiting spinner + streaming printer
# -----------------------------
class WaitingIndicator:
    """
    Spinner that can be started immediately and stopped when streaming begins.
    """
    def __init__(self, message: str = "Waiting for LLM response...") -> None:
        self._status = Status(message, console=console, spinner="dots", spinner_style="cyan")
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._status.start()
            self._started = True

    def stop(self) -> None:
        if self._started:
            self._status.stop()
            self._started = False


class StreamPrinter:
    """
    Streaming assistant output rendered inside a single Panel (no duplication).
    - Shows spinner until first non-whitespace chunk arrives.
    - Starts a Live panel on first token and continuously updates it.
    - On end(), swaps the panel content to Markdown-rendered output.
    """
    def __init__(self, waiting: Optional[WaitingIndicator] = None) -> None:
        self._waiting = waiting
        self._started = False
        self._saw_text = False
        self._buffer: List[str] = []
        self._live: Optional[Live] = None
        self._start_ts = _now_ts()

    def _panel_title(self) -> Text:
        return Text("Assistant", style=TITLE_ASSISTANT)

    def _panel_subtitle_stream(self) -> Text:
        # during streaming: just show start time
        s = Text()
        s.append(self._start_ts, style=TXT_WHITE)
        return s

    def _panel_subtitle_final(self, usage: Optional[Dict[str, int]]) -> Text:
        s = Text()
        u = format_usage(usage) if usage else ""
        if u:
            s.append(u, style=TXT_WHITE)
            s.append("  ·  ", style=TXT_MUTED)
        s.append(_now_ts(), style=TXT_WHITE)
        return s

    def _render_panel_text(self) -> Panel:
        body = "".join(self._buffer).rstrip()
        return Panel(
            Text(body, style=TXT_VALUE),
            title=self._panel_title(),
            subtitle=self._panel_subtitle_stream(),
            border_style=BORDER_ASSISTANT,
            expand=True,
            box=box.ROUNDED,
        )

    def _render_panel_markdown(self, usage: Optional[Dict[str, int]]) -> Panel:
        body = "".join(self._buffer).rstrip()
        md = Markdown(body, code_theme="monokai", hyperlinks=True)

        return Panel(
            md,
            title=self._panel_title(),
            subtitle=self._panel_subtitle_final(usage),
            border_style=BORDER_ASSISTANT,
            expand=True,
            box=box.ROUNDED,
        )

    def write(self, chunk: str) -> None:
        if not chunk:
            return

        self._buffer.append(chunk)

        # first real text => stop spinner, start live panel
        if not self._saw_text and chunk.strip():
            self._saw_text = True
            if self._waiting:
                self._waiting.stop()

            # start live panel once
            self._live = Live(self._render_panel_text(), console=console, refresh_per_second=20)
            self._live.__enter__()
            self._started = True
            return

        # update panel continuously
        if self._started and self._live:
            self._live.update(self._render_panel_text())

    def end(self, usage: Optional[Dict[str, int]] = None) -> None:
        # always stop spinner
        if self._waiting:
            self._waiting.stop()

        if not self._started or not self._live:
            return

        # final: swap the panel to markdown view
        try:
            self._live.update(self._render_panel_markdown(usage))
        finally:
            # close live cleanly
            self._live.__exit__(None, None, None)
            self._live = None
            self._started = False

        console.print()  # spacing after the panel
