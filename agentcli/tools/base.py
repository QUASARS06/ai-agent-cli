# agentcli/tools/base.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol


JSONSchema = Dict[str, Any]


class Tool(Protocol):
    """
    Minimal tool protocol to:
      - describe the tool for the LLM (name/description/schema)
      - run it with validated-ish args
    """

    name: str
    description: str
    input_schema: JSONSchema

    def run(self, state: Any, args: Dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: JSONSchema
    runner: Callable[[Any, Dict[str, Any]], Any]

    def to_openai_schema(self) -> Dict[str, Any]:
        """
        Return an OpenAI-style tool schema for function calling.
        Compatible with LiteLLM.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def run(self, state: Any, args: Dict[str, Any]) -> Any:
        return self.runner(state, args)


def object_schema(
    properties: Dict[str, Any],
    required: Optional[list[str]] = None,
    additional_properties: bool = False,
) -> JSONSchema:
    """
    Helper to build JSON schema objects quickly.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": additional_properties,
    }


def str_schema(description: str = "", default: Optional[str] = None) -> Dict[str, Any]:
    s: Dict[str, Any] = {"type": "string"}
    if description:
        s["description"] = description
    if default is not None:
        s["default"] = default
    return s


def int_schema(description: str = "", default: Optional[int] = None, minimum: Optional[int] = None) -> Dict[str, Any]:
    s: Dict[str, Any] = {"type": "integer"}
    if description:
        s["description"] = description
    if default is not None:
        s["default"] = default
    if minimum is not None:
        s["minimum"] = minimum
    return s


def bool_schema(description: str = "", default: Optional[bool] = None) -> Dict[str, Any]:
    s: Dict[str, Any] = {"type": "boolean"}
    if description:
        s["description"] = description
    if default is not None:
        s["default"] = default
    return s
