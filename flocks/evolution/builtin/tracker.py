"""
Built-in L3 UsageTracker - per-project sidecar JSON files.

Telemetry layout::

    ~/.flocks/data/evolution/usage/
    ├── _user.json                     # all user-scope skills
    └── <project_id_hash>.json         # one file per project (project-scope)

Each file is a JSON object keyed by skill name with the columns defined
in ``UsageRow``. Writes go through tempfile + os.replace so a crash in
the middle of a save never corrupts existing data.

Project routing
---------------
Project-scope rows are written to a file named after a short SHA-256
prefix of ``Instance.get_project().id``. This keeps file names safe on
all filesystems while still being deterministic, so reopening the same
project lands on the same sidecar.

Authorship gating
-----------------
``bump_use`` always writes the row, but the curator only consumes
entries for skills that appear in ``AuthorManifest`` — so usage is
recorded for every skill (useful for analytics) yet only agent-created
ones are eligible for archival.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from flocks.evolution.strategies import UsageTracker
from flocks.evolution.types import SkillScope, UsageRow, UsageState
from flocks.project.instance import Instance
from flocks.utils.log import Log

log = Log.create(service="evolution.tracker")


def _data_dir() -> Path:
    raw = os.getenv("FLOCKS_ROOT")
    base = Path(raw) if raw else (Path.home() / ".flocks")
    return base / "data" / "evolution" / "usage"


def _project_id() -> Optional[str]:
    try:
        proj = Instance.get_project()
        if proj and proj.id:
            return str(proj.id)
    except Exception:
        return None
    return None


def _project_hash(project_id: Optional[str]) -> str:
    """Short, filesystem-safe slug derived from a project id.

    Falls back to ``"_default"`` when we're not running inside an
    Instance (e.g. CLI evolution status). Using SHA-256 prefix keeps the
    name safe and stable across OSes without leaking project paths.
    """
    if not project_id:
        return "_default"
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    return digest[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BuiltinFsUsageTracker(UsageTracker):
    """Default tracker. Per-project JSON files under ``~/.flocks/data/evolution/usage/``."""

    name = "builtin"
    is_noop = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # In-process cache: file_path -> dict[skill_name -> UsageRow-as-dict]
        self._cache: Dict[Path, Dict[str, Dict]] = {}

    # ------------------------------------------------------------------
    # File routing
    # ------------------------------------------------------------------

    def _file_for(self, scope: SkillScope) -> Path:
        if scope == "project":
            slug = _project_hash(_project_id())
            return _data_dir() / f"{slug}.json"
        return _data_dir() / "_user.json"

    # ------------------------------------------------------------------
    # IO helpers (caller MUST hold self._lock)
    # ------------------------------------------------------------------

    def _load(self, path: Path) -> Dict[str, Dict]:
        if path in self._cache:
            return self._cache[path]
        if not path.exists():
            self._cache[path] = {}
            return self._cache[path]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                log.warn("tracker.invalid_file", {"path": str(path)})
                data = {}
        except (OSError, json.JSONDecodeError) as exc:
            log.warn("tracker.read_failed", {"path": str(path), "error": str(exc)})
            data = {}
        self._cache[path] = data
        return data

    def _save(self, path: Path, data: Dict[str, Dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.tmp.",
            suffix="",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._cache[path] = data

    def _row(self, data: Dict[str, Dict], skill_name: str, scope: SkillScope) -> Dict:
        row = data.get(skill_name)
        if row is None:
            row = {
                "name": skill_name,
                "scope": scope,
                "use_count": 0,
                "view_count": 0,
                "patch_count": 0,
                "last_used_at": None,
                "last_viewed_at": None,
                "last_patched_at": None,
                "created_at": _now_iso(),
                "state": UsageState.ACTIVE.value,
                "pinned": False,
                "archived_at": None,
            }
            data[skill_name] = row
        return row

    # ------------------------------------------------------------------
    # UsageTracker protocol
    # ------------------------------------------------------------------

    def bump_use(self, skill_name: str, scope: SkillScope = "user") -> None:
        if not skill_name:
            return
        path = self._file_for(scope)
        with self._lock:
            data = self._load(path)
            row = self._row(data, skill_name, scope)
            row["use_count"] = int(row.get("use_count", 0)) + 1
            row["last_used_at"] = _now_iso()
            # Bumping use auto-reactivates a stale row (curator may re-archive later).
            if row.get("state") == UsageState.STALE.value:
                row["state"] = UsageState.ACTIVE.value
            self._save(path, data)

    def bump_view(self, skill_name: str, scope: SkillScope = "user") -> None:
        if not skill_name:
            return
        path = self._file_for(scope)
        with self._lock:
            data = self._load(path)
            row = self._row(data, skill_name, scope)
            row["view_count"] = int(row.get("view_count", 0)) + 1
            row["last_viewed_at"] = _now_iso()
            self._save(path, data)

    def bump_patch(self, skill_name: str, scope: SkillScope = "user") -> None:
        if not skill_name:
            return
        path = self._file_for(scope)
        with self._lock:
            data = self._load(path)
            row = self._row(data, skill_name, scope)
            row["patch_count"] = int(row.get("patch_count", 0)) + 1
            row["last_patched_at"] = _now_iso()
            self._save(path, data)

    def set_state(self, skill_name: str, state: UsageState, scope: SkillScope = "user") -> None:
        if not skill_name:
            return
        path = self._file_for(scope)
        with self._lock:
            data = self._load(path)
            row = self._row(data, skill_name, scope)
            row["state"] = state.value if hasattr(state, "value") else str(state)
            if row["state"] == UsageState.ARCHIVED.value:
                row["archived_at"] = _now_iso()
            self._save(path, data)

    def set_pinned(self, skill_name: str, pinned: bool, scope: SkillScope = "user") -> None:
        if not skill_name:
            return
        path = self._file_for(scope)
        with self._lock:
            data = self._load(path)
            row = self._row(data, skill_name, scope)
            row["pinned"] = bool(pinned)
            self._save(path, data)

    def report(self) -> List[UsageRow]:
        """Return rows from both _user.json and the current project file."""
        rows: List[UsageRow] = []
        for path in (self._file_for("user"), self._file_for("project")):
            with self._lock:
                data = self._load(path)
            for raw in data.values():
                try:
                    rows.append(UsageRow(**raw))
                except Exception as exc:  # pragma: no cover - defensive
                    log.warn("tracker.row_invalid", {"row": raw, "error": str(exc)})
        return rows

    def forget(self, skill_name: str, scope: SkillScope = "user") -> None:
        path = self._file_for(scope)
        with self._lock:
            data = self._load(path)
            if skill_name in data:
                del data[skill_name]
                self._save(path, data)

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Drop the in-memory cache so the next read re-loads from disk.

        Production never needs this — every mutation flows through the
        tracker so the cache and disk stay in sync. Tests that mutate
        usage files directly to simulate aged data must call this to
        invalidate the cached dicts.
        """
        with self._lock:
            self._cache.clear()


__all__ = ["BuiltinFsUsageTracker"]
