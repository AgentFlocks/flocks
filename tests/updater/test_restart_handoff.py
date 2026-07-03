import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.cli import service_manager
from flocks.updater import restart_handoff
from tests.helpers.service_supervisor import make_short_runtime_root, start_supervisor, stop_supervisor, wait_for_supervisor


def _handoff_args(tmp_path: Path, restart_argv: list[str], *, prepare_handover: bool = False) -> list[str]:
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
    if prepare_handover:
        args.append("--prepare-handover")
    return [*args, "--", *restart_argv]


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
        lambda argv, cwd=None, close_fds=False: events.append(f"spawn:{list(argv)}:{cwd}:{close_fds}")
        or SimpleNamespace(pid=4321),
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
        lambda argv, cwd=None, close_fds=False: events.append(f"spawn:{list(argv)}:{cwd}:{close_fds}")
        or SimpleNamespace(pid=4321),
    )

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv))

    assert code == 0
    assert f"spawn:{restart_argv}:{tmp_path}:True" in events


def test_run_accepts_legacy_backend_pid_file_argument(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "start"]
    args = _handoff_args(tmp_path, restart_argv)
    args[args.index("--install-root"):args.index("--install-root")] = [
        "--backend-pid-file",
        str(tmp_path / "backend.pid"),
    ]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda backend_port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: None)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda: True)
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda argv, cwd=None, close_fds=False: events.append(f"spawn:{list(argv)}:{cwd}:{close_fds}")
        or SimpleNamespace(pid=4321),
    )

    code = restart_handoff.run(args)

    assert code == 0
    assert f"spawn:{restart_argv}:{tmp_path}:True" in events


def test_run_prepares_handover_after_parent_exit_without_waiting_for_page_port(
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
        "_prepare_upgrade_handover",
        lambda args: events.append(f"prepare:{args.version}") or True,
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
        lambda argv, cwd=None, close_fds=False: events.append(f"spawn:{list(argv)}:{cwd}:{close_fds}")
        or SimpleNamespace(pid=4321),
    )

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv, prepare_handover=True))

    assert code == 0
    assert events[1:] == [
        "wait-parent:1234",
        "prepare:2026.4.1",
        "tasks",
        "stop-supervisor",
        f"spawn:{restart_argv}:{tmp_path}:True",
        "log:restart_spawned pid=4321",
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
    assert events == ["log:started parent_pid=1234 backend=127.0.0.1:8000 frontend=127.0.0.1:5173", "log:parent_exit_timeout parent_pid=1234"]


def test_run_does_not_spawn_when_upgrade_tasks_fail(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "serve"]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda backend_port: True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: "sync failed")
    monkeypatch.setattr(restart_handoff, "_rollback_failed_upgrade", lambda args, error: events.append(f"rollback:{error}"))
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: events.append("spawn"),
    )

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv))

    assert code == 1
    assert "rollback:sync failed" in events
    assert "spawn" not in events


def test_run_rolls_back_and_cleans_up_when_upgrade_tasks_crash(monkeypatch, tmp_path: Path) -> None:
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
    monkeypatch.setattr(restart_handoff, "_rollback_failed_upgrade", lambda args, error: events.append(f"rollback:{error}"))
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: events.append("spawn"),
    )

    code = restart_handoff.run(args)

    assert code == 1
    assert "rollback:upgrade tasks crashed: boom" in events
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


def test_run_rolls_back_prepared_handover_when_supervisor_stop_fails(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "start"]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_prepare_upgrade_handover", lambda args: events.append("prepare") or True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: None)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda: False)
    monkeypatch.setattr(restart_handoff, "_rollback_upgrade_handover", lambda: events.append("rollback-handover"))
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: events.append("spawn"),
    )

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv, prepare_handover=True))

    assert code == 1
    assert "rollback-handover" in events
    assert "spawn" not in events


def test_run_rolls_back_prepared_handover_when_restart_spawn_fails(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "start"]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_prepare_upgrade_handover", lambda args: events.append("prepare") or True)
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: None)
    monkeypatch.setattr(restart_handoff, "_stop_supervisor_before_restart", lambda: True)
    monkeypatch.setattr(restart_handoff, "_rollback_upgrade_handover", lambda: events.append("rollback-handover"))
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv, prepare_handover=True))

    assert code == 1
    assert "log:restart_spawn_failed error=spawn failed" in events
    assert "rollback-handover" in events


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
