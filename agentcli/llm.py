# agentcli/llm.py
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import litellm
from rich.text import Text

from agentcli.config import AgentState
from agentcli.prompts import build_tool_message, build_user_message
from agentcli.tools.registry import get_tool_schemas, run_tool
from agentcli.ui import StreamPrinter, WaitingIndicator, console, print_tool_panel
from agentcli.util import normalize_whitespace

# Reduce LiteLLM banner noise
litellm.suppress_debug_info = True
litellm.drop_params = True  # safer across providers


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


def _extract_usage(resp: Any) -> Optional[Dict[str, int]]:
    candidates = []

    try:
        if hasattr(resp, "usage") and resp.usage:
            candidates.append(resp.usage)
    except Exception:
        pass

    if isinstance(resp, dict):
        if resp.get("usage"):
            candidates.append(resp["usage"])
        if resp.get("x_usage"):
            candidates.append(resp["x_usage"])
        if isinstance(resp.get("response"), dict) and resp["response"].get("usage"):
            candidates.append(resp["response"]["usage"])

    for u in candidates:
        try:
            u = dict(u)
        except Exception:
            continue

        out: Dict[str, int] = {}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            v = u.get(k)
            if isinstance(v, int):
                out[k] = v

        if out:
            if "total_tokens" not in out and "prompt_tokens" in out and "completion_tokens" in out:
                out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
            return out

    return None


def _render_tool_action(tool_name: str, args: Dict[str, Any]) -> str:
    p = args.get("path") or args.get("dir") or args.get("cwd")
    if tool_name == "read_file" and p:
        return f"Reading {p}"
    if tool_name == "write_file" and p:
        return f"Writing {p}"
    if tool_name == "delete_file" and p:
        return f"Deleting {p}"
    if tool_name == "apply_patch" and p:
        return f"Patching {p}"
    if tool_name == "list_dir" and p:
        return f"Listing {p}"
    if tool_name == "walk_dir" and p:
        return f"Walking {p}"
    if tool_name == "search_text":
        q = normalize_whitespace(str(args.get("query", "")))
        where = args.get("path", ".")
        return f"Searching '{q}' under {where}"
    if tool_name == "web_search":
        q = normalize_whitespace(str(args.get("query", "")))
        return f"Web searching: {q}"
    if tool_name == "web_fetch":
        url = normalize_whitespace(str(args.get("url", "")))
        return f"Fetching: {url}"
    if tool_name == "shell":
        cmd = normalize_whitespace(str(args.get("command", "")))
        return f"Running: {cmd}" if cmd else "Running shell command"
    return ""


def _truncate_text_by_lines(text: str, max_lines: int) -> tuple[str, bool]:
    """
    Returns (maybe_truncated_text, was_truncated).
    max_lines <= 0 means no truncation.
    """
    if not text:
        return text, False
    if max_lines <= 0:
        return text, False

    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False

    remaining = len(lines) - max_lines
    marker = f"\n\n--- output truncated: {remaining} more lines ---"
    return "\n".join(lines[:max_lines]) + marker, True


def _safe_str(x: Any) -> str:
    try:
        return str(x)
    except Exception:
        return repr(x)


