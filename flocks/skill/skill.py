"""
Skill Discovery and Management

Discovers SKILL.md files from Flocks-compatible locations and provides
access to skill information. Mirrors original Flocks Skill namespace.
"""

import json
import os
import glob
import re
import shutil
import sys
import tempfile
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Literal, Optional, Set
from pathlib import Path
from pydantic import BaseModel, Field

from flocks.utils.log import Log
from flocks.project.instance import Instance


log = Log.create(service="skill")

# Process-wide reentrant lock guarding ~/.flocks/config/skill_settings.json.
# FastAPI runs request handlers on a thread pool, so concurrent toggle calls
# can race on the JSON file.  We use an RLock so high-level read-modify-write
# helpers (toggle_disabled, set_disabled, etc.) can hold the lock across the
# entire load → mutate → save sequence while still calling the lower-level
# load_disabled / save_disabled primitives, which also acquire it.
_SETTINGS_LOCK = threading.RLock()

# Sidecar path used purely as a target for OS file-locking primitives.  We
# don't lock the JSON itself because (a) ``os.replace`` would atomically
# swap the inode out from under any open handle, and (b) on Windows the
# real file is briefly absent during ``tempfile.mkstemp + replace``.  A
# dedicated zero-byte ``.lock`` file gives every process a stable fd to
# coordinate on.
_LOCK_FILENAME = "skill_settings.json.lock"


def _platform_file_lock(fd: int) -> None:
    """Acquire an exclusive OS-level lock on ``fd`` (blocking).

    Linux / macOS: ``fcntl.flock`` with ``LOCK_EX``.
    Windows:       ``msvcrt.locking`` with ``LK_LOCK``.

    Both are advisory and *cross-process*, which is exactly what we need
    when uvicorn runs with ``--workers N`` and several worker processes
    might call :meth:`Skill.toggle_disabled` concurrently.
    """
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        import msvcrt

        # ``locking`` requires a non-zero length to lock.  One byte is
        # enough for an advisory range lock on the sentinel file.
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _platform_file_unlock(fd: int) -> None:
    if sys.platform == "win32":  # pragma: no cover
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


@contextmanager
def _settings_cross_process_lock(directory: Path) -> Iterator[None]:
    """Acquire a cross-process advisory lock on a sentinel file.

    The lock file is created lazily inside ``~/.flocks/config/`` and is
    *never* removed — keeping the inode stable across runs is what makes
    the lock meaningful.  An empty file is fine; the lock is on the fd,
    not the contents.

    Best-effort: if the platform refuses to grant a lock (read-only FS,
    NFS without lockd, sandboxing, …) we log and continue, falling back
    to the in-process ``_SETTINGS_LOCK`` only.  Better to over-write than
    to wedge the user's UI on file-lock failure.
    """
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / _LOCK_FILENAME
    fd: Optional[int] = None
    locked = False
    try:
        # ``O_CREAT`` so the very first caller bootstraps the file.
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _platform_file_lock(fd)
            locked = True
        except OSError as exc:
            log.warn(
                "skill.disabled.flock_failed",
                {"path": str(lock_path), "error": str(exc)},
            )
        yield
    finally:
        if fd is not None:
            if locked:
                _platform_file_unlock(fd)
            try:
                os.close(fd)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Metadata Models
# ---------------------------------------------------------------------------

class SkillInstallSpec(BaseModel):
    """Spec for installing a skill's tool dependency."""
    id: Optional[str] = Field(default=None, description="Unique id within the skill's install list")
    kind: Literal["brew", "npm", "uv", "pip", "go", "download"] = Field(
        ..., description="Package manager / download kind"
    )
    label: Optional[str] = Field(default=None, description="Human-readable install label")
    bins: Optional[List[str]] = Field(default=None, description="Binaries provided after install")
    formula: Optional[str] = Field(default=None, description="Homebrew formula name")
    package: Optional[str] = Field(default=None, description="npm / uv / pip / go package name")
    module: Optional[str] = Field(default=None, description="Go module path")
    url: Optional[str] = Field(default=None, description="Download URL (kind=download)")
    archive: Optional[str] = Field(default=None, description="Archive type: zip / tar.gz / tar.bz2")
    os: Optional[List[str]] = Field(default=None, description="Supported OS list (darwin/linux/win32)")


