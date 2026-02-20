# agentcli/util.py
from __future__ import annotations

import re
from typing import Tuple

# Accept both /cmd and \cmd; treat as the same.
# Commands are case-insensitive.
# Aliases are normalized to a canonical command.
_ALIASES = {
    "h": "help",
    "?": "help",
    "commands": "help",

    "tool": "tools",
    "ls-tools": "tools",

    "cls": "clear",

    "multiline": "paste",
    "ml": "paste",

    "q": "quit",
    "exit": "quit",
}


def normalize_command(raw: str) -> str:
    """
    Normalize a command string to a canonical form:
      - strips whitespace
      - accepts / or \\ prefix
      - case-insensitive
      - maps aliases (exit -> quit, q -> quit, h -> help, etc.)
    Returns a string like "/help", "/quit", "/paste", "/cwd", etc.
    If input isn't a command, returns the original trimmed string.
    """
    s = (raw or "").strip()
    if not s:
        return ""

    if s[0] not in ("/", "\\"):
        return s  # not a command

    # Split command token from rest
    token = s.split(maxsplit=1)[0]
    # Remove leading slash/backslash
    name = token[1:].strip().lower()

    # Map aliases
    name = _ALIASES.get(name, name)

    return f"/{name}"


def split_command(raw: str) -> Tuple[str, str]:
    """
    Split a raw command line into (command, args).

    Examples:
      "/cwd /tmp" -> ("/cwd", "/tmp")
      "\\HELP" -> ("/help", "")
    """
    s = (raw or "").strip()
    if not s:
        return "", ""

    if s[0] not in ("/", "\\"):
        return "", s

    parts = s.split(maxsplit=1)
    cmd_raw = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    cmd_norm = normalize_command(cmd_raw)
    return cmd_norm, args


_SANITIZE_WS = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    """
    Collapse multiple whitespace runs into a single space.
    Useful for log lines / UI titles.
    """
    return _SANITIZE_WS.sub(" ", (text or "").strip())
