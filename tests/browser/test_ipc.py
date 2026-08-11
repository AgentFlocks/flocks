import asyncio
import json
import stat
import tempfile
from pathlib import Path

import pytest

from flocks.browser import _ipc as ipc


def test_browser_ipc_files_live_under_flocks_browser_dir() -> None:
    expected_dir = Path.home() / ".flocks" / "browser"

    assert Path(ipc.BU_TMP_DIR) == expected_dir
    assert ipc.log_path("default") == expected_dir / "bu.log"
    assert ipc.pid_path("default") == expected_dir / "bu.pid"
    assert ipc.port_path("default") == expected_dir / "bu.port"
    if ipc.IS_WINDOWS:
        assert ipc.sock_addr("default") == "tcp:bu"
    else:
        assert ipc.sock_addr("default") == str(expected_dir / "bu.sock")


def test_named_sessions_have_isolated_runtime_artifacts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))

    default = ipc.runtime_paths("default")
    alpha = ipc.runtime_paths("alpha")
    beta = ipc.runtime_paths("beta_2")

    assert default.socket == tmp_path / "browser/bu.sock"
    assert default.lock == tmp_path / "browser/bu.lock"
    assert alpha.socket == tmp_path / "browser/bu-alpha.sock"
    assert alpha.pid != beta.pid
    assert not (tmp_path / "browser").exists()


@pytest.mark.parametrize("name", ["", "has space", "../escape", "x" * 65])
def test_invalid_session_names_are_rejected(name) -> None:
    with pytest.raises(ValueError, match="invalid BU_NAME"):
        ipc.runtime_paths(name)


def test_runtime_root_honors_flocks_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "custom"))

    assert ipc.runtime_dir() == tmp_path / "custom/browser"


def test_daemon_lock_serializes_same_name_but_not_different_names(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    first = ipc.DaemonLock("alpha")
    same_name = ipc.DaemonLock("alpha")
    other_name = ipc.DaemonLock("beta")
    try:
        assert first.acquire()
        assert not same_name.acquire()
        assert other_name.acquire()
    finally:
        first.release()
        same_name.release()
        other_name.release()


def test_identify_rejects_boolean_pid(monkeypatch) -> None:
    monkeypatch.setattr(
        ipc,
        "request",
        lambda name, payload, timeout=1.0: {
            "pong": True,
            "protocol_version": 1,
            "name": "default",
            "pid": True,
            "instance_id": "instance-1",
            "browser_kind": "local",
        },
    )

    with pytest.raises(ipc.IPCProtocolError, match="invalid PID"):
        ipc.identify("default")


def test_identify_classifies_explicit_unknown_ping_as_legacy(monkeypatch) -> None:
    monkeypatch.setattr(
        ipc,
        "request",
        lambda name, payload, timeout=1.0: {"error": "unknown meta request: ping"},
    )

    with pytest.raises(ipc.LegacyProtocolError, match="predates"):
        ipc.identify("default")


def test_read_port_record_supports_legacy_and_authenticated_formats(tmp_path) -> None:
    path = tmp_path / "bu.port"
    path.write_text("9222", encoding="utf-8")
    assert ipc._read_port_record(path) == (9222, None)

    path.write_text(json.dumps({"port": 9333, "token": "secret"}), encoding="utf-8")
    assert ipc._read_port_record(path) == (9333, "secret")


def test_read_port_record_rejects_malformed_record(tmp_path) -> None:
    path = tmp_path / "bu.port"
    path.write_text('{"port":"9222","token":"secret"}', encoding="utf-8")

    with pytest.raises(ipc.EndpointRecordError, match="invalid browser daemon port record"):
        ipc._read_port_record(path)


def test_request_injects_windows_endpoint_token(monkeypatch) -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.sent = b""
            self.response = b'{"ok":true}\n'
            self.closed = False

        def sendall(self, data) -> None:
            self.sent += data

        def recv(self, _size) -> bytes:
            response, self.response = self.response, b""
            return response

        def close(self) -> None:
            self.closed = True

    sock = FakeSocket()
    monkeypatch.setattr(ipc, "_connect_with_token", lambda name, timeout: (sock, "secret"))

    assert ipc.request("default", {"meta": "ping"}) == {"ok": True}
    assert json.loads(sock.sent) == {"meta": "ping", "_ipc_token": "secret"}
    assert sock.closed is True


@pytest.mark.asyncio
@pytest.mark.skipif(ipc.IS_WINDOWS, reason="POSIX socket permissions")
async def test_posix_server_uses_private_socket_and_cleans_it_up(monkeypatch) -> None:
    with tempfile.TemporaryDirectory(prefix="flocks-ipc-", dir="/tmp") as root:
        monkeypatch.setenv("FLOCKS_ROOT", root)
        lock = ipc.DaemonLock("test")
        assert lock.acquire()

        async def handler(request):
            return {"echo": request["value"]}

        task = asyncio.create_task(ipc.serve("test", handler, lock))
        socket_path = ipc.runtime_paths("test").socket
        try:
            for _ in range(100):
                if socket_path.exists() or task.done():
                    break
                await asyncio.sleep(0.01)
            if task.done():
                try:
                    await task
                except PermissionError:
                    pytest.skip("sandbox does not permit Unix socket binding")
            assert socket_path.exists()
            assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
            assert await asyncio.to_thread(ipc.request, "test", {"value": "ok"}) == {"echo": "ok"}
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            lock.release()

        assert not socket_path.exists()
