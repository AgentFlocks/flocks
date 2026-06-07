"""
Workspace Manager

Manages ~/.flocks/workspace/ — the user-facing file storage for:
- outputs/     : agent-generated task artifacts (organized by session_id)
- knowledge/   : user-curated knowledge base (future: vector indexing)

Memory files stay in ~/.flocks/data/memory/ (agent-managed, not migrated).
This manager provides a read-only view into data/memory/ for the WebUI.
"""

import datetime as dt
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from flocks.utils.log import Log

log = Log.create(service="workspace.manager")

# Extensions treated as plain-text (previewable + editable in WebUI).
# Note: dotfiles like .gitignore have suffix='' in Python, so they are NOT
# matched here; they will fall through to the binary-file path (download only).
TEXT_EXTENSIONS = {
    ".md", ".txt", ".log", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".py", ".js", ".ts",
    ".sh", ".bash", ".csv", ".xml", ".html", ".css",
    ".tsx", ".jsx", ".env",
    ".sql", ".rs", ".go", ".java", ".c", ".cpp", ".h",
}

# Conventional subdirectories (created on init, not enforced)
CONVENTION_DIRS = ["outputs", "knowledge", "users", "shared"]
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def _get_workspace_dir() -> Path:
    """
    Resolve workspace directory.

    Priority:
    1. FLOCKS_WORKSPACE_DIR environment variable
    2. ~/.flocks/workspace (default, adjacent to data/ logs/ plugins/)
    """
    override = os.getenv("FLOCKS_WORKSPACE_DIR")
    if override:
        return Path(override)

    from flocks.config.config import Config
    # data_dir is ~/.flocks/data; workspace is sibling of data/
    return Config.get_data_path().parent / "workspace"


