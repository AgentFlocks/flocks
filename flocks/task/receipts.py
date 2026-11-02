"""Crash-durable completion facts, independent of tasks.db and its write lock.

Only the elected TaskManager reconciles these files. A started receipt without
a completion is uncertainty, never proof that the business work did nothing.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from contextlib import asynccontextmanager, contextmanager

from filelock import FileLock, Timeout

from .models import TaskExecution


def atomic_json(path: Path, value: dict) -> None:
    """Replace a private file, syncing content and the directory entry."""
    ensure_private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sync_directory(path: Path) -> None:
    # Windows does not expose directory fsync through os.open. File content
    # is still fsynced before replace; power-loss durability is platform dependent.
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def ensure_private_directory(path: Path) -> None:
    missing = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True, mode=0o700)
        sync_directory(directory.parent)


async def disk_operation(function, *args):
    """Do not abandon a file replace/unlink when the coroutine is cancelled."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            break  # Inspect the worker error together with cancellation below.
    try:
        result = task.result()
    except BaseException as exc:
        if cancelled:
            raise asyncio.CancelledError from exc
        raise
    if cancelled:
        raise asyncio.CancelledError
    return result


@asynccontextmanager
async def initialization_lock(path: Path, timeout: float = 20):
    """Serialize core schema/migration startup without blocking the event loop."""
    lock = FileLock(str(path), thread_local=False, mode=0o600)
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock.acquire(timeout=0)
            break
        except Timeout:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.05)
    try:
        yield
    finally:
        lock.release()


class CompletionReceipts:
    MAX_FILES = 1024
    MAX_FILE_BYTES = 16 * 1024 * 1024
    MAX_TOTAL_BYTES = 256 * 1024 * 1024

    def __init__(self, root: Path):
        self.root = root

    @classmethod
    def for_store(cls):
        from .store import TaskStore

        return cls(TaskStore.get_db_path().with_name("task-runtime") / "receipts")

    @staticmethod
    def _name(execution: TaskExecution) -> str:
        if execution.started_at is None:
            raise ValueError("Receipt requires an attempt start time")
        from datetime import timezone

        start = execution.started_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        key = execution.id + "\0" + start.astimezone(timezone.utc).isoformat()
        return hashlib.sha256(key.encode()).hexdigest() + ".json"

    @contextmanager
    def _gate(self):
        # Workers finalize concurrently in different threads. Serialize file
        # accounting, replace, and acknowledgement so limits and stat() remain
        # correct when another receipt is being deleted at the same time.
        ensure_private_directory(self.root.parent)
        try:
            with FileLock(str(self.root.parent / "receipts.lock"), timeout=5, mode=0o600):
                yield
        except Timeout as exc:
            raise OSError("Task receipt storage is busy") from exc

    def _write(self, execution: TaskExecution, phase: str) -> None:
        with self._gate():
            self._write_locked(execution, phase)

    def _write_locked(self, execution: TaskExecution, phase: str) -> None:
        payload = {"version": 1, "phase": phase,
                   "execution": execution.model_dump(mode="json", by_alias=True)}
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if len(data) > self.MAX_FILE_BYTES - 128:
            raise OSError("Task completion receipt exceeds size limit")
        ensure_private_directory(self.root.parent)
        ensure_private_directory(self.root)
        target = self.root / self._name(execution)
        files = list(self.root.glob("*.json"))
        if target not in files and len(files) >= self.MAX_FILES:
            raise OSError("Task completion receipt count limit reached")
        total = sum(p.stat().st_size for p in files if p != target)
        if total + len(data) + 128 > self.MAX_TOTAL_BYTES:
            raise OSError("Task completion receipt storage limit reached")
        atomic_json(target, {**payload, "checksum": hashlib.sha256(data).hexdigest()})

    async def started(self, execution: TaskExecution) -> None:
        await disk_operation(self._write, execution.model_copy(deep=True), "started")

    async def completed(self, execution: TaskExecution) -> None:
        if not execution.is_terminal:
            raise ValueError("Completion receipt requires a terminal outcome")
        await disk_operation(self._write, execution.model_copy(deep=True), "completed")

    def _remove(self, execution: TaskExecution) -> None:
        with self._gate():
            (self.root / self._name(execution)).unlink(missing_ok=True)
            if self.root.exists():
                sync_directory(self.root)

    async def acknowledge(self, execution: TaskExecution) -> None:
        await disk_operation(self._remove, execution)

    def cleanup_temporary(self) -> None:
        # Called only before the elected leader starts workers. A crash before
        # atomic replace can leave temporary content, never a completed fact.
        for path in self.root.glob(".receipt-*"):
            path.unlink(missing_ok=True)

    def paths(self) -> list[Path]:
        files = sorted(self.root.glob("*.json"))
        if len(files) > self.MAX_FILES:
            raise OSError("Too many task completion receipts; reconciliation required")
        return files

    def read(self, path: Path) -> tuple[str, TaskExecution]:
        if path.is_symlink() or path.stat().st_size > self.MAX_FILE_BYTES:
            raise ValueError("Invalid task completion receipt file")
        payload = json.loads(path.read_text(encoding="utf-8"))
        checksum = payload.pop("checksum")
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if checksum != hashlib.sha256(data).hexdigest() or payload["version"] != 1:
            raise ValueError("Invalid task completion receipt checksum/version")
        phase = payload["phase"]
        execution = TaskExecution.model_validate(payload["execution"])
        if (phase not in ("started", "completed") or path.name != self._name(execution)
                or (phase == "completed" and not execution.is_terminal)):
            raise ValueError("Invalid task completion receipt identity/outcome")
        return phase, execution
