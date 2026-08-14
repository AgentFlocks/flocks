"""Durable filesystem primitives for the phase-one report product runtime."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from flocks.config.config import Config


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def product_root() -> Path:
    override = os.getenv("SITUATION_REPORT_PRODUCT_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Config.get_data_path() / "situation-report-product").resolve()


def validate_session_id(session_id: str) -> str:
    """Validate the only business locator accepted by the product runtime."""

    if (
        not session_id.startswith("ses_")
        or len(session_id) > 128
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in session_id)
    ):
        raise ValueError("sessionID is invalid")
    return session_id


def session_root(session_id: str) -> Path:
    """Return the managed Project worktree for one report Session.

    The public ``sessionID`` maps directly to its Project directory so an
    operator can locate the workspace without a secondary lookup.
    """

    validated = validate_session_id(session_id)
    return product_root() / "projects" / validated


def _lock_fd(fd: int) -> None:
    if sys.platform == "win32":  # pragma: no cover - Windows only
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock_fd(fd: int) -> None:
    if sys.platform == "win32":  # pragma: no cover - Windows only
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Acquire a cross-process exclusive lock for a product state mutation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    try:
        _lock_fd(fd)
        locked = True
        yield
    finally:
        if locked:
            _unlock_fd(fd)
        os.close(fd)


@asynccontextmanager
async def async_file_lock(path: Path):
    """Async wrapper that never blocks the event loop while acquiring an OS lock."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    try:
        await asyncio.to_thread(_lock_fd, fd)
        locked = True
        yield
    finally:
        if locked:
            _unlock_fd(fd)
        os.close(fd)


def atomic_write_bytes(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write_bytes(path, encoded)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value