class WorkspaceManager:
    """
    Singleton manager for the workspace directory.

    All path arguments accepted by public methods are relative to the
    workspace root (or memory root for memory methods).  Absolute paths
    are rejected to prevent path traversal attacks.
    """

    _instance: Optional["WorkspaceManager"] = None

    def __init__(self) -> None:
        self._workspace_dir: Optional[Path] = None
        self._memory_dir: Optional[Path] = None
        self._dirs_ensured: bool = False

    @classmethod
    def get_instance(cls) -> "WorkspaceManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------ #
    # Directory resolution
    # ------------------------------------------------------------------ #

    def get_workspace_dir(self, user_id: Optional[str] = None, *, shared: bool = False) -> Path:
        if self._workspace_dir is None:
            self._workspace_dir = _get_workspace_dir()
        root = self._workspace_dir
        if shared:
            return root / "shared"
        if user_id:
            return root / "users" / user_id
        return root

    def get_memory_dir(self) -> Path:
        """Return path to agent-managed memory directory (read-only view)."""
        if self._memory_dir is None:
            from flocks.config.config import Config
            self._memory_dir = Config.get_data_path() / "memory"
        return self._memory_dir

    @staticmethod
    def normalize_username_for_path(username: str) -> str:
        """
        Normalize username to a filesystem-safe directory component.
        """
        value = (username or "").strip()
        value = value.replace("/", "_").replace("\\", "_")
        value = re.sub(r"[\x00-\x1f\x7f]+", "_", value)
        value = re.sub(r"\s+", "_", value)
        value = value.strip(" .")
        if not value:
            value = "anonymous"
        if value.upper() in _WINDOWS_RESERVED_NAMES:
            value = f"user_{value}"
        return value

    def get_user_workspace_dir(self, username: str) -> Path:
        """
        Return per-username workspace root under ``workspace/users/<username>``.
        """
        root = self.get_workspace_dir()
        return root / "users" / self.normalize_username_for_path(username)

    def get_default_outputs_dir(
        self,
        *,
        username: Optional[str] = None,
        today: Optional[dt.date] = None,
        include_today: bool = True,
    ) -> Path:
        """
        Return and create the default outputs directory.

        - OSS default: ``workspace/outputs/<today>``
        - Pro-style override when username provided:
          ``workspace/users/<username>/outputs/<today>``
        """
        self.ensure_dirs()
        if username:
            base = self.get_user_workspace_dir(username) / "outputs"
        else:
            base = self.get_workspace_dir() / "outputs"
        if include_today:
            day = (today or dt.date.today()).isoformat()
            base = base / day
        base.mkdir(parents=True, exist_ok=True)
        return base

    def ensure_dirs(self) -> None:
        """Create workspace root and conventional subdirectories if absent.

        Idempotent: a boolean flag prevents redundant syscalls after the
        first successful call within the same process lifetime.
        """
        if self._dirs_ensured:
            return
        workspace = self.get_workspace_dir()
        workspace.mkdir(parents=True, exist_ok=True)
        for name in CONVENTION_DIRS:
            (workspace / name).mkdir(exist_ok=True)
        # shared area conventions
        for name in ["outputs", "knowledge"]:
            (workspace / "shared" / name).mkdir(parents=True, exist_ok=True)
        self._dirs_ensured = True
        log.info("workspace.dirs.ensured", {"path": str(workspace)})

    def migrate_root_workspace_to_user(self, admin_user_id: str, *, dry_run: bool = False) -> dict:
        """
        Migrate legacy single-user layout to users/shared layout.

        - ``outputs`` -> ``users/<admin_user_id>/outputs``
        - ``knowledge`` -> ``shared/knowledge`` (team-shared by design)
        """
        self.ensure_dirs()
        root = self.get_workspace_dir()
        user_root = self.get_workspace_dir(admin_user_id)
        shared_root = self.get_workspace_dir(shared=True)

        legacy_outputs = root / "outputs"
        legacy_knowledge = root / "knowledge"
        target_outputs = user_root / "outputs"
        target_knowledge = shared_root / "knowledge"

        summary = {"moved_outputs": False, "moved_knowledge": False, "dry_run": dry_run}

        if legacy_outputs.exists():
            summary["moved_outputs"] = True
            if not dry_run:
                target_outputs.parent.mkdir(parents=True, exist_ok=True)
                if target_outputs.exists():
                    for child in legacy_outputs.iterdir():
                        shutil.move(str(child), str(target_outputs / child.name))
                    legacy_outputs.rmdir()
                else:
                    shutil.move(str(legacy_outputs), str(target_outputs))

        if legacy_knowledge.exists():
            summary["moved_knowledge"] = True
            if not dry_run:
                target_knowledge.parent.mkdir(parents=True, exist_ok=True)
                if target_knowledge.exists():
                    for child in legacy_knowledge.iterdir():
                        shutil.move(str(child), str(target_knowledge / child.name))
                    legacy_knowledge.rmdir()
                else:
                    shutil.move(str(legacy_knowledge), str(target_knowledge))

        return summary

    # ------------------------------------------------------------------ #
    # Path safety
    # ------------------------------------------------------------------ #

    def resolve_workspace_path(self, rel_path: str) -> Path:
        """
        Resolve a relative path inside the workspace root.

        Raises ValueError if the resolved path escapes the workspace.
        Uses Path.is_relative_to() (Python 3.9+) to avoid the prefix-match
        pitfall where '/tmp/ws_evil' would wrongly pass a startswith check
        against '/tmp/ws'.
        """
        workspace = self.get_workspace_dir().resolve()
        if Path(rel_path).is_absolute():
            raise ValueError(f"Absolute paths not allowed: {rel_path}")
        resolved = (workspace / rel_path).resolve()
        if not resolved.is_relative_to(workspace):
            raise ValueError(f"Path traversal detected: {rel_path}")
        return resolved

    def resolve_memory_path(self, rel_path: str) -> Path:
        """
        Resolve a relative path inside the memory root (read-only).

        Raises ValueError if the resolved path escapes memory root.
        Uses Path.is_relative_to() (Python 3.9+) for safe boundary checks.
        """
        memory = self.get_memory_dir().resolve()
        if Path(rel_path).is_absolute():
            raise ValueError(f"Absolute paths not allowed: {rel_path}")
        resolved = (memory / rel_path).resolve()
        if not resolved.is_relative_to(memory):
            raise ValueError(f"Path traversal detected: {rel_path}")
        return resolved

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def is_text_file(path: Path) -> bool:
        return path.suffix.lower() in TEXT_EXTENSIONS
