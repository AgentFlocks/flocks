"""User-scoped project registry backed by JSON files.

Projects are UI/session groupings bound to existing directories. Legacy project
records in Storage are intentionally ignored; sessions that do not reference a
registered project are presented under the virtual default project.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterator, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from flocks.utils.log import Log

log = Log.create(service="project")

DEFAULT_PROJECT_ID = "default"
DEFAULT_PROJECT_NAME = "默认"
_RESERVED_PROJECT_NAMES = {"default", DEFAULT_PROJECT_NAME.casefold()}


def _platform_file_lock(fd: int) -> None:
    if sys.platform == "win32":  # pragma: no cover - Windows only
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _platform_file_unlock(fd: int) -> None:
    if sys.platform == "win32":  # pragma: no cover - Windows only
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _registry_cross_process_lock(registry_path: Path) -> Iterator[None]:
    """Serialize registry read-modify-write operations across processes."""

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = registry_path.with_suffix(".json.lock")
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    try:
        _platform_file_lock(fd)
        locked = True
        yield
    finally:
        try:
            if locked:
                _platform_file_unlock(fd)
        finally:
            os.close(fd)


class ProjectNameConflictError(ValueError):
    """Raised when a project name is already registered for the user."""


class ProjectPathConflictError(ValueError):
    """Raised when a project directory is already registered."""

    def __init__(self, project: "ProjectInfo") -> None:
        super().__init__(f"Project directory is already registered as '{project.name}'")
        self.project = project


class ProjectDeletionError(ValueError):
    """Raised when a protected project cannot be deleted."""


class ProjectIcon(BaseModel):
    """Project icon configuration."""

    url: Optional[str] = None
    override: Optional[str] = None
    color: Optional[str] = None


class ProjectTime(BaseModel):
    """Project time metadata."""

    created: int
    updated: int
    initialized: Optional[int] = None


class ProjectInfo(BaseModel):
    """Project information returned by project and session APIs."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    worktree: str
    vcs: Optional[str] = None
    name: Optional[str] = None
    icon: Optional[ProjectIcon] = None
    time: ProjectTime
    sandboxes: List[str] = Field(default_factory=list)
    is_default: bool = Field(False, alias="isDefault")
    path_status: Literal["available", "missing", "unreadable"] = Field(
        "available",
        alias="pathStatus",
    )
    session_count: int = Field(0, alias="sessionCount")
    matched_session_count: int = Field(0, alias="matchedSessionCount")
    last_activity_at: Optional[int] = Field(None, alias="lastActivityAt")
    owner_user_id: Optional[str] = Field(None, alias="ownerUserID")
    can_write: bool = Field(True, alias="canWrite")
    can_delete: bool = Field(True, alias="canDelete")
    is_shared: bool = Field(False, alias="isShared")


