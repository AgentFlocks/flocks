"""Conditions an operator has to act on, raised by the process itself.

An alert is raised once, when the condition starts, as an ERROR line
(``alert.raised``, with the alert's name) in the process log, and as a line
on the process's own stderr -- what systemd hands to journald -- so it is
seen even when the log file or its shipping is what failed.  It is listed
with the readiness details until it is cleared.
None of them takes the node out of rotation: they are about the node's
upkeep (its logs, their shipping), not about whether it can serve.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from flocks.utils.log import Log, scrub_credentials

log = Log.create(service="alerts")


@dataclass
class Alert:
    name: str
    detail: str
    since: float
    fields: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "detail": self.detail, "since": int(self.since * 1000), **self.fields}


_active: Dict[str, Alert] = {}
_lock = threading.Lock()


def raise_alert(name: str, detail: str, **fields: Any) -> bool:
    """Raise *name*; True when it was not already raised (that is when it is logged)."""
    with _lock:
        current = _active.get(name)
        if current is not None:
            current.detail, current.fields = detail, dict(fields)
            return False
        _active[name] = Alert(name, detail, time.time(), dict(fields))
    log.error("alert.raised", {"alert": name, "detail": detail, **fields})
    _to_journal(f"flocks alert raised: {name}: {detail}")
    return True


def _to_journal(line: str) -> None:
    """One line on the process's stderr, apart from the log writer (which may write to a file)."""
    stream = sys.__stderr__
    if stream is None:
        return
    try:
        stream.write(scrub_credentials(line) + "\n")
        stream.flush()
    except (OSError, ValueError):
        pass


def clear_alert(name: str) -> bool:
    with _lock:
        cleared = _active.pop(name, None)
    if cleared is not None:
        log.info("alert.cleared", {"alert": name, "raised_for_s": round(time.time() - cleared.since)})
        _to_journal(f"flocks alert cleared: {name}")
    return cleared is not None


def active_alerts() -> List[dict]:
    with _lock:
        return [alert.to_dict() for alert in sorted(_active.values(), key=lambda a: a.since)]


def reset_alerts() -> None:
    """Forget every alert.  For tests."""
    with _lock:
        _active.clear()


__all__ = ["Alert", "active_alerts", "clear_alert", "raise_alert", "reset_alerts"]
