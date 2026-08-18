"""Read-only, reproducible target snapshot creation."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

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
MAX_SNAPSHOT_TOTAL_BYTES = 512 * 1024 * 1024


def normalize_relative_path(value: str, *, allow_root: bool = False) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if len(raw) > 1024:
        raise ValueError("Snapshot-relative paths may contain at most 1024 characters")
    if not raw and allow_root:
        return "."
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValueError("Path must be a snapshot-relative path without '..'")
    normalized = path.as_posix()
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
    def __init__(self, snapshots_root: Path, store: ScanStore):
        self.snapshots_root = snapshots_root
        self.store = store

    def create(
        self,
        target_path: str,
        *,
        include_paths: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_file_bytes: int = 1_048_576,
    ) -> SnapshotRef:
        raw_target = Path(target_path).expanduser()
        if raw_target.is_symlink():
            raise ValueError("The target root cannot be a symbolic link")
        target = raw_target.resolve(strict=True)
        if not target.is_dir():
            raise ValueError("Target must be a local directory")
        if max_file_bytes < 1 or max_file_bytes > 20 * 1024 * 1024:
            raise ValueError("max_file_bytes must be between 1 and 20971520")
        if len(include_paths or ["."]) > 256:
            raise ValueError("At most 256 include paths are allowed")
        if len(exclude_patterns or []) > 256:
            raise ValueError("At most 256 exclude patterns are allowed")

        includes = [
            normalize_relative_path(item, allow_root=True)
            for item in (include_paths or ["."])
        ]
        patterns = tuple(DEFAULT_EXCLUDES) + tuple(
            normalize_relative_path(item, allow_root=True)
            for item in (exclude_patterns or [])
        )
        files = self._enumerate(target, includes, patterns)
        if len(files) > MAX_SNAPSHOT_FILES:
            raise ValueError(
                f"Snapshot contains more than {MAX_SNAPSHOT_FILES} files"
            )

        self.snapshots_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.snapshots_root.chmod(0o700)
        temporary = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=self.snapshots_root))
        snapshot_id = f"snap_{uuid.uuid4().hex}"
        final_root = self.snapshots_root / snapshot_id
        records: list[SnapshotFile] = []
        omissions: list[SnapshotOmission] = []
        total_bytes = 0
        root_descriptor: int | None = None
        try:
            root_descriptor = self._open_directory(target)
            for relative_path, source_path in files:
                data, observed_size = self._read_regular_file(
                    root_descriptor,
                    relative_path,
                    max_file_bytes,
                )
                if data is None:
                    omissions.append(
                        SnapshotOmission(
                            relative_path=relative_path,
                            reason="file_size_limit_exceeded",
                            size_bytes=observed_size,
                        )
                    )
                    continue
                if total_bytes + len(data) > MAX_SNAPSHOT_TOTAL_BYTES:
                    raise ValueError(
                        "Snapshot exceeds the 536870912-byte total size limit"
                    )
                destination = temporary / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                digest = hashlib.sha256(data).hexdigest()
                is_binary = b"\x00" in data[:8192]
                line_count = 0 if is_binary else len(data.decode("utf-8", errors="replace").splitlines())
                records.append(
                    SnapshotFile(
                        relative_path=relative_path,
                        blob_digest=digest,
                        size_bytes=len(data),
                        line_count=line_count,
                        language=_language(source_path),
                        is_binary=is_binary,
                    )
                )
                total_bytes += len(data)

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
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            repository_identity = hashlib.sha256(str(target).encode("utf-8")).hexdigest()

            os.replace(temporary, final_root)
            for path in sorted(final_root.rglob("*"), reverse=True):
                path.chmod(0o500 if path.is_dir() else 0o400)
            final_root.chmod(0o500)

            snapshot = SnapshotRef(
                snapshot_id=snapshot_id,
                repository_identity=repository_identity,
                source_revision=None,
                tree_digest=tree_digest,
                scope_digest=scope_digest,
                file_count=len(records),
                total_bytes=total_bytes,
                created_at=datetime.now(timezone.utc).isoformat(),
                root_path=str(final_root),
                omitted_file_count=len(omissions),
            )
            self.store.save_snapshot(snapshot, records, omissions)
            return snapshot
        except Exception:
            cleanup_root = temporary if temporary.exists() else final_root
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
        self.store.delete_snapshot(snapshot_id)
        root = Path(snapshot.root_path)
        if root.exists():
            root.chmod(0o700)
            for path in root.rglob("*"):
                path.chmod(0o700 if path.is_dir() else 0o600)
            shutil.rmtree(root)

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
                    if path.is_file() and not _matches_exclude(relative, exclude_patterns):
                        selected[relative] = path
        return sorted(selected.items())

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
        max_file_bytes: int,
    ) -> tuple[bytes | None, int]:
        descriptor = cls._open_snapshot_file(root_descriptor, relative_path)
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(
                    f"Snapshot input is not a regular file: {relative_path}"
                )
            if file_stat.st_size > max_file_bytes:
                return None, file_stat.st_size
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(64 * 1024, max_file_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_file_bytes:
                    return None, total
            return b"".join(chunks), total
        finally:
            os.close(descriptor)