class ProjectRegistryEntry(BaseModel):
    """Persisted project entry."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    worktree: str
    created_at: int = Field(alias="createdAt")
    updated_at: int = Field(alias="updatedAt")
    owner_user_id: Optional[str] = Field(None, alias="ownerUserID")
    shared_local: bool = Field(False, alias="sharedLocal")


class ProjectRegistry(BaseModel):
    """Per-user project registry file."""

    model_config = ConfigDict(populate_by_name=True)

    default_worktree: str = Field(alias="defaultWorktree")
    projects: List[ProjectRegistryEntry] = Field(default_factory=list)


class Project:
    """Project registry and virtual default-project operations."""

    _lock = asyncio.Lock()
    _session_stats_cache: ClassVar[
        Dict[Tuple[str, str], Tuple[float, Dict[str, Tuple[int, int, Optional[int]]]]]
    ] = {}
    _session_stats_cache_ttl_seconds = 30.0

    @staticmethod
    def _now_ms() -> int:
        return int(datetime.now().timestamp() * 1000)

    @staticmethod
    def _flocks_root() -> Path:
        return Path(os.getenv("FLOCKS_ROOT", str(Path.home() / ".flocks"))).expanduser()

    @classmethod
    def _owner_hash(cls, owner_id: str) -> str:
        return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:24]

    @classmethod
    def registry_path(cls, owner_id: str) -> Path:
        """Return the project registry path for a user."""

        return cls._flocks_root() / "projects" / f"{cls._owner_hash(owner_id)}.json"

    @staticmethod
    def _normalized_worktree(worktree: str) -> str:
        return os.path.normcase(str(Path(worktree).expanduser().resolve(strict=True)))

    @staticmethod
    def _directory_context(directory: str) -> Tuple[Path, Path, Optional[str]]:
        path = Path(directory).expanduser().resolve()
        worktree = path
        vcs: Optional[str] = None
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                worktree = Path(result.stdout.strip()).resolve()
                vcs = "git"
        except (OSError, subprocess.SubprocessError):
            pass
        return path, worktree, vcs

    @classmethod
    def worktree_for_directory(cls, directory: str) -> str:
        """Return a stable runtime scope for CLI sessions in a directory."""

        _, worktree, _ = cls._directory_context(directory)
        return os.path.normcase(str(worktree))

    @staticmethod
    def _path_status(worktree: str) -> Literal["available", "missing", "unreadable"]:
        path = Path(worktree)
        if not path.is_dir():
            return "missing"
        if not os.access(path, os.R_OK | os.X_OK):
            return "unreadable"
        return "available"

    @classmethod
    def default_worktree_candidate(cls) -> str:
        """Resolve the initial default directory without using the server cwd."""

        configured = os.getenv("FLOCKS_DEFAULT_PROJECT_DIR")
        if configured:
            path = Path(configured).expanduser()
        else:
            configured_workspace = os.getenv("FLOCKS_WORKSPACE_DIR")
            path = (
                Path(configured_workspace).expanduser()
                if configured_workspace
                else cls._flocks_root() / "workspace"
            )
        path.mkdir(parents=True, exist_ok=True)
        return str(path.resolve())

    @classmethod
    def allowed_roots(cls, default_worktree: Optional[str] = None) -> List[Path]:
        """Return canonical roots available to the server-side folder picker."""

        configured = os.getenv("FLOCKS_PROJECT_ROOTS", "").strip()
        candidates = [Path(item).expanduser() for item in configured.split(os.pathsep) if item]
        if not candidates:
            candidates = [Path.home()]
        if default_worktree:
            candidates.append(Path(default_worktree).expanduser())

        roots: List[Path] = []
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not resolved.is_dir():
                continue
            if any(resolved == root or resolved.is_relative_to(root) for root in roots):
                continue
            roots = [root for root in roots if not root.is_relative_to(resolved)]
            roots.append(resolved)
        return sorted(roots, key=lambda item: str(item).casefold())

    @classmethod
    def validate_worktree(
        cls,
        worktree: str,
        *,
        default_worktree: Optional[str] = None,
        create_if_missing: bool = False,
    ) -> str:
        """Validate and canonicalize a project directory."""

        if not worktree or not worktree.strip():
            raise ValueError("Project directory cannot be empty")
        requested_path = Path(worktree).expanduser()
        if not requested_path.is_absolute():
            raise ValueError("Project directory must be an absolute path")
        requested_path = Path(os.path.abspath(requested_path))
        roots = cls.allowed_roots(default_worktree)

        if create_if_missing and not requested_path.exists():
            ancestor = requested_path
            missing_parts: List[str] = []
            while not ancestor.exists() and ancestor != ancestor.parent:
                missing_parts.append(ancestor.name)
                ancestor = ancestor.parent
            try:
                resolved_ancestor = ancestor.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValueError("Project directory cannot be resolved") from exc
            target = resolved_ancestor.joinpath(*reversed(missing_parts))
            if roots and not any(target == root or target.is_relative_to(root) for root in roots):
                raise ValueError("Project directory is outside the allowed roots")
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ValueError("Project directory could not be created") from exc

        try:
            path = requested_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("Project directory does not exist") from exc
        if not path.is_dir():
            raise ValueError("Project path must be a directory")
        if not os.access(path, os.R_OK | os.X_OK):
            raise ValueError("Project directory is not readable")

        if roots and not any(path == root or path.is_relative_to(root) for root in roots):
            raise ValueError("Project directory is outside the allowed roots")
        return os.path.normcase(str(path))

    @classmethod
    def _read_registry_file(cls, path: Path) -> Optional[ProjectRegistry]:
        if not path.exists():
            return None
        try:
            return ProjectRegistry.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.error("project.registry.read_failed", {"path": str(path), "error": str(exc)})
            return None

    @classmethod
    def _read_registry(
        cls,
        owner_id: str,
        *,
        default_worktree: Optional[str] = None,
    ) -> ProjectRegistry:
        path = cls.registry_path(owner_id)
        registry = cls._read_registry_file(path)
        if registry is not None:
            return registry

        candidate = default_worktree or cls.default_worktree_candidate()
        try:
            candidate = cls.validate_worktree(candidate, default_worktree=candidate)
        except ValueError:
            candidate = cls.default_worktree_candidate()
        return ProjectRegistry(defaultWorktree=candidate, projects=[])

    @classmethod
    def _write_registry(cls, owner_id: str, registry: ProjectRegistry) -> None:
        path = cls.registry_path(owner_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        fd, temporary_path = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.stem}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    registry.model_dump(by_alias=True),
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
            raise

    @classmethod
    def _entry_to_info(
        cls,
        entry: ProjectRegistryEntry,
        *,
        can_write: bool = True,
    ) -> ProjectInfo:
        return ProjectInfo(
            id=entry.id,
            worktree=entry.worktree,
            name=entry.name,
            vcs="git" if (Path(entry.worktree) / ".git").exists() else None,
            time=ProjectTime(created=entry.created_at, updated=entry.updated_at),
            pathStatus=cls._path_status(entry.worktree),
            ownerUserID=entry.owner_user_id,
            canWrite=can_write,
            canDelete=can_write,
            isShared=entry.shared_local,
        )

    @classmethod
    def _default_to_info(cls, registry: ProjectRegistry) -> ProjectInfo:
        now = cls._now_ms()
        return ProjectInfo(
            id=DEFAULT_PROJECT_ID,
            worktree=registry.default_worktree,
            name=DEFAULT_PROJECT_NAME,
            vcs="git" if (Path(registry.default_worktree) / ".git").exists() else None,
            time=ProjectTime(created=now, updated=now),
            isDefault=True,
            pathStatus=cls._path_status(registry.default_worktree),
            canDelete=False,
        )

    @classmethod
    async def ensure_registry(
        cls,
        owner_id: str,
        *,
        default_worktree: Optional[str] = None,
    ) -> ProjectRegistry:
        """Load a registry and persist its initial default directory once."""

        async with cls._lock:
            path = cls.registry_path(owner_id)
            with _registry_cross_process_lock(path):
                registry = cls._read_registry(owner_id, default_worktree=default_worktree)
                if not path.exists():
                    cls._write_registry(owner_id, registry)
                return registry

    @classmethod
    async def create(
        cls,
        *,
        owner_id: str,
        name: Optional[str],
        worktree: str,
    ) -> ProjectInfo:
        """Register an existing directory for a user."""

        async with cls._lock:
            with _registry_cross_process_lock(cls.registry_path(owner_id)):
                registry = cls._read_registry(owner_id)
                normalized_worktree = cls.validate_worktree(
                    worktree,
                    default_worktree=registry.default_worktree,
                    create_if_missing=True,
                )

                if normalized_worktree == cls._normalized_worktree(registry.default_worktree):
                    raise ProjectPathConflictError(cls._default_to_info(registry))

                for entry in registry.projects:
                    if cls._normalized_worktree(entry.worktree) == normalized_worktree:
                        raise ProjectPathConflictError(cls._entry_to_info(entry))

                normalized_name = (name or Path(normalized_worktree).name).strip()
                if not normalized_name:
                    raise ValueError("Project name cannot be empty")
                if normalized_name.casefold() in _RESERVED_PROJECT_NAMES:
                    raise ProjectNameConflictError("Project name is reserved")
                if any(entry.name.strip().casefold() == normalized_name.casefold() for entry in registry.projects):
                    raise ProjectNameConflictError(f"Project name '{normalized_name}' already exists")

                now = cls._now_ms()
                project_id = f"prj_{uuid.uuid4()}"
                entry = ProjectRegistryEntry(
                    id=project_id,
                    name=normalized_name,
                    worktree=normalized_worktree,
                    createdAt=now,
                    updatedAt=now,
                    ownerUserID=owner_id,
                )
                registry.projects.append(entry)
                cls._write_registry(owner_id, registry)
            cls.invalidate_session_stats(owner_id)
            log.info("project.created", {"id": project_id, "owner": cls._owner_hash(owner_id)})
            return cls._entry_to_info(entry)

    @classmethod
    async def list(
        cls,
        *,
        owner_id: str,
        default_worktree: Optional[str] = None,
    ) -> List[ProjectInfo]:
        registry = await cls.ensure_registry(owner_id, default_worktree=default_worktree)
        projects = [cls._entry_to_info(entry) for entry in registry.projects]
        projects.sort(key=lambda item: item.time.updated, reverse=True)
        return [cls._default_to_info(registry), *projects]

    @classmethod
    def _all_registry_entries(cls) -> List[ProjectRegistryEntry]:
        """Return valid entries across local user registries."""

        registry_dir = cls._flocks_root() / "projects"
        if not registry_dir.is_dir():
            return []
        entries: List[ProjectRegistryEntry] = []
        for path in registry_dir.glob("*.json"):
            registry = cls._read_registry_file(path)
            if registry is not None:
                entries.extend(registry.projects)
        return entries

    @classmethod
    def shared_project_ids(cls) -> set[str]:
        """Return IDs of all projects shared to local accounts."""

        return {entry.id for entry in cls._all_registry_entries() if entry.shared_local}

    @classmethod
    async def list_visible(
        cls,
        *,
        owner_id: str,
        default_worktree: Optional[str] = None,
    ) -> List[ProjectInfo]:
        """List owned projects plus projects shared by other local users."""

        owned = await cls.list(owner_id=owner_id, default_worktree=default_worktree)
        owned_ids = {project.id for project in owned}
        shared = [
            cls._entry_to_info(entry, can_write=False)
            for entry in cls._all_registry_entries()
            if entry.shared_local and entry.owner_user_id != owner_id and entry.id not in owned_ids
        ]
        shared.sort(key=lambda item: item.time.updated, reverse=True)
        return [*owned, *shared]

    @classmethod
    def visible_project_ids(cls, owner_id: str) -> set[str]:
        """Return project IDs visible to a local user."""

        return cls.registered_project_ids(owner_id) | cls.shared_project_ids()

    @classmethod
    def is_local_shared(cls, owner_id: Optional[str], project_id: Optional[str]) -> bool:
        """Return whether an owner's registered project is locally shared."""

        if not owner_id or not project_id or project_id == DEFAULT_PROJECT_ID:
            return False
        registry = cls._read_registry(owner_id)
        return any(entry.id == project_id and entry.shared_local for entry in registry.projects)

    @classmethod
    async def set_local_shared(
        cls,
        project_id: str,
        *,
        owner_id: str,
        shared: bool,
    ) -> ProjectInfo:
        """Share or unshare an owned project with all local accounts."""

        if project_id == DEFAULT_PROJECT_ID:
            raise ProjectDeletionError("The default project cannot be shared")
        async with cls._lock:
            with _registry_cross_process_lock(cls.registry_path(owner_id)):
                registry = cls._read_registry(owner_id)
                entry = next((item for item in registry.projects if item.id == project_id), None)
                if entry is None:
                    raise ValueError(f"Project {project_id} not found")
                entry.owner_user_id = owner_id
                entry.shared_local = shared
                entry.updated_at = cls._now_ms()
                cls._write_registry(owner_id, registry)
            cls.invalidate_session_stats()
            log.info(
                "project.sharing.updated",
                {"id": project_id, "owner": cls._owner_hash(owner_id), "shared": shared},
            )
            return cls._entry_to_info(entry)

    @classmethod
    async def get(
        cls,
        project_id: str,
        *,
        owner_id: str,
        default_worktree: Optional[str] = None,
    ) -> Optional[ProjectInfo]:
        registry = await cls.ensure_registry(owner_id, default_worktree=default_worktree)
        if project_id == DEFAULT_PROJECT_ID:
            return cls._default_to_info(registry)
        entry = next((item for item in registry.projects if item.id == project_id), None)
        return cls._entry_to_info(entry) if entry else None

    @classmethod
    async def update(cls, project_id: str, *, owner_id: str, name: str) -> ProjectInfo:
        """Rename a registered project."""

        if project_id == DEFAULT_PROJECT_ID:
            raise ProjectDeletionError("The default project cannot be renamed")
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Project name cannot be empty")
        if normalized_name.casefold() in _RESERVED_PROJECT_NAMES:
            raise ProjectNameConflictError("Project name is reserved")

        async with cls._lock:
            with _registry_cross_process_lock(cls.registry_path(owner_id)):
                registry = cls._read_registry(owner_id)
                entry = next((item for item in registry.projects if item.id == project_id), None)
                if entry is None:
                    raise ValueError(f"Project {project_id} not found")
                if any(
                    item.id != project_id and item.name.strip().casefold() == normalized_name.casefold()
                    for item in registry.projects
                ):
                    raise ProjectNameConflictError(f"Project name '{normalized_name}' already exists")

                entry.name = normalized_name
                entry.updated_at = cls._now_ms()
                cls._write_registry(owner_id, registry)
            cls.invalidate_session_stats(owner_id)
            log.info("project.updated", {"id": project_id})
            return cls._entry_to_info(entry)

    @classmethod
    async def delete(cls, project_id: str, *, owner_id: str) -> bool:
        """Remove a project registration without changing sessions or files."""

        if project_id == DEFAULT_PROJECT_ID:
            raise ProjectDeletionError("The default project cannot be deleted")
        async with cls._lock:
            with _registry_cross_process_lock(cls.registry_path(owner_id)):
                registry = cls._read_registry(owner_id)
                remaining = [entry for entry in registry.projects if entry.id != project_id]
                if len(remaining) == len(registry.projects):
                    raise ValueError(f"Project {project_id} not found")
                registry.projects = remaining
                cls._write_registry(owner_id, registry)
            cls.invalidate_session_stats(owner_id)
            log.info("project.deleted", {"id": project_id})
            return True

    @classmethod
    def registered_project_ids(cls, owner_id: str) -> set[str]:
        registry = cls._read_registry(owner_id)
        return {entry.id for entry in registry.projects}

    @classmethod
    def effective_project_id(cls, owner_id: str, stored_project_id: Optional[str]) -> str:
        if stored_project_id and stored_project_id in cls.visible_project_ids(owner_id):
            return stored_project_id
        return DEFAULT_PROJECT_ID

    @classmethod
    def get_session_stats_cache(
        cls,
        owner_id: str,
        search: str,
    ) -> Optional[Dict[str, Tuple[int, int, Optional[int]]]]:
        """Return a short-lived copy of cached project session statistics."""

        key = (owner_id, search.casefold())
        cached = cls._session_stats_cache.get(key)
        if cached is None:
            return None
        cached_at, stats = cached
        if time.monotonic() - cached_at >= cls._session_stats_cache_ttl_seconds:
            cls._session_stats_cache.pop(key, None)
            return None
        return dict(stats)

    @classmethod
    def set_session_stats_cache(
        cls,
        owner_id: str,
        search: str,
        stats: Dict[str, Tuple[int, int, Optional[int]]],
    ) -> None:
        """Cache project session statistics for an owner and search term."""

        cls._session_stats_cache[(owner_id, search.casefold())] = (
            time.monotonic(),
            dict(stats),
        )

    @classmethod
    def invalidate_session_stats(cls, owner_id: Optional[str] = None) -> None:
        """Invalidate cached project counts after session or registry changes."""

        if owner_id is None:
            cls._session_stats_cache.clear()
            return
        for key in [key for key in cls._session_stats_cache if key[0] == owner_id]:
            cls._session_stats_cache.pop(key, None)

    @classmethod
    async def from_directory(cls, directory: str) -> Dict[str, Any]:
        """Build an ephemeral runtime context without persisting a project."""

        path, worktree, vcs = cls._directory_context(directory)

        now = cls._now_ms()
        project = ProjectInfo(
            id=DEFAULT_PROJECT_ID,
            worktree=str(worktree),
            vcs=vcs,
            name=DEFAULT_PROJECT_NAME,
            time=ProjectTime(created=now, updated=now),
            isDefault=True,
            pathStatus=cls._path_status(str(path)),
        )
        return {"project": project, "sandbox": str(worktree)}
