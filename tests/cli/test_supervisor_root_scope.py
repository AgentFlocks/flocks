"""Regression tests for two supervisor findings from the 2026-09 full test pass.

- ``flocks stop`` / ``flocks status`` of one runtime root (``FLOCKS_ROOT``) must never
  claim (and kill) the daemon of another root that shares the same install: without
  a pid record only daemons whose environment names this root are candidates.
- A backend whose start failed (or whose restart is waiting for its backoff slot)
  must keep ``state=degraded`` / ``restarting`` on later ticks instead of flipping to
  ``stopped``; the CLI reported ``flocks state=stopped`` for a port clash.

From the 2026-10 Windows upgrade check: the first ``flocks start`` of a second root
(no pid record yet) stopped the first root's daemon. A daemon now names its root on its
command line (all Windows lets another process read), and a daemon without
``FLOCKS_ROOT`` lives under its own HOME, not under the HOME of the CLI asking.
"""

from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path
from types import SimpleNamespace

from flocks.cli import service_manager, service_supervisor
from tests.helpers.service_supervisor import SleeperProcessAdapter, make_runtime_paths, make_short_runtime_root


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _fake_process_env(mapping: dict[int, dict[str, str] | None]):
    def _env(pid: int):
        return mapping.get(pid)

    return _env


def test_no_pid_record_only_claims_daemons_running_with_this_root(monkeypatch, tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path / "root-a")
    paths.run_dir.mkdir(parents=True)
    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "_process_list_pids", lambda: [111, 222, 333])
    monkeypatch.setattr(service_manager, "_trusted_flocks_daemon_owner", lambda pid, *, root: True)
    monkeypatch.setattr(
        service_manager,
        "_process_environment",
        _fake_process_env({
            111: {"FLOCKS_ROOT": str(tmp_path / "root-a")},
            222: {"FLOCKS_ROOT": str(tmp_path / "root-b")},
            333: None,  # environment unreadable: never claimed
        }),
    )

    assert service_manager.trusted_daemon_process_pids(root=tmp_path / "install", paths=paths) == [111]


def test_default_root_claims_daemons_without_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    paths = make_runtime_paths(tmp_path / ".flocks")
    paths.run_dir.mkdir(parents=True)
    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "_process_list_pids", lambda: [111, 222])
    monkeypatch.setattr(service_manager, "_trusted_flocks_daemon_owner", lambda pid, *, root: True)
    monkeypatch.setattr(
        service_manager,
        "_process_environment",
        _fake_process_env({111: {"PATH": "/usr/bin"}, 222: {"FLOCKS_ROOT": str(tmp_path / "elsewhere")}}),
    )

    assert service_manager.trusted_daemon_process_pids(root=tmp_path / "install", paths=paths) == [111]


def test_pid_record_wins_over_environment_attribution(monkeypatch, tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path / "root-a")
    paths.run_dir.mkdir(parents=True)
    service_supervisor.supervisor_pid_path(paths).write_text("222\n", encoding="utf-8")
    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "pid_is_running", lambda pid: True)
    monkeypatch.setattr(service_manager, "_process_list_pids", lambda: [111, 222])
    monkeypatch.setattr(service_manager, "_trusted_flocks_daemon_owner", lambda pid, *, root: True)
    monkeypatch.setattr(service_manager, "_process_environment", _fake_process_env({111: {"FLOCKS_ROOT": str(tmp_path / "root-a")}}))

    assert service_manager.trusted_daemon_process_pids(root=tmp_path / "install", paths=paths) == [222]


def test_stop_of_one_root_does_not_kill_another_roots_daemon(monkeypatch, tmp_path: Path) -> None:
    """End to end through cleanup_trusted_daemon_processes: root A cleanup never targets root B's daemon."""
    paths_a = make_runtime_paths(tmp_path / "a")
    paths_a.run_dir.mkdir(parents=True)
    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "_process_list_pids", lambda: [4242])
    monkeypatch.setattr(service_manager, "_trusted_flocks_daemon_owner", lambda pid, *, root: True)
    monkeypatch.setattr(service_manager, "_process_environment", _fake_process_env({4242: {"FLOCKS_ROOT": str(tmp_path / "b")}}))
    terminated: list[int] = []
    monkeypatch.setattr(service_manager, "_terminate_orphan_pid", lambda pid, label, console: terminated.append(pid))

    class _Console:
        def print(self, *_args, **_kwargs) -> None:
            return None

    cleaned = service_manager.cleanup_trusted_daemon_processes(console=_Console(), root=tmp_path / "install", paths=paths_a)

    assert cleaned == []
    assert terminated == []


