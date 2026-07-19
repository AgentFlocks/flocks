"""Detached restart and source-upgrade handoff helper."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Sequence

from flocks.cli import service_manager
from flocks.updater import updater as updater_module
from flocks.utils.log import append_upgrade_text_log

DEFAULT_PARENT_TIMEOUT_SECONDS = 20.0
DEFAULT_PORT_TIMEOUT_SECONDS = 10.0
POST_STOP_PORT_TIMEOUT_SECONDS = 20.0
SUPERVISOR_STOP_TIMEOUT_SECONDS = 20.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.25


class _NullConsole:
    """Discard service-manager progress output in the detached helper."""

    def print(self, *_args, **_kwargs) -> None:
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


def _ensure_backend_port_free(backend_port: int) -> bool:
    if _wait_for_backend_port_free(backend_port):
        return True

    _record_handoff_log(f"backend_port_still_in_use port={backend_port}")
    return _wait_for_backend_port_free(backend_port, timeout_seconds=POST_STOP_PORT_TIMEOUT_SECONDS)


def _stop_supervisor_before_restart(
    *,
    daemon_pid: int | None = None,
    backend_port: int | None = None,
    service_ports: Sequence[int] = (),
    timeout_seconds: float = SUPERVISOR_STOP_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> bool:
    from flocks.cli import service_control

    paths = service_manager.runtime_paths()
    ports = {port for port in (backend_port, *service_ports) if port is not None}
    if service_control.supervisor_is_running(paths):
        try:
            service_control.request_stop(paths=paths, timeout=timeout_seconds)
        except Exception as exc:
            _record_handoff_log(f"supervisor_stop_request_failed error={exc}")
            return False

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        control_stopped = not service_control.supervisor_is_running(paths)
        daemon_stopped = not service_manager.pid_is_running(daemon_pid)
        ports_stopped = all(not _backend_port_in_use(port) for port in ports)
        if control_stopped and daemon_stopped and ports_stopped:
            return True
        time.sleep(poll_interval_seconds)
    return (
        not service_control.supervisor_is_running(paths)
        and not service_manager.pid_is_running(daemon_pid)
        and all(not _backend_port_in_use(port) for port in ports)
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flocks restart handoff helper")
    parser.add_argument("--mode", choices=("restart", "upgrade"), default="restart")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--backend-host", required=True)
    parser.add_argument("--backend-port", type=int, required=True)
    parser.add_argument("--frontend-host", required=True)
    parser.add_argument("--frontend-port", type=int, required=True)
    parser.add_argument("--backend-pid-file")
    parser.add_argument("--install-root", required=True)
    parser.add_argument("--content-root")
    parser.add_argument("--backup-path")
    parser.add_argument("--was-running", action="store_true")
    parser.add_argument("--daemon-pid", type=int)
    parser.add_argument("--service-config-json")
    parser.add_argument("--uv-path", required=True)
    parser.add_argument("--sync-timeout", type=int, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--uv-default-index")
    parser.add_argument("--npm-registry")
    parser.add_argument("--pro-wheel-path")
    parser.add_argument("--pro-bundle-manifest-path")
    parser.add_argument("--bundle-sha256")
    parser.add_argument("--cleanup-dir")
    parser.add_argument("--prepare-handover", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("restart_argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.restart_argv and args.restart_argv[0] == "--":
        args.restart_argv = args.restart_argv[1:]
    return args


def _run_upgrade_tasks(args: argparse.Namespace) -> str | None:
    try:
        asyncio.run(
            updater_module.install_or_repair_source(
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
    except RuntimeError as exc:
        return str(exc)
    return None


def _service_config_from_args(args: argparse.Namespace) -> service_manager.ServiceConfig:
    """Load the captured service config without consulting the old daemon."""
    from flocks.cli.service_config import service_config_from_payload

    try:
        payload = json.loads(args.service_config_json or "")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid service config JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("service config JSON must contain an object")
    return service_config_from_payload(payload)


def _validate_simple_upgrade_args(args: argparse.Namespace) -> None:
    """Validate all inputs before stopping or changing the active install."""
    if not args.content_root:
        raise ValueError("upgrade content root is missing")
    content_root = Path(args.content_root)
    if not content_root.is_dir():
        raise ValueError(f"upgrade content root does not exist: {content_root}")
    if not args.backup_path:
        raise ValueError("upgrade backup path is missing")
    backup_path = Path(args.backup_path)
    if not backup_path.is_file():
        raise ValueError(f"upgrade backup does not exist: {backup_path}")
    if args.was_running and not args.restart_argv:
        raise ValueError("running service requires a restart runtime")
    _service_config_from_args(args)


def _service_ports(args: argparse.Namespace) -> tuple[int, ...]:
    """Return every port that must be released before source replacement."""
    config = _service_config_from_args(args)
    return tuple(sorted({config.backend_port, config.frontend_port}))


def _stop_services_before_upgrade(args: argparse.Namespace) -> bool:
    """Stop managed services and wait for the captured daemon and port to exit."""
    try:
        service_manager.stop_all(_NullConsole())
    except service_manager.ServiceError as exc:
        _record_handoff_log(f"service_stop_failed error={exc}")
        return False
    return _stop_supervisor_before_restart(
        daemon_pid=args.daemon_pid,
        backend_port=args.backend_port,
        service_ports=_service_ports(args),
    )


def _apply_new_source(args: argparse.Namespace) -> None:
    """Replace the active source tree with the staged source tree."""
    if not args.content_root:
        raise RuntimeError("upgrade content root is missing")
    content_root = Path(args.content_root)
    if not content_root.is_dir():
        raise RuntimeError(f"upgrade content root does not exist: {content_root}")
    updater_module._replace_install_dir(content_root, Path(args.install_root))


def _build_captured_start_argv(args: argparse.Namespace) -> list[str]:
    """Build ``flocks start`` directly from the pre-upgrade config snapshot."""
    if not args.restart_argv:
        return []
    config = _service_config_from_args(args)
    argv = [
        args.restart_argv[0],
        "-m",
        "flocks.cli.main",
        "start",
        "--host",
        config.frontend_host,
        "--port",
        str(config.frontend_port),
    ]
    if config.no_browser:
        argv.append("--no-browser")
    if config.skip_frontend_build:
        argv.append("--skip-webui-build")
    if config.legacy_backend_host is not None:
        argv.extend(["--server-host", config.legacy_backend_host])
    if config.legacy_backend_port is not None:
        argv.extend(["--server-port", str(config.legacy_backend_port)])
    return argv


def _start_service_after_upgrade(args: argparse.Namespace) -> tuple[bool, str, str]:
    """Synchronously restore the service only when it ran before the upgrade."""
    if not args.was_running:
        return True, "", ""
    start_argv = _build_captured_start_argv(args)
    if not start_argv:
        _record_handoff_log("missing_restart_argv")
        return False, "", "missing restart runtime"
    completed = subprocess.run(
        start_argv,
        cwd=Path(args.install_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return True, completed.stdout, completed.stderr
    _record_handoff_log(
        "restart_failed "
        f"returncode={completed.returncode} stdout={completed.stdout.strip()} stderr={completed.stderr.strip()}"
    )
    return False, completed.stdout, completed.stderr


def _write_upgrade_result(
    *,
    args: argparse.Namespace,
    phase: str,
    failed_stage: str | None = None,
    error: str | None = None,
    backup_path: Path | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> None:
    """Persist a result record without driving automatic recovery."""
    try:
        config_payload = json.loads(args.service_config_json or "{}")
    except json.JSONDecodeError:
        config_payload = {}
    payload = {
        "phase": phase,
        "version": args.version,
        "current_version": args.current_version,
        "was_running": args.was_running,
        "service_config": config_payload,
    }
    if error:
        payload["last_error"] = error
    if failed_stage:
        payload["failed_stage"] = failed_stage
    if backup_path is not None:
        payload["backup_path"] = str(backup_path)
    if stdout:
        payload["stdout"] = stdout
    if stderr:
        payload["stderr"] = stderr
    try:
        updater_module._write_upgrade_result_state(payload)
    except Exception as exc:
        try:
            _record_handoff_log(f"upgrade_result_write_failed phase={phase} error={exc}")
        except Exception:
            pass


def _report_pending_pro_bundle_install_receipt(args: argparse.Namespace) -> None:
    if not args.pro_bundle_manifest_path:
        return
    try:
        from flocks.console.login import ConsoleLoginService

        reported = asyncio.run(ConsoleLoginService.report_pending_pro_bundle_install_receipt())
    except Exception as exc:
        _record_handoff_log(f"install_receipt_report_failed error={exc}")
        return
    if reported:
        _record_handoff_log("install_receipt_reported")
    else:
        _record_handoff_log("install_receipt_report_skipped")


def _run_simple_upgrade(args: argparse.Namespace) -> int:
    """Run stop, source replacement, installation, and restart in order."""
    try:
        _validate_simple_upgrade_args(args)
    except ValueError as exc:
        error = str(exc)
        _record_handoff_log(f"upgrade_validation_failed error={error}")
        _write_upgrade_result(
            args=args,
            phase="failed",
            failed_stage="validation",
            error=error,
        )
        _cleanup_dir(args.cleanup_dir)
        return 1

    if not _wait_for_parent_exit(args.parent_pid):
        error = f"parent exit timed out: {args.parent_pid}"
        _record_handoff_log(error)
        _write_upgrade_result(args=args, phase="failed", failed_stage="wait_parent", error=error)
        return 1

    _write_upgrade_result(args=args, phase="running")

    if not _stop_services_before_upgrade(args):
        error = "service stop timed out"
        _record_handoff_log(error)
        _write_upgrade_result(args=args, phase="failed", failed_stage="stop", error=error)
        return 1

    backup_path = Path(args.backup_path)

    try:
        _apply_new_source(args)
    except Exception as exc:
        error = str(exc)
        _record_handoff_log(f"source_replace_failed error={error}")
        _write_upgrade_result(
            args=args,
            phase="failed",
            failed_stage="source_replace",
            error=error,
            backup_path=backup_path,
        )
        return 1

    try:
        task_error = _run_upgrade_tasks(args)
    except Exception as exc:
        task_error = f"upgrade tasks crashed: {exc}"
    if task_error is not None:
        _record_handoff_log(f"install_failed error={task_error}")
        _write_upgrade_result(
            args=args,
            phase="failed",
            failed_stage="install",
            error=task_error,
            backup_path=backup_path,
            stderr=task_error,
        )
        return 1

    _report_pending_pro_bundle_install_receipt(args)
    started, stdout, stderr = _start_service_after_upgrade(args)
    if not started:
        error = "service restart failed"
        _write_upgrade_result(
            args=args,
            phase="failed",
            failed_stage="start",
            error=error,
            backup_path=backup_path,
            stdout=stdout,
            stderr=stderr,
        )
        return 1

    _write_upgrade_result(args=args, phase="done", backup_path=backup_path)
    _cleanup_dir(args.cleanup_dir)
    return 0


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
    if args.mode == "upgrade":
        return _run_simple_upgrade(args)

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

    supervisor_stopped = False
    if args.prepare_handover:
        supervisor_stopped = _stop_supervisor_before_restart(
            backend_port=args.backend_port,
            service_ports=(args.frontend_port,),
        )
        if not supervisor_stopped:
            _record_handoff_log("legacy_handover_stop_timeout")
            _cleanup_dir(args.cleanup_dir)
            return 1
    elif args.pro_wheel_path or args.pro_bundle_manifest_path:
        supervisor_stopped = _stop_supervisor_before_restart(
            backend_port=args.backend_port,
            service_ports=(args.frontend_port,),
        )
        if not supervisor_stopped:
            _record_handoff_log("pro_handover_stop_timeout")
            _cleanup_dir(args.cleanup_dir)
            return 1
        if not _ensure_backend_port_free(args.backend_port):
            _record_handoff_log(f"backend_port_unavailable port={args.backend_port}")
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
        _record_handoff_log(f"upgrade_tasks_failed error={task_error}")
        _cleanup_dir(args.cleanup_dir)
        return 1
    _report_pending_pro_bundle_install_receipt(args)

    if not supervisor_stopped and not _stop_supervisor_before_restart():
        _record_handoff_log("supervisor_stop_timeout")
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
        _cleanup_dir(args.cleanup_dir)
        return 1

    _record_handoff_log(f"restart_spawned pid={process.pid}")
    _cleanup_dir(args.cleanup_dir)
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
