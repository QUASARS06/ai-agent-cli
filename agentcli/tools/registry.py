# agentcli/tools/registry.py
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from agentcli.tools.base import ToolDef


_TOOL_REGISTRY: Dict[str, ToolDef] = {}


def register_tool(tool: ToolDef) -> None:
    if tool.name in _TOOL_REGISTRY:
        raise ValueError(f"Tool already registered: {tool.name}")
    _TOOL_REGISTRY[tool.name] = tool


def get_tool_names() -> List[str]:
    return sorted(_TOOL_REGISTRY.keys())


def get_tools() -> List[ToolDef]:
    """
    Return ToolDef objects (name/description/schema) for UI/help.
    """
    return [t for _, t in sorted(_TOOL_REGISTRY.items(), key=lambda kv: kv[0])]


def get_tool_schemas() -> List[Dict[str, Any]]:
    """
    Return OpenAI-style tool schemas for the LLM call.
    """
    return [t.to_openai_schema() for t in _TOOL_REGISTRY.values()]


def run_tool(state: Any, tool_name: str, args: Dict[str, Any]) -> Any:
    tool = _TOOL_REGISTRY.get(tool_name)
    if not tool:
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return {"error": f"Tool args must be an object/dict; got {type(args).__name__}"}

        return tool.run(state, args)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# Register built-in tools on import
def _register_builtins() -> None:
    # Importing these modules registers tools via register_tool(...)
    from agentcli.tools import fs  # noqa: F401
    from agentcli.tools import shell  # noqa: F401
    from agentcli.tools import search  # noqa: F401
    from agentcli.tools import web  # noqa: F401


_register_builtins()