def _format_tool_output_compact(tool_name: str, tool_output: Any) -> str:
    """
    Compact, user-friendly display for tool output when verbose=OFF.
    This is DISPLAY ONLY. The model still gets the full tool_output_str.
    """
    if tool_output is None:
        return ""

    # error dict
    if isinstance(tool_output, dict) and tool_output.get("error"):
        err = _safe_str(tool_output.get("error"))
        msg = _safe_str(tool_output.get("message", "")).strip()
        if msg and msg != err:
            return f"[error] {err}\n{msg}"
        return f"[error] {err}"

    # Common tools
    if tool_name == "list_dir" and isinstance(tool_output, dict):
        items = tool_output.get("items") or []
        names = []
        for it in items[:50]:
            n = it.get("name") if isinstance(it, dict) else None
            if n:
                names.append(f"- {n}")
        more = ""
        if isinstance(items, list) and len(items) > 50:
            more = f"\n...and {len(items) - 50} more"
        return "\n".join(names) + more if names else ""

    if tool_name == "walk_dir" and isinstance(tool_output, dict):
        files = tool_output.get("files") or []
        shown = files[:60] if isinstance(files, list) else []
        body = "\n".join(f"- {f}" for f in shown)
        if isinstance(files, list) and len(files) > 60:
            body += f"\n...and {len(files) - 60} more"
        if tool_output.get("truncated"):
            body += "\n(note: walk_dir results truncated by tool limits)"
        return body.strip()

    if tool_name == "search_text" and isinstance(tool_output, dict):
        matches = tool_output.get("matches") or []
        lines: List[str] = []
        for m in matches[:25]:
            if not isinstance(m, dict):
                continue
            path = m.get("path", "")
            line_no = m.get("line", "")
            snippet = normalize_whitespace(_safe_str(m.get("text", "")))
            lines.append(f"- {path}:{line_no}  {snippet}")
        if isinstance(matches, list) and len(matches) > 25:
            lines.append(f"...and {len(matches) - 25} more")
        return "\n".join(lines)

    if tool_name == "web_search" and isinstance(tool_output, dict):
        results = tool_output.get("results") or []
        lines: List[str] = []
        for r in results[:5]:
            if not isinstance(r, dict):
                continue
            title = normalize_whitespace(_safe_str(r.get("title", "")))
            url = _safe_str(r.get("url", ""))
            if title and url:
                lines.append(f"- {title}\n  {url}")
            elif url:
                lines.append(f"- {url}")
        if isinstance(results, list) and len(results) > 5:
            lines.append(f"...and {len(results) - 5} more")
        return "\n".join(lines)

    if tool_name == "web_fetch" and isinstance(tool_output, dict):
        url = _safe_str(tool_output.get("url", ""))
        ctype = _safe_str(tool_output.get("content_type", ""))
        text = _safe_str(tool_output.get("text", "")).strip()

        lines: List[str] = []
        if url:
            lines.append(f"url: {url}")
        if ctype:
            lines.append(f"content_type: {ctype}")

        if text:
            tlines = text.splitlines()
            preview = "\n".join(tlines[:12])
            if len(tlines) > 12:
                preview += f"\n...and {len(tlines) - 12} more lines"
            lines.append("")
            lines.append("preview:")
            lines.append(preview)

        return "\n".join(lines).strip()

    if tool_name == "shell":
        if isinstance(tool_output, dict):
            exit_code = tool_output.get("exit_code")
            stdout = _safe_str(tool_output.get("stdout", "")).strip()
            stderr = _safe_str(tool_output.get("stderr", "")).strip()

            lines: List[str] = []
            if exit_code is not None:
                lines.append(f"exit_code: {exit_code}")

            if stdout:
                out_lines = stdout.splitlines()
                preview = "\n".join(out_lines[:30])
                if len(out_lines) > 30:
                    preview += f"\n...and {len(out_lines) - 30} more lines"
                lines.append("")
                lines.append("stdout:")
                lines.append(preview)

            if stderr:
                err_lines = stderr.splitlines()
                preview = "\n".join(err_lines[:20])
                if len(err_lines) > 20:
                    preview += f"\n...and {len(err_lines) - 20} more lines"
                lines.append("")
                lines.append("stderr:")
                lines.append(preview)

            return "\n".join(lines).strip()

    if tool_name in {"write_file", "apply_patch", "delete_file"} and isinstance(tool_output, dict):
        if tool_output.get("ok"):
            p = tool_output.get("path", "")
            extra = []
            if "bytes_written" in tool_output:
                extra.append(f"bytes_written={tool_output.get('bytes_written')}")
            if "deleted" in tool_output:
                extra.append(f"deleted={tool_output.get('deleted')}")
            suffix = f" ({', '.join(extra)})" if extra else ""
            return f"ok: true\npath: {p}{suffix}"

    return ""


