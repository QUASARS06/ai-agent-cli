# agentcli/tools/fs.py
from __future__ import annotations

import os
import re
import shutil
import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from rich import box
from rich.panel import Panel
from rich.console import Group
from rich.text import Text
from agentcli.ui import console

from agentcli.tools.base import ToolDef, bool_schema, int_schema, object_schema, str_schema
from agentcli.tools.registry import register_tool


# ---------- path safety helpers ----------

def _root(state: Any) -> Path:
    return Path(state.cwd).expanduser().resolve()


def _resolve_under_root(state: Any, user_path: str) -> Path:
    """
    Resolve user_path under state.cwd, preventing escape via .. or absolute paths.
    Allows relative paths, and absolute paths ONLY if they are under root.
    """
    root = _root(state)
    p = Path(user_path).expanduser()

    if not p.is_absolute():
        p = (root / p)

    try:
        resolved = p.resolve()
    except FileNotFoundError:
        resolved = p.absolute()

    try:
        resolved.relative_to(root)
    except Exception:
        raise ValueError(f"Path escapes workspace root. Root={root}, path={user_path}")

    return resolved


def _rel_to_root(state: Any, p: Path) -> str:
    root = _root(state)
    try:
        return str(p.resolve().relative_to(root))
    except Exception:
        return str(p)


# ---------- diff preview + approval ----------

_DIFF_MAX_LINES = 200
_DIFF_CONTEXT = 3


def _print_diff_preview(old_text: str, new_text: str, path_label: str) -> None:
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path_label}",
            tofile=f"b/{path_label}",
            n=_DIFF_CONTEXT,
        )
    )

    if not diff_lines:
        console.print(
            Panel(
                Text("No changes.", style="muted"),
                title=Text("Diff Preview", style="magenta"),
                border_style="magenta",
                expand=True,
                box=box.ROUNDED,
            )
        )
        console.print()

        return

    shown = diff_lines
    truncated_note = ""
    if len(diff_lines) > _DIFF_MAX_LINES:
        shown = diff_lines[:_DIFF_MAX_LINES]
        truncated_note = f"...[diff truncated: {len(diff_lines) - _DIFF_MAX_LINES} more lines]..."

    rendered: List[Text] = []

    for line in shown:
        s = line.rstrip("\n")

        if s.startswith(("--- ", "+++ ")):
            rendered.append(Text(s, style="cyan"))
        elif s.startswith("@@"):
            rendered.append(Text(s, style="yellow"))
        elif s.startswith("+"):
            rendered.append(Text(s, style="green"))
        elif s.startswith("-"):
            rendered.append(Text(s, style="red"))
        else:
            rendered.append(Text(s, style="dim"))

    if truncated_note:
        rendered.append(Text(truncated_note, style="dim"))

    console.print(
        Panel(
            Group(*rendered),
            title=Text("Diff Preview", style="magenta"),
            border_style="magenta",
            expand=True,
            box=box.ROUNDED,
        )
    )
    console.print()



def _require_approval_if_needed(state: Any, action: str, *, diff_preview: Tuple[str, str, str] | None = None) -> None:
    """
    If auto_approve is False, show optional diff preview and prompt for confirmation.
    diff_preview = (old_text, new_text, path_label)
    """
    if getattr(state, "auto_approve", False):
        return

    if diff_preview is not None:
        old_text, new_text, path_label = diff_preview
        _print_diff_preview(old_text, new_text, path_label)

    ans = input(f"[approve] {action}? (y/N): ").strip().lower()
    if ans not in {"y", "yes"}:
        raise PermissionError(
            f"USER_DISAPPROVED: The user rejected this action: {action}. "
            f"Do NOT retry automatically. Ask the user how to proceed."
        )


# ---------- tools implementations ----------

def list_dir_tool(state: Any, args: Dict[str, Any]) -> Any:
    path = args.get("path", ".")
    target = _resolve_under_root(state, path)

    if not target.exists():
        return {"error": f"Not found: {path}"}
    if not target.is_dir():
        return {"error": f"Not a directory: {path}"}

    items = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        try:
            stat = child.stat()
            items.append(
                {
                    "name": child.name + ("/" if child.is_dir() else ""),
                    "type": "dir" if child.is_dir() else "file",
                    "size": stat.st_size,
                }
            )
        except Exception:
            items.append(
                {
                    "name": child.name + ("/" if child.is_dir() else ""),
                    "type": "dir" if child.is_dir() else "file",
                    "size": None,
                }
            )
    return {"path": str(path), "items": items}


