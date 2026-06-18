from pathlib import Path
from types import SimpleNamespace

from flocks.updater import restart_handoff


def _handoff_args(tmp_path: Path, restart_argv: list[str], *, no_restart: bool = False) -> list[str]:
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
        "--backend-pid-file",
        str(tmp_path / "backend.pid"),
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
    if no_restart:
        args.append("--no-restart")
    if restart_argv:
        args.extend(["--", *restart_argv])
    return args


def test_run_waits_for_parent_and_backend_port_before_spawning(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "serve", "--host", "127.0.0.1", "--port", "8000"]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_parent_exit",
        lambda parent_pid: events.append(f"wait-parent:{parent_pid}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_ensure_backend_port_free",
        lambda backend_port, backend_pid_file: events.append(f"free-port:{backend_port}:{backend_pid_file.name}") or True,
    )
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda argv, cwd=None, close_fds=False: events.append(f"spawn:{list(argv)}:{cwd}:{close_fds}")
        or SimpleNamespace(pid=4321),
    )
    monkeypatch.setattr(
        restart_handoff,
        "_record_backend_runtime_if_direct_serve",
        lambda process, argv, **kwargs: events.append(f"record:{process.pid}:{list(argv)}:{kwargs['backend_port']}"),
    )
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: events.append("tasks") or None)

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv))

    assert code == 0
    assert events[1:] == [
        "wait-parent:1234",
        "free-port:8000:backend.pid",
        "tasks",
        f"spawn:{restart_argv}:{tmp_path}:True",
        f"record:4321:{restart_argv}:8000",
        "log:restart_spawned pid=4321",
    ]


def test_run_no_restart_runs_tasks_without_spawning(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_parent_exit",
        lambda parent_pid: events.append(f"wait-parent:{parent_pid}") or True,
    )
    monkeypatch.setattr(
        restart_handoff,
        "_ensure_backend_port_free",
        lambda backend_port, backend_pid_file: events.append(f"free-port:{backend_port}:{backend_pid_file.name}") or True,
    )
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda *_args, **_kwargs: events.append("spawn"),
    )
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: events.append("tasks") or None)

    code = restart_handoff.run(_handoff_args(tmp_path, [], no_restart=True))

    assert code == 0
    assert events[1:] == [
        "wait-parent:1234",
        "free-port:8000:backend.pid",
        "tasks",
        "log:upgrade_tasks_completed no_restart=true",
    ]
    assert "spawn" not in events


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

    code = restart_handoff.run(_handoff_args(tmp_path, ["python.exe", "-m", "flocks.cli.main", "serve"]))

    assert code == 1
    assert events == ["log:started parent_pid=1234 backend=127.0.0.1:8000 frontend=127.0.0.1:5173", "log:parent_exit_timeout parent_pid=1234"]


def test_run_does_not_spawn_when_upgrade_tasks_fail(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    restart_argv = ["python.exe", "-m", "flocks.cli.main", "serve"]

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_parent_exit", lambda parent_pid: True)
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda backend_port, backend_pid_file: True)
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
    monkeypatch.setattr(restart_handoff, "_ensure_backend_port_free", lambda backend_port, backend_pid_file: True)
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


def test_ensure_backend_port_free_stops_backend_after_wait_timeout(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    wait_results = iter([False, True])
    backend_pid_file = tmp_path / "backend.pid"

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(
        restart_handoff,
        "_wait_for_backend_port_free",
        lambda port, **kwargs: events.append(f"wait:{port}:{kwargs.get('timeout_seconds')}") or next(wait_results),
    )
    monkeypatch.setattr(
        restart_handoff.service_manager,
        "stop_one",
        lambda port, pid_file, name, console: events.append(f"stop:{port}:{pid_file.name}:{name}"),
    )

    assert restart_handoff._ensure_backend_port_free(8000, backend_pid_file) is True
    assert events == [
        "wait:8000:None",
        "log:backend_port_still_in_use port=8000; stopping backend",
        "stop:8000:backend.pid:backend",
        "wait:8000:20.0",
    ]
