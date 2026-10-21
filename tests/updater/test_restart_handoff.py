import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.cli import service_manager
from flocks.cli.service_config import service_config_payload
from flocks.updater import restart_handoff
from tests.helpers.service_supervisor import (
    make_short_runtime_root,
    start_supervisor,
    stop_supervisor,
    wait_for_supervisor,
)


def _handoff_args(tmp_path: Path, restart_argv: list[str]) -> list[str]:
    args = [
        "--parent-pid",
        "1234",
        "--backend-host",
        "127.0.0.1",
        "--backend-port",
        "8000",
        "--frontend-host",
        "127.0.0.1",
        "--frontend-port",
        "5173",
        "--install-root",
        str(tmp_path),
        "--uv-path",
        "uv",
        "--sync-timeout",
        "300",
        "--version",
        "2026.4.1",
        "--current-version",
        "2026.3.31",
    ]
    return [*args, "--", *restart_argv]


def _simple_upgrade_handoff_args(tmp_path: Path) -> list[str]:
    content_root = tmp_path / "staged"
    content_root.mkdir()
    backup_path = tmp_path / "backup.tar.gz"
    backup_path.write_text("backup", encoding="utf-8")
    config = service_manager.ServiceConfig(
        backend_host="10.0.0.8",
        backend_port=5273,
        frontend_host="10.0.0.8",
        frontend_port=5273,
        legacy_backend_host="0.0.0.0",
        legacy_backend_port=9000,
        no_browser=True,
        skip_frontend_build=True,
    )
    return [
        "--mode",
        "upgrade",
        "--parent-pid",
        "1234",
        "--backend-host",
        config.backend_host,
        "--backend-port",
        str(config.backend_port),
        "--frontend-host",
        config.frontend_host,
        "--frontend-port",
        str(config.frontend_port),
        "--install-root",
        str(tmp_path / "install"),
        "--content-root",
        str(content_root),
        "--backup-path",
        str(backup_path),
        "--was-running",
        "--daemon-pid",
        "2468",
        "--service-config-json",
        json.dumps(service_config_payload(config)),
        "--uv-path",
        "uv",
        "--sync-timeout",
        "300",
        "--version",
        "2026.4.1",
        "--current-version",
        "2026.3.31",
        "--",
        "/install/.venv/bin/python",
    ]


def _v2026_7_1_handoff_args(tmp_path: Path, restart_argv: list[str]) -> list[str]:
    """Build the handoff protocol emitted by the v2026.7.1 updater."""
    args = _handoff_args(tmp_path, restart_argv)
    args[args.index("--install-root") : args.index("--install-root")] = [
        "--backend-pid-file",
        str(tmp_path / "backend.pid"),
    ]
    separator_index = args.index("--")
    args[separator_index:separator_index] = [
        "--backup-path",
        str(tmp_path / "backup.tar.gz"),
    ]
    return args


def _v2026_7_15_handoff_args(tmp_path: Path, restart_argv: list[str]) -> list[str]:
    """Build the handoff protocol emitted by the v2026.7.15 updater."""
    args = _handoff_args(tmp_path, restart_argv)
    args[args.index("--backend-port") + 1] = "5173"
    separator_index = args.index("--")
    args[separator_index:separator_index] = [
        "--backup-path",
        str(tmp_path / "backup.tar.gz"),
        "--prepare-handover",
    ]
    return args


def test_current_version_upgrade_handoff_stops_replaces_installs_and_restarts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    args = _simple_upgrade_handoff_args(tmp_path)
    args[args.index("--") : args.index("--")] = ["--cleanup-dir", str(cleanup_dir)]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)
    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_parent_exit",
        lambda parent_pid: events.append(f"wait-parent:{parent_pid}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_stop_services_before_upgrade",
        lambda args: events.append(f"stop:{args.daemon_pid}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_apply_new_source",
        lambda args: events.append("replace"),
    )
    monkeypatch.setattr(
        restart_handoff,
        "_run_upgrade_tasks",
        lambda args: events.append("install") or None,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_start_service_after_upgrade",
        lambda args: events.append("start") or (True, "", ""),
    )
    monkeypatch.setattr(restart_handoff, "_write_upgrade_result", lambda **_kwargs: None)

    assert restart_handoff.run(args) == 0
    assert events == [
        "wait-parent:1234",
        "stop:2468",
        "replace",
        "install",
        "start",
    ]
    assert not cleanup_dir.exists()


def test_current_version_upgrade_handoff_can_run_without_waiting_for_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    args = _simple_upgrade_handoff_args(tmp_path)
    parent_index = args.index("--parent-pid")
    del args[parent_index : parent_index + 2]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)
    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_parent_exit",
        lambda _parent_pid: pytest.fail("parent wait must be skipped"),
    )
    monkeypatch.setattr(
        restart_handoff,
        "_stop_services_before_upgrade",
        lambda _args: events.append("stop") or True,
    )
    monkeypatch.setattr(restart_handoff, "_apply_new_source", lambda _args: events.append("replace"))
    monkeypatch.setattr(
        restart_handoff,
        "_run_upgrade_tasks",
        lambda _args: events.append("install") or None,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_start_service_after_upgrade",
        lambda _args: events.append("start") or (True, "", ""),
    )
    monkeypatch.setattr(restart_handoff, "_write_upgrade_result", lambda **_kwargs: None)

    assert restart_handoff.run(args) == 0
    assert events == ["stop", "replace", "install", "start"]


@pytest.mark.parametrize(
    ("host", "port"),
    [
        ("127.0.0.1", 5173),
        ("0.0.0.0", 5273),
        ("10.20.30.40", 9527),
        ("::", 6173),
        ("2001:db8::20", 7173),
    ],
)
def test_upgrade_start_argv_uses_captured_host_port_without_control_api(host: str, port: int) -> None:
    config = service_manager.ServiceConfig(
        backend_host=host,
        backend_port=port,
        frontend_host=host,
        frontend_port=port,
        legacy_backend_host="0.0.0.0",
        legacy_backend_port=9000,
        no_browser=True,
        skip_frontend_build=True,
    )
    args = SimpleNamespace(
        restart_argv=["/install/.venv/bin/python"],
        service_config_json=json.dumps(service_config_payload(config)),
    )

    assert restart_handoff._build_captured_start_argv(args) == [
        "/install/.venv/bin/python",
        "-m",
        "flocks.cli.main",
        "start",
        "--host",
        host,
        "--port",
        str(port),
        "--no-browser",
        "--skip-webui-build",
        "--server-host",
        "0.0.0.0",
        "--server-port",
        "9000",
    ]


