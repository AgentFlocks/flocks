"""Windows restart handoff helper for the self-updater.

The updater process owns the backend port while it is spawning the restart
command.  On Windows, starting the new backend before that process has fully
exited can race with port release and fail with WinError 10048.  This helper is
spawned instead; it waits for the old backend to exit, clears any remaining
backend listener, and then starts the real restart command.
"""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path
from typing import Sequence

from flocks.cli import service_manager
from flocks.utils.log import append_upgrade_text_log

DEFAULT_PARENT_TIMEOUT_SECONDS = 20.0
DEFAULT_PORT_TIMEOUT_SECONDS = 10.0
POST_STOP_PORT_TIMEOUT_SECONDS = 20.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.25


class _NullConsole:
    def print(self, *args, **kwargs) -> None:
        return None


def _record_handoff_log(message: str) -> None:
    append_upgrade_text_log(f"restart_handoff {message}")


def _wait_for_parent_exit(
    parent_pid: int,
    *,
    timeout_seconds: float = DEFAULT_PARENT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not service_manager.pid_is_running(parent_pid):
            return True
        time.sleep(poll_interval_seconds)
    return not service_manager.pid_is_running(parent_pid)


def _backend_port_in_use(port: int) -> bool:
    listeners = service_manager.port_owner_pids(port)
    return service_manager.port_is_in_use(port, listeners)


def _wait_for_backend_port_free(
    port: int,
    *,
    timeout_seconds: float = DEFAULT_PORT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _backend_port_in_use(port):
            return True
        time.sleep(poll_interval_seconds)
    return not _backend_port_in_use(port)


def _ensure_backend_port_free(backend_port: int, backend_pid_file: Path) -> bool:
    if _wait_for_backend_port_free(backend_port):
        return True

    _record_handoff_log(f"backend_port_still_in_use port={backend_port}; stopping backend")
    try:
        service_manager.stop_one(backend_port, backend_pid_file, "backend", _NullConsole())
    except Exception as exc:
        _record_handoff_log(f"backend_stop_failed port={backend_port} error={exc}")
        return False

    return _wait_for_backend_port_free(backend_port, timeout_seconds=POST_STOP_PORT_TIMEOUT_SECONDS)


def _cli_subcommand(argv: Sequence[str]) -> str | None:
    for index, value in enumerate(argv[:-2]):
        if value == "-m" and argv[index + 1] == "flocks.cli.main":
            return argv[index + 2]
    return None


def _record_backend_runtime_if_direct_serve(
    process: subprocess.Popen,
    restart_argv: Sequence[str],
    *,
    backend_host: str,
    backend_port: int,
    backend_pid_file: Path,
) -> None:
    if _cli_subcommand(restart_argv) != "serve":
        return

    try:
        service_manager.write_runtime_record(
            backend_pid_file,
            service_manager.process_runtime_record(
                process,
                host=backend_host,
                port=backend_port,
                command=restart_argv,
            ),
        )
    except Exception as exc:
        _record_handoff_log(f"backend_runtime_record_failed error={exc}")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flocks Windows restart handoff helper")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--backend-host", required=True)
    parser.add_argument("--backend-port", type=int, required=True)
    parser.add_argument("--frontend-host", required=True)
    parser.add_argument("--frontend-port", type=int, required=True)
    parser.add_argument("--backend-pid-file", required=True)
    parser.add_argument("--install-root", required=True)
    parser.add_argument("restart_argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.restart_argv and args.restart_argv[0] == "--":
        args.restart_argv = args.restart_argv[1:]
    return args


def run(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    restart_argv = list(args.restart_argv)
    if not restart_argv:
        _record_handoff_log("missing_restart_argv")
        return 2

    _record_handoff_log(
        "started "
        f"parent_pid={args.parent_pid} backend={args.backend_host}:{args.backend_port} "
        f"frontend={args.frontend_host}:{args.frontend_port}"
    )

    if not _wait_for_parent_exit(args.parent_pid):
        _record_handoff_log(f"parent_exit_timeout parent_pid={args.parent_pid}")
        return 1

    backend_pid_file = Path(args.backend_pid_file)
    if not _ensure_backend_port_free(args.backend_port, backend_pid_file):
        _record_handoff_log(f"backend_port_unavailable port={args.backend_port}")
        return 1

    try:
        process = subprocess.Popen(
            restart_argv,
            cwd=Path(args.install_root),
            close_fds=True,
        )
    except OSError as exc:
        _record_handoff_log(f"restart_spawn_failed error={exc}")
        return 1

    _record_backend_runtime_if_direct_serve(
        process,
        restart_argv,
        backend_host=args.backend_host,
        backend_port=args.backend_port,
        backend_pid_file=backend_pid_file,
    )
    _record_handoff_log(f"restart_spawned pid={process.pid}")
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
