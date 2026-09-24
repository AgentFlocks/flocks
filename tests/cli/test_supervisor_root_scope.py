"""Regression tests for two supervisor findings from the 2026-09 full test pass.

- ``flocks stop`` / ``flocks status`` of one runtime root (``FLOCKS_ROOT``) must never
  claim (and kill) the daemon of another root that shares the same install: without
  a pid record only daemons whose environment names this root are candidates.
- A backend whose start failed (or whose restart is waiting for its backoff slot)
  must keep ``state=degraded`` / ``restarting`` on later ticks instead of flipping to
  ``stopped``; the CLI reported ``flocks state=stopped`` for a port clash.
"""

from __future__ import annotations

from pathlib import Path

from flocks.cli import service_manager, service_supervisor
from tests.helpers.service_supervisor import SleeperProcessAdapter, make_runtime_paths


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