@pytest.mark.parametrize(
    ("public_host", "public_port"),
    [
        ("0.0.0.0", 5173),
        ("10.20.30.40", 9527),
        ("::", 6173),
        ("2001:db8::20", 7173),
    ],
)
def test_upgrade_start_argv_preserves_distinct_legacy_public_endpoint(
    public_host: str,
    public_port: int,
) -> None:
    config = service_manager.ServiceConfig(
        backend_host="127.0.0.1",
        backend_port=8000,
        frontend_host=public_host,
        frontend_port=public_port,
        legacy_backend_host="127.0.0.1",
        legacy_backend_port=8000,
        no_browser=True,
        skip_frontend_build=True,
    )
    args = SimpleNamespace(
        restart_argv=["/install/.venv/bin/python"],
        service_config_json=json.dumps(service_config_payload(config)),
    )

    assert restart_handoff._build_captured_start_argv(args) == [
        "/install/.venv/bin/python",
        "-m",
        "flocks.cli.main",
        "start",
        "--host",
        public_host,
        "--port",
        str(public_port),
        "--no-browser",
        "--skip-webui-build",
        "--server-host",
        "127.0.0.1",
        "--server-port",
        "8000",
    ]


def test_upgrade_wait_ports_exclude_legacy_cleanup_port(tmp_path: Path) -> None:
    args = restart_handoff._parse_args(_simple_upgrade_handoff_args(tmp_path))

    assert restart_handoff._service_ports(args) == (5273,)


def test_upgrade_stop_forces_captured_daemon_after_graceful_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    args = restart_handoff._parse_args(_simple_upgrade_handoff_args(tmp_path))

    monkeypatch.setattr(
        restart_handoff.service_manager,
        "stop_all",
        lambda _console: (_ for _ in ()).throw(
            service_manager.ServiceError("daemon did not exit"),
        ),
    )
    monkeypatch.setattr(
        restart_handoff,
        "_stop_supervisor_before_restart",
        lambda **kwargs: events.append(f"stop:{kwargs}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_record_handoff_log",
        lambda message: events.append(f"log:{message}"),
    )

    assert restart_handoff._stop_services_before_upgrade(args)
    assert events == [
        "log:service_graceful_stop_failed error=daemon did not exit",
        (
            "stop:{'daemon_pid': 2468, 'backend_port': 5273, "
            "'service_ports': (5273,), 'force_daemon_stop': True, "
            "'timeout_seconds': 5.0}"
        ),
    ]


def test_upgrade_install_failure_keeps_backup_and_temp_without_restart_or_rollback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    results: list[dict[str, object]] = []
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    backup_path = tmp_path / "backup.tar.gz"
    backup_path.write_text("backup", encoding="utf-8")
    args = _simple_upgrade_handoff_args(tmp_path)
    args[args.index("--") : args.index("--")] = ["--cleanup-dir", str(cleanup_dir)]

    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda _pid: True)
    monkeypatch.setattr(restart_handoff, "_stop_services_before_upgrade", lambda _args: True)
    monkeypatch.setattr(
        restart_handoff,
        "_apply_new_source",
        lambda _args: events.append("replace"),
    )
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda _args: "npm build failed")
    monkeypatch.setattr(
        restart_handoff,
        "_start_service_after_upgrade",
        lambda _args: events.append("start") or (True, "", ""),
    )
    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(message))
    monkeypatch.setattr(restart_handoff, "_write_upgrade_result", lambda **kwargs: results.append(kwargs))

    assert restart_handoff.run(args) == 1
    assert events[0] == "replace"
    assert "start" not in events
    assert not any("rollback" in event for event in events)
    assert cleanup_dir.exists()
    assert backup_path.exists()
    assert results[-1]["failed_stage"] == "install"
    assert results[-1]["backup_path"] == backup_path


def test_upgrade_start_failure_records_process_output_and_keeps_temp(
    monkeypatch,
    tmp_path: Path,
) -> None:
    results: list[dict[str, object]] = []
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    backup_path = tmp_path / "backup.tar.gz"
    backup_path.write_text("backup", encoding="utf-8")
    args = _simple_upgrade_handoff_args(tmp_path)
    args[args.index("--") : args.index("--")] = ["--cleanup-dir", str(cleanup_dir)]

    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda _pid: True)
    monkeypatch.setattr(restart_handoff, "_stop_services_before_upgrade", lambda _args: True)
    monkeypatch.setattr(restart_handoff, "_apply_new_source", lambda _args: None)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda _args: None)
    monkeypatch.setattr(restart_handoff, "_report_pending_pro_bundle_install_receipt", lambda _args: None)
    monkeypatch.setattr(
        restart_handoff,
        "_start_service_after_upgrade",
        lambda _args: (False, "start stdout", "start stderr"),
    )
    monkeypatch.setattr(restart_handoff, "_write_upgrade_result", lambda **kwargs: results.append(kwargs))

    assert restart_handoff.run(args) == 1
    assert cleanup_dir.exists()
    assert results[-1]["failed_stage"] == "start"
    assert results[-1]["stdout"] == "start stdout"
    assert results[-1]["stderr"] == "start stderr"


def test_upgrade_writes_running_after_parent_exits(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    args = _simple_upgrade_handoff_args(tmp_path)

    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_parent_exit",
        lambda _pid: events.append("parent-exited") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_write_upgrade_result",
        lambda **kwargs: events.append(f"write:{kwargs['phase']}"),
    )
    monkeypatch.setattr(
        restart_handoff,
        "_stop_services_before_upgrade",
        lambda _args: events.append("stop") or False,
    )
    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)

    assert restart_handoff.run(args) == 1
    assert events[:3] == ["parent-exited", "write:running", "stop"]