def walk_dir_tool(state: Any, args: Dict[str, Any]) -> Any:
    path = args.get("path", ".")
    max_depth = int(args.get("max_depth", 6))
    max_files = int(args.get("max_files", 200))

    root_dir = _resolve_under_root(state, path)
    if not root_dir.exists():
        return {"error": f"Not found: {path}"}
    if not root_dir.is_dir():
        return {"error": f"Not a directory: {path}"}

    results: List[str] = []
    root = root_dir.resolve()

    def depth_of(p: Path) -> int:
        try:
            return len(p.relative_to(root).parts)
        except Exception:
            return 0

    for dirpath, dirnames, filenames in os.walk(root):
        dpath = Path(dirpath)
        d = depth_of(dpath)
        if d > max_depth:
            dirnames[:] = []
            continue

        dirnames[:] = [dn for dn in dirnames if not dn.startswith(".")]

        results.append(_rel_to_root(state, dpath) + "/")

        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            fp = dpath / fn
            results.append(_rel_to_root(state, fp))
            if len(results) >= max_files:
                return {"path": str(path), "files": results, "truncated": True}

    return {"path": str(path), "files": results, "truncated": False}


def read_file_tool(state: Any, args: Dict[str, Any]) -> Any:
    path = args.get("path")
    if not path:
        return {"error": "Missing required arg: path"}
    target = _resolve_under_root(state, path)

    if not target.exists():
        return {"error": f"Not found: {path}"}
    if target.is_dir():
        return {"error": f"Is a directory: {path}"}

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = target.read_bytes().decode("utf-8", errors="replace")

    return {"path": str(path), "content": content}


def _ensure_parent_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def write_file_tool(state: Any, args: Dict[str, Any]) -> Any:
    path = args.get("path")
    content = args.get("content", "")
    overwrite = bool(args.get("overwrite", True))

    if not path:
        return {"error": "Missing required arg: path"}

    target = _resolve_under_root(state, path)
    _ensure_parent_dir(target)

    if target.exists() and not overwrite:
        return {"error": f"File exists and overwrite=false: {path}"}

    new_text = str(content)

    if target.exists():
        old_text = target.read_text(encoding="utf-8", errors="replace")
    else:
        old_text = ""

    try:
        _require_approval_if_needed(
            state,
            f"write_file {path}",
            diff_preview=(old_text, new_text, str(path)),
        )
    except PermissionError as e:
        return {"error": "USER_DISAPPROVED", "message": str(e)}

    target.write_text(new_text, encoding="utf-8")
    return {"ok": True, "path": str(path), "bytes_written": len(new_text.encode("utf-8"))}


def delete_file_tool(state: Any, args: Dict[str, Any]) -> Any:
    path = args.get("path")
    if not path:
        return {"error": "Missing required arg: path"}

    target = _resolve_under_root(state, path)
    if not target.exists():
        return {"error": f"Not found: {path}"}

    try:
        _require_approval_if_needed(state, f"delete_file {path}")
    except PermissionError as e:
        return {"error": "USER_DISAPPROVED", "message": str(e)}

    if target.is_dir():
        shutil.rmtree(target)
        return {"ok": True, "path": str(path), "deleted": "dir"}
    else:
        target.unlink()
        return {"ok": True, "path": str(path), "deleted": "file"}


# ---- apply_patch (unified diff) ----
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

@dataclass
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: List[str]


def _parse_unified_diff(patch: str) -> Tuple[str, List[_Hunk]]:
    lines = patch.splitlines()
    target_path = ""

    hunks: List[_Hunk] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        if line.startswith("+++ "):
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                p = parts[1].strip()
                if p.startswith("a/") or p.startswith("b/"):
                    p = p[2:]
                target_path = p
            i += 1
            break
        i += 1

    while i < len(lines):
        m = _HUNK_RE.match(lines[i])
        if not m:
            i += 1
            continue
        old_start = int(m.group(1))
        old_count = int(m.group(2) or "1")
        new_start = int(m.group(3))
        new_count = int(m.group(4) or "1")
        i += 1

        hunk_lines: List[str] = []
        while i < len(lines) and not lines[i].startswith("@@ "):
            hunk_lines.append(lines[i])
            i += 1

        hunks.append(_Hunk(old_start, old_count, new_start, new_count, hunk_lines))

    return target_path, hunks