def test_no_pid_record_follows_the_root_marker_on_the_daemon_command_line(monkeypatch, tmp_path: Path) -> None:
    paths_a = make_runtime_paths(tmp_path / "root-a")
    paths_b = make_runtime_paths(tmp_path / "root-b")
    paths_a.run_dir.mkdir(parents=True)
    marker_a = service_manager.daemon_runtime_root_marker(paths_a)
    marker_b = service_manager.daemon_runtime_root_marker(paths_b)
    command_lines = {
        111: f"/install/.venv/bin/flocks service-daemon {marker_a} --server-port 5391",
        222: f"/install/.venv/bin/flocks service-daemon {marker_b} --server-port 5392",
        333: "/install/.venv/bin/flocks service-daemon --server-port 8000",  # from before the marker
    }
    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "_process_list_pids", lambda: [111, 222, 333])
    monkeypatch.setattr(service_manager, "_trusted_flocks_daemon_owner", lambda pid, *, root: True)
    monkeypatch.setattr(service_manager, "_process_command_line", lambda pid: command_lines[pid])
    # Root B's daemon even claims root A in its environment: the marker decides.
    monkeypatch.setattr(
        service_manager,
        "_process_environment",
        _fake_process_env({222: {"FLOCKS_ROOT": str(paths_a.root)}, 333: {"FLOCKS_ROOT": str(paths_a.root)}}),
    )

    assert marker_a != marker_b
    assert service_manager.trusted_daemon_process_pids(root=tmp_path / "install", paths=paths_a) == [111, 333]


def test_default_root_is_the_daemon_processes_own_home(monkeypatch, tmp_path: Path) -> None:
    """Instances kept apart by HOME (no FLOCKS_ROOT) each have their own default root."""
    monkeypatch.setenv("HOME", str(tmp_path / "home-b"))
    paths_b = make_runtime_paths(tmp_path / "home-b" / ".flocks")
    paths_b.run_dir.mkdir(parents=True)
    monkeypatch.setattr(service_manager.sys, "platform", "darwin")
    monkeypatch.setattr(service_manager, "_process_list_pids", lambda: [111, 222])
    monkeypatch.setattr(service_manager, "_trusted_flocks_daemon_owner", lambda pid, *, root: True)
    monkeypatch.setattr(service_manager, "_process_command_line", lambda pid: "flocks service-daemon --server-port 8000")
    monkeypatch.setattr(
        service_manager,
        "_process_environment",
        _fake_process_env({111: {"HOME": str(tmp_path / "home-a")}, 222: {"HOME": str(tmp_path / "home-b")}}),
    )

    assert service_manager.trusted_daemon_process_pids(root=tmp_path / "install", paths=paths_b) == [222]


def test_windows_without_record_never_claims_another_roots_daemon(monkeypatch, tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path / "root-a")
    paths.run_dir.mkdir(parents=True)
    queries: list[dict[str, str]] = []

    def fake_run(command, **kwargs):
        queries.append(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="111 own\n222 other\n333 legacy\n")

    monkeypatch.setattr(service_manager.sys, "platform", "win32")
    monkeypatch.setattr(service_manager, "which", lambda name: "powershell.exe" if name == "powershell" else None)
    monkeypatch.setattr(service_manager.subprocess, "run", fake_run)
    monkeypatch.setattr(service_manager, "_legacy_control_port_daemon_pid", lambda _paths: None)

    # Detection still lists the daemon from before the marker; cleanup only kills what is proven ours.
    assert service_manager.trusted_daemon_process_pids(root=tmp_path / "install", paths=paths) == [111, 333]
    assert service_manager.trusted_daemon_process_pids(
        root=tmp_path / "install", paths=paths, confirmed_only=True
    ) == [111]
    assert queries[-1]["FLOCKS_DAEMON_RUNTIME_MARKER"] == service_manager.daemon_runtime_root_marker(paths)

    # With a pid record the record decides.
    service_supervisor.supervisor_pid_path(paths).write_text("222\n", encoding="utf-8")
    monkeypatch.setattr(service_manager, "pid_is_running", lambda pid: True)
    assert service_manager.trusted_daemon_process_pids(root=tmp_path / "install", paths=paths) == [222]


def test_windows_cleanup_of_a_new_root_spares_an_old_daemon_of_another_root(monkeypatch, tmp_path: Path) -> None:
    """Old daemon A (no root marker) on the shared port; new root B starts: A must survive B's cleanup."""
    from flocks.cli import service_control

    legacy_port = _free_port()
    monkeypatch.delattr(service_control.socket, "AF_UNIX", raising=False)
    monkeypatch.setattr(service_control, "SUPERVISOR_CONTROL_PORT", legacy_port)
    monkeypatch.setenv("FLOCKS_CONTROL_PORT", str(legacy_port))
    paths_a = make_runtime_paths(tmp_path / "a")
    paths_b = make_runtime_paths(tmp_path / "b")
    for paths in (paths_a, paths_b):
        paths.run_dir.mkdir(parents=True)
        paths.log_dir.mkdir(parents=True)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths_a)
    daemon_a = service_supervisor.SupervisorDaemon(service_manager.ServiceConfig(), backend_adapter=SleeperProcessAdapter())
    daemon_a._start_control_server()
    monkeypatch.delenv("FLOCKS_CONTROL_PORT")
    daemon_pid = os.getpid()  # the control server answers from this process
    killed: list[int] = []
    monkeypatch.setattr(service_manager.sys, "platform", "win32")
    monkeypatch.setattr(service_manager, "which", lambda name: "powershell.exe" if name == "powershell" else None)
    monkeypatch.setattr(
        service_manager.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=f"{daemon_pid} legacy\n"),
    )
    monkeypatch.setattr(service_manager, "_terminate_orphan_pid", lambda pid, *_args, **_kwargs: killed.append(pid))

    class _Console:
        def print(self, *_args, **_kwargs) -> None:
            return None

    try:
        cleaned_b = service_manager.cleanup_trusted_daemon_processes(console=_Console(), root=tmp_path / "install", paths=paths_b)
        cleaned_a = service_manager.cleanup_trusted_daemon_processes(console=_Console(), root=tmp_path / "install", paths=paths_a)
    finally:
        daemon_a._stop_control_server()

    assert cleaned_b == []
    assert cleaned_a == [daemon_pid]
    assert killed == [daemon_pid]