def test_upgrade_result_write_failure_is_non_fatal(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    args = restart_handoff._parse_args(_simple_upgrade_handoff_args(tmp_path))

    monkeypatch.setattr(
        restart_handoff.updater_module,
        "_write_upgrade_result_state",
        lambda _payload: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    monkeypatch.setattr(
        restart_handoff,
        "_record_handoff_log",
        lambda message: events.append(message),
    )

    restart_handoff._write_upgrade_result(args=args, phase="running")

    assert events == ["upgrade_result_write_failed phase=running error=disk unavailable"]


def test_upgrade_that_was_stopped_does_not_start_service(monkeypatch, tmp_path: Path) -> None:
    args = _simple_upgrade_handoff_args(tmp_path)
    args.remove("--was-running")
    parsed = restart_handoff._parse_args(args)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("stopped service must remain stopped"),
    )

    assert restart_handoff._start_service_after_upgrade(parsed) == (True, "", "")


def test_upgrade_start_decodes_invalid_windows_output_without_crashing(monkeypatch, tmp_path: Path) -> None:
    parsed = restart_handoff._parse_args(_simple_upgrade_handoff_args(tmp_path))
    logs: list[str] = []

    def fake_run(command, **kwargs):
        assert kwargs["text"] is False
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=b"start stdout \x80",
            stderr=b"start stderr \x81",
        )

    monkeypatch.setattr(restart_handoff.subprocess, "run", fake_run)
    monkeypatch.setattr(restart_handoff, "_record_handoff_log", logs.append)

    assert restart_handoff._start_service_after_upgrade(parsed) == (
        False,
        "start stdout �",
        "start stderr �",
    )
    assert logs == ["restart_failed returncode=1 stdout=start stdout � stderr=start stderr �"]


def test_run_waits_for_parent_and_backend_port_before_spawning(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "serve", "--host", "127.0.0.1", "--port", "8000"]
    expected_restart_argv = [
        "python.exe",
        "-m",
        "flocks.cli.main",
        "start",
        "--no-browser",
        "--skip-webui-build",
        "--host",
        "127.0.0.1",
        "--port",
        "5173",
        "--server-host",
        "127.0.0.1",
        "--server-port",
        "8000",
    ]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_parent_exit",
        lambda parent_pid: events.append(f"wait-parent:{parent_pid}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_ensure_backend_port_free",
        lambda backend_port: events.append(f"free-port:{backend_port}") or True,
    )
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda argv, cwd=None, close_fds=False: (
            events.append(f"spawn:{list(argv)}:{cwd}:{close_fds}") or SimpleNamespace(pid=4321)
        ),
    )
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: events.append("tasks") or None)
    monkeypatch.setattr(
        restart_handoff,
        "_stop_supervisor_before_restart",
        lambda: events.append("stop-supervisor") or True,
    )

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv))

    assert code == 0
    assert events == [
        f"log:legacy_serve_restart_migrated argv={expected_restart_argv}",
        "log:started parent_pid=1234 backend=127.0.0.1:8000 frontend=127.0.0.1:5173",
        "wait-parent:1234",
        "free-port:8000",
        "tasks",
        "stop-supervisor",
        f"spawn:{expected_restart_argv}:{tmp_path}:True",
        "log:restart_spawned pid=4321",
    ]


def test_run_keeps_current_start_restart_argv(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    restart_argv = [
        "python.exe",
        "-m",
        "flocks.cli.main",
        "start",
        "--no-browser",
        "--skip-webui-build",
        "--host",
        "127.0.0.1",
        "--port",
        "5173",
    ]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda backend_port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: None)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda: True)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda argv, cwd=None, close_fds=False: (
            events.append(f"spawn:{list(argv)}:{cwd}:{close_fds}") or SimpleNamespace(pid=4321)
        ),
    )

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv))

    assert code == 0
    assert f"spawn:{restart_argv}:{tmp_path}:True" in events


def test_run_accepts_legacy_backend_pid_file_argument(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "start"]
    args = _handoff_args(tmp_path, restart_argv)
    args[args.index("--install-root") : args.index("--install-root")] = [
        "--backend-pid-file",
        str(tmp_path / "backend.pid"),
    ]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda backend_port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: None)
    monkeypatch.setattr(restart_handoff, "_cleanup_legacy_upgrade_handover", lambda args: True)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda: True)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda argv, cwd=None, close_fds=False: (
            events.append(f"spawn:{list(argv)}:{cwd}:{close_fds}") or SimpleNamespace(pid=4321)
        ),
    )

    code = restart_handoff.run(args)

    assert code == 0
    assert f"spawn:{restart_argv}:{tmp_path}:True" in events


def test_v2026_7_1_upgrade_handoff_runs_tasks_and_restarts(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    flocks_root = tmp_path / "flocks-root"
    upgrade_state = flocks_root / "run" / "upgrade-state.json"
    upgrade_state.parent.mkdir(parents=True)
    upgrade_state.write_text(
        json.dumps(
            {
                "backend_host": "127.0.0.1",
                "backend_port": 8000,
                "frontend_host": "0.0.0.0",
                "frontend_port": 8888,
            }
        ),
        encoding="utf-8",
    )
    restart_argv = [
        "python.exe",
        "-m",
        "flocks.cli.main",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    expected_restart_argv = [
        "python.exe",
        "-m",
        "flocks.cli.main",
        "start",
        "--no-browser",
        "--skip-webui-build",
        "--host",
        "0.0.0.0",
        "--port",
        "8888",
        "--server-host",
        "127.0.0.1",
        "--server-port",
        "8000",
    ]
    args = _v2026_7_1_handoff_args(tmp_path, restart_argv)

    monkeypatch.setattr(restart_handoff.updater_module, "_flocks_root", lambda: flocks_root)
    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)
    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_parent_exit",
        lambda parent_pid: events.append(f"wait-parent:{parent_pid}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_ensure_backend_port_free",
        lambda backend_port: events.append(f"free-port:{backend_port}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_run_upgrade_tasks",
        lambda _args: events.append("install") or None,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_cleanup_legacy_upgrade_handover",
        lambda _args: events.append("cleanup-handover") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_stop_supervisor_before_restart",
        lambda: events.append("stop-supervisor") or True,
    )
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda argv, cwd=None, close_fds=False: (
            events.append(f"spawn:{list(argv)}:{cwd}:{close_fds}") or SimpleNamespace(pid=4321)
        ),
    )

    assert restart_handoff.run(args) == 0
    assert events == [
        "wait-parent:1234",
        "free-port:8000",
        "install",
        "cleanup-handover",
        "stop-supervisor",
        f"spawn:{expected_restart_argv}:{tmp_path}:True",
    ]


def test_v2026_7_15_upgrade_handoff_pauses_before_tasks_and_restarts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    restart_argv = [
        "python.exe",
        "-m",
        "flocks.cli.main",
        "start",
        "--no-browser",
        "--skip-webui-build",
        "--host",
        "0.0.0.0",
        "--port",
        "5173",
        "--server-host",
        "127.0.0.1",
        "--server-port",
        "8000",
    ]
    args = _v2026_7_15_handoff_args(tmp_path, restart_argv)
    args[args.index("--backend-host") + 1] = "0.0.0.0"
    args[args.index("--frontend-host") + 1] = "0.0.0.0"

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)
    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_parent_exit",
        lambda parent_pid: events.append(f"wait-parent:{parent_pid}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_prepare_legacy_supervisor_for_upgrade",
        lambda **kwargs: events.append(f"prepare-supervisor:{kwargs}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_stop_supervisor_before_restart",
        lambda **kwargs: events.append(f"stop-supervisor:{kwargs}") or True,
    )
    monkeypatch.setattr(restart_handoff, "_legacy_supervisor_pid", lambda _args: 2468)
    monkeypatch.setattr(
        restart_handoff,
        "_run_upgrade_tasks",
        lambda _args: events.append("install") or None,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_cleanup_legacy_upgrade_handover",
        lambda _args: events.append("cleanup-handover") or True,
    )
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda argv, cwd=None, close_fds=False: (
            events.append(f"spawn:{list(argv)}:{cwd}:{close_fds}") or SimpleNamespace(pid=4321)
        ),
    )

    assert restart_handoff.run(args) == 0
    assert events == [
        "wait-parent:1234",
        ("prepare-supervisor:{'daemon_pid': 2468, 'service_ports': (5173,), 'timeout_seconds': 300.0}"),
        "install",
        "cleanup-handover",
        (
            "stop-supervisor:{'daemon_pid': 2468, 'backend_port': 5173, "
            "'service_ports': (5173,), 'force_daemon_stop': True}"
        ),
        f"spawn:{restart_argv}:{tmp_path}:True",
    ]


