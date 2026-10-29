"""Line tracing must not turn Python computation into unbounded cancellation I/O."""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

import pytest

from flocks.workflow.errors import RunCancelledError
from flocks.workflow.repl_runtime import PythonExecRuntime


def test_line_trace_bounds_checks_independently_of_python_line_count() -> None:
    calls = 0

    def check() -> bool:
        nonlocal calls
        calls += 1
        return False

    runtime = PythonExecRuntime(tool_registry=SimpleNamespace(), cancel_checker=check)
    started = time.monotonic()
    result, _ = runtime.execute(
        "total = 0\nfor value in range(100000):\n    total += value\noutputs['total'] = total", {},
    )
    elapsed = time.monotonic() - started
    assert result == {"total": 4999950000}
    # Allow the initial check and scheduling slack; line count must not control I/O.
    assert calls <= int(elapsed / 0.05) + 3


def test_sampled_trace_still_interrupts_a_busy_node_and_restores_previous_trace() -> None:
    cancelled = threading.Event()
    runtime = PythonExecRuntime(tool_registry=SimpleNamespace(), cancel_checker=cancelled.is_set)
    previous_trace = sys.gettrace()
    timer = threading.Timer(0.05, cancelled.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(RunCancelledError):
            runtime.execute("n = 0\nwhile True:\n    n += 1", {})
    finally:
        timer.cancel()
    assert time.monotonic() - started < 2
    assert sys.gettrace() is previous_trace


def test_explicit_cancel_query_remains_available_between_sampled_checks() -> None:
    cancelled = threading.Event()
    runtime = PythonExecRuntime(
        globals={"stop": cancelled.set}, tool_registry=SimpleNamespace(), cancel_checker=cancelled.is_set,
    )
    try:
        result, _ = runtime.execute("stop()\noutputs['cancelled'] = is_cancelled()", {})
    except RunCancelledError:
        pass  # A scheduled trace check may observe the same cancellation first.
    else:
        assert result == {"cancelled": True}
