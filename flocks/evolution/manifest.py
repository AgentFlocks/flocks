"""
Author manifest - append-only ledger of agent-created skills.

Lives at ``~/.flocks/data/evolution/authored.jsonl`` and is the single
source of truth for distinguishing skills the agent itself produced (via
the L2 SkillAuthor) from skills installed via ``flocks skills install``
or shipped in the bundle. The L4 Curator only operates on names that
appear in this manifest, so upstream-managed skills are never archived
or rewritten on the user's behalf.

Concurrency model
-----------------
The manifest is JSON Lines so concurrent appenders rely on POSIX
``O_APPEND`` atomicity for sub-PIPE_BUF writes (Windows: same effect via
``open('a', buffering=0)``). Each record is a single JSON object on one
line and is small enough (< 4 KiB) to be written atomically by a single
``write()`` syscall on every supported OS. Readers parse the file
top-to-bottom and tolerate lines that fail to decode (truncated tail
lines from a crashed writer, etc.).

Record schema
-------------
::

    {
      "name": "build-gradle-plugin",
      "scope": "user" | "project",
      "skill_dir": "/abs/path/to/skill_dir",
      "created_at": "2026-04-29T16:21:03.124+00:00",
      "created_by_session": "<session_id>",
      "category": "automation",
      "tags": ["gradle", "android"]
    }
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from flocks.utils.log import Log

log = Log.create(service="evolution.manifest")


def _evolution_data_dir() -> Path:
    """Return the evolution module's persistent data directory.

    Honours ``FLOCKS_ROOT`` / ``$HOME`` the same way ``flocks.utils.log``
    does so user installs and CI sandboxes resolve consistently.
    """
    raw = os.getenv("FLOCKS_ROOT")
    base = Path(raw) if raw else (Path.home() / ".flocks")
    return base / "data" / "evolution"


def _manifest_path() -> Path:
    return _evolution_data_dir() / "authored.jsonl"


class AuthorManifest:
    """Append-only ledger of agent-created skills.

    The class is process-shared (no per-instance state aside from cache),
    but each public mutating method takes a coarse-grained lock so
    intra-process race windows around read-modify-write helpers like
    ``forget()`` stay closed.
    """

    _instance_lock = threading.Lock()
    _instance: Optional["AuthorManifest"] = None

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: Optional[List[Dict]] = None

    @classmethod
    def get(cls) -> "AuthorManifest":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Test-only: drop the singleton + its cached records."""
        with cls._instance_lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def path(self) -> Path:
        return _manifest_path()

    def all_records(self, refresh: bool = False) -> List[Dict]:
        """Return every well-formed record in the manifest (cached)."""
        with self._lock:
            if not refresh and self._cache is not None:
                return list(self._cache)

            path = _manifest_path()
            if not path.exists():
                self._cache = []
                return []

            records: List[Dict] = []
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for lineno, line in enumerate(fh, start=1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError as exc:
                            log.warn(
                                "manifest.parse_error",
                                {"line": lineno, "error": str(exc)},
                            )
            except OSError as exc:  # pragma: no cover - filesystem error
                log.error("manifest.read_failed", {"error": str(exc)})
                return []

            self._cache = records
            return list(records)

    def get_record(self, name: str, scope: Optional[str] = None) -> Optional[Dict]:
        """Return the latest record for ``name`` (last write wins, including tombstones)."""
        match: Optional[Dict] = None
        for rec in self.all_records():
            if rec.get("name") != name:
                continue
            if scope and rec.get("scope") != scope:
                continue
            match = rec
        return match

    def is_authored(self, name: str, scope: Optional[str] = None) -> bool:
        """True iff the latest record for ``name`` exists and is not a tombstone."""
        rec = self.get_record(name, scope)
        return bool(rec) and not rec.get("deleted")

    def names(self, scope: Optional[str] = None) -> List[str]:
        """Return the live (non-deleted) skill names, optionally filtered by scope."""
        latest: Dict[str, Dict] = {}
        for rec in self.all_records():
            if scope and rec.get("scope") != scope:
                continue
            name = rec.get("name")
            if name:
                latest[name] = rec
        return [n for n, rec in latest.items() if not rec.get("deleted")]

    # ------------------------------------------------------------------
    # Write paths (append-only)
    # ------------------------------------------------------------------

    def append(self, record: Dict) -> None:
        """Atomically append a single record to the manifest."""
        if "created_at" not in record:
            record["created_at"] = datetime.now(timezone.utc).isoformat()

        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        path = _manifest_path()

        with self._lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                # Open with O_APPEND so concurrent writers from other
                # processes never overwrite each other's lines. Buffering=0
                # forces each .write() to a single syscall.
                with path.open("ab", buffering=0) as fh:
                    fh.write(line.encode("utf-8"))
            except OSError as exc:  # pragma: no cover - filesystem error
                log.error("manifest.append_failed", {"error": str(exc)})
                raise
            self._cache = None  # invalidate cache; next read reloads

    def forget(self, name: str, scope: Optional[str] = None) -> None:
        """Append a tombstone record so subsequent reads see ``name`` as deleted.

        We never rewrite the manifest in place — readers honour the
        latest record per (name, scope) tuple, and a record with
        ``deleted=True`` removes it from ``names()`` / ``is_authored()``.
        """
        record = {
            "name": name,
            "scope": scope or "user",
            "deleted": True,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
        }
        self.append(record)


__all__ = ["AuthorManifest"]