def _completion_once(state: AgentState, tools: List[Dict[str, Any]]) -> Any:
    kwargs: Dict[str, Any] = {
        "model": state.model,
        "messages": state.messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": True,
        "stream_options": {"include_usage": True},
        "timeout": state.request_timeout,
    }

    if state.base_url:
        kwargs["api_base"] = state.base_url

    kwargs["api_key"] = state.api_key

    return litellm.completion(**kwargs)


def _friendly_llm_error_message(e: Exception) -> str:
    msg = _safe_str(e)

    lowered = msg.lower()
    if "authenticationerror" in lowered or "api key not valid" in lowered or "api_key_invalid" in lowered:
        return "Authentication error: your API key is invalid or missing."
    if "ratelimiterror" in lowered or "resource_exhausted" in lowered or "429" in lowered:
        return "Rate limit error: provider is throttling you. Try again later or switch models."
    if "notfounderror" in lowered or "model not found" in lowered:
        return "Model not found: check your LLM_MODEL value."
    if "timeout" in lowered:
        return "Request timed out: increase LLM_TIMEOUT or try again."
    return f"LLM request failed: {type(e).__name__}: {msg}"


def _stream_assistant_and_collect(
    state: AgentState,
    tools: List[Dict[str, Any]],
) -> Tuple[str, List[ToolCall], Optional[Dict[str, int]]]:
    """
    Streams assistant output. Guarantees spinner cleanup even on LLM errors.
    Returns ("", [], None) on error and prints a clean message.
    """
    waiting = WaitingIndicator(f"Waiting for LLM response... ({state.model})")
    waiting.start()
    printer = StreamPrinter(waiting=waiting)

    full_text_parts: List[str] = []
    tool_calls_delta: Dict[int, Dict[str, Any]] = {}
    final_chunk: Optional[Any] = None

    try:
        stream = _completion_once(state, tools)

        for chunk in stream:
            final_chunk = chunk
            try:
                choices = chunk.get("choices") if isinstance(chunk, dict) else getattr(chunk, "choices", None)
                if not choices:
                    continue

                delta = choices[0].get("delta") if isinstance(choices[0], dict) else getattr(choices[0], "delta", None)
                if delta is None:
                    continue

                content = delta.get("content") if isinstance(delta, dict) else getattr(delta, "content", None)
                if content:
                    full_text_parts.append(content)
                    printer.write(content)

                tcs = delta.get("tool_calls") if isinstance(delta, dict) else getattr(delta, "tool_calls", None)
                if tcs:
                    for tc in tcs:
                        idx = tc.get("index", 0) if isinstance(tc, dict) else getattr(tc, "index", 0)
                        entry = tool_calls_delta.setdefault(int(idx), {"id": "", "name": "", "arguments": ""})

                        tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                        if tc_id:
                            entry["id"] = tc_id

                        fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
                        if fn:
                            name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
                            if name:
                                entry["name"] = name
                            args_part = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", None)
                            if args_part:
                                entry["arguments"] += args_part
            except Exception:
                continue

        assistant_text = "".join(full_text_parts).strip()

        tool_calls: List[ToolCall] = []
        if tool_calls_delta:
            for _, tc in sorted(tool_calls_delta.items(), key=lambda kv: kv[0]):
                name = str(tc.get("name") or "")
                tc_id = str(tc.get("id") or "")
                args_raw = tc.get("arguments") or "{}"
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
                except json.JSONDecodeError:
                    args = {}
                if name:
                    tool_calls.append(ToolCall(id=tc_id or f"toolcall_{len(tool_calls)}", name=name, arguments=args))

        usage = _extract_usage(final_chunk)
        state.last_usage = usage

        printer.end(usage=usage)
        return assistant_text, tool_calls, usage

    except Exception as e:
        # Ensure spinner stops even if stream fails before any tokens
        try:
            printer.end(usage=None)
        except Exception:
            try:
                waiting.stop()
            except Exception:
                pass

        friendly = _friendly_llm_error_message(e)
        setattr(state, "last_error", _safe_str(e))

        console.print(Text(f"[error] {friendly}", style="red"))
        # console.print(Text("Fix: check LLM_API_KEY / LLM_MODEL (and LLM_BASE_URL if set).", style="dim"))
        console.print()

        # Return empty so caller prints the "LLM didn't respond" message if desired
        return "", [], None

    finally:
        # Double safety
        try:
            waiting.stop()
        except Exception:
            pass