def test_daemon_command_carries_the_marker_of_its_runtime_root(monkeypatch, tmp_path: Path) -> None:
    short_root = make_short_runtime_root("flocks-marker-")  # the control socket path must fit
    try:
        paths = make_runtime_paths(short_root)
        paths.run_dir.mkdir(parents=True)
        paths.log_dir.mkdir(parents=True)
        spawned: list[list[str]] = []
        monkeypatch.setattr(service_manager, "ensure_install_layout", lambda: tmp_path)
        monkeypatch.setattr(service_manager, "resolve_flocks_cli_command", lambda _root: ["flocks"])
        monkeypatch.setattr(service_manager, "_spawn_process", lambda command, **_kwargs: spawned.append(command) or object())

        service_manager._start_supervisor_process(service_manager.ServiceConfig(), paths, None)

        marker = service_manager.daemon_runtime_root_marker(paths)
        assert spawned[0][1:3] == ["service-daemon", marker]
        assert marker == service_manager.daemon_runtime_root_marker(make_runtime_paths(short_root))
        assert marker != service_manager.daemon_runtime_root_marker(make_runtime_paths(tmp_path / "other"))
    finally:
        shutil.rmtree(short_root, ignore_errors=True)


def test_service_daemon_command_accepts_the_root_marker(monkeypatch) -> None:
    from typer.testing import CliRunner

    import flocks.cli.main as cli_main

    configs: list[object] = []
    monkeypatch.setattr(cli_main, "run_service_daemon", lambda config: configs.append(config))

    result = CliRunner().invoke(
        cli_main.app,
        ["service-daemon", "--runtime-root-id=0123456789abcdef", "--server-port", "5391", "--webui-port", "5391"],
    )

    assert result.exit_code == 0, result.output
    assert len(configs) == 1 and configs[0].backend_port == 5391


def test_failed_start_keeps_degraded_state_on_following_ticks(monkeypatch, tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)

    class FailingAdapter(SleeperProcessAdapter):
        def start(self, _config, _paths, *, built_once: bool = False):
            raise service_manager.ServiceError("server 端口 9995 已被占用 (PID: 1)")

    daemon = service_supervisor.SupervisorDaemon(
        service_manager.ServiceConfig(backend_port=9995, frontend_port=9995),
        interval=0.01,
        backend_adapter=FailingAdapter(),
    )
    daemon.paths = paths
    daemon.restart_all(reason="startup")
    assert daemon.backend.state == "degraded"

    daemon.tick()  # backoff slot not reached: no new start attempt
    daemon.tick()

    assert daemon.backend.state == "degraded"
    assert daemon.backend.last_error == "server 端口 9995 已被占用 (PID: 1)"
    assert daemon.status_payload()["backend"]["state"] == "degraded"
    assert daemon.status_payload()["webui"]["state"] == "degraded"


def test_explicit_stop_still_reads_as_stopped(monkeypatch, tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    adapter = SleeperProcessAdapter()
    daemon = service_supervisor.SupervisorDaemon(
        service_manager.ServiceConfig(backend_port=9995, frontend_port=9995),
        interval=0.01,
        backend_adapter=adapter,
    )
    daemon.paths = paths
    daemon.restart_all(reason="startup")
    assert daemon.backend.state == "healthy"

    daemon.shutdown_children()
    daemon._probe_backend_locked()

    assert daemon.backend.state == "stopped"


def test_daemon_log_keeps_localized_text_readable_on_utf8_stdout(monkeypatch, capsys) -> None:
    import io

    class _Utf8Stdout(io.StringIO):
        encoding = "utf-8"

    stream = _Utf8Stdout()
    monkeypatch.setattr(service_supervisor.sys, "stdout", stream)
    service_supervisor._daemon_log("service_start_failed", {"error": "后端服务 启动超时"})
    assert "后端服务 启动超时" in stream.getvalue()

    class _CpStdout(io.StringIO):
        encoding = "cp936"

    stream = _CpStdout()
    monkeypatch.setattr(service_supervisor.sys, "stdout", stream)
    service_supervisor._daemon_log("service_start_failed", {"error": "后端服务"})
    assert "\\u540e\\u7aef" in stream.getvalue()
