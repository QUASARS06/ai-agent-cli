# agentcli/sessions.py
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

INDEX_FILE = "index.json"
SCHEMA_VERSION = 1

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_root() -> Path:
    """
    Deterministic root: parent of the installed package directory.
    Assumes repo layout:
      <root>/
        pyproject.toml
        agentcli/
          __init__.py
          ...
    """
    return Path(__file__).resolve().parents[1]

def sessions_dir_at_root() -> Path:
    return project_root() / "sessions"


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def sanitize_session_name(name: str) -> str:
    name = name.strip()
    if not name:
        return ""
    name = name.replace(" ", "-")
    name = _SAFE_NAME_RE.sub("-", name)
    name = name.strip("-")
    name = name[:64]  # keep filenames manageable
    return name or ""


def generate_default_session_name() -> str:
    # session-YYYY-MM-DD_HHMMSS
    return datetime.now().strftime("session-%Y-%m-%d_%H%M%S")


@dataclass
class SessionInfo:
    name: str
    file: str
    created_at: str
    updated_at: str


class SessionStore:
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = base_dir or sessions_dir_at_root()

    @property
    def index_path(self) -> Path:
        return self.base_dir / INDEX_FILE

    def _load_index(self) -> Dict[str, Any]:
        if not self.index_path.exists():
            return {"version": SCHEMA_VERSION, "last_session": None, "sessions": {}}
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            # Corrupt index: start fresh but don't crash.
            return {"version": SCHEMA_VERSION, "last_session": None, "sessions": {}}

    def _save_index(self, index: Dict[str, Any]) -> None:
        index.setdefault("version", SCHEMA_VERSION)
        index.setdefault("sessions", {})
        _atomic_write_json(self.index_path, index)

    def _session_path(self, name: str) -> Path:
        return self.base_dir / f"{name}.json"

    def list_sessions(self) -> List[SessionInfo]:
        idx = self._load_index()
        sessions = []
        for name, meta in (idx.get("sessions") or {}).items():
            if not isinstance(meta, dict):
                continue
            sessions.append(
                SessionInfo(
                    name=name,
                    file=str(meta.get("file") or f"{name}.json"),
                    created_at=str(meta.get("created_at") or ""),
                    updated_at=str(meta.get("updated_at") or ""),
                )
            )
        # sort by updated desc
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def session_exists(self, name: str) -> bool:
        return (self.base_dir / f"{name}.json").exists()

    def get_last_session_name(self) -> Optional[str]:
        idx = self._load_index()
        ls = idx.get("last_session")
        if isinstance(ls, str) and ls.strip():
            return ls.strip()
        return None

    def set_last_session_name(self, name: str) -> None:
        idx = self._load_index()
        idx["last_session"] = name
        self._save_index(idx)

    def _ensure_unique_name(self, base: str) -> str:
        base = sanitize_session_name(base)
        if not base:
            base = generate_default_session_name()

        idx = self._load_index()
        existing = set((idx.get("sessions") or {}).keys())
        if base not in existing and not self._session_path(base).exists():
            return base

        i = 2
        while True:
            cand = f"{base}-{i}"
            if cand not in existing and not self._session_path(cand).exists():
                return cand
            i += 1

    def create_session(self, name: Optional[str] = None) -> str:
        self.base_dir.mkdir(parents=True, exist_ok=True)

        final_name = self._ensure_unique_name(name or generate_default_session_name())
        now = _utc_now_iso()

        idx = self._load_index()
        sess_meta = {
            "name": final_name,
            "file": f"{final_name}.json",
            "created_at": now,
            "updated_at": now,
        }
        idx.setdefault("sessions", {})
        idx["sessions"][final_name] = sess_meta
        idx["last_session"] = final_name
        self._save_index(idx)

        # Create minimal session file (messages filled by caller)
        if not self._session_path(final_name).exists():
            _atomic_write_json(
                self._session_path(final_name),
                {
                    "version": SCHEMA_VERSION,
                    "name": final_name,
                    "created_at": now,
                    "updated_at": now,
                    "messages": [],
                    "meta": {},
                },
            )

        return final_name

    def load_session(self, name: str) -> Dict[str, Any]:
        name = sanitize_session_name(name)
        if not name:
            raise ValueError("Invalid session name")

        path = self._session_path(name)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {name}")

        data = json.loads(path.read_text(encoding="utf-8"))
        # Update last_session pointer (non-destructive)
        idx = self._load_index()
        if "sessions" not in idx:
            idx["sessions"] = {}
        if name not in idx["sessions"]:
            # Session exists but wasn't in index (manual file added) — register it.
            now = _utc_now_iso()
            idx["sessions"][name] = {
                "name": name,
                "file": f"{name}.json",
                "created_at": data.get("created_at") or now,
                "updated_at": data.get("updated_at") or now,
            }
        idx["last_session"] = name
        self._save_index(idx)

        return data

    def save_session(self, name: str, messages: List[Dict[str, Any]], meta: Optional[Dict[str, Any]] = None) -> None:
        name = sanitize_session_name(name)
        if not name:
            raise ValueError("Invalid session name")

        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self._session_path(name)

        now = _utc_now_iso()

        # Preserve created_at if exists
        created_at = now
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                created_at = existing.get("created_at") or created_at
            except Exception:
                pass

        data = {
            "version": SCHEMA_VERSION,
            "name": name,
            "created_at": created_at,
            "updated_at": now,
            "messages": messages,
            "meta": meta or {},
        }
        _atomic_write_json(path, data)

        # Update index
        idx = self._load_index()
        idx.setdefault("sessions", {})
        if name not in idx["sessions"]:
            idx["sessions"][name] = {
                "name": name,
                "file": f"{name}.json",
                "created_at": created_at,
                "updated_at": now,
            }
        else:
            idx["sessions"][name]["updated_at"] = now
            idx["sessions"][name].setdefault("created_at", created_at)
            idx["sessions"][name].setdefault("file", f"{name}.json")
        idx["last_session"] = name
        self._save_index(idx)

    def delete_session(self, name: str) -> None:
        name = sanitize_session_name(name)
        if not name:
            raise ValueError("Invalid session name")

        path = self._session_path(name)
        if path.exists():
            path.unlink()

        idx = self._load_index()
        sessions = idx.get("sessions") or {}
        if name in sessions:
            sessions.pop(name, None)
        if idx.get("last_session") == name:
            idx["last_session"] = None
        idx["sessions"] = sessions
        self._save_index(idx)

    def rename_session(self, old: str, new: str) -> str:
        old = sanitize_session_name(old)
        new = sanitize_session_name(new)
        if not old or not new:
            raise ValueError("Invalid session name")

        new_final = self._ensure_unique_name(new)

        old_path = self._session_path(old)
        if not old_path.exists():
            raise FileNotFoundError(f"Session not found: {old}")

        new_path = self._session_path(new_final)
        os.replace(old_path, new_path)

        # Update index
        idx = self._load_index()
        sessions = idx.get("sessions") or {}
        meta = sessions.pop(old, None) or {}
        meta["name"] = new_final
        meta["file"] = f"{new_final}.json"
        meta["updated_at"] = _utc_now_iso()
        sessions[new_final] = meta

        if idx.get("last_session") == old:
            idx["last_session"] = new_final

        idx["sessions"] = sessions
        self._save_index(idx)

        # Update session file name field
        try:
            data = json.loads(new_path.read_text(encoding="utf-8"))
            data["name"] = new_final
            data["updated_at"] = _utc_now_iso()
            _atomic_write_json(new_path, data)
        except Exception:
            pass

        return new_final