class SkillRequires(BaseModel):
    """Runtime requirements that must be satisfied for the skill to be eligible."""
    bins: Optional[List[str]] = Field(default=None, description="All binaries must exist in PATH")
    any_bins: Optional[List[str]] = Field(default=None, description="At least one binary must exist")
    env: Optional[List[str]] = Field(default=None, description="All env vars must be set")


class SkillMetadata(BaseModel):
    """Structured metadata parsed from SKILL.md frontmatter."""
    requires: Optional[SkillRequires] = None
    install: Optional[List[SkillInstallSpec]] = None
    os: Optional[List[str]] = None
    homepage: Optional[str] = None
    emoji: Optional[str] = None
    ui_hidden: Optional[bool] = None


class SkillInfo(BaseModel):
    """Skill information"""
    name: str = Field(..., description="Skill name")
    description: str = Field(..., description="Skill description")
    location: str = Field(..., description="Path to SKILL.md file")
    source: Optional[str] = Field(default=None, description="Discovery source")
    category: Optional[str] = Field(default=None, description="Skill category (e.g. 'system')")
    ui_hidden: bool = Field(default=False, description="Whether the skill should be omitted from skill management UI")
    native: bool = Field(default=False, description=(
        "True only for project-installed skills (<cwd>/.flocks/plugins/skills/). "
        "All other locations (.flocks/skills/, ~/.flocks/plugins/skills/, .claude/) "
        "are considered custom (user-defined). "
        "Derived from source; not declared in SKILL.md frontmatter."
    ))

    # Extended metadata (populated from frontmatter metadata.flocks / metadata.openclaw)
    metadata: Optional[SkillMetadata] = Field(default=None, description="Parsed skill metadata")
    install_specs: Optional[List[SkillInstallSpec]] = Field(
        default=None, description="Dependency install specs from metadata"
    )
    requires: Optional[SkillRequires] = Field(
        default=None, description="Runtime requirements from metadata"
    )

    # Eligibility (populated by Skill.check_eligibility)
    eligible: Optional[bool] = Field(default=None, description="True if all requirements are met")
    missing: Optional[List[str]] = Field(
        default=None, description="List of missing bins/env vars"
    )


# ---------------------------------------------------------------------------
# Skill Discovery
# ---------------------------------------------------------------------------

