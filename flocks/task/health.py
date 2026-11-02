"""Process-local task health: readable even when the task database is unavailable."""

from dataclasses import dataclass
from datetime import datetime, timezone
import time


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ComponentHealth:
    last_success_at: str | None = None
    first_failure_at: str | None = None
    last_failure_at: str | None = None
    consecutive_failures: int = 0
    failure_count: int = 0
    operation: str | None = None
    sqlite_error_code: int | None = None
    _last_success: float | None = None
    _last_started: float | None = None

    def started(self) -> None:
        self._last_started = time.monotonic()

    def succeeded(self) -> None:
        self.last_success_at = _stamp()
        self._last_success = time.monotonic()
        self.consecutive_failures = 0

    def failed(self, exc: BaseException, *, operation: str) -> None:
        stamp = _stamp()
        if self.consecutive_failures == 0:
            self.first_failure_at = stamp
        self.last_failure_at = stamp
        self.consecutive_failures += 1
        self.failure_count += 1
        self.operation = operation
        self.sqlite_error_code = getattr(exc, "sqlite_errorcode", None)

    def snapshot(self, *, max_silence: float | None = None) -> dict:
        now = time.monotonic()
        anchor = self._last_success if self._last_success is not None else self._last_started
        stalled = max_silence is not None and anchor is not None and now - anchor > max_silence
        return {
            "status": "degraded" if self.consecutive_failures or stalled else "healthy" if self.last_success_at else "starting",
            "last_success_at": self.last_success_at,
            "first_failure_at": self.first_failure_at,
            "last_failure_at": self.last_failure_at,
            "consecutive_failures": self.consecutive_failures,
            "failure_count": self.failure_count,
            "operation": self.operation,
            "sqlite_error_code": self.sqlite_error_code,
            "stalled": stalled,
        }
