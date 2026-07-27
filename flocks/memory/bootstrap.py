"""Build filesystem Memory-State snapshots for model context."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from flocks.config import Config
from flocks.utils.log import Log


log = Log.create(service="memory.bootstrap")

GLOBAL_MEMORY_LIMIT = 4 * 1024
USER_MEMORY_LIMIT = 4 * 1024
PROJECT_MEMORY_LIMIT = 8 * 1024
MISSION_CONTEXT_LIMIT = 16 * 1024
_DAILY_LOCK = threading.RLock()

MEMORY_INSTRUCTIONS = """## Memory-State System Guidance

Persistent memory uses ordinary Markdown files, not a search service.

- Global memory: `{global_root}/MEMORY.md`
- User profile: `{global_root}/USER.md`
- Daily notes: `{global_root}/daily/YYYY-MM-DD.md`
- Project memory: `{project_root}/MEMORY.md`

Use read, glob, and grep for explicit lookup. Use write only to create a missing
file and edit for precise changes to an existing file. Daily notes are not
loaded automatically.

Save only stable preferences, non-derivable project constraints, explicit
corrections, and verified reusable experience. Do not save secrets, guesses,
large tool output, code facts available from the repository, or live Mission
progress. Mission files are protected: update them with todo and
mission_record. A Mission is complete only after its contract and evidence
gate pass."""


def _read_bounded(path: Path, limit: int) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    content = raw[:limit].decode("utf-8", errors="replace")
    truncated = len(raw) > limit
    if truncated:
        content = content.rstrip() + "\n\n[Memory file truncated for context.]"
    return {
        "path": str(path),
        "abs_path": str(path),
        "content": content,
        "size": len(raw),
        "hash": hashlib.sha256(raw).hexdigest(),
        "truncated": truncated,
        "inject": True,
    }


class MemoryBootstrap:
    """Create and load global, project, and current-Mission context."""

    def __init__(
        self,
        workspace_dir: Optional[str | Path] = None,
        mission_id: Optional[str] = None,
    ):
        self.workspace_dir = (
            Path(workspace_dir).expanduser().resolve(strict=False)
            if workspace_dir
            else None
        )
        self.mission_id = mission_id
        self.memory_dir = Config.get_memory_path().expanduser().resolve(strict=False)
        self.daily_dir = self.memory_dir / "daily"
        self.project_memory_dir = (
            self.workspace_dir / ".flocks" / "memory"
            if self.workspace_dir
            else None
        )

    async def create_memory_structure(self) -> None:
        """Create stable memory files without overwriting existing content."""
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_file(
            self.memory_dir / "MEMORY.md",
            "# Global Memory\n\n"
            "Stable cross-project preferences, rules, and verified experience.\n",
        )
        self._ensure_file(
            self.memory_dir / "USER.md",
            "# User Profile\n\n"
            "Stable user role, background, and collaboration preferences.\n",
        )
        if self.project_memory_dir:
            self.project_memory_dir.mkdir(parents=True, exist_ok=True)
            (self.project_memory_dir / "missions").mkdir(parents=True, exist_ok=True)
            self._ensure_file(
                self.project_memory_dir / "MEMORY.md",
                "# Project Memory\n\n"
                "Stable project constraints, decisions, and verified experience.\n",
            )

    @staticmethod
    def _ensure_file(path: Path, initial_content: str) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(initial_content)
            handle.flush()
            os.fsync(handle.fileno())

    async def load_main_memory(self) -> Optional[Dict[str, Any]]:
        """Compatibility helper returning global MEMORY.md."""
        return _read_bounded(self.memory_dir / "MEMORY.md", GLOBAL_MEMORY_LIMIT)

    def get_daily_memory_paths(
        self,
        days_back: int = 1,
        today: Optional[str] = None,
    ) -> list[str]:
        """Return possible daily paths without loading their contents."""
        current = datetime.strptime(today, "%Y-%m-%d") if today else datetime.now()
        return [
            f"daily/{(current - timedelta(days=index)).strftime('%Y-%m-%d')}.md"
            for index in range(days_back + 1)
        ]

    async def load_daily_memories(
        self,
        days_back: int = 1,
        today: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """Read explicitly requested daily files; bootstrap never calls this."""
        result = []
        for relative in self.get_daily_memory_paths(days_back, today):
            loaded = _read_bounded(self.memory_dir / relative, GLOBAL_MEMORY_LIMIT)
            if loaded:
                result.append(loaded)
        return result

    def append_daily(self, content: str, *, date: Optional[str] = None) -> Path:
        """Append one session digest to today's daily file atomically."""
        day = date or datetime.now().strftime("%Y-%m-%d")
        if not re_full_date(day):
            raise ValueError(f"Invalid daily memory date: {day!r}")
        path = self.daily_dir / f"{day}.md"
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        with _DAILY_LOCK:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            separator = "\n\n" if existing.strip() else ""
            combined = existing.rstrip() + separator + content.rstrip() + "\n"
            fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(combined)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        return path

    def get_agent_instructions(self, **_: Any) -> str:
        project_root = self.project_memory_dir or Path("<no-project-memory>")
        return MEMORY_INSTRUCTIONS.format(
            global_root=self.memory_dir,
            project_root=project_root,
        )

    async def bootstrap(
        self,
        load_main: bool = True,
        load_daily: bool = False,
        days_back: int = 1,
    ) -> Dict[str, Any]:
        """Return a fresh layered Memory-State snapshot.

        ``load_daily`` remains accepted for compatibility but daily files are
        deliberately excluded from automatic context.
        """
        del load_daily, days_back
        await self.create_memory_structure()

        memory_files = []
        global_memory = (
            _read_bounded(self.memory_dir / "MEMORY.md", GLOBAL_MEMORY_LIMIT)
            if load_main
            else None
        )
        user_memory = _read_bounded(
            self.memory_dir / "USER.md",
            USER_MEMORY_LIMIT,
        )
        if global_memory:
            global_memory["label"] = "Global MEMORY.md"
            memory_files.append(global_memory)
        if user_memory:
            user_memory["label"] = "USER.md"
            memory_files.append(user_memory)
        if self.project_memory_dir:
            project_memory = _read_bounded(
                self.project_memory_dir / "MEMORY.md",
                PROJECT_MEMORY_LIMIT,
            )
            if project_memory:
                project_memory["label"] = "Project MEMORY.md"
                memory_files.append(project_memory)

        mission_context = None
        mission_revision = None
        if self.workspace_dir and self.mission_id:
            try:
                from flocks.memory.mission import MissionStore

                store = MissionStore(self.workspace_dir)
                state = store.load(self.mission_id)
                content = store.render_hot_context(self.mission_id)
                mission_context = {
                    "path": str(store.mission_path(self.mission_id)),
                    "content": content[:MISSION_CONTEXT_LIMIT],
                    "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "inject": True,
                }
                mission_revision = state["meta"].get("revision")
            except (FileNotFoundError, ValueError, OSError) as exc:
                log.warn(
                    "bootstrap.mission_load_failed",
                    {"mission_id": self.mission_id, "error": str(exc)},
                )

        snapshot_hash = hashlib.sha256(
            "".join(
                str(item.get("hash", ""))
                for item in memory_files + ([mission_context] if mission_context else [])
            ).encode("utf-8")
        ).hexdigest()
        result = {
            "main_memory": global_memory,
            "memory_files": memory_files,
            "daily_memories": [],
            "mission_context": mission_context,
            "mission_id": self.mission_id,
            "mission_revision": mission_revision,
            "snapshot_hash": snapshot_hash,
            "instructions": self.get_agent_instructions(),
            "today": datetime.now().strftime("%Y-%m-%d"),
            "yesterday": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        log.info(
            "bootstrap.complete",
            {
                "memory_files": len(memory_files),
                "mission_id": self.mission_id,
                "mission_revision": mission_revision,
            },
        )
        return result


def re_full_date(value: str) -> bool:
    """Return whether value is a canonical YYYY-MM-DD date."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False