class Skill:
    """
    Skill discovery and management.

    Discovers SKILL.md files from (lowest → highest priority):
    - .flocks dirs   (global + project-level)
    - .claude dirs     (global ~/.claude + project-level)
    - ~/.flocks        (global user-level)
    - <project>/.flocks (project-level, wins on collision)
    """

    _cache: Optional[Dict[str, SkillInfo]] = None

    @classmethod
    def _parse_skill_md(cls, filepath: str, source: Optional[str] = None) -> Optional[SkillInfo]:
        """
        Parse a SKILL.md file to extract name, description, category and metadata.

        Args:
            filepath: Path to SKILL.md file

        Returns:
            SkillInfo or None if parsing fails
        """
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            data = cls._parse_frontmatter(content)
            if not data:
                return None

            name = (data.get("name") or "").strip()
            description = (data.get("description") or "").strip()
            category = (data.get("category") or "").strip().lower() or None
            ui_hidden = cls._as_bool(data.get("ui_hidden"))

            if not cls._is_valid_name(name) or not cls._is_valid_description(description):
                return None

            # Parse extended metadata — try metadata.flocks first, then metadata.openclaw
            skill_metadata: Optional[SkillMetadata] = None
            install_specs: Optional[List[SkillInstallSpec]] = None
            requires: Optional[SkillRequires] = None

            raw_meta = data.get("metadata")
            if isinstance(raw_meta, dict):
                raw_flocks = raw_meta.get("flocks") or raw_meta.get("openclaw")
                if isinstance(raw_flocks, dict):
                    try:
                        skill_metadata = SkillMetadata.model_validate(raw_flocks)
                        install_specs = skill_metadata.install or None
                        requires = skill_metadata.requires or None
                        ui_hidden = ui_hidden or bool(skill_metadata.ui_hidden)
                    except Exception as exc:
                        log.warn("skill.metadata.parse.error", {
                            "filepath": filepath,
                            "error": str(exc),
                        })

            # native: only project-installed skills (<cwd>/.flocks/plugins/skills/) are built-in;
            # all other sources (flocks/skills/, ~/.flocks/plugins/, .claude/) are custom.
            is_native = source == "project"

            return SkillInfo(
                name=name,
                description=description,
                location=filepath,
                source=source,
                category=category,
                ui_hidden=ui_hidden,
                native=is_native,
                metadata=skill_metadata,
                install_specs=install_specs,
                requires=requires,
            )
        except Exception as e:
            log.warn("skill.parse.error", {"filepath": filepath, "error": str(e)})
            return None

    @staticmethod
    def _parse_frontmatter(content: str) -> Dict[str, Any]:
        """Parse YAML frontmatter using yaml.safe_load for full nested support."""
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}

        end_index = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_index = i
                break

        if end_index is None:
            return {}

        frontmatter_text = "\n".join(lines[1:end_index])
        try:
            import yaml  # pyyaml
            parsed = yaml.safe_load(frontmatter_text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        # Fallback: simple key: value line parser (no nesting)
        data: Dict[str, Any] = {}
        for line in lines[1:end_index]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"\'')
            if key and value:
                data[key] = value
        return data

    @staticmethod
    def _is_valid_name(name: str) -> bool:
        return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name))

    @staticmethod
    def _is_valid_description(description: str) -> bool:
        return 1 <= len(description) <= 1024

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @classmethod
    def _scan_directory(
        cls,
        directory: str,
        pattern: str,
        skills: Dict[str, SkillInfo],
        source: Optional[str] = None,
    ) -> None:
        """
        Scan a directory for SKILL.md files

        Args:
            directory: Base directory to scan
            pattern: Glob pattern (e.g., "**/SKILL.md")
            skills: Dictionary to add found skills to
        """
        if not os.path.exists(directory):
            return

        try:
            search_pattern = os.path.join(directory, pattern)
            matches = glob.glob(search_pattern, recursive=True)

            for match in matches:
                skill_info = cls._parse_skill_md(match, source=source)
                if skill_info:
                    if skill_info.name in skills:
                        log.warn("skill.duplicate", {
                            "name": skill_info.name,
                            "existing": skills[skill_info.name].location,
                            "duplicate": match,
                        })

                    skills[skill_info.name] = skill_info
                    log.debug("skill.found", {
                        "name": skill_info.name,
                        "location": match,
                    })

        except Exception as e:
            log.error("skill.scan.error", {
                "directory": directory,
                "error": str(e)
            })

    @classmethod
    def _discover(cls) -> Dict[str, SkillInfo]:
        """
        Discover all skills. Last wins on name collision.

        Scan order (lowest → highest priority):
          1. .claude dirs       (global ~/.claude + project-level)
          2. ~/.flocks          (global user-level, overrides .claude)
          3. <project>/.flocks  (project-level, highest priority)

        Source labels:
          "flocks"   — built-in skills inside .flocks/skills/ directories
          "claude"   — skills discovered from .claude/ directories
          "user"     — user-installed skills under ~/.flocks/plugins/skills/
          "project"  — project-installed skills under <project>/.flocks/plugins/skills/
        """
        skills: Dict[str, SkillInfo] = {}

        home_dir = os.path.expanduser("~")
        current_dir = Instance.get_directory() or os.getcwd()
        worktree = Instance.get_worktree() or current_dir
        # Built-in skill patterns (legacy skill[s]/ directories, not under plugins/)
        builtin_patterns = (
            "skill/**/SKILL.md",
            "skills/**/SKILL.md",
        )
        # User/project-installed plugin patterns
        plugin_patterns = (
            "plugins/skill/**/SKILL.md",
            "plugins/skills/**/SKILL.md",
        )
        global_flocks = os.path.join(home_dir, ".flocks")

        # 1) .claude directories — lowest priority
        global_claude = os.path.join(home_dir, ".claude")
        if os.path.isdir(global_claude):
            cls._scan_directory(global_claude, "skills/**/SKILL.md", skills, source="claude")
        for claude_dir in cls._find_dirs_up(".claude", current_dir, worktree):
            cls._scan_directory(claude_dir, "skills/**/SKILL.md", skills, source="claude")

        # 2) Global ~/.flocks — overrides .claude
        #    Built-in skills: source="flocks"; user-installed plugins: source="user"
        if os.path.isdir(global_flocks):
            for pattern in builtin_patterns:
                cls._scan_directory(global_flocks, pattern, skills, source="flocks")
            for pattern in plugin_patterns:
                cls._scan_directory(global_flocks, pattern, skills, source="user")

        # 3) Project-level .flocks — highest priority
        #    Built-in skills: source="flocks"; project-installed plugins: source="project"
        for flocks_dir in cls._find_dirs_up(".flocks", current_dir, worktree):
            if os.path.normpath(flocks_dir) == os.path.normpath(global_flocks):
                continue
            for pattern in builtin_patterns:
                cls._scan_directory(flocks_dir, pattern, skills, source="flocks")
            for pattern in plugin_patterns:
                cls._scan_directory(flocks_dir, pattern, skills, source="project")

        log.info("skill.discovery.complete", {"count": len(skills), "names": list(skills.keys())})
        return skills

    @staticmethod
    def _find_dirs_up(target: str, start_dir: str, stop_dir: str) -> List[str]:
        results: List[str] = []
        current = Path(start_dir).resolve()
        stop = Path(stop_dir).resolve()
        while True:
            candidate = current / target
            if candidate.exists() and candidate.is_dir():
                results.append(str(candidate))
            if current == stop or current == current.parent:
                break
            current = current.parent
        return results

    @classmethod
    async def all(cls) -> List[SkillInfo]:
        """
        Get all available skills (including disabled ones).

        Use this for management UIs that need to display the full inventory
        and reflect each skill's disabled state.  For agent runtime use
        :meth:`list_enabled` so disabled skills are excluded from the system
        prompt and the ``skill`` tool's description.

        Matches TypeScript Skill.all().

        Returns:
            List of all discovered skills
        """
        if cls._cache is None:
            cls._cache = cls._discover()

        return list(cls._cache.values())

    @classmethod
    async def list_enabled(cls) -> List[SkillInfo]:
        """
        Get skills that are enabled (i.e. visible to the agent).

        Disabled skills — those whose names appear in
        ``~/.flocks/config/skill_settings.json`` under ``disabled`` — are
        filtered out.  Agent loaders and the ``skill`` tool's description
        builder must use this method so that toggling a skill off in the
        Skill UI actually removes it from the LLM's system prompt.
        """
        skills = await cls.all()
        disabled = cls.load_disabled()
        if not disabled:
            return skills
        return [s for s in skills if s.name not in disabled]

    @classmethod
    async def get(cls, name: str) -> Optional[SkillInfo]:
        """
        Get a skill by name

        Matches TypeScript Skill.get()

        Args:
            name: Skill name

        Returns:
            SkillInfo or None if not found
        """
        if cls._cache is None:
            cls._cache = cls._discover()

        skill = cls._cache.get(name)
        if not skill:
            return None
        return skill

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the skill cache (for testing or forced refresh)"""
        cls._cache = None
        log.info("skill.cache.cleared")

    @classmethod
    async def refresh(cls) -> List[SkillInfo]:
        """
        Force refresh of skill cache

        Returns:
            List of all discovered skills
        """
        cls.clear_cache()
        return await cls.all()

    # ----- Disabled state (user preferences) -----

    @staticmethod
    def settings_path() -> Path:
        """Path to the user-level skill settings file."""
        return Path.home() / ".flocks" / "config" / "skill_settings.json"

    @classmethod
    @contextmanager
    def _locked_rmw(cls) -> Iterator[None]:
        """Hold both the in-process RLock and the cross-process file lock.

        Use this around every read-modify-write of the disabled-skills
        file so multiple worker processes (uvicorn ``--workers N``) cannot
        interleave their loads / saves and lose each other's updates.
        Single-shot reads (:meth:`load_disabled`) don't need this — the
        on-disk file is always a valid JSON snapshot because
        :meth:`save_disabled` publishes via ``os.replace``.
        """
        with _SETTINGS_LOCK:
            with _settings_cross_process_lock(cls.settings_path().parent):
                yield

    @classmethod
    def load_disabled(cls) -> Set[str]:
        """Return the set of skill names the user has marked as disabled.

        Disabled skills are still discoverable (so the management UI can
        list them) but are excluded from :meth:`list_enabled`, which is what
        the agent uses.  Missing or malformed files yield an empty set.
        """
        path = cls.settings_path()
        with _SETTINGS_LOCK:
            try:
                if not path.exists():
                    return set()
                data = json.loads(path.read_text(encoding="utf-8"))
                disabled = data.get("disabled", [])
                if isinstance(disabled, list):
                    return {str(n) for n in disabled if isinstance(n, str)}
            except Exception as exc:
                log.warn("skill.disabled.load_failed", {"error": str(exc)})
        return set()

    @classmethod
    def save_disabled(cls, disabled: Set[str]) -> None:
        """Persist the disabled-skill set, creating parent dirs as needed.

        Write atomically via ``tempfile`` + :func:`os.replace`.  A plain
        ``write_text`` opens the file in truncate mode, so a crash, SIGKILL,
        or full disk midway through the write can leave a half-written /
        empty JSON behind.  On the next ``load_disabled`` call the parser
        would throw and we would silently fall back to an empty set —
        wiping every disabled preference the user had configured.  The
        ``tmp + rename`` dance keeps the on-disk file pointing at a fully
        written payload until the very last instant.
        """
        path = cls.settings_path()
        with _SETTINGS_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"disabled": sorted(disabled)}
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent),
                prefix=".skill_settings_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                os.replace(tmp_path, str(path))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    @classmethod
    def is_disabled(cls, name: str) -> bool:
        return name in cls.load_disabled()

    @classmethod
    def set_disabled(cls, name: str, disabled: bool) -> bool:
        """Set whether the given skill is disabled.

        The whole read-modify-write runs under :meth:`_locked_rmw` so two
        concurrent callers — including separate uvicorn workers — cannot
        lose each other's updates.

        Returns the new disabled state (mirrors the input ``disabled``).
        """
        with cls._locked_rmw():
            current = cls.load_disabled()
            if disabled:
                current.add(name)
            else:
                current.discard(name)
            cls.save_disabled(current)
        return disabled

    @classmethod
    def toggle_disabled(cls, name: str) -> bool:
        """Flip the disabled state of a skill and return the new value."""
        with cls._locked_rmw():
            current = cls.load_disabled()
            if name in current:
                current.discard(name)
                new_value = False
            else:
                current.add(name)
                new_value = True
            cls.save_disabled(current)
        return new_value

    @classmethod
    def forget_disabled(cls, name: str) -> None:
        """Remove a name from the disabled list (no-op if not present).

        Call this after deleting a skill so its preference does not linger
        as a ghost record in the settings file.
        """
        with cls._locked_rmw():
            current = cls.load_disabled()
            if name in current:
                current.discard(name)
                cls.save_disabled(current)

    @classmethod
    def rename_disabled(cls, old_name: str, new_name: str) -> None:
        """Migrate a disabled flag from ``old_name`` to ``new_name``."""
        if old_name == new_name:
            return
        with cls._locked_rmw():
            current = cls.load_disabled()
            if old_name in current:
                current.discard(old_name)
                current.add(new_name)
                cls.save_disabled(current)

    # ----- Eligibility -----

    @classmethod
    def check_eligibility(cls, skill: SkillInfo) -> SkillInfo:
        """
        Check if a skill's runtime requirements are satisfied.

        Returns a **new** SkillInfo with `eligible` and `missing` populated.
        The original object is not modified.  Missing entries use the format
        "<kind>:<name>", e.g. "bin:gh" or "env:GITHUB_TOKEN".
        """
        if skill.requires is None:
            skill = skill.model_copy(update={"eligible": True, "missing": []})
            return skill

        missing: List[str] = []
        req = skill.requires

        # Check required binaries (all must be present)
        if req.bins:
            for b in req.bins:
                if not shutil.which(b):
                    missing.append(f"bin:{b}")

        # Check any_bins (at least one must be present)
        if req.any_bins:
            if not any(shutil.which(b) for b in req.any_bins):
                missing.append(f"any_bin:{','.join(req.any_bins)}")

        # Check environment variables
        if req.env:
            for var in req.env:
                if not os.environ.get(var):
                    missing.append(f"env:{var}")

        skill = skill.model_copy(update={
            "eligible": len(missing) == 0,
            "missing": missing,
        })
        return skill

    # ----- File Watcher Integration -----

    _watcher: Optional["SkillFileWatcher"] = None

    @classmethod
    def start_watcher(cls) -> None:
        """Start watching skill directories for changes."""
        if cls._watcher is not None:
            return
        cls._watcher = SkillFileWatcher(cls)
        cls._watcher.start()

    @classmethod
    def stop_watcher(cls) -> None:
        """Stop the file watcher."""
        if cls._watcher is not None:
            cls._watcher.stop()
            cls._watcher = None


def _skill_event_should_reload(event: object) -> bool:
    """Return True if a watchdog event affects a ``SKILL.md`` file.

    Atomic-save flows rename a temp file onto the real ``SKILL.md``; we have
    to consult both ``src_path`` and ``dest_path`` so the watcher reloads on
    those renames as well.
    """
    for attr in ("src_path", "dest_path"):
        path = getattr(event, attr, "") or ""
        if path.endswith("SKILL.md"):
            return True
    return False


class SkillFileWatcher:
    """
    Watches skill directories for SKILL.md changes and auto-invalidates
    the Skill cache via watchdog.

    Uses a debounce timer so rapid successive writes only trigger one
    cache clear.
    """

    _DEBOUNCE_SECONDS = 0.5
    _FLOCKS_SKILL_DIRS = (
        "skill",
        "skills",
        os.path.join("plugins", "skill"),
        os.path.join("plugins", "skills"),
    )
    _CLAUDE_SKILL_DIRS = ("skills",)

    def __init__(self, skill_cls: type):
        self._skill_cls = skill_cls
        self._observer: Optional[object] = None
        self._debounce_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    # ---- public ----

    def start(self) -> None:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler, FileSystemEvent
        except ImportError:
            log.warn("skill.watcher.watchdog_missing",
                      {"msg": "watchdog not installed, skill file watcher disabled"})
            return

        watch_dirs = self._collect_watch_dirs()
        if not watch_dirs:
            log.info("skill.watcher.no_dirs", {"msg": "no skill directories to watch"})
            return

        watcher = self

        # Only react to actual content-mutation events.  watchdog emits
        # ``opened``/``closed``/``closed_no_write`` events whenever any code
        # (including the skill loader itself) reads ``SKILL.md`` files, which
        # would otherwise cause a self-sustaining cache-invalidation loop.
        _RELOAD_EVENT_TYPES = frozenset({"modified", "created", "deleted", "moved"})

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent):
                if event.is_directory:
                    return
                if getattr(event, "event_type", "") not in _RELOAD_EVENT_TYPES:
                    return
                if _skill_event_should_reload(event):
                    watcher._schedule_clear()

        handler = _Handler()
        observer = Observer()
        for d in watch_dirs:
            try:
                observer.schedule(handler, d, recursive=True)
                log.debug("skill.watcher.watching", {"directory": d})
            except Exception as e:
                log.warn("skill.watcher.schedule_error", {"directory": d, "error": str(e)})

        observer.daemon = True
        observer.start()
        self._observer = observer
        log.info("skill.watcher.started", {"directories": list(watch_dirs)})

    def stop(self) -> None:
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
        if self._observer is not None:
            try:
                self._observer.stop()  # type: ignore[union-attr]
                self._observer.join(timeout=2)  # type: ignore[union-attr]
            except Exception:
                pass
            self._observer = None
            log.info("skill.watcher.stopped")

    # ---- internal ----

    def _schedule_clear(self) -> None:
        """Debounced cache invalidation."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                self._DEBOUNCE_SECONDS, self._do_clear
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _do_clear(self) -> None:
        self._skill_cls.clear_cache()
        log.info("skill.watcher.cache_cleared", {"reason": "SKILL.md changed on disk"})

    def _collect_watch_dirs(self) -> Set[str]:
        """Gather concrete skill roots that may contain SKILL.md files."""
        dirs: Set[str] = set()
        home = os.path.expanduser("~")

        try:
            current_dir = Instance.get_directory() or os.getcwd()
        except Exception:
            current_dir = os.getcwd()
        try:
            worktree = Instance.get_worktree() or current_dir
        except Exception:
            worktree = current_dir

        flocks_roots = Skill._find_dirs_up(".flocks", current_dir, worktree)
        global_flocks = os.path.join(home, ".flocks")
        if os.path.isdir(global_flocks):
            flocks_roots.append(global_flocks)
        for root in flocks_roots:
            dirs.update(self._existing_subdirs(root, self._FLOCKS_SKILL_DIRS))

        claude_roots = Skill._find_dirs_up(".claude", current_dir, worktree)
        global_claude = os.path.join(home, ".claude")
        if os.path.isdir(global_claude):
            claude_roots.append(global_claude)
        for root in claude_roots:
            dirs.update(self._existing_subdirs(root, self._CLAUDE_SKILL_DIRS))

        return dirs

    @staticmethod
    def _existing_subdirs(root: str, relative_dirs: tuple[str, ...]) -> Set[str]:
        """Return existing watch roots below a discovery root, with stable dedupe."""
        dirs: Set[str] = set()
        for rel in relative_dirs:
            candidate = os.path.realpath(os.path.join(root, rel))
            if os.path.isdir(candidate):
                dirs.add(candidate)
        return dirs
