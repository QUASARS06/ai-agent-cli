# agentcli/cli.py
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel
from rich.text import Text

from agentcli.config import AgentState, load_env_and_build_state
from agentcli.llm import run_agent_turn
from agentcli.prompts import build_system_message
from agentcli.sessions import SessionStore, sessions_dir_at_root
from agentcli.ui import (
    clear_screen,
    console,
    print_banner,
    print_help,
    print_tools,
    print_config_panel,
)

app = typer.Typer(add_completion=False, no_args_is_help=False)


# -------------------------
# Command parsing helpers
# -------------------------
def _has_prefix(user_text: str) -> bool:
    s = (user_text or "").lstrip()
    return s.startswith("/") or s.startswith("\\")


def _head_from_text(user_text: str) -> str:
    # returns the command head if user_text is command-like (prefix-stripped)
    cmd = _normalize_command(user_text)
    return (cmd.split()[0] if cmd else "")


def _normalize_command(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if s.startswith("/") or s.startswith("\\"):
        s = s[1:]
    return s.strip().lower()


def _parse_bool(s: str) -> Optional[bool]:
    v = (s or "").strip().lower()
    if v in {"on", "true", "1", "yes", "y"}:
        return True
    if v in {"off", "false", "0", "no", "n"}:
        return False
    return None


def _is_command(user_text: str) -> bool:
    if not user_text.strip():
        return False

    head = _head_from_text(user_text)
    if not head:
        return False

    # Commands that should work WITHOUT slash
    no_slash_ok = {
        "help", "tools", "clear", "paste", "exit", "quit", "reset", "config"
    }

    # Commands that REQUIRE an explicit slash/backslash
    slash_only = {
        "session", "sessions", "new-session", "newsession",
        "load", "save", "delete", "rename", "autosave",
        # optionally keep these slash-only too if you want:
        "cwd", "approve", "model", "truncate", "verbose"
    }

    if _has_prefix(user_text):
        return head in (no_slash_ok | slash_only)
    else:
        return head in no_slash_ok


# -------------------------
# Session helpers
# -------------------------
def _get_store(state: AgentState) -> SessionStore:
    # Deterministic: repo root / sessions
    # Do NOT use cwd for sessions storage.
    if not hasattr(state, "sessions_dir"):
        setattr(state, "sessions_dir", str(sessions_dir_at_root()))
    return SessionStore(Path(getattr(state, "sessions_dir")))


def _ensure_state_fields(state: AgentState) -> AgentState:
    # UI defaults (you already do these, keeping consistent)
    if not hasattr(state, "truncate_lines"):
        setattr(state, "truncate_lines", 10)
    if not hasattr(state, "verbose"):
        setattr(state, "verbose", False)

    # Sessions defaults
    if not hasattr(state, "autosave"):
        setattr(state, "autosave", True)
    if not hasattr(state, "session_name"):
        setattr(state, "session_name", "default")

    # Messages
    if not hasattr(state, "messages") or state.messages is None:
        setattr(state, "messages", [])

    return state


def _autosave_if_needed(state: AgentState) -> None:
    if not getattr(state, "autosave", True):
        return
    store = _get_store(state)
    store.save_session(
        getattr(state, "session_name"),
        state.messages,
        meta={"cwd": state.cwd, "model": state.model},
    )


def _init_or_load_session(state: AgentState, requested: Optional[str]) -> AgentState:
    """
    Startup rule:
      - if requested is provided -> load/create that session name
      - else -> load last used session
      - if none exists -> create a new one
    """
    store = _get_store(state)
    store.base_dir.mkdir(parents=True, exist_ok=True)

    name = (requested or "").strip() or None
    if not name:
        name = store.get_last_session_name()

    if name:
        # Load if exists, else create and start new
        try:
            data = store.load_session(name)
            setattr(state, "session_name", data.get("name") or name)
            msgs = data.get("messages") or []
            if not msgs:
                msgs = [build_system_message(state)]
            state.messages = msgs
            # Always refresh system message so workspace path is correct for this run
            if state.messages and state.messages[0].get("role") == "system":
                state.messages[0] = build_system_message(state)
            else:
                state.messages.insert(0, build_system_message(state))
            return state
        except FileNotFoundError:
            # create named session
            created = store.create_session(name=name)
            setattr(state, "session_name", created)
            state.messages = [build_system_message(state)]
            _autosave_if_needed(state)
            return state
        except Exception:
            console.print(Text(f"Warning: failed to load session '{name}'. Starting a new session.", style="yellow"))
            console.print()

    # No name found -> first ever run
    created = store.create_session()
    setattr(state, "session_name", created)
    state.messages = [build_system_message(state)]
    _autosave_if_needed(state)
    return state


# -------------------------
# Existing command handlers
# -------------------------
def _print_config(state: AgentState) -> None:
    print_config_panel(state)


def _paste_mode() -> str:
    console.print(Text("Paste mode: enter multi-line input. End with /end", style="dim"))
    lines = []
    while True:
        line = input()
        if _normalize_command(line) == "end":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _set_cwd(state: AgentState, new_path: str) -> AgentState:
    p = Path(new_path).expanduser()
    if not p.is_absolute():
        p = Path(state.cwd) / p
    p = p.resolve()

    if not p.exists():
        console.print(Text(f"[error] Path does not exist: {p}", style="red"))
        console.print()
        return state
    if not p.is_dir():
        console.print(Text(f"[error] Not a directory: {p}", style="red"))
        console.print()
        return state

    console.print(Text(f"Workspace changed: {p}", style="green"))
    console.print()
    
    new_state = replace(state, cwd=str(p))

    # Refresh system prompt so tool boundary matches new workspace
    if getattr(new_state, "messages", None):
        if new_state.messages[0].get("role") == "system":
            new_state.messages[0] = build_system_message(new_state)
        else:
            new_state.messages.insert(0, build_system_message(new_state))
    else:
        new_state.messages = [build_system_message(new_state)]

    return new_state


def _toggle_approve(state: AgentState, value: str) -> AgentState:
    v = (value or "").strip().lower()
    if v in {"on", "true", "1", "yes", "y"}:
        console.print(Text("Auto-approve: ON", style="green"))
        console.print()
        return replace(state, auto_approve=True)
    if v in {"off", "false", "0", "no", "n"}:
        console.print(Text("Auto-approve: OFF", style="yellow"))
        console.print()
        return replace(state, auto_approve=False)

    console.print(Text("[error] approve expects: on|off", style="red"))
    console.print()
    return state


def _set_model(state: AgentState, model: str) -> AgentState:
    m = (model or "").strip()
    if not m:
        console.print(Text("[error] model expects a model name", style="red"))
        console.print()
        return state
    console.print(Text(f"Model set: {m}", style="green"))
    console.print()
    return replace(state, model=m)


def _reset_context(state: AgentState) -> AgentState:
    new_state = replace(state)
    new_state.messages = [build_system_message(new_state)]

    # Clear per-session metadata (safe even if missing)
    for attr in ("last_usage", "last_error", "last_tool", "last_tool_result", "session_id"):
        if hasattr(new_state, attr):
            setattr(new_state, attr, None)

    # Clear caches (if present)
    for attr in ("tool_cache", "web_cache", "file_cache"):
        if hasattr(new_state, attr):
            try:
                getattr(new_state, attr).clear()
            except Exception:
                setattr(new_state, attr, {})

    console.print(Text("Session reset. Starting fresh.", style="green"))
    console.print()
    return new_state


# -------------------------
# Session command handlers
# -------------------------
def _cmd_session_show(state: AgentState) -> None:
    store = _get_store(state)
    name = getattr(state, "session_name", "default")
    fpath = store.base_dir / f"{name}.json"

    lines = [
        f"session:      {name}",
        f"autosave:     {getattr(state, 'autosave', True)}",
        f"sessions_dir: {store.base_dir}",
        f"file:         {fpath}",
    ]
    console.print(Panel("\n".join(lines), title="Current Session", border_style="cyan"))
    console.print()


def _cmd_sessions_list(state: AgentState) -> None:
    store = _get_store(state)
    sessions = store.list_sessions()
    cur = getattr(state, "session_name", "")

    if not sessions:
        console.print(Panel("(no sessions yet)", title="Sessions", border_style="cyan"))
        console.print()
        return

    lines = []
    for s in sessions:
        star = "★" if s.name == cur else "•"
        lines.append(f"{star} {s.name}  (updated {s.updated_at})")

    console.print(Panel("\n".join(lines), title="Sessions", border_style="cyan"))
    console.print()


def _cmd_new_session(state: AgentState, maybe_name: Optional[str]) -> AgentState:
    store = _get_store(state)
    created = store.create_session(name=maybe_name)
    setattr(state, "session_name", created)
    state.messages = [build_system_message(state)]

    console.print(Text(f"New session: {created}", style="green"))
    console.print()

    # If autosave on, persist immediately
    _autosave_if_needed(state)
    return state


def _cmd_load(state: AgentState, name: str) -> AgentState:
    store = _get_store(state)
    if not store.session_exists(name):
        console.print(Text(f"[error] Session does not exist: {name}", style="red"))
        console.print()
        return state
    data = store.load_session(name)
    loaded = data.get("name") or name

    setattr(state, "session_name", loaded)
    msgs = data.get("messages") or []
    if not msgs:
        msgs = [build_system_message(state)]
    state.messages = msgs

    console.print(Text(f"Loaded session: {loaded}", style="green"))
    console.print()
    return state


def _cmd_save(state: AgentState, maybe_name: Optional[str]) -> AgentState:
    store = _get_store(state)

    if maybe_name:
        if store.session_exists(maybe_name):
            console.print(Text(f"[error] Session already exists: {maybe_name} (choose a new name)", style="red"))
            console.print()
            return state
        setattr(state, "session_name", maybe_name)

    store.save_session(
        getattr(state, "session_name"),
        state.messages,
        meta={"cwd": state.cwd, "model": state.model},
    )
    console.print(Text(f"Saved session: {getattr(state, 'session_name')}", style="green"))
    console.print()
    return state


def _cmd_delete(state: AgentState, name: str) -> AgentState:
    store = _get_store(state)
    if not store.session_exists(name):
        console.print(Text(f"[error] Session does not exist: {name}", style="red"))
        console.print()
        return state
    cur = getattr(state, "session_name", "")
    deleting_current = (name == cur)

    store.delete_session(name)
    console.print(Text(f"Deleted session: {name}", style="green"))
    console.print()

    if deleting_current:
        created = store.create_session()
        setattr(state, "session_name", created)
        state.messages = [build_system_message(state)]
        console.print(Text(f"Switched to new session: {created}", style="cyan"))
        console.print()
        _autosave_if_needed(state)

    return state


def _cmd_rename(state: AgentState, old: str, new: str) -> AgentState:
    store = _get_store(state)
    new_name = store.rename_session(old, new)

    console.print(Text(f"Renamed session: {old} -> {new_name}", style="green"))
    console.print()

    if getattr(state, "session_name", "") == old:
        setattr(state, "session_name", new_name)
    return state


def _cmd_autosave(state: AgentState, value: Optional[str]) -> AgentState:
    if not value:
        console.print(Text(f"autosave = {getattr(state, 'autosave', True)} (usage: /autosave on|off)", style="cyan"))
        console.print()
        return state

    b = _parse_bool(value)
    if b is None:
        console.print(Text("[error] usage: /autosave on|off", style="red"))
        console.print()
        return state

    setattr(state, "autosave", b)
    console.print(Text(f"Autosave: {'ON' if b else 'OFF'}", style="green"))
    console.print()

    # If turning ON, immediately save current session to disk
    if b:
        _autosave_if_needed(state)

    return state


# -------------------------
# Main entry
# -------------------------
@app.command()
def main(
    cwd: Optional[str] = typer.Option(None, "--cwd", "-C", help="Workspace directory"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="LLM model name"),
    auto_approve: Optional[bool] = typer.Option(
        None,
        "--auto-approve/--no-auto-approve",
        "-y/-n",
        help="Auto-approve file/shell changes",
    ),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Optional LLM base URL"),
    request_timeout: Optional[int] = typer.Option(None, "--request-timeout", "-t", help="LLM request timeout in seconds"),
    truncate_lines: Optional[int] = typer.Option(None, "--truncate-lines", help="Tool output line limit (0 = no truncation)"),
    verbose: Optional[bool] = typer.Option(None, "--verbose/--no-verbose", help="Verbose tool output"),
    autosave: Optional[bool] = typer.Option(None, "--autosave/--no-autosave", help="Autosave sessions to disk"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Session name to load/create"),
) -> None:
    state = load_env_and_build_state(
        cwd=cwd,
        model=model,
        auto_approve=auto_approve,
        base_url=base_url,
        request_timeout=request_timeout,
        truncate_lines=truncate_lines,
        verbose=verbose,
        autosave=autosave,
        session=session,
    )
    state = _ensure_state_fields(state)

    # Load last session by default; load/create requested if provided
    state = _init_or_load_session(state, requested=session)

    print_banner(state)

    while True:
        try:
            user_text = input("[user]: ").rstrip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            break

        if not user_text.strip():
            continue

        if _is_command(user_text):
            cmd = _normalize_command(user_text)
            parts = cmd.split()
            head = parts[0] if parts else ""

            if head in {"quit", "exit"}:
                break

            if head == "help":
                print_help(state)
                continue

            if head == "tools":
                print_tools(state)
                continue

            if head == "clear":
                clear_screen()
                print_banner(state)
                continue

            if head == "paste":
                pasted = _paste_mode()
                if pasted:
                    console.print()
                    print("")
                    run_agent_turn(state, pasted)
                    _autosave_if_needed(state)
                continue

            if head == "cwd":
                if len(parts) < 2:
                    console.print(Text("[error] usage: /cwd <path>", style="red"))
                    console.print()
                else:
                    state = _set_cwd(state, " ".join(parts[1:]))
                continue

            if head == "approve":
                if len(parts) < 2:
                    console.print(Text("[error] usage: /approve on|off", style="red"))
                    console.print()
                else:
                    state = _toggle_approve(state, parts[1])
                continue

            if head == "model":
                if len(parts) < 2:
                    console.print(Text("[error] usage: /model <name>", style="red"))
                    console.print()
                else:
                    state = _set_model(state, " ".join(parts[1:]))
                continue

            if head == "reset":
                state = _reset_context(state)
                _autosave_if_needed(state)
                continue

            if head == "config":
                _print_config(state)
                continue

            if head == "truncate":
                if len(parts) < 2:
                    cur = getattr(state, "truncate_lines", 10)
                    console.print(f"truncate_lines = {cur} (0 = no truncation)")
                    console.print()
                    continue
                try:
                    n = int(parts[1])
                except ValueError:
                    console.print(Text("[error] usage: /truncate <number> (0 = no truncation)", style="red"))
                    console.print()
                    continue
                if n < 0:
                    console.print(Text("[error] truncate must be >= 0", style="red"))
                    console.print()
                    continue
                setattr(state, "truncate_lines", n)
                if n == 0:
                    console.print(Text("Tool output truncation: OFF", style="green"))
                else:
                    console.print(Text(f"Tool output truncation: {n} lines", style="green"))
                console.print()
                continue

            if head == "verbose":
                if len(parts) < 2:
                    cur = getattr(state, "verbose", False)
                    console.print(f"verbose = {cur} (usage: /verbose on|off)")
                    console.print()
                    continue
                v = parts[1].strip().lower()
                if v in {"on", "true", "1", "yes", "y"}:
                    setattr(state, "verbose", True)
                    console.print(Text("Verbose mode: ON (show full tool output)", style="green"))
                    console.print()
                    continue
                if v in {"off", "false", "0", "no", "n"}:
                    setattr(state, "verbose", False)
                    console.print(Text("Verbose mode: OFF (show compact tool output)", style="green"))
                    console.print()
                    continue
                console.print(Text("[error] usage: /verbose on|off", style="red"))
                console.print()
                continue

            # ---- New session commands ----
            if head == "session":
                _cmd_session_show(state)
                continue

            if head == "sessions":
                _cmd_sessions_list(state)
                continue

            if head in {"new-session", "newsession"}:
                name = " ".join(parts[1:]).strip() if len(parts) > 1 else None
                state = _cmd_new_session(state, name if name else None)
                continue

            if head == "load":
                if len(parts) < 2:
                    console.print(Text("[error] usage: /load <name>", style="red"))
                    console.print()
                    continue
                state = _cmd_load(state, parts[1])
                continue

            if head == "save":
                name = " ".join(parts[1:]).strip() if len(parts) > 1 else None
                state = _cmd_save(state, name if name else None)
                continue

            if head == "delete":
                if len(parts) < 2:
                    console.print(Text("[error] usage: /delete <name>", style="red"))
                    console.print()
                    continue
                state = _cmd_delete(state, parts[1])
                continue

            if head == "rename":
                if len(parts) < 3:
                    console.print(Text("[error] usage: /rename <old> <new>", style="red"))
                    console.print()
                    continue
                state = _cmd_rename(state, parts[1], parts[2])
                continue

            if head == "autosave":
                val = parts[1] if len(parts) > 1 else None
                state = _cmd_autosave(state, val)
                continue

            console.print(Text(f"[error] Unknown command: {head}", style="red"))
            console.print()
            continue

        # Non-command: run agent turn
        print("")
        try:
            run_agent_turn(state, user_text)
            _autosave_if_needed(state)  # autosave after every successful turn (if enabled)
        except Exception as e:
            console.print(Text(f"[error] {type(e).__name__}: {e}", style="red"))
            console.print()


if __name__ == "__main__":
    main()