def test_v2026_7_15_upgrade_handoff_force_stops_after_pause_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "start"]
    args = _v2026_7_15_handoff_args(tmp_path, restart_argv)

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_parent_exit",
        lambda parent_pid: events.append(f"wait-parent:{parent_pid}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_prepare_legacy_supervisor_for_upgrade",
        lambda **_kwargs: events.append("prepare-supervisor") or False,
    )
    monkeypatch.setattr(restart_handoff, "_legacy_supervisor_pid", lambda _args: 2468)
    monkeypatch.setattr(
        restart_handoff,
        "_stop_supervisor_before_restart",
        lambda **kwargs: events.append(f"stop-supervisor:{kwargs}") or True,
    )
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda _args: events.append("install") or None)
    monkeypatch.setattr(restart_handoff, "_cleanup_legacy_upgrade_handover", lambda _args: True)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda _argv, **_kwargs: events.append("spawn") or SimpleNamespace(pid=4321),
    )

    assert restart_handoff.run(args) == 0
    assert events == [
        "log:started parent_pid=1234 backend=127.0.0.1:5173 frontend=127.0.0.1:5173",
        "wait-parent:1234",
        "prepare-supervisor",
        "log:legacy_handover_pause_timeout",
        (
            "stop-supervisor:{'daemon_pid': 2468, 'backend_port': 5173, "
            "'service_ports': (5173,), 'force_daemon_stop': True}"
        ),
        "install",
        "spawn",
        "log:restart_spawned pid=4321",
    ]


def test_cleanup_legacy_upgrade_handover_stops_trusted_page(monkeypatch, tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    page_dir = run_dir / "upgrade-page"
    page_dir.mkdir(parents=True)
    state_path = run_dir / "upgrade-state.json"
    state_path.write_text("{}", encoding="utf-8")
    pid_path = run_dir / "upgrade_server.pid"
    pid_path.write_text("2468", encoding="utf-8")
    alive = {2468}

    monkeypatch.setattr(restart_handoff.updater_module, "_flocks_root", lambda: tmp_path)
    monkeypatch.setattr(
        restart_handoff.service_manager,
        "port_owner_pids",
        lambda _port: sorted(alive),
    )
    monkeypatch.setattr(
        restart_handoff.service_manager,
        "_process_command_line",
        lambda pid: f"python -m http.server --directory {page_dir}" if pid in alive else "",
    )
    monkeypatch.setattr(
        restart_handoff.service_manager,
        "_terminate_orphan_pid",
        lambda pid, _label, _console: alive.discard(pid),
    )

    assert restart_handoff._cleanup_legacy_upgrade_handover(SimpleNamespace(frontend_port=5173))
    assert not state_path.exists()
    assert not pid_path.exists()
    assert not page_dir.exists()


def test_restart_only_waits_for_port_after_parent_exit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "start"]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_parent_exit",
        lambda parent_pid: events.append(f"wait-parent:{parent_pid}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_ensure_backend_port_free",
        lambda backend_port: events.append(f"free-port:{backend_port}") or True,
    )
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: events.append("tasks") or None)
    monkeypatch.setattr(
        restart_handoff,
        "_stop_supervisor_before_restart",
        lambda: events.append("stop-supervisor") or True,
    )
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda argv, cwd=None, close_fds=False: (
            events.append(f"spawn:{list(argv)}:{cwd}:{close_fds}") or SimpleNamespace(pid=4321)
        ),
    )

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv))

    assert code == 0
    assert events[1:] == [
        "wait-parent:1234",
        "free-port:8000",
        "tasks",
        "stop-supervisor",
        f"spawn:{restart_argv}:{tmp_path}:True",
        "log:restart_spawned pid=4321",
    ]


def test_run_reports_pending_install_receipt_after_pro_bundle_tasks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "serve"]
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    args = _handoff_args(tmp_path, restart_argv)
    separator_index = args.index("--")
    args[separator_index:separator_index] = ["--pro-bundle-manifest-path", str(manifest)]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda **_kwargs: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda backend_port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: events.append("tasks") or None)
    monkeypatch.setattr(
        restart_handoff,
        "_report_pending_pro_bundle_install_receipt",
        lambda args: events.append("receipt"),
    )
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda argv, cwd=None, close_fds=False: events.append(f"spawn:{list(argv)}") or SimpleNamespace(pid=4321),
    )
    code = restart_handoff.run(args)

    assert code == 0
    assert events.index("tasks") < events.index("receipt")
    assert any(event.startswith("spawn:") for event in events)


