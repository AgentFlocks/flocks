"""
Delegatable override settings for agent footer toggles.

Keeps runtime UI overrides in a sidecar JSON file instead of rewriting
agent YAML sources inside the repo.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

from flocks.utils.log import Log

log = Log.create(service="agent.delegatable_settings")

_SETTINGS_LOCK = threading.RLock()
_LOCK_FILENAME = "agent_delegatable_settings.json.lock"


def settings_path() -> Path:
    return Path.home() / ".flocks" / "config" / "agent_delegatable_settings.json"


def _platform_file_lock(fd: int) -> None:
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        import msvcrt

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
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / _LOCK_FILENAME
    fd: Optional[int] = None
    locked = False
    try:
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _platform_file_lock(fd)
            locked = True
        except OSError as exc:
            log.warn(
                "agent.delegatable_settings.flock_failed",
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


@contextmanager
def _locked_rmw() -> Iterator[None]:
    with _SETTINGS_LOCK:
        with _settings_cross_process_lock(settings_path().parent):
            yield


def load_overrides() -> Dict[str, bool]:
    path = settings_path()
    with _SETTINGS_LOCK:
        try:
            if not path.exists():
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
            overrides = data.get("delegatable_overrides", {})
            if isinstance(overrides, dict):
                return {
                    str(name): value
                    for name, value in overrides.items()
                    if isinstance(name, str) and isinstance(value, bool)
                }
        except Exception as exc:
            log.warn("agent.delegatable_settings.load_failed", {"error": str(exc)})
    return {}


def save_overrides(overrides: Dict[str, bool]) -> None:
    path = settings_path()
    with _SETTINGS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"delegatable_overrides": {name: overrides[name] for name in sorted(overrides)}}
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=".agent_delegatable_settings_",
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


def get_override(name: str) -> Optional[bool]:
    return load_overrides().get(name)


def set_override(name: str, delegatable: bool) -> bool:
    with _locked_rmw():
        current = load_overrides()
        current[name] = delegatable
        save_overrides(current)
    return delegatable


def forget_override(name: str) -> None:
    with _locked_rmw():
        current = load_overrides()
        if name in current:
            current.pop(name, None)
            save_overrides(current)
