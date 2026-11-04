"""TCP control API (Windows): each runtime root reaches only its own daemon.

Before ports were recorded per runtime root every daemon listened on the same localhost
port, so on a Windows host with two instances ``flocks start`` of the second one found the
first one's daemon, took its different ports for a config change and stopped it.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

from flocks.cli import service_control, service_manager, service_supervisor
from tests.helpers.service_supervisor import SleeperProcessAdapter, make_runtime_paths


class DummyConsole:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *args, **_kwargs) -> None:
        self.messages.append(" ".join(str(arg) for arg in args))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _runtime(root: Path) -> service_manager.RuntimePaths:
    paths = make_runtime_paths(root)
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    return paths


@pytest.fixture
def tcp_control(monkeypatch) -> int:
    """Use the localhost TCP control API, as on Windows, with a private legacy port."""
    monkeypatch.delattr(service_control.socket, "AF_UNIX", raising=False)
    monkeypatch.delenv("FLOCKS_CONTROL_PORT", raising=False)
    legacy_port = _free_port()
    monkeypatch.setattr(service_control, "SUPERVISOR_CONTROL_PORT", legacy_port)
    return legacy_port


@pytest.fixture
def daemons(monkeypatch):
    started: list[service_supervisor.SupervisorDaemon] = []

    def start(paths: service_manager.RuntimePaths) -> service_supervisor.SupervisorDaemon:
        monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
        daemon = service_supervisor.SupervisorDaemon(
            service_manager.ServiceConfig(),
            backend_adapter=SleeperProcessAdapter(),
        )
        daemon._start_control_server()
        started.append(daemon)
        return daemon

    yield start
    for daemon in reversed(started):
        if daemon._server_thread is not None and daemon._server_thread.is_alive():
            daemon._stop_control_server()


def test_tcp_daemons_of_two_runtime_roots_listen_on_their_own_ports(tcp_control, daemons, tmp_path: Path) -> None:
    runtime_a = _runtime(tmp_path / "a")
    runtime_b = _runtime(tmp_path / "b")
    daemons(runtime_a)
    daemon_b = daemons(runtime_b)

    port_a = service_control.read_recorded_control_port(runtime_a)
    port_b = service_control.read_recorded_control_port(runtime_b)
    assert port_a is not None and port_b is not None
    assert port_a != port_b
    assert tcp_control not in {port_a, port_b}
    assert service_control.supervisor_control_port(runtime_a) == port_a
    status_a = service_control.read_supervisor_status(paths=runtime_a)
    status_b = service_control.read_supervisor_status(paths=runtime_b)
    assert status_a.daemon.log_path == str(service_control.supervisor_log_path(runtime_a))
    assert status_b.daemon.log_path == str(service_control.supervisor_log_path(runtime_b))

    daemon_b._stop_control_server()

    assert not service_control.supervisor_port_path(runtime_b).exists()
    assert service_control.supervisor_is_running(runtime_b) is False
    assert service_control.supervisor_is_running(runtime_a) is True


def test_start_of_second_runtime_leaves_the_daemon_on_the_shared_port_alone(
    tcp_control, daemons, monkeypatch, tmp_path: Path
) -> None:
    runtime_a = _runtime(tmp_path / "a")
    runtime_b = _runtime(tmp_path / "b")
    # Runtime A's daemon holds the shared port every daemon used to listen on.
    monkeypatch.setenv("FLOCKS_CONTROL_PORT", str(tcp_control))
    daemon_a = daemons(runtime_a)
    monkeypatch.delenv("FLOCKS_CONTROL_PORT")
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: runtime_b)
    stopped: list[object] = []
    started: list[int] = []
    monkeypatch.setattr(service_manager, "_stop_all_unlocked", lambda _console, **kwargs: stopped.append(kwargs.get("paths")))
    monkeypatch.setattr(
        service_manager,
        "_start_all_without_stop",
        lambda config, _console: started.append(config.backend_port),
    )

    service_manager.start_all(
        service_manager.ServiceConfig(backend_port=5392, frontend_port=5392, no_browser=True),
        DummyConsole(),
    )

    assert stopped == []
    assert started == [5392]
    assert daemon_a.is_stopping() is False
    assert service_control.supervisor_is_running(runtime_a) is True


def test_tcp_client_refuses_another_runtimes_daemon(tcp_control, daemons, monkeypatch, tmp_path: Path) -> None:
    runtime_a = _runtime(tmp_path / "a")
    runtime_b = _runtime(tmp_path / "b")
    monkeypatch.setenv("FLOCKS_CONTROL_PORT", str(tcp_control))
    daemon_a = daemons(runtime_a)
    monkeypatch.delenv("FLOCKS_CONTROL_PORT")

    assert service_control.supervisor_is_running(runtime_b) is False
    with pytest.raises(service_control.ForeignSupervisorError):
        service_control.request_stop(paths=runtime_b)
    with pytest.raises(service_control.ForeignSupervisorError):
        service_control.read_supervisor_status(paths=runtime_b)

    assert daemon_a.is_stopping() is False
    assert service_control.supervisor_is_running(runtime_a) is True


def test_stale_port_record_naming_another_runtimes_daemon_is_not_trusted(
    tcp_control, daemons, tmp_path: Path
) -> None:
    runtime_a = _runtime(tmp_path / "a")
    runtime_b = _runtime(tmp_path / "b")
    daemon_a = daemons(runtime_a)
    # Runtime B's daemon died without clearing its record and A's daemon got that port later.
    service_control.record_control_port(runtime_b, daemon_a._control_port)

    assert service_control.supervisor_is_running(runtime_b) is False
    assert service_control.supervisor_is_running(runtime_a) is True


def test_daemon_started_before_port_records_is_still_found_and_stopped(
    tcp_control, daemons, monkeypatch, tmp_path: Path
) -> None:
    runtime = _runtime(tmp_path / "a")
    monkeypatch.setenv("FLOCKS_CONTROL_PORT", str(tcp_control))
    daemon = daemons(runtime)
    monkeypatch.delenv("FLOCKS_CONTROL_PORT")
    # An older version's daemon listens on the fixed port and records nothing.
    service_control.supervisor_port_path(runtime).unlink()

    assert service_control.supervisor_is_running(runtime) is True
    assert service_control.request_stop(paths=runtime)["status"] == "stopping"
    assert daemon.is_stopping() is True


def test_flocks_control_port_pins_the_daemon_and_its_clients(tcp_control, daemons, monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "a")
    pinned = _free_port()
    monkeypatch.setenv("FLOCKS_CONTROL_PORT", str(pinned))

    daemon = daemons(runtime)

    assert daemon._control_port == pinned
    assert service_control.read_recorded_control_port(runtime) == pinned
    assert service_control.supervisor_control_port(runtime) == pinned
    assert service_control.supervisor_is_running(runtime) is True


def test_clearing_the_port_record_keeps_a_newer_daemons_record(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "a")
    service_control.record_control_port(runtime, 50001)

    service_control.clear_recorded_control_port(runtime, 50000)
    assert service_control.read_recorded_control_port(runtime) == 50001

    service_control.clear_recorded_control_port(runtime, 50001)
    assert not service_control.supervisor_port_path(runtime).exists()


def test_port_record_replace_retries_while_windows_holds_the_file(monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "a")
    attempts: list[str] = []
    real_replace = os.replace

    def replace_after_sharing_violations(source, destination) -> None:
        attempts.append(str(destination))
        if len(attempts) < 3:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        real_replace(source, destination)

    monkeypatch.setattr(service_control.os, "replace", replace_after_sharing_violations)
    monkeypatch.setattr(service_control.time, "sleep", lambda _seconds: None)

    service_control.record_control_port(runtime, 50002)

    assert len(attempts) == 3
    assert service_control.read_recorded_control_port(runtime) == 50002
    assert list(runtime.run_dir.glob("*.tmp")) == []


def test_start_clears_a_stale_port_record_before_spawning_the_daemon(tcp_control, monkeypatch, tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "a")
    service_control.record_control_port(runtime, 50003)
    spawned: list[list[str]] = []
    monkeypatch.setattr(service_manager, "ensure_install_layout", lambda: tmp_path)
    monkeypatch.setattr(service_manager, "resolve_flocks_cli_command", lambda _root: ["flocks"])
    monkeypatch.setattr(
        service_manager,
        "_spawn_process",
        lambda command, **_kwargs: spawned.append(command) or object(),
    )

    service_manager._start_supervisor_process(service_manager.ServiceConfig(), runtime, DummyConsole())

    assert spawned and spawned[0][:2] == ["flocks", "service-daemon"]
    assert not service_control.supervisor_port_path(runtime).exists()


@pytest.mark.skipif(sys.platform != "win32", reason="SO_EXCLUSIVEADDRUSE is Windows-only")
def test_second_tcp_control_server_cannot_share_a_port_on_windows(tmp_path: Path) -> None:
    first = service_supervisor._TcpControlServer(("127.0.0.1", 0), service_supervisor.BaseHTTPRequestHandler)
    try:
        with pytest.raises(OSError):
            service_supervisor._TcpControlServer(first.server_address, service_supervisor.BaseHTTPRequestHandler)
    finally:
        first.server_close()