def _apply_hunks(original: str, hunks: List[_Hunk]) -> str:
    orig_lines = original.splitlines(keepends=True)
    out: List[str] = []
    orig_i = 0

    for h in hunks:
        hunk_start_idx = max(h.old_start - 1, 0)
        while orig_i < hunk_start_idx and orig_i < len(orig_lines):
            out.append(orig_lines[orig_i])
            orig_i += 1

        for hl in h.lines:
            if not hl:
                continue

            prefix = hl[:1]
            text = hl[1:]

            if prefix == " ":
                if orig_i >= len(orig_lines):
                    raise ValueError("Patch context goes past end of file")
                if orig_lines[orig_i].rstrip("\n") != text.rstrip("\n"):
                    raise ValueError("Patch context mismatch")
                out.append(orig_lines[orig_i])
                orig_i += 1
            elif prefix == "-":
                if orig_i >= len(orig_lines):
                    raise ValueError("Patch removal goes past end of file")
                if orig_lines[orig_i].rstrip("\n") != text.rstrip("\n"):
                    raise ValueError("Patch removal mismatch")
                orig_i += 1
            elif prefix == "+":
                out.append(text + ("\n" if not text.endswith("\n") else ""))
            elif prefix == "\\":
                continue
            else:
                continue

    while orig_i < len(orig_lines):
        out.append(orig_lines[orig_i])
        orig_i += 1

    return "".join(out)


def apply_patch_tool(state: Any, args: Dict[str, Any]) -> Any:
    path = args.get("path")
    patch = args.get("patch")

    if not path:
        return {"error": "Missing required arg: path"}
    if not patch:
        return {"error": "Missing required arg: patch"}

    target = _resolve_under_root(state, path)
    if not target.exists() or target.is_dir():
        return {"error": f"Not a file: {path}"}

    original = target.read_text(encoding="utf-8", errors="replace")

    _, hunks = _parse_unified_diff(str(patch))
    if not hunks:
        return {"error": "Patch parse failed: no hunks found"}

    try:
        updated = _apply_hunks(original, hunks)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    try:
        _require_approval_if_needed(
            state,
            f"apply_patch {path}",
            diff_preview=(original, updated, str(path)),
        )
    except PermissionError as e:
        return {"error": "USER_DISAPPROVED", "message": str(e)}

    target.write_text(updated, encoding="utf-8")
    return {"ok": True, "path": str(path), "changed": True}


# ---------- tool registration ----------

register_tool(
    ToolDef(
        name="list_dir",
        description="List files and folders in a directory under the workspace root.",
        input_schema=object_schema(
            properties={
                "path": str_schema("Directory path relative to workspace root.", default="."),
            },
            required=[],
        ),
        runner=list_dir_tool,
    )
)

register_tool(
    ToolDef(
        name="walk_dir",
        description="Recursively list files under a directory (bounded by max_depth/max_files).",
        input_schema=object_schema(
            properties={
                "path": str_schema("Directory path relative to workspace root.", default="."),
                "max_depth": int_schema("Maximum depth to recurse.", default=6, minimum=0),
                "max_files": int_schema("Maximum number of entries to return.", default=200, minimum=1),
            },
            required=[],
        ),
        runner=walk_dir_tool,
    )
)

register_tool(
    ToolDef(
        name="read_file",
        description="Read a text file under the workspace root. Returns file content.",
        input_schema=object_schema(
            properties={
                "path": str_schema("File path relative to workspace root."),
            },
            required=["path"],
        ),
        runner=read_file_tool,
    )
)

register_tool(
    ToolDef(
        name="write_file",
        description="Write a text file under the workspace root (creates parent directories). Shows diff preview before approval.",
        input_schema=object_schema(
            properties={
                "path": str_schema("File path relative to workspace root."),
                "content": str_schema("Full file content to write."),
                "overwrite": bool_schema("Overwrite if file exists.", default=True),
            },
            required=["path", "content"],
        ),
        runner=write_file_tool,
    )
)

register_tool(
    ToolDef(
        name="delete_file",
        description="Delete a file or directory under the workspace root.",
        input_schema=object_schema(
            properties={
                "path": str_schema("Path relative to workspace root."),
            },
            required=["path"],
        ),
        runner=delete_file_tool,
    )
)

register_tool(
    ToolDef(
        name="apply_patch",
        description="Apply a unified diff patch to a file under the workspace root. Shows diff preview before approval.",
        input_schema=object_schema(
            properties={
                "path": str_schema("File path relative to workspace root."),
                "patch": str_schema("Unified diff patch text."),
            },
            required=["path", "patch"],
        ),
        runner=apply_patch_tool,
    )
)