def run_agent_turn(state: AgentState, user_text: str, max_loops: int = 12) -> None:
    start_len = len(state.messages)
    state.messages.append(build_user_message(user_text))

    tools = get_tool_schemas()
    tool_cache: Dict[str, Any] = {}

    try:
        for _ in range(max_loops):
            assistant_text, tool_calls, _usage = _stream_assistant_and_collect(state, tools)

            # If model produced nothing (no text, no tools), show a friendly message
            if not assistant_text and not tool_calls:
                console.print(
                    Text(
                        "LLM didn't respond. Please try again. If the issue persists, check LLM env configs or restart the CLI.",
                        style="yellow",
                    )
                )
                console.print()
                return

            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_text or ""}

            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in tool_calls
                ]

            state.messages.append(assistant_msg)

            if not tool_calls:
                return

            # display settings
            truncate_n = int(getattr(state, "truncate_lines", 10))
            verbose = bool(getattr(state, "verbose", False))

            for tc in tool_calls:
                try:
                    key_args = json.dumps(tc.arguments, sort_keys=True)
                except Exception:
                    key_args = str(tc.arguments)
                cache_key = f"{tc.name}:{key_args}"

                panel_lines: List[str] = []
                action = _render_tool_action(tc.name, tc.arguments)
                if action:
                    panel_lines.append(action)

                t0 = time.perf_counter()
                if cache_key in tool_cache:
                    tool_output = tool_cache[cache_key]
                    panel_lines.append("(cache hit) reused previous result")
                else:
                    tool_output = run_tool(state, tc.name, tc.arguments)
                    tool_cache[cache_key] = tool_output
                elapsed = time.perf_counter() - t0

                # Friendly disapproval UX
                if isinstance(tool_output, dict) and tool_output.get("error") == "USER_DISAPPROVED":
                    panel_lines.append("Operation rejected by user.")
                    print_tool_panel(f"Tool: {tc.name}", panel_lines, footer=f"done in {elapsed:.2f}s")
                    state.messages.append(build_tool_message(tc.id, tc.name, json.dumps(tool_output)))
                    return

                # Model payload (keep full-ish)
                if tool_output is None:
                    tool_output_str = ""
                elif isinstance(tool_output, (dict, list)):
                    tool_output_str = json.dumps(tool_output, indent=2)[:4000]
                else:
                    tool_output_str = str(tool_output)[:4000]

                # Display payload
                if verbose:
                    shown, _ = _truncate_text_by_lines(tool_output_str, truncate_n)
                    if shown:
                        panel_lines.append(shown)
                else:
                    compact = _format_tool_output_compact(tc.name, tool_output)
                    if compact:
                        shown, _ = _truncate_text_by_lines(compact, truncate_n)
                        panel_lines.append(shown)

                print_tool_panel(f"Tool: {tc.name}", panel_lines, footer=f"done in {elapsed:.2f}s")
                state.messages.append(build_tool_message(tc.id, tc.name, tool_output_str))

        state.messages.append(
            {
                "role": "assistant",
                "content": "Stopped after too many tool-call loops. If you need more progress, re-run with a more specific instruction.",
            }
        )
    except Exception:
        state.messages = state.messages[:start_len]
        raise