def test_pro_bundle_handoff_stops_supervisor_before_port_check_and_install(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "start"]
    pro_wheel = tmp_path / "flockspro.whl"
    manifest = tmp_path / "manifest.json"
    args = _handoff_args(tmp_path, restart_argv)
    separator_index = args.index("--")
    args[separator_index:separator_index] = [
        "--pro-wheel-path",
        str(pro_wheel),
        "--pro-bundle-manifest-path",
        str(manifest),
    ]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)
    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_parent_exit",
        lambda parent_pid: events.append(f"wait-parent:{parent_pid}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_stop_supervisor_before_restart",
        lambda **kwargs: events.append(f"stop-supervisor:{kwargs}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_ensure_backend_port_free",
        lambda backend_port: events.append(f"free-port:{backend_port}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_run_upgrade_tasks",
        lambda _args: events.append("install") or None,
    )
    monkeypatch.setattr(restart_handoff, "_report_pending_pro_bundle_install_receipt", lambda _args: None)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda _argv, **_kwargs: events.append("spawn") or SimpleNamespace(pid=4321),
    )

    assert restart_handoff.run(args) == 0
    assert events == [
        "wait-parent:1234",
        "stop-supervisor:{'backend_port': 8000, 'service_ports': (5173,)}",
        "free-port:8000",
        "install",
        "spawn",
    ]


def test_run_does_not_spawn_when_parent_exit_times_out(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: False)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: events.append("spawn"),
    )
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: events.append("tasks") or None)

    code = restart_handoff.run(_handoff_args(tmp_path, ["python.exe", "-m", "flocks.cli.main", "start"]))

    assert code == 1
    assert events == [
        "log:started parent_pid=1234 backend=127.0.0.1:8000 frontend=127.0.0.1:5173",
        "log:parent_exit_timeout parent_pid=1234",
    ]


def test_run_does_not_spawn_when_upgrade_tasks_fail(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "serve"]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda backend_port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: "sync failed")
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: events.append("spawn"),
    )

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv))

    assert code == 1
    assert "log:upgrade_tasks_failed error=sync failed" in events
    assert "spawn" not in events


def test_run_logs_and_cleans_up_when_upgrade_tasks_crash(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "serve"]

    def crash(_args):
        raise RuntimeError("boom")

    args = _handoff_args(tmp_path, restart_argv)
    separator_index = args.index("--")
    args[separator_index:separator_index] = ["--cleanup-dir", str(cleanup_dir)]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda backend_port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", crash)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: events.append("spawn"),
    )

    code = restart_handoff.run(args)

    assert code == 1
    assert "log:upgrade_tasks_failed error=upgrade tasks crashed: boom" in events
    assert not cleanup_dir.exists()
    assert "spawn" not in events


def test_run_does_not_spawn_when_supervisor_stop_fails(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "start"]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda backend_port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: None)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda: False)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: events.append("spawn"),
    )

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv))

    assert code == 1
    assert "log:supervisor_stop_timeout" in events
    assert "spawn" not in events


def test_restart_only_does_not_rollback_when_supervisor_stop_fails(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "start"]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda _port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: None)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda: False)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: events.append("spawn"),
    )

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv))

    assert code == 1
    assert not any("rollback" in event for event in events)
    assert "spawn" not in events


def test_restart_only_does_not_rollback_when_restart_spawn_fails(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "start"]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda _port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: None)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda: True)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv))

    assert code == 1
    assert "log:restart_spawn_failed error=spawn failed" in events
    assert not any("rollback" in event for event in events)


def test_stop_supervisor_requests_stop_when_health_probe_is_temporarily_unavailable(monkeypatch) -> None:
    from flocks.cli import service_control

    events: list[str] = []
    daemon_running = True

    monkeypatch.setattr(service_control, "supervisor_is_running", lambda _paths: False)
    monkeypatch.setattr(
        restart_handoff.service_manager,
        "pid_is_running",
        lambda _pid: daemon_running,
    )
    monkeypatch.setattr(restart_handoff, "_backend_port_in_use", lambda _port: False)

    def request_stop(*, paths, timeout):
        del paths, timeout
        nonlocal daemon_running
        events.append("request-stop")
        daemon_running = False

    monkeypatch.setattr(service_control, "request_stop", request_stop)

    assert restart_handoff._stop_supervisor_before_restart(
        daemon_pid=2468,
        timeout_seconds=0.01,
        poll_interval_seconds=0.001,
    )
    assert events == ["request-stop"]


def test_stop_supervisor_force_stops_after_graceful_timeout(monkeypatch) -> None:
    from flocks.cli import service_control

    events: list[str] = []
    daemon_running = True

    monkeypatch.setattr(service_control, "supervisor_is_running", lambda _paths: daemon_running)
    monkeypatch.setattr(
        restart_handoff.service_manager,
        "pid_is_running",
        lambda _pid: daemon_running,
    )
    monkeypatch.setattr(restart_handoff, "_backend_port_in_use", lambda _port: False)
    monkeypatch.setattr(
        service_control,
        "request_stop",
        lambda **_kwargs: events.append("request-stop"),
    )

    def terminate(pid, _label, _console) -> None:
        nonlocal daemon_running
        events.append(f"terminate:{pid}")
        daemon_running = False

    monkeypatch.setattr(restart_handoff.service_manager, "_terminate_orphan_pid", terminate)
    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))

    assert restart_handoff._stop_supervisor_before_restart(
        daemon_pid=2468,
        backend_port=5173,
        force_daemon_stop=True,
        timeout_seconds=0,
        poll_interval_seconds=0,
    )
    assert events == [
        "request-stop",
        "log:supervisor_force_stop_after_timeout pid=2468",
        "terminate:2468",
    ]


def test_prepare_legacy_supervisor_requests_upgrade_pause(monkeypatch) -> None:
    from flocks.cli import service_control

    events: list[str] = []
    paused_status = SimpleNamespace(
        backend=SimpleNamespace(paused=True),
        webui=SimpleNamespace(paused=True),
    )
    response = SimpleNamespace(json=lambda: {"status": "paused"})

    monkeypatch.setattr(service_control, "supervisor_is_running", lambda _paths: True)
    monkeypatch.setattr(restart_handoff.service_manager, "pid_is_running", lambda _pid: True)
    monkeypatch.setattr(restart_handoff, "_backend_port_in_use", lambda _port: False)
    monkeypatch.setattr(service_control, "parse_supervisor_status", lambda _payload: paused_status)

    def control_api_request(method, path, **kwargs):
        events.append(f"request:{method}:{path}:{kwargs['timeout']}")
        return response

    monkeypatch.setattr(service_control, "control_api_request", control_api_request)

    assert restart_handoff._prepare_legacy_supervisor_for_upgrade(
        daemon_pid=2468,
        service_ports=(5173,),
        timeout_seconds=45,
        poll_interval_seconds=0,
    )
    assert events == ["request:POST:/upgrade/prepare:45"]


