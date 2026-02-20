# agentcli/tools/shell.py
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from agentcli.tools.base import ToolDef, bool_schema, object_schema, str_schema
from agentcli.tools.registry import register_tool


def _require_approval_if_needed(state: Any, action: str) -> None:
    if getattr(state, "auto_approve", False):
        return
    ans = input(f"[approve] {action}? (y/N): ").strip().lower()
    if ans not in {"y", "yes"}:
        raise RuntimeError("User did not approve.")


def shell_tool(state: Any, args: Dict[str, Any]) -> Any:
    command = args.get("command")
    if not command or not str(command).strip():
        return {"error": "Missing required arg: command"}

    timeout = args.get("timeout_seconds")
    if timeout is not None:
        try:
            timeout = float(timeout)
        except ValueError:
            timeout = None

    _require_approval_if_needed(state, f"shell {command}")

    cwd = Path(state.cwd).expanduser().resolve()

    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()

        # Keep it sane — don't spam the UI or the model with huge output
        if len(out) > 8000:
            out = out[:8000] + "\n...[truncated]..."
        if len(err) > 8000:
            err = err[:8000] + "\n...[truncated]..."

        return {
            "ok": True,
            "command": command,
            "cwd": str(cwd),
            "exit_code": proc.returncode,
            "stdout": out,
            "stderr": err,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout} seconds"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


register_tool(
    ToolDef(
        name="shell",
        description="Run a shell command in the workspace directory and return stdout/stderr/exit code.",
        input_schema=object_schema(
            properties={
                "command": str_schema("Shell command to run."),
                "timeout_seconds": str_schema(
                    "Optional timeout in seconds (number as string is OK)."
                ),
            },
            required=["command"],
        ),
        runner=shell_tool,
    )
)
