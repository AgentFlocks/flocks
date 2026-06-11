from pathlib import Path
from types import SimpleNamespace

from flocks.updater import restart_handoff


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

    code = restart_handoff.run(
        [
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
            "--",
            *restart_argv,
        ]
    )

    assert code == 0
    assert events[1:] == [
        "wait-parent:1234",
        "free-port:8000:backend.pid",
        f"spawn:{restart_argv}:{tmp_path}:True",
        f"record:4321:{restart_argv}:8000",
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

    code = restart_handoff.run(
        [
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
            "--",
            "python.exe",
            "-m",
            "flocks.cli.main",
            "serve",
        ]
    )

    assert code == 1
    assert events == ["log:started parent_pid=1234 backend=127.0.0.1:8000 frontend=127.0.0.1:5173", "log:parent_exit_timeout parent_pid=1234"]


def test_ensure_backend_port_free_stops_backend_after_wait_timeout(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    wait_results = iter([False, True])
    backend_pid_file = tmp_path / "backend.pid"

    monkeypatch.setattr(restart_handoff, "_record_handoff_log", lambda message: events.append(f"log:{message}"))
    monkeypatch.setattr(restart_handoff, "_wait_for_backend_port_free", lambda port: next(wait_results))
    monkeypatch.setattr(
        restart_handoff.service_manager,
        "stop_one",
        lambda port, pid_file, name, console: events.append(f"stop:{port}:{pid_file.name}:{name}"),
    )

    assert restart_handoff._ensure_backend_port_free(8000, backend_pid_file) is True
    assert events == [
        "log:backend_port_still_in_use port=8000; stopping backend",
        "stop:8000:backend.pid:backend",
    ]