def test_prepare_legacy_supervisor_polls_paused_state_after_request_timeout(monkeypatch) -> None:
    from flocks.cli import service_control

    events: list[str] = []
    paused_status = SimpleNamespace(
        backend=SimpleNamespace(paused=True),
        webui=SimpleNamespace(paused=True),
    )

    monkeypatch.setattr(service_control, "supervisor_is_running", lambda _paths: True)
    monkeypatch.setattr(restart_handoff.service_manager, "pid_is_running", lambda _pid: True)
    monkeypatch.setattr(restart_handoff, "_backend_port_in_use", lambda _port: False)
    monkeypatch.setattr(
        service_control,
        "control_api_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("busy building")),
    )
    monkeypatch.setattr(service_control, "read_supervisor_status", lambda **_kwargs: paused_status)
    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(message))

    assert restart_handoff._prepare_legacy_supervisor_for_upgrade(
        daemon_pid=2468,
        service_ports=(5173,),
        timeout_seconds=45,
        poll_interval_seconds=0,
    )
    assert events == ["legacy_handover_prepare_request_failed error=busy building"]


@pytest.mark.skipif(sys.platform == "win32", reason="uses the Unix domain socket control API")
def test_stop_supervisor_before_restart_waits_until_real_control_api_stops(monkeypatch) -> None:
    short_root = make_short_runtime_root("flocks-handoff-")
    monkeypatch.setenv("FLOCKS_ROOT", str(short_root))
    paths = service_manager.runtime_paths()
    daemon, thread = start_supervisor(
        service_manager.ServiceConfig(backend_port=9995, frontend_port=9996),
    )

    try:
        wait_for_supervisor(paths, running=True)

        assert restart_handoff._stop_supervisor_before_restart(timeout_seconds=5.0, poll_interval_seconds=0.05) is True

        wait_for_supervisor(paths, running=False)
        thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        stop_supervisor(daemon, thread)
        shutil.rmtree(short_root, ignore_errors=True)


def test_ensure_backend_port_free_waits_again_after_timeout(monkeypatch) -> None:
    events: list[str] = []
    wait_results = iter([False, True])

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_backend_port_free",
        lambda port, **kwargs: events.append(f"wait:{port}:{kwargs.get('timeout_seconds')}") or next(wait_results),
    )

    assert restart_handoff._ensure_backend_port_free(8000) is True
    assert events == [
        "wait:8000:None",
        "log:backend_port_still_in_use port=8000",
        "wait:8000:20.0",
    ]


class _FakeResponder:
    instances: list["_FakeResponder"] = []

    def __init__(self, progress, host, port, token) -> None:
        self.progress = progress
        self.endpoint = (host, port)
        self.token = token
        self.events: list[str] = []
        _FakeResponder.instances.append(self)

    def start(self) -> bool:
        self.events.append("start")
        return True

    def stop(self) -> None:
        self.events.append("stop")

    def linger_for_failure(self, *, timeout: float, grace: float = 3.0) -> None:
        self.events.append(f"linger:{timeout}")


@pytest.fixture
def fake_responder(monkeypatch):
    _FakeResponder.instances = []
    monkeypatch.setattr(restart_handoff.handoff_progress, "ProgressResponder", _FakeResponder)
    return _FakeResponder


def _statuses(progress: dict) -> dict[str, str]:
    return {step["id"]: step["status"] for step in progress["steps"]}


def test_upgrade_handoff_reports_each_step_and_frees_port_before_start(
    monkeypatch,
    tmp_path: Path,
    fake_responder,
) -> None:
    results: list[dict[str, object]] = []
    events: list[str] = []
    args = _simple_upgrade_handoff_args(tmp_path)
    monkeypatch.setenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, "tok-123")

    def run_tasks(handoff_args) -> None:
        for step in ("check_runtime", "sync_dependencies", "build_webui", "finalize"):
            handoff_args.upgrade_step_callback(step)
        events.append("install")

    def start_service(_args):
        events.append("start")
        events.extend(fake_responder.instances[0].events)
        events.append(f"token-in-env:{restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV in restart_handoff.os.environ}")
        return True, "", ""

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda _pid: True)
    monkeypatch.setattr(restart_handoff, "_stop_services_before_upgrade", lambda _args: events.append("stop") or True)
    monkeypatch.setattr(restart_handoff, "_apply_new_source", lambda _args: events.append("replace"))
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", run_tasks)
    monkeypatch.setattr(restart_handoff, "_report_pending_pro_bundle_install_receipt", lambda _args: None)
    monkeypatch.setattr(restart_handoff, "_start_service_after_upgrade", start_service)
    monkeypatch.setattr(restart_handoff, "_write_upgrade_result", lambda **kwargs: results.append(kwargs))

    assert restart_handoff.run(args) == 0

    responder = fake_responder.instances[0]
    assert responder.endpoint == ("10.0.0.8", 5273)
    assert responder.token == "tok-123"
    # served from right after the stop until right before the new service starts
    assert events == ["stop", "replace", "install", "start", "start", "stop", "token-in-env:False"]
    assert [result["progress"]["step"] for result in results if result["phase"] == "running"] == [
        "stop",
        "source_replace",
        "check_runtime",
        "sync_dependencies",
        "build_webui",
        "finalize",
        "start",
    ]
    final = results[-1]
    assert final["phase"] == "done"
    assert set(_statuses(final["progress"]).values()) == {"done"}


