"""Bounded, sanitized first-failure evidence outside the task database."""

import os
from pathlib import Path
import time
import traceback
from datetime import datetime, timezone

from filelock import FileLock

from .receipts import atomic_json, disk_operation, ensure_private_directory


class FailureEvidence:
    def __init__(self, path: Path):
        self.path = path
        self._incident: dict | None = None
        self._last_flush = 0.0
        self.write_error = False

    # Evidence uses an object envelope, separately from receipt envelopes.
    def _persist(self, incident: dict) -> None:
        import json

        ensure_private_directory(self.path.parent)
        with FileLock(str(self.path) + ".lock", timeout=1):
            try:
                if self.path.stat().st_size > 256 * 1024:
                    raise ValueError("Oversized evidence journal")
                history = json.loads(self.path.read_text(encoding="utf-8"))["incidents"]
                if not isinstance(history, list):
                    raise ValueError("Invalid evidence journal")
            except FileNotFoundError:
                history = []
            history = [item for item in history if item.get("id") != incident["id"]]
            atomic_json(self.path, {"incidents": (history + [incident])[-32:]})

    async def failed(self, exc: BaseException, *, operation: str, **details) -> bool:
        """Return True on the first failure; duplicates flush at most every 30s.

        No exception message, SQL, parameters, prompt, or result is recorded.
        A crash may lose the last aggregation interval, never the first write
        that completed successfully.
        """
        stamp = datetime.now(timezone.utc).isoformat()
        first = self._incident is None
        if first:
            frames = traceback.extract_tb(exc.__traceback__)[-8:]
            self._incident = {
                "id": f"{os.getpid()}:{stamp}", "pid": os.getpid(),
                "first_at": stamp, "operation": operation,
                "exception_type": type(exc).__name__,
                "sqlite_error_code": getattr(exc, "sqlite_errorcode", None),
                "frames": [{"file": Path(f.filename).name, "line": f.lineno, "function": f.name} for f in frames],
                "first_context": details, "count": 0,
            }
        self._incident["count"] += 1
        self._incident["last_at"] = stamp
        if first or time.monotonic() - self._last_flush >= 30:
            await self._flush()
        return first

    async def recovered(self) -> None:
        if self._incident is not None:
            self._incident["recovered_at"] = datetime.now(timezone.utc).isoformat()
            await self._flush()
            if not self.write_error:
                self._incident = None

    async def _flush(self) -> None:
        try:
            await disk_operation(self._persist, dict(self._incident))
            self.write_error = False
            self._last_flush = time.monotonic()
        except Exception:
            self.write_error = True

    def snapshot(self) -> dict:
        return {"write_error": self.write_error,
                "active_failure_count": self._incident["count"] if self._incident else 0}
