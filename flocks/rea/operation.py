"""One REA install or uninstall at a time on this host, whichever Flocks process runs it.

Both change what every process on the host shares: flocks.json, the skill,
``data/rea`` and ``/opt/ghidra_*``. A job holds an exclusive lock on
``data/rea/operation.lock`` for its whole run, and the operating system lets
go of it only when the job releases it or its process ends: no other job takes
over while work this one started (a file step in a thread) still runs, and
nothing expires under a slow job.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

from flocks.rea import runtime

try:
    import fcntl
except ImportError:  # Windows runs one Flocks process: a lock in this process is enough
    fcntl = None  # type: ignore[assignment]

_fallback_lock = threading.Lock()
_fallback_holder: Optional[str] = None


def lock_path() -> Path:
    return runtime.state_dir() / "operation.lock"


class OperationLock:
    """The host's REA operation lock, held by one job until it is released."""

    def __init__(self, job_id: str, handle: Any = None):
        self.job_id = job_id
        self._handle = handle
        self.held = True

    def release(self) -> None:
        global _fallback_holder
        if not self.held:
            return
        self.held = False
        if self._handle is None:
            with _fallback_lock:
                if _fallback_holder == self.job_id:
                    _fallback_holder = None
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def acquire(job_id: str) -> Optional[OperationLock]:
    """The lock for ``job_id``, or None while another job on this host holds it."""

    global _fallback_holder
    if fcntl is None:
        with _fallback_lock:
            if _fallback_holder is not None:
                return None
            _fallback_holder = job_id
        return OperationLock(job_id)
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    # Name the job for the processes that look (see holder).
    handle.seek(0)
    handle.truncate()
    handle.write(job_id)
    handle.flush()
    return OperationLock(job_id, handle)


def holder() -> Optional[str]:
    """The job holding the lock now (``""`` while it has not named itself), or None."""

    if fcntl is None:
        with _fallback_lock:
            return _fallback_holder
    try:
        handle = open(lock_path(), "r", encoding="utf-8")
    except FileNotFoundError:
        return None
    with handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            return handle.read().strip()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return None
