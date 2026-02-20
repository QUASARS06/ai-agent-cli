# agentcli/config.py
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentcli.sessions import SessionStore, sessions_dir_at_root

import typer

from dotenv import load_dotenv


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class AgentState:
    # Core runtime config
    cwd: str
    model: str
    api_key: str
    base_url: str
    auto_approve: bool
    request_timeout: int

    # UI config
    truncate_lines: int = 10
    verbose: bool = False

    # Session config
    autosave: bool = True
    session_name: str = "default"
    sessions_dir: str = field(default_factory=lambda: str(sessions_dir_at_root()))

    # Conversation state
    messages: List[Dict[str, Any]] = field(default_factory=list)
    last_usage: Optional[Dict[str, int]] = None


def resolve_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_env_and_build_state(
    *,
    cwd: Optional[str] = None,
    model: Optional[str] = None,
    auto_approve: Optional[bool] = None,
    base_url: Optional[str] = None,
    request_timeout: Optional[int] = None,
    truncate_lines: Optional[int] = None,
    verbose: Optional[bool] = None,
    autosave: Optional[bool] = None,
    session: Optional[str] = None,
) -> AgentState:
    """
    Build state from env + CLI overrides.
    Sessions directory is deterministic at <project_root>/sessions
    """
    # Load .env from project root
    project_root = resolve_project_root()
    load_dotenv(project_root / ".env")

    # Defaults from env
    env_model = _env("LLM_MODEL", "openrouter/arcee-ai/trinity-large-preview:free")
    env_key = _env("LLM_API_KEY", "")
    env_base = _env("LLM_BASE_URL", "")
    env_timeout = int(_env("LLM_TIMEOUT", "60") or "60")
    env_truncate = int(_env("TRUNCATE_LINES", "10") or "10")
    env_verbose = _env("VERBOSE", "0") in {"1", "true", "yes", "on"}
    env_autosave = _env("AUTOSAVE", "1") in {"1", "true", "yes", "on"}

    # Determine cwd (STRICT)
    if cwd:
        p = Path(cwd).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        p = p.resolve()

        if not p.exists():
            raise typer.BadParameter(f"--cwd path does not exist: {p}")
        if not p.is_dir():
            raise typer.BadParameter(f"--cwd is not a directory: {p}")

        final_cwd = str(p)
    else:
        final_cwd = str(Path.cwd().resolve())
    
    st = AgentState(
        cwd=final_cwd,
        model=model or env_model,
        api_key=env_key,
        base_url=base_url if base_url is not None else env_base,
        auto_approve=bool(auto_approve)
        if auto_approve is not None
        else (_env("AUTO_APPROVE", "0") in {"1", "true", "yes", "on"}),
        request_timeout=int(request_timeout) if request_timeout is not None else env_timeout,
        truncate_lines=int(truncate_lines) if truncate_lines is not None else env_truncate,
        verbose=bool(verbose) if verbose is not None else env_verbose,
        autosave=bool(autosave) if autosave is not None else env_autosave,
        session_name=session or "default",
        sessions_dir=str(sessions_dir_at_root()),
        messages=[],
    )
    return st


def get_session_store(state: AgentState) -> SessionStore:
    # Always deterministic at project root, ignoring cwd
    return SessionStore(Path(state.sessions_dir))