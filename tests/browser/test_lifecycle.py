import pytest

from flocks.browser import _ipc as ipc
from flocks.browser import lifecycle


def _identity(pid: int = 1234) -> dict:
    return {
        "pong": True,
        "name": "default",
        "pid": pid,
        "instance_id": "instance-1",
        "browser_kind": "local",
        "protocol_version": 1,
    }


def test_stale_pid_file_is_never_used_as_process_identity(monkeypatch) -> None:
    cleaned = []
    monkeypatch.setattr(ipc, "identify", lambda name, timeout=1.0: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(lifecycle, "_cleanup_stale", lambda name, lock_timeout=0.0: cleaned.append(name))

    lifecycle.stop_daemon("default")

    assert cleaned == ["default"]


def test_current_daemon_refusal_preserves_endpoint_without_signalling(monkeypatch) -> None:
    monkeypatch.setattr(ipc, "identify", lambda name, timeout=1.0: _identity())
    monkeypatch.setattr(ipc, "request", lambda name, payload, timeout=3.0: {"ok": True})
    monkeypatch.setattr(lifecycle, "_wait_until", lambda predicate, timeout: False)
    monkeypatch.setattr(
        lifecycle,
        "_cleanup_stale",
        lambda name, lock_timeout=0.0: pytest.fail("live endpoint must be preserved"),
    )
    with pytest.raises(RuntimeError, match="refused graceful shutdown"):
        lifecycle.stop_daemon("default", timeout=0)


def test_current_daemon_stops_gracefully_and_cleans_after_lock_release(monkeypatch) -> None:
    requests = []
    cleaned = []
    monkeypatch.setattr(ipc, "identify", lambda name, timeout=1.0: _identity())
    monkeypatch.setattr(
        ipc,
        "request",
        lambda name, payload, timeout=3.0: requests.append(payload) or {"ok": True},
    )
    monkeypatch.setattr(lifecycle, "_wait_until", lambda predicate, timeout: True)
    monkeypatch.setattr(
        lifecycle,
        "_cleanup_stale",
        lambda name, lock_timeout=0.0: cleaned.append((name, lock_timeout)),
    )

    lifecycle.stop_daemon("default", timeout=10.0)

    assert requests == [{"meta": "shutdown"}]
    assert cleaned == [("default", 3.0)]


def test_malformed_endpoint_record_is_cleaned_as_stale_state(monkeypatch) -> None:
    cleaned = []
    monkeypatch.setattr(
        ipc,
        "identify",
        lambda name, timeout=1.0: (_ for _ in ()).throw(ipc.EndpointRecordError("invalid port record")),
    )
    monkeypatch.setattr(
        lifecycle,
        "_cleanup_stale",
        lambda name, lock_timeout=0.0: cleaned.append(name) or True,
    )

    lifecycle.stop_daemon("default")

    assert cleaned == ["default"]


def test_legacy_daemon_only_receives_graceful_shutdown(monkeypatch) -> None:
    requests = []
    monkeypatch.setattr(
        ipc,
        "identify",
        lambda name, timeout=1.0: (_ for _ in ()).throw(ipc.LegacyProtocolError("no ping")),
    )

    def fake_request(name, payload, timeout=1.0):
        requests.append(payload)
        if payload == {"meta": "connection_status"}:
            return {"target_id": "target-1", "session_id": "session-1", "page": None}
        return {"ok": True}

    monkeypatch.setattr(ipc, "request", fake_request)
    monkeypatch.setattr(lifecycle, "_wait_until", lambda predicate, timeout: True)
    monkeypatch.setattr(lifecycle, "_cleanup_stale", lambda name, lock_timeout=0.0: None)

    lifecycle.stop_daemon("default", timeout=0)

    assert requests == [{"meta": "connection_status"}, {"meta": "shutdown"}]


def test_legacy_daemon_refusal_preserves_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        ipc,
        "identify",
        lambda name, timeout=1.0: (_ for _ in ()).throw(ipc.LegacyProtocolError("no ping")),
    )
    monkeypatch.setattr(
        ipc,
        "request",
        lambda name, payload, timeout=1.0: {"target_id": "target-1", "session_id": "session-1", "page": None},
    )
    monkeypatch.setattr(lifecycle, "_wait_until", lambda predicate, timeout: False)
    monkeypatch.setattr(
        lifecycle,
        "_cleanup_stale",
        lambda name, lock_timeout=0.0: pytest.fail("live legacy endpoint must be preserved"),
    )

    with pytest.raises(RuntimeError, match="refused graceful shutdown"):
        lifecycle.stop_daemon("default", timeout=0)


def test_legacy_named_daemon_is_preserved(monkeypatch) -> None:
    monkeypatch.setattr(
        ipc,
        "identify",
        lambda name, timeout=1.0: (_ for _ in ()).throw(ipc.LegacyProtocolError("no ping")),
    )
    monkeypatch.setattr(
        ipc,
        "request",
        lambda name, payload, timeout=1.0: pytest.fail("legacy named daemon must not be stopped"),
    )

    with pytest.raises(RuntimeError, match="legacy named daemon.*unsupported"):
        lifecycle.stop_daemon("named")


def test_locked_unresponsive_session_is_not_cleaned(monkeypatch) -> None:
    class FakeLock:
        def __init__(self, name):
            self.name = name

        def acquire(self):
            return False

    monkeypatch.setattr(ipc, "DaemonLock", FakeLock)
    monkeypatch.setattr(
        ipc,
        "cleanup_endpoint",
        lambda name, lock: pytest.fail("locked endpoint must not be removed"),
    )

    with pytest.raises(RuntimeError, match="locked but not responding"):
        lifecycle._cleanup_stale("default")


def test_invalid_current_identity_is_not_treated_as_legacy(monkeypatch) -> None:
    monkeypatch.setattr(
        ipc,
        "identify",
        lambda name, timeout=1.0: (_ for _ in ()).throw(ipc.IPCProtocolError("session mismatch")),
    )
    monkeypatch.setattr(
        ipc,
        "request",
        lambda name, payload, timeout=1.0: pytest.fail("invalid identity must not receive legacy shutdown"),
    )

    with pytest.raises(RuntimeError, match="invalid identity"):
        lifecycle.stop_daemon("default")


def test_stop_all_continues_after_one_session_fails(monkeypatch) -> None:
    stopped = []
    monkeypatch.setattr(ipc, "discover_names", lambda: ["default", "broken", "other"])

    def fake_stop(name, timeout=15.0):
        stopped.append(name)
        if name == "broken":
            raise RuntimeError("locked")
        return None

    monkeypatch.setattr(lifecycle, "stop_daemon", fake_stop)

    names, errors = lifecycle.stop_all_daemons()

    assert names == ["default", "broken", "other"]
    assert stopped == names
    assert errors == {"broken": "locked"}
