"""Regression tests for ``_DropWarningThrottle``.

The throttle replaced a one-warning-per-drop ``log.warning`` with a windowed
aggregate so a sustained ``QueueFull`` flood does not flood the log itself.
These tests pin three behavioural contracts so future refactors do not
silently regress to either the old per-drop spew **or** to silently dropping
the trailing count.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from flocks.ingest.syslog import manager as syslog_manager


@pytest.fixture
def captured_warnings() -> List[Dict[str, Any]]:
    """Capture every call to ``syslog.manager.log.warning``."""
    seen: List[Dict[str, Any]] = []

    def _record(event: str, fields: Dict[str, Any] | None = None, **_kwargs: Any) -> None:
        seen.append({"event": event, "fields": dict(fields or {})})

    with patch.object(syslog_manager.log, "warning", side_effect=_record):
        yield seen


def _make_full_queue(maxsize: int = 4) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    for i in range(maxsize):
        q.put_nowait({"_seq": i})
    return q


@pytest.mark.asyncio
async def test_record_drop_aggregates_under_one_warning_per_window(
    captured_warnings: List[Dict[str, Any]],
) -> None:
    """A burst of drops within the window must collapse to <=1 warning."""
    queue = _make_full_queue()
    throttle = syslog_manager._DropWarningThrottle(
        workflow_id="wf-throttle",
        queue=queue,
        window_s=10.0,  # long window so the burst never crosses it
    )

    # First drop fires immediately (last_log is 0.0 → elapsed >> window_s).
    for _ in range(500):
        throttle.record_drop()

    drop_warnings = [w for w in captured_warnings if w["event"] == "syslog.queue_full_dropped"]
    assert len(drop_warnings) == 1, (
        f"expected exactly one aggregated warning, got {len(drop_warnings)}"
    )
    payload = drop_warnings[0]["fields"]
    assert payload["workflow_id"] == "wf-throttle"
    assert payload["trigger"] == "threshold"
    # The aggregated count must reflect the burst minus drops counted *after*
    # the warning fired (since the warning resets the counter on first fire).
    assert payload["dropped_in_window"] == 1
    # And the still-buffered count must equal the rest of the burst.
    assert throttle.count == 499


@pytest.mark.asyncio
async def test_maybe_flush_emits_trailing_count_after_window(
    captured_warnings: List[Dict[str, Any]],
) -> None:
    """After the window elapses, ``maybe_flush`` must emit the leftover count."""
    queue = _make_full_queue()
    throttle = syslog_manager._DropWarningThrottle(
        workflow_id="wf-flush",
        queue=queue,
        window_s=0.05,  # tiny window so the test runs fast
    )

    for _ in range(10):
        throttle.record_drop()

    # Window not elapsed yet → no extra warning, count holds the tail.
    assert throttle.count == 9
    throttle.maybe_flush()
    assert throttle.count == 9  # still within window

    # Sleep just past the window then flush.
    await asyncio.sleep(0.07)
    throttle.maybe_flush()
    assert throttle.count == 0

    flush_warnings = [
        w for w in captured_warnings
        if w["event"] == "syslog.queue_full_dropped" and w["fields"].get("trigger") == "flush"
    ]
    assert len(flush_warnings) == 1
    assert flush_warnings[0]["fields"]["dropped_in_window"] == 9


@pytest.mark.asyncio
async def test_flush_remaining_emits_unconditionally(
    captured_warnings: List[Dict[str, Any]],
) -> None:
    """``flush_remaining`` is the shutdown safety net — must ignore the window."""
    queue = _make_full_queue()
    throttle = syslog_manager._DropWarningThrottle(
        workflow_id="wf-shutdown",
        queue=queue,
        window_s=10.0,  # long window so a normal flush would be suppressed
    )

    # Seed the counter with a few drops; first one fires "threshold", the
    # rest accumulate silently because the window has not elapsed.
    for _ in range(5):
        throttle.record_drop()
    captured_warnings.clear()

    throttle.flush_remaining()

    shutdown_warnings = [
        w for w in captured_warnings
        if w["event"] == "syslog.queue_full_dropped" and w["fields"].get("trigger") == "shutdown"
    ]
    assert len(shutdown_warnings) == 1
    assert shutdown_warnings[0]["fields"]["dropped_in_window"] == 4
    assert throttle.count == 0

    # A second call with no pending drops must be a silent no-op.
    captured_warnings.clear()
    throttle.flush_remaining()
    assert captured_warnings == []
