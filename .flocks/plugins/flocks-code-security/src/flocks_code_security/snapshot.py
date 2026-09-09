"""Read-only, reproducible target snapshot creation."""

from __future__ import annotations

import codecs
import fnmatch
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

from flocks_code_security.manifest import RepositoryManifestService
from flocks_code_security.models import SnapshotFile, SnapshotOmission, SnapshotRef
from flocks_code_security.store import ScanStore

DEFAULT_EXCLUDES = (
    ".git",
    ".git/**",
    "**/.git",
    "**/.git/**",
    ".flocks",
    ".flocks/**",
    "node_modules",
    "node_modules/**",
    "**/node_modules",
    "**/node_modules/**",
    ".venv",
    ".venv/**",
    "**/.venv",
    "**/.venv/**",
    "venv",
    "venv/**",
    "**/venv",
    "**/venv/**",
    "__pycache__",
    "**/__pycache__/**",
    ".projection-complete",
    "**/.projection-complete",
)

LANGUAGES = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".scala": "scala",
    ".sh": "shell",
    ".sql": "sql",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "vue",
}

MAX_SNAPSHOT_FILES = 50_000
MAX_SNAPSHOT_TOTAL_BYTES = 4 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class _GitSnapshotState:
    revision: str
    clean: bool
    status_digest: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class _ReadFileResult:
    blob_digest: str
    size_bytes: int
    line_count: int
    is_binary: bool


class _DecodedLineCounter:
    _BREAKS = {"\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"}

    def __init__(self) -> None:
        self._break_count = 0
        self._has_text = False
        self._ends_with_break = False
        self._previous_was_cr = False

    def feed(self, text: str) -> None:
        if not text:
            return
        self._has_text = True
        self._break_count += sum(text.count(character) for character in self._BREAKS)
        self._break_count -= text.count("\r\n")
        if self._previous_was_cr and text.startswith("\n"):
            self._break_count -= 1
        self._previous_was_cr = text.endswith("\r")
        self._ends_with_break = text[-1] in self._BREAKS

    @property
    def value(self) -> int:
        return self._break_count + int(self._has_text and not self._ends_with_break)


def normalize_relative_path(value: str, *, allow_root: bool = False) -> str:
    raw = "" if value is None else str(value)
    if os.name == "nt":
        raw = raw.replace("\\", "/")
    if len(raw) > 1024:
        raise ValueError("Snapshot-relative paths may contain at most 1024 characters")
    if "\x00" in raw:
        raise ValueError("Snapshot-relative paths cannot contain NUL bytes")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValueError("Path must be a snapshot-relative path without '..'")
    normalized = path.as_posix()
    if normalized != raw:
        raise ValueError("Path must use canonical snapshot-relative syntax")
    if normalized in {"", "."} and not allow_root:
        raise ValueError("A file path is required")
    return normalized


def _matches_exclude(relative_path: str, patterns: Iterable[str]) -> bool:
    return any(
        relative_path == pattern.rstrip("/**")
        or fnmatch.fnmatch(relative_path, pattern)
        for pattern in patterns
    )


def _language(path: Path) -> str:
    return LANGUAGES.get(path.suffix.lower(), "other")