def test_upgrade_handoff_failure_names_the_step_and_lingers_for_the_dialog(
    monkeypatch,
    tmp_path: Path,
    fake_responder,
) -> None:
    results: list[dict[str, object]] = []
    args = _simple_upgrade_handoff_args(tmp_path)
    monkeypatch.setenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, "tok-123")

    def run_tasks(handoff_args) -> str:
        handoff_args.upgrade_step_callback("sync_dependencies")
        handoff_args.upgrade_step_callback("build_webui")
        return "Frontend build failed (npm run build): exit 1"

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda _pid: True)
    monkeypatch.setattr(restart_handoff, "_stop_services_before_upgrade", lambda _args: True)
    monkeypatch.setattr(restart_handoff, "_apply_new_source", lambda _args: None)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", run_tasks)
    monkeypatch.setattr(
        restart_handoff,
        "_start_service_after_upgrade",
        lambda _args: pytest.fail("a failed upgrade must not start the service"),
    )
    monkeypatch.setattr(restart_handoff, "_write_upgrade_result", lambda **kwargs: results.append(kwargs))

    assert restart_handoff.run(args) == 1

    final = results[-1]
    assert final["phase"] == "failed"
    assert final["failed_stage"] == "install"
    assert final["progress"]["step"] == "build_webui"
    statuses = _statuses(final["progress"])
    assert statuses["sync_dependencies"] == "done"
    assert statuses["build_webui"] == "failed"
    assert statuses["start"] == "pending"
    assert fake_responder.instances[0].events == [
        "start",
        f"linger:{restart_handoff.PROGRESS_FAILURE_LINGER_SECONDS}",
    ]


def test_upgrade_handoff_without_token_does_not_bind_the_service_port(
    monkeypatch,
    tmp_path: Path,
    fake_responder,
) -> None:
    args = _simple_upgrade_handoff_args(tmp_path)
    monkeypatch.delenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, raising=False)
    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda _pid: True)
    monkeypatch.setattr(restart_handoff, "_stop_services_before_upgrade", lambda _args: True)
    monkeypatch.setattr(restart_handoff, "_apply_new_source", lambda _args: None)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda _args: None)
    monkeypatch.setattr(restart_handoff, "_report_pending_pro_bundle_install_receipt", lambda _args: None)
    monkeypatch.setattr(restart_handoff, "_start_service_after_upgrade", lambda _args: (True, "", ""))
    monkeypatch.setattr(restart_handoff, "_write_upgrade_result", lambda **_kwargs: None)

    assert restart_handoff.run(args) == 0
    assert fake_responder.instances == []


def test_planned_upgrade_steps_follow_the_install_mode(tmp_path: Path) -> None:
    online = restart_handoff._parse_args(_simple_upgrade_handoff_args(tmp_path))
    assert restart_handoff._planned_upgrade_steps(online) == [
        "stop",
        "source_replace",
        "check_runtime",
        "sync_dependencies",
        "build_webui",
        "finalize",
        "start",
    ]
    online.pro_wheel_path = "/tmp/flockspro.whl"
    assert "install_pro" in restart_handoff._planned_upgrade_steps(online)
    online.prebuilt = True
    online.dependency_wheels_dir = None
    assert restart_handoff._planned_upgrade_steps(online) == ["stop", "source_replace", "install_pro", "finalize", "start"]


def _patch_reported_upgrade(monkeypatch, results: list[dict[str, object]], **overrides) -> None:
    steps = {
        "_record_handoff_log": lambda _message: None,
        "_wait_for_parent_exit": lambda _pid: True,
        "_stop_services_before_upgrade": lambda _args: True,
        "_apply_new_source": lambda _args: None,
        "_run_upgrade_tasks": lambda _args: None,
        "_report_pending_pro_bundle_install_receipt": lambda _args: None,
        "_start_service_after_upgrade": lambda _args: (True, "", ""),
        "_write_upgrade_result": lambda **kwargs: results.append(kwargs),
    }
    steps.update(overrides)
    for name, replacement in steps.items():
        monkeypatch.setattr(restart_handoff, name, replacement)


def test_upgrade_handoff_serves_a_failed_start_again_with_the_output_tail(
    monkeypatch,
    tmp_path: Path,
    fake_responder,
) -> None:
    results: list[dict[str, object]] = []
    monkeypatch.setenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, "tok-123")
    stderr = "Traceback (most recent call last):\n" + "x" * 1000 + "\nModuleNotFoundError: No module named 'flockspro'"
    _patch_reported_upgrade(
        monkeypatch,
        results,
        _start_service_after_upgrade=lambda _args: (False, "", stderr),
    )

    assert restart_handoff.run(_simple_upgrade_handoff_args(tmp_path)) == 1

    final = results[-1]
    assert final["phase"] == "failed"
    assert final["failed_stage"] == "start"
    assert final["error"].startswith("service restart failed: ...")
    assert final["error"].endswith("No module named 'flockspro'")
    assert _statuses(final["progress"])["start"] == "failed"
    # the first responder made way for the new service; the failure is served again
    assert [instance.events for instance in fake_responder.instances] == [
        ["start", "stop"],
        ["start", f"linger:{restart_handoff.PROGRESS_FAILURE_LINGER_SECONDS}"],
    ]


def test_upgrade_handoff_reports_a_start_that_raises(monkeypatch, tmp_path: Path, fake_responder) -> None:
    results: list[dict[str, object]] = []
    monkeypatch.setenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, "tok-123")

    def start_service(_args):
        raise FileNotFoundError("python not found")

    _patch_reported_upgrade(monkeypatch, results, _start_service_after_upgrade=start_service)

    assert restart_handoff.run(_simple_upgrade_handoff_args(tmp_path)) == 1

    assert results[-1]["failed_stage"] == "start"
    assert results[-1]["error"] == "service restart failed: python not found"
    assert fake_responder.instances[-1].events[-1].startswith("linger:")


def test_upgrade_handoff_serves_a_stop_timeout_when_the_port_is_free(
    monkeypatch,
    tmp_path: Path,
    fake_responder,
) -> None:
    results: list[dict[str, object]] = []
    monkeypatch.setenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, "tok-123")
    _patch_reported_upgrade(
        monkeypatch,
        results,
        _stop_services_before_upgrade=lambda _args: False,
        _apply_new_source=lambda _args: pytest.fail("source must stay untouched"),
    )

    assert restart_handoff.run(_simple_upgrade_handoff_args(tmp_path)) == 1

    assert results[-1]["failed_stage"] == "stop"
    assert _statuses(results[-1]["progress"])["stop"] == "failed"
    assert fake_responder.instances[0].events == ["start", f"linger:{restart_handoff.PROGRESS_FAILURE_LINGER_SECONDS}"]


