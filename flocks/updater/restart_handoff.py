"""Restart handoff helper for the self-updater.

The updater process owns the backend port while it is spawning the restart
command. Starting the new backend before that process has fully exited can race
with port release. This helper is spawned instead; it waits for the old backend
to exit, clears any remaining backend listener, runs post-apply upgrade tasks,
and then starts the real restart command.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import time
from pathlib import Path
from typing import Sequence

from flocks.cli import service_manager
from flocks.utils.log import append_upgrade_text_log

DEFAULT_PARENT_TIMEOUT_SECONDS = 20.0
DEFAULT_PORT_TIMEOUT_SECONDS = 10.0
POST_STOP_PORT_TIMEOUT_SECONDS = 20.0
SUPERVISOR_STOP_TIMEOUT_SECONDS = 20.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.25


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


def _ensure_backend_port_free(backend_port: int) -> bool:
    if _wait_for_backend_port_free(backend_port):
        return True

    _record_handoff_log(f"backend_port_still_in_use port={backend_port}")
    return _wait_for_backend_port_free(backend_port, timeout_seconds=POST_STOP_PORT_TIMEOUT_SECONDS)


def _stop_supervisor_before_restart(
    *,
    timeout_seconds: float = SUPERVISOR_STOP_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> bool:
    from flocks.cli import service_control

    paths = service_manager.runtime_paths()
    if not service_control.supervisor_is_running(paths):
        return True

    try:
        service_control.request_stop(paths=paths, timeout=timeout_seconds)
    except Exception as exc:
        _record_handoff_log(f"supervisor_stop_request_failed error={exc}")
        return False

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not service_control.supervisor_is_running(paths):
            return True
        time.sleep(poll_interval_seconds)
    return not service_control.supervisor_is_running(paths)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flocks restart handoff helper")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--backend-host", required=True)
    parser.add_argument("--backend-port", type=int, required=True)
    parser.add_argument("--frontend-host", required=True)
    parser.add_argument("--frontend-port", type=int, required=True)
    parser.add_argument("--backend-pid-file")
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--uv-path", required=True)
    parser.add_argument("--sync-timeout", type=int, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--backup-path")
    parser.add_argument("--uv-default-index")
    parser.add_argument("--npm-registry")
    parser.add_argument("--pro-wheel-path")
    parser.add_argument("--pro-bundle-manifest-path")
    parser.add_argument("--bundle-sha256")
    parser.add_argument("--cleanup-dir")
    parser.add_argument("--prepare-handover", action="store_true")
    parser.add_argument("restart_argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.restart_argv and args.restart_argv[0] == "--":
        args.restart_argv = args.restart_argv[1:]
    return args


def _run_upgrade_tasks(args: argparse.Namespace) -> str | None:
    from flocks.updater import updater

    return asyncio.run(
        updater.run_handoff_upgrade_tasks(
            install_root=Path(args.install_root),
            uv_path=args.uv_path,
            version=args.version,
            uv_default_index=args.uv_default_index,
            npm_registry=args.npm_registry,
            pro_wheel_path=Path(args.pro_wheel_path) if args.pro_wheel_path else None,
            pro_bundle_manifest_path=(
                Path(args.pro_bundle_manifest_path) if args.pro_bundle_manifest_path else None
            ),
            bundle_sha256=args.bundle_sha256,
            sync_timeout=args.sync_timeout,
        )
    )


def _rollback_failed_upgrade(args: argparse.Namespace, error: str) -> None:
    from flocks.updater import updater

    _record_handoff_log(f"upgrade_tasks_failed error={error}")
    backup_path = Path(args.backup_path) if args.backup_path else None
    try:
        updater._rollback_failed_update(
            backup_path,
            Path(args.install_root),
            args.current_version,
        )
    except Exception as exc:
        _record_handoff_log(f"rollback_failed error={exc}")


def _prepare_upgrade_handover(args: argparse.Namespace) -> bool:
    from flocks.updater import updater

    try:
        updater._prepare_upgrade_handover(args.version)
    except Exception as exc:
        _record_handoff_log(f"prepare_handover_failed error={exc}")
        return False
    return True


def _rollback_upgrade_handover() -> None:
    from flocks.updater import updater

    try:
        updater.rollback_upgrade_handover()
    except Exception as exc:
        _record_handoff_log(f"handover_rollback_failed error={exc}")


def _cleanup_dir(path_value: str | None) -> None:
    if not path_value:
        return
    shutil.rmtree(Path(path_value), ignore_errors=True)


def _cli_subcommand(argv: Sequence[str]) -> str | None:
    """Return the flocks.cli.main subcommand embedded in a Python argv."""
    for index, value in enumerate(argv[:-2]):
        if value == "-m" and argv[index + 1] == "flocks.cli.main":
            return argv[index + 2]
    return None


def _restart_argv_for_current_runtime(args: argparse.Namespace, restart_argv: Sequence[str]) -> list[str]:
    if _cli_subcommand(restart_argv) != "serve":
        return list(restart_argv)

    argv = [
        restart_argv[0],
        "-m",
        "flocks.cli.main",
        "start",
        "--no-browser",
        "--skip-webui-build",
        "--host",
        str(args.frontend_host),
        "--port",
        str(args.frontend_port),
        "--server-host",
        str(args.backend_host),
        "--server-port",
        str(args.backend_port),
    ]
    _record_handoff_log(f"legacy_serve_restart_migrated argv={argv}")
    return argv


def run(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    restart_argv = _restart_argv_for_current_runtime(args, args.restart_argv)
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
        _cleanup_dir(args.cleanup_dir)
        return 1

    if args.prepare_handover:
        if not _prepare_upgrade_handover(args):
            _cleanup_dir(args.cleanup_dir)
            return 1
    elif not _ensure_backend_port_free(args.backend_port):
        _record_handoff_log(f"backend_port_unavailable port={args.backend_port}")
        _cleanup_dir(args.cleanup_dir)
        return 1

    try:
        task_error = _run_upgrade_tasks(args)
    except Exception as exc:
        task_error = f"upgrade tasks crashed: {exc}"
    if task_error is not None:
        _rollback_failed_upgrade(args, task_error)
        _cleanup_dir(args.cleanup_dir)
        return 1

    if not _stop_supervisor_before_restart():
        _record_handoff_log("supervisor_stop_timeout")
        if args.prepare_handover:
            _rollback_upgrade_handover()
        _cleanup_dir(args.cleanup_dir)
        return 1

    try:
        process = subprocess.Popen(
            restart_argv,
            cwd=Path(args.install_root),
            close_fds=True,
        )
    except OSError as exc:
        _record_handoff_log(f"restart_spawn_failed error={exc}")
        if args.prepare_handover:
            _rollback_upgrade_handover()
        _cleanup_dir(args.cleanup_dir)
        return 1

    _record_handoff_log(f"restart_spawned pid={process.pid}")
    _cleanup_dir(args.cleanup_dir)
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
