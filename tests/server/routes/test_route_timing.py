import pytest

from flocks.server.routes import _timing as timing_module


class _Recorder:
    def __init__(self) -> None:
        self.debug_calls: list[tuple[str, dict]] = []
        self.info_calls: list[tuple[str, dict]] = []

    def debug(self, message, extra=None) -> None:
        self.debug_calls.append((message, extra or {}))

    def info(self, message, extra=None) -> None:
        self.info_calls.append((message, extra or {}))


def test_log_route_timing_uses_debug_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = _Recorder()
    monkeypatch.setattr(timing_module.time, "perf_counter", lambda: 100.2)

    duration_ms = timing_module.log_route_timing(
        logger,
        "session.list.complete",
        started_at=100.0,
        extra={"count": 2},
        slow_threshold_ms=300,
    )

    assert 199 <= duration_ms <= 200
    assert logger.info_calls == []
    assert logger.debug_calls == [
        ("session.list.complete", {"duration_ms": duration_ms, "count": 2}),
    ]


def test_log_route_timing_uses_info_at_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = _Recorder()
    monkeypatch.setattr(timing_module.time, "perf_counter", lambda: 200.3)

    duration_ms = timing_module.log_route_timing(
        logger,
        "task.dashboard.complete",
        started_at=200.0,
        extra={"running": 1},
        slow_threshold_ms=300,
    )

    assert 299 <= duration_ms <= 300
    assert logger.debug_calls == []
    assert logger.info_calls == [
        ("task.dashboard.complete", {"duration_ms": duration_ms, "running": 1}),
    ]