def test_upgrade_handoff_reports_an_unexpected_crash_at_the_current_step(
    monkeypatch,
    tmp_path: Path,
    fake_responder,
) -> None:
    results: list[dict[str, object]] = []
    monkeypatch.setenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, "tok-123")

    def run_tasks(handoff_args) -> None:
        handoff_args.upgrade_step_callback("build_webui")
        handoff_args.upgrade_step_callback("finalize")

    def report_receipt(_args) -> None:
        raise RuntimeError("receipt store is locked")

    _patch_reported_upgrade(
        monkeypatch,
        results,
        _run_upgrade_tasks=run_tasks,
        _report_pending_pro_bundle_install_receipt=report_receipt,
    )

    assert restart_handoff.run(_simple_upgrade_handoff_args(tmp_path)) == 1

    assert results[-1]["error"] == "upgrade handoff crashed: receipt store is locked"
    assert results[-1]["progress"]["failed_step"] == "finalize"
    assert fake_responder.instances[0].events[-1].startswith("linger:")


def _pro_component_restart_args(tmp_path: Path) -> list[str]:
    args = _handoff_args(tmp_path, ["python", "-m", "flocks.cli.main", "start"])
    separator_index = args.index("--")
    args[separator_index:separator_index] = ["--pro-wheel-path", str(tmp_path / "flockspro.whl")]
    return args


def test_restart_handoff_never_passes_the_upgrade_token_to_the_service(monkeypatch, tmp_path: Path) -> None:
    spawned_env: list[bool] = []
    monkeypatch.setenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, "tok-123")
    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda _pid: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda _port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda _args: None)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda **_kwargs: True)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda _argv, **_kwargs: spawned_env.append(
            restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV in restart_handoff.os.environ
        )
        or SimpleNamespace(pid=4321),
    )

    assert restart_handoff.run(_handoff_args(tmp_path, ["python", "-m", "flocks.cli.main", "start"])) == 0
    assert spawned_env == [False]


def test_pro_component_restart_reports_steps_and_frees_the_port_before_start(
    monkeypatch,
    tmp_path: Path,
    fake_responder,
) -> None:
    results: list[dict[str, object]] = []
    events: list[str] = []
    monkeypatch.setenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, "tok-123")

    def run_tasks(handoff_args) -> None:
        for step in ("check_runtime", "sync_dependencies", "install_pro", "build_webui", "finalize"):
            handoff_args.upgrade_step_callback(step)

    def start_service(argv, **kwargs):
        events.extend(fake_responder.instances[0].events)
        events.append(f"start:{argv[-1]}:{kwargs['cwd']}")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda _pid: True)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda **_kwargs: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda _port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", run_tasks)
    monkeypatch.setattr(restart_handoff, "_report_pending_pro_bundle_install_receipt", lambda _args: None)
    monkeypatch.setattr(restart_handoff, "_write_upgrade_result", lambda **kwargs: results.append(kwargs))
    monkeypatch.setattr(restart_handoff.subprocess, "run", start_service)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("a web upgrade waits for the start command"),
    )

    assert restart_handoff.run(_pro_component_restart_args(tmp_path)) == 0

    assert fake_responder.instances[0].endpoint == ("127.0.0.1", 5173)
    assert events == ["start", "stop", f"start:start:{tmp_path}"]
    assert [result["progress"]["step"] for result in results if result["phase"] == "running"] == [
        "stop",
        "check_runtime",
        "sync_dependencies",
        "install_pro",
        "build_webui",
        "finalize",
        "start",
    ]
    assert results[-1]["phase"] == "done"


def test_pro_component_restart_failure_is_served_to_the_dialog(
    monkeypatch,
    tmp_path: Path,
    fake_responder,
) -> None:
    results: list[dict[str, object]] = []
    monkeypatch.setenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, "tok-123")

    def run_tasks(handoff_args) -> str:
        handoff_args.upgrade_step_callback("install_pro")
        return "Pro wheel install failed: hash mismatch"

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda _pid: True)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda **_kwargs: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda _port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", run_tasks)
    monkeypatch.setattr(restart_handoff, "_write_upgrade_result", lambda **kwargs: results.append(kwargs))
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("a failed install must not start the service"),
    )

    assert restart_handoff.run(_pro_component_restart_args(tmp_path)) == 1

    assert results[-1]["phase"] == "failed"
    assert results[-1]["failed_stage"] == "install"
    assert results[-1]["error"] == "Pro wheel install failed: hash mismatch"
    assert _statuses(results[-1]["progress"])["install_pro"] == "failed"
    assert fake_responder.instances[0].events == ["start", f"linger:{restart_handoff.PROGRESS_FAILURE_LINGER_SECONDS}"]


def test_pro_component_restart_serves_a_start_command_that_exits_with_an_error(
    monkeypatch,
    tmp_path: Path,
    fake_responder,
) -> None:
    results: list[dict[str, object]] = []
    monkeypatch.setenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, "tok-123")
    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda _message: None)
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda _pid: True)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda **_kwargs: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda _port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda _args: None)
    monkeypatch.setattr(restart_handoff, "_report_pending_pro_bundle_install_receipt", lambda _args: None)
    monkeypatch.setattr(restart_handoff, "_write_upgrade_result", lambda **kwargs: results.append(kwargs))
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 1, b"", b"ImportError: cannot import name 'PolicyGateHook'"),
    )

    assert restart_handoff.run(_pro_component_restart_args(tmp_path)) == 1

    assert results[-1]["phase"] == "failed"
    assert results[-1]["failed_stage"] == "start"
    assert results[-1]["error"] == "service restart failed: ImportError: cannot import name 'PolicyGateHook'"
    assert [instance.events for instance in fake_responder.instances] == [
        ["start", "stop"],
        ["start", f"linger:{restart_handoff.PROGRESS_FAILURE_LINGER_SECONDS}"],
    ]


def test_start_failure_masks_url_credentials_before_cutting_the_output(
    monkeypatch,
    tmp_path: Path,
    fake_responder,
) -> None:
    results: list[dict[str, object]] = []
    monkeypatch.setenv(restart_handoff.handoff_progress.UPGRADE_PROGRESS_TOKEN_ENV, "tok-123")
    # the kept tail starts inside the URL, right after "https://deploy:"
    stderr = "x" * 1000 + " https://deploy:s3cr3t-pass@registry.example.com/simple " + "y" * 360
    _patch_reported_upgrade(monkeypatch, results, _start_service_after_upgrade=lambda _args: (False, "", stderr))

    assert restart_handoff.run(_simple_upgrade_handoff_args(tmp_path)) == 1

    error = results[-1]["error"]
    assert "s3cr3t-pass" not in error
    assert "***@registry.example.com" in error
    assert "s3cr3t-pass" not in fake_responder.instances[-1].progress.snapshot()["error"]
