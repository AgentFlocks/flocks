from pathlib import Path
from types import SimpleNamespace

from flocks.updater import restart_handoff


def _handoff_args(tmp_path: Path, restart_argv: list[str]) -> list[str]:
    return [
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
        "--",
        *restart_argv,
    ]


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
        lambda backend_port: events.append(f"free-port:{backend_port}") or True,
    )
    monkeypatch.setattr(
        restart_handoff.subprocess,
        "Popen",
        lambda argv, cwd=None, close_fds=False: events.append(f"spawn:{list(argv)}:{cwd}:{close_fds}")
        or SimpleNamespace(pid=4321),
    )
    monkeypatch.setattr(restart_handoff, "_run_upgrade_tasks", lambda args: events.append("tasks") or None)

    code = restart_handoff.run(_handoff_args(tmp_path, restart_argv))

    assert code == 0
    assert events[1:] == [
        "wait-parent:1234",
        "free-port:8000",
        "tasks",
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

    code = restart_handoff.run(_handoff_args(tmp_path, ["python.exe", "-m", "flocks.cli.main", "serve"]))

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