class TargetSnapshotService:
    def __init__(
        self,
        snapshots_root: Path,
        store: ScanStore,
        *,
        protected_roots: Iterable[Path] = (),
    ):
        self.snapshots_root = snapshots_root
        self.store = store
        self.manifests = RepositoryManifestService(store)
        self.protected_roots = tuple(Path(path).expanduser() for path in protected_roots)

    def create(
        self,
        target_path: str,
        *,
        include_paths: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_file_bytes: int | None = None,
        max_total_bytes: int | None = None,
        copy_source: bool = True,
    ) -> SnapshotRef:
        raw_target = Path(target_path).expanduser()
        if not raw_target.is_absolute():
            raise ValueError("target_path must be an absolute directory path")
        if raw_target.is_symlink():
            raise ValueError("The target root cannot be a symbolic link")
        target = raw_target.resolve(strict=True)
        if not target.is_dir():
            raise ValueError("Target must be a local directory")
        for protected_root in self.protected_roots:
            protected = protected_root.resolve(strict=False)
            if target.is_relative_to(protected) or protected.is_relative_to(target):
                raise ValueError(
                    "The audit target overlaps code-security runtime storage"
                )
        if max_file_bytes is not None and (
            not isinstance(max_file_bytes, int) or isinstance(max_file_bytes, bool) or max_file_bytes < 1
        ):
            raise ValueError("max_file_bytes must be a positive integer when provided")
        if len(include_paths or ["."]) > 256:
            raise ValueError("At most 256 include paths are allowed")
        if max_total_bytes is None:
            max_total_bytes = MAX_SNAPSHOT_TOTAL_BYTES
        if type(max_total_bytes) is not int or max_total_bytes < 1:
            raise ValueError("max_total_bytes must be a positive integer when provided")
        if len(exclude_patterns or []) > 256:
            raise ValueError("At most 256 exclude patterns are allowed")

        includes = [
            normalize_relative_path(item, allow_root=True)
            for item in (include_paths or ["."])
        ]
        requested_patterns = tuple(
            normalize_relative_path(item, allow_root=True)
            for item in (exclude_patterns or [])
        )
        patterns = DEFAULT_EXCLUDES + requested_patterns
        git_state = self._git_snapshot_state(target)
        root_descriptor: int | None = self._open_directory(target)
        root_identity = self._stat_signature(os.fstat(root_descriptor))
        temporary: Path | None = None
        snapshot_id = f"snap_{uuid.uuid4().hex}"
        final_root = self.snapshots_root / snapshot_id
        records: list[SnapshotFile] = []
        omissions: list[SnapshotOmission] = []
        total_bytes = 0
        try:
            files = self._enumerate_for_state(
                target,
                includes,
                patterns,
                git_state,
            )
            self._assert_root_identity(target, root_identity)
            if len(files) > MAX_SNAPSHOT_FILES:
                raise ValueError(
                    f"Snapshot contains more than {MAX_SNAPSHOT_FILES} files"
                )
            initial_states = {
                relative_path: self._source_file_signature(
                    root_descriptor,
                    relative_path,
                )
                for relative_path, _source_path in files
            }
            included_bytes = sum(
                signature[3] for signature in initial_states.values()
                if max_file_bytes is None or signature[3] <= max_file_bytes
            )
            if included_bytes > max_total_bytes:
                raise ValueError(
                    f"Snapshot requires {included_bytes} bytes, exceeding max_total_bytes={max_total_bytes}; "
                    "raise the snapshot byte limit or select an explicit file scope"
                )

            if copy_source:
                self.snapshots_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.snapshots_root.chmod(0o700)
                temporary = Path(
                    tempfile.mkdtemp(prefix=".snapshot-", dir=self.snapshots_root)
                )
            for relative_path, source_path in files:
                destination = None
                if temporary is not None:
                    destination = temporary / relative_path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                result, observed_size = self._read_regular_file(
                    root_descriptor,
                    relative_path,
                    max_file_bytes,
                    max_total_bytes=max_total_bytes - total_bytes,
                    expected_signature=initial_states[relative_path],
                    destination=destination,
                )
                if result is None:
                    if destination is not None:
                        destination.unlink(missing_ok=True)
                    omissions.append(
                        SnapshotOmission(
                            relative_path=relative_path,
                            reason="file_size_limit_exceeded",
                            size_bytes=observed_size,
                        )
                    )
                    continue
                if total_bytes + result.size_bytes > max_total_bytes:
                    raise ValueError(
                        f"Snapshot exceeds max_total_bytes={max_total_bytes}"
                    )
                records.append(
                    SnapshotFile(
                        relative_path=relative_path,
                        blob_digest=result.blob_digest,
                        size_bytes=result.size_bytes,
                        line_count=result.line_count,
                        language=_language(source_path),
                        is_binary=result.is_binary,
                    )
                )
                total_bytes += result.size_bytes

            final_git_state = self._git_snapshot_state(target)
            if final_git_state != git_state:
                raise ValueError("Audit target Git state changed during snapshot creation")
            final_files = self._enumerate_for_state(
                target,
                includes,
                patterns,
                final_git_state,
            )
            self._assert_root_identity(target, root_identity)
            if [item[0] for item in final_files] != [item[0] for item in files]:
                raise ValueError("Audit target file set changed during snapshot creation")
            final_states = {
                relative_path: self._source_file_signature(
                    root_descriptor,
                    relative_path,
                )
                for relative_path, _source_path in final_files
            }
            if final_states != initial_states:
                raise ValueError("Audit target content changed during snapshot creation")

            records.sort(key=lambda item: item.relative_path)
            omissions.sort(key=lambda item: item.relative_path)
            tree_digest = hashlib.sha256(
                (
                    "".join(
                        f"file\0{item.relative_path}\0{item.blob_digest}\0"
                        f"{item.size_bytes}\n"
                        for item in records
                    )
                    + "".join(
                        f"omitted\0{item.relative_path}\0{item.reason}\0"
                        f"{item.size_bytes}\n"
                        for item in omissions
                    )
                ).encode("utf-8")
            ).hexdigest()
            scope_digest = hashlib.sha256(
                json.dumps(
                    {
                        "include_paths": includes,
                        "exclude_patterns": list(patterns),
                        "max_file_bytes": max_file_bytes,
                        "copy_source": copy_source,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            repository_identity = "target_sha256_" + hashlib.sha256(
                f"local-workspace\0{target}".encode("utf-8")
            ).hexdigest()
            if git_state is None:
                target_kind = "directory_snapshot"
                source_revision = None
            else:
                target_kind = "git_revision" if git_state.clean else "git_worktree"
                source_revision = git_state.revision

            if copy_source:
                assert temporary is not None
                os.replace(temporary, final_root)
                for path in sorted(final_root.rglob("*"), reverse=True):
                    path.chmod(0o500 if path.is_dir() else 0o400)
                final_root.chmod(0o500)
                snapshot_root = final_root
            else:
                snapshot_root = target

            snapshot = SnapshotRef(
                snapshot_id=snapshot_id,
                repository_identity=repository_identity,
                source_revision=source_revision,
                tree_digest=tree_digest,
                scope_digest=scope_digest,
                file_count=len(records),
                total_bytes=total_bytes,
                created_at=datetime.now(timezone.utc).isoformat(),
                root_path=str(snapshot_root),
                omitted_file_count=len(omissions),
                target_kind=target_kind,
                display_name=target.name,
                include_paths=tuple(includes),
                exclude_patterns=tuple(patterns),
                copy_source=copy_source,
            )
            self.store.save_snapshot(snapshot, records, omissions)
            self.manifests.build(snapshot.snapshot_id)
            return snapshot
        except Exception:
            if self.store.get_snapshot(snapshot_id) is not None:
                self.store.delete_snapshot(snapshot_id)
            cleanup_root = (
                temporary
                if temporary is not None and temporary.exists()
                else final_root
            )
            if cleanup_root.exists():
                cleanup_root.chmod(0o700)
                for path in cleanup_root.rglob("*"):
                    if path.exists():
                        path.chmod(0o700 if path.is_dir() else 0o600)
                shutil.rmtree(cleanup_root)
            raise
        finally:
            if root_descriptor is not None:
                os.close(root_descriptor)

    def delete(self, snapshot_id: str) -> None:
        snapshot = self.store.get_snapshot(snapshot_id)
        if snapshot is None:
            return
        owned_root: Path | None = None
        if snapshot.copy_source:
            root = Path(snapshot.root_path).expanduser()
            expected_root = (
                self.snapshots_root.expanduser().resolve() / snapshot.snapshot_id
            )
            if root.is_symlink() or root.resolve() != expected_root:
                raise OSError(
                    f"Refusing to delete snapshot outside owned storage: {root}"
                )
            owned_root = expected_root
        self.store.delete_snapshot(snapshot_id)
        if owned_root is None or not owned_root.exists():
            return
        owned_root.chmod(0o700)
        for path in owned_root.rglob("*"):
            if path.is_symlink():
                continue
            path.chmod(0o700 if path.is_dir() else 0o600)
        shutil.rmtree(owned_root)

    def _enumerate(
        self,
        target: Path,
        includes: list[str],
        exclude_patterns: tuple[str, ...],
    ) -> list[tuple[str, Path]]:
        selected: dict[str, Path] = {}
        for include in includes:
            candidate = target if include == "." else target / include
            self._reject_symlink_components(target, include)
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(target):
                raise ValueError("Included path escapes the target root")
            if resolved.is_file():
                relative = resolved.relative_to(target).as_posix()
                if not _matches_exclude(relative, exclude_patterns):
                    selected[relative] = resolved
                continue
            if not resolved.is_dir():
                raise ValueError(f"Unsupported included path: {include}")

            for current_root, directory_names, file_names in os.walk(resolved, followlinks=False):
                current = Path(current_root)
                kept_directories: list[str] = []
                for name in sorted(directory_names):
                    path = current / name
                    relative = path.relative_to(target).as_posix()
                    if path.is_symlink():
                        raise ValueError(f"Symbolic links are not allowed: {relative}")
                    if not _matches_exclude(relative, exclude_patterns):
                        kept_directories.append(name)
                directory_names[:] = kept_directories
                for name in sorted(file_names):
                    path = current / name
                    relative = path.relative_to(target).as_posix()
                    if path.is_symlink():
                        raise ValueError(f"Symbolic links are not allowed: {relative}")
                    if path.is_file() and not _matches_exclude(
                        relative, exclude_patterns
                    ):
                        selected[relative] = path
        return sorted(selected.items())

    def _enumerate_for_state(
        self,
        target: Path,
        includes: list[str],
        exclude_patterns: tuple[str, ...],
        git_state: _GitSnapshotState | None,
    ) -> list[tuple[str, Path]]:
        if git_state is None:
            return self._enumerate(target, includes, exclude_patterns)
        for include in includes:
            candidate = target if include == "." else target / include
            self._reject_symlink_components(target, include)
            try:
                resolved = candidate.resolve(strict=True)
            except FileNotFoundError as exc:
                raise ValueError(f"Included path does not exist: {include}") from exc
            if not resolved.is_relative_to(target):
                raise ValueError("Included path escapes the target root")
            if not resolved.is_file() and not resolved.is_dir():
                raise ValueError(f"Unsupported included path: {include}")
        selected: list[tuple[str, Path]] = []
        for relative_path in git_state.paths:
            if not any(
                include == "."
                or relative_path == include
                or relative_path.startswith(f"{include}/")
                for include in includes
            ):
                continue
            if _matches_exclude(relative_path, exclude_patterns):
                continue
            self._reject_symlink_components(target, relative_path)
            source = target / relative_path
            # A tracked deletion is part of a valid dirty-worktree snapshot. The
            # revision plus snapshot digest still binds the resulting target.
            if not source.exists():
                continue
            if source.is_symlink():
                raise ValueError(f"Symbolic links are not allowed: {relative_path}")
            if not source.is_file():
                raise ValueError(
                    f"Git inventory contains a non-regular path: {relative_path}"
                )
            selected.append((relative_path, source))
        return selected

    @staticmethod
    def _git_snapshot_state(target: Path) -> _GitSnapshotState | None:
        marker = target / ".git"
        if marker.is_symlink():
            raise ValueError("Symbolic links are not allowed: .git")

        def run(*args: str) -> subprocess.CompletedProcess[bytes]:
            environment = {
                name: value
                for name, value in os.environ.items()
                if not name.startswith("GIT_")
            }
            command = [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                str(target),
                *args,
            ]
            try:
                return subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    env=environment,
                    timeout=15,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return subprocess.CompletedProcess(command, 127, b"", b"")

        root = run("rev-parse", "--show-toplevel")
        revision = run("rev-parse", "--verify", "HEAD")
        if root.returncode != 0 or revision.returncode != 0:
            return None
        try:
            repository_root = Path(os.fsdecode(root.stdout.strip())).resolve(strict=True)
        except (OSError, UnicodeError):
            return None
        if repository_root != target:
            return None

        status = run(
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
        )
        listed = run("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        if status.returncode != 0 or listed.returncode != 0:
            return None
        try:
            revision_text = revision.stdout.strip().decode("ascii")
            paths = tuple(
                sorted(
                    normalize_relative_path(os.fsdecode(raw_path))
                    for raw_path in listed.stdout.split(b"\0")
                    if raw_path
                )
            )
        except (UnicodeError, ValueError):
            return None
        return _GitSnapshotState(
            revision=revision_text,
            clean=not status.stdout,
            status_digest=hashlib.sha256(status.stdout).hexdigest(),
            paths=paths,
        )

    @staticmethod
    def _reject_symlink_components(target: Path, relative_path: str) -> None:
        current = target
        if relative_path == ".":
            return
        for part in PurePosixPath(relative_path).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"Symbolic links are not allowed: {relative_path}")

    @staticmethod
    def _open_directory(path: Path) -> int:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise RuntimeError("Secure snapshot traversal is not supported on this platform")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        return os.open(path, flags)

    @staticmethod
    def _open_snapshot_file(root_descriptor: int, relative_path: str) -> int:
        parts = PurePosixPath(relative_path).parts
        if not parts:
            raise ValueError("A snapshot-relative file path is required")
        directory_descriptor = os.dup(root_descriptor)
        try:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            file_flags = os.O_RDONLY | os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                directory_flags |= os.O_CLOEXEC
                file_flags |= os.O_CLOEXEC
            for part in parts[:-1]:
                next_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=directory_descriptor,
                )
                os.close(directory_descriptor)
                directory_descriptor = next_descriptor
            return os.open(parts[-1], file_flags, dir_fd=directory_descriptor)
        finally:
            os.close(directory_descriptor)

    @classmethod
    def _read_regular_file(
        cls,
        root_descriptor: int,
        relative_path: str,
        max_file_bytes: int | None,
        *,
        max_total_bytes: int,
        expected_signature: tuple[int, int, int, int, int, int],
        destination: Path | None = None,
    ) -> tuple[_ReadFileResult | None, int]:
        descriptor = cls._open_snapshot_file(root_descriptor, relative_path)
        output = None
        try:
            file_stat = os.fstat(descriptor)
            if cls._stat_signature(file_stat) != expected_signature:
                raise ValueError(
                    f"Snapshot input changed before it could be read: {relative_path}"
                )
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(
                    f"Snapshot input is not a regular file: {relative_path}"
                )
            if max_file_bytes is not None and file_stat.st_size > max_file_bytes:
                if cls._stat_signature(os.fstat(descriptor)) != expected_signature:
                    raise ValueError(f"Snapshot input changed while reading: {relative_path}")
                return None, file_stat.st_size
            if file_stat.st_size > max_total_bytes:
                raise ValueError("Snapshot file exceeds the remaining total byte budget")
            if destination is not None:
                output = destination.open("xb")
            digest = hashlib.sha256()
            binary_prefix = bytearray()
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            line_counter = _DecodedLineCounter()
            is_binary = False
            total = 0
            while True:
                effective_limit = max_total_bytes
                if max_file_bytes is not None:
                    effective_limit = min(effective_limit, max_file_bytes)
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, max(1, effective_limit + 1 - total)),
                )
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
                if len(binary_prefix) < 8192:
                    binary_prefix.extend(chunk[: 8192 - len(binary_prefix)])
                    if b"\x00" in binary_prefix:
                        is_binary = True
                if not is_binary:
                    line_counter.feed(decoder.decode(chunk, final=False))
                if output is not None:
                    output.write(chunk)
                if max_file_bytes is not None and total > max_file_bytes:
                    if cls._stat_signature(os.fstat(descriptor)) != expected_signature:
                        raise ValueError(f"Snapshot input changed while reading: {relative_path}")
                    return None, total
                if total > max_total_bytes:
                    raise ValueError("Snapshot file exceeds the remaining total byte budget")
            if not is_binary:
                line_counter.feed(decoder.decode(b"", final=True))
            if cls._stat_signature(os.fstat(descriptor)) != expected_signature:
                raise ValueError(f"Snapshot input changed while reading: {relative_path}")
            return (
                _ReadFileResult(
                    blob_digest=digest.hexdigest(),
                    size_bytes=total,
                    line_count=0 if is_binary else line_counter.value,
                    is_binary=is_binary,
                ),
                total,
            )
        finally:
            if output is not None:
                output.close()
            os.close(descriptor)

    @staticmethod
    def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    @classmethod
    def _source_file_signature(
        cls,
        root_descriptor: int,
        relative_path: str,
    ) -> tuple[int, int, int, int, int, int]:
        descriptor = cls._open_snapshot_file(root_descriptor, relative_path)
        try:
            value = os.fstat(descriptor)
            if not stat.S_ISREG(value.st_mode):
                raise ValueError(
                    f"Snapshot input is not a regular file: {relative_path}"
                )
            return cls._stat_signature(value)
        finally:
            os.close(descriptor)

    @classmethod
    def _assert_root_identity(
        cls,
        target: Path,
        expected: tuple[int, int, int, int, int, int],
    ) -> None:
        current = os.stat(target, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or cls._stat_signature(current)[:2] != expected[:2]:
            raise ValueError("Audit target root changed during snapshot creation")
