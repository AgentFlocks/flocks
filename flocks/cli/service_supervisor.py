"""Supervisor daemon for the local Flocks service."""

from __future__ import annotations

import datetime
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from flocks.browser.admin import stop_all_daemons as stop_all_browser_daemons
from flocks.cli.service_config import service_config_from_payload, service_config_payload
from flocks.cli.service_control import (
    supervisor_control_port,
    supervisor_log_path,
    supervisor_socket_path,
)
from flocks.cli.service_process import BackendProcessAdapter, ProcessAdapter, WebUIProcessAdapter

SUPERVISOR_CHECK_INTERVAL_SECONDS = 5.0
SUPERVISOR_HEALTH_FAILURE_THRESHOLD = 2
SUPERVISOR_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)


@dataclass
class ManagedService:
    name: str
    label: str
    host: str
    port: int
    log_path: Path
    process: subprocess.Popen | None = None
    command: tuple[str, ...] = ()
    state: str = "stopped"
    last_error: str | None = None
    restart_count: int = 0
    last_restart_at: float | None = None
    health_failure_count: int = 0
    next_restart_at: float = 0.0
    built_once: bool = False

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process is not None else None


def _daemon_log(event: str, details: dict[str, object] | None = None) -> None:
    """Write a structured supervisor log line to stdout."""
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    suffix = ""
    if details:
        suffix = " " + json.dumps(details, ensure_ascii=True, sort_keys=True)
    sys.stdout.write(f"[{timestamp}] supervisor.{event}{suffix}\n")
    sys.stdout.flush()


def _health_status_from_service_state(state: str) -> str:
    if state in {"healthy", "starting", "restarting", "stopped", "paused"}:
        return state
    return "degraded"


def _service_payload(service: ManagedService, *, paused: bool = False) -> dict[str, object]:
    return {
        "pid": service.pid,
        "host": service.host,
        "port": service.port,
        "state": "paused" if paused else service.state,
        "health": _health_status_from_service_state("paused" if paused else service.state),
        "last_error": service.last_error,
        "restart_count": service.restart_count,
        "last_restart_at": service.last_restart_at,
        "log_path": str(service.log_path),
        "command": list(service.command),
        "paused": paused,
    }


class _UnixControlServer(ThreadingHTTPServer):
    address_family = socket.AF_UNIX


class SupervisorDaemon:
    """Owns backend/WebUI child processes and exposes a local control API."""

    def __init__(
        self,
        config,
        *,
        interval: float = SUPERVISOR_CHECK_INTERVAL_SECONDS,
        failure_threshold: int = SUPERVISOR_HEALTH_FAILURE_THRESHOLD,
        backend_adapter: ProcessAdapter | None = None,
        webui_adapter: ProcessAdapter | None = None,
    ) -> None:
        from flocks.cli.service_manager import ensure_runtime_dirs

        self.config = config
        self.paths = ensure_runtime_dirs()
        self.interval = interval
        self.failure_threshold = failure_threshold
        self.backend_adapter = backend_adapter or BackendProcessAdapter()
        self.webui_adapter = webui_adapter or WebUIProcessAdapter()
        self.started_at = time.time()
        self._lock = threading.RLock()
        self._shutdown_requested = threading.Event()
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._backend_paused = False
        self._webui_paused = False
        self.backend = ManagedService(
            name="backend",
            label="后端",
            host=config.backend_host,
            port=config.backend_port,
            log_path=self.paths.backend_log,
        )
        self.webui = ManagedService(
            name="webui",
            label="WebUI",
            host=config.frontend_host,
            port=config.frontend_port,
            log_path=self.paths.frontend_log,
        )

    def run(self) -> None:
        """Run the supervisor until the control API asks it to stop."""
        self._install_signal_handlers()
        self._cleanup_legacy_runtime()
        self._start_control_server()
        try:
            self.restart_all(reason="startup")
            while not self._shutdown_requested.wait(self.interval):
                self.tick()
        finally:
            self.shutdown_children()
            self._stop_control_server()
            stop_all_browser_daemons()
            _daemon_log("stopped")

    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        def _handle(_signum, _frame) -> None:
            self.request_stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handle)
            except (OSError, ValueError):  # pragma: no cover - platform defensive
                pass

    def _cleanup_legacy_runtime(self) -> None:
        from flocks.cli import service_manager

        console = service_manager._StdoutConsole()
        for pid_file, name in (
            (service_manager.watchdog_pid_path(self.paths), "watchdog"),
            (self.paths.frontend_pid, "WebUI"),
            (self.paths.backend_pid, "backend"),
        ):
            record = service_manager.read_runtime_record(pid_file)
            if record is not None and service_manager.runtime_record_is_running(record):
                service_manager.stop_runtime_record_process(pid_file, name, console)
            else:
                pid_file.unlink(missing_ok=True)

    def _start_control_server(self) -> None:
        handler = self._handler_class()
        if sys.platform == "win32":
            server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", supervisor_control_port()), handler)
        else:
            socket_path = supervisor_socket_path(self.paths)
            socket_path.parent.mkdir(parents=True, exist_ok=True)
            socket_path.unlink(missing_ok=True)
            server = _UnixControlServer(str(socket_path), handler)
        self._server = server
        self._server_thread = threading.Thread(target=server.serve_forever, name="flocks-supervisor-control", daemon=True)
        self._server_thread.start()
        _daemon_log("control_started", {"platform": sys.platform})

    def _stop_control_server(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._server_thread is not None:
            self._server_thread.join(timeout=5.0)
        if sys.platform != "win32":
            supervisor_socket_path(self.paths).unlink(missing_ok=True)

    def _handler_class(self):
        daemon = self

        class ControlHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, _format, *_args) -> None:
                return

            def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or "0")
                if length <= 0:
                    return {}
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return {}
                return payload if isinstance(payload, dict) else {}

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                try:
                    if parsed.path == "/status":
                        self._send_json(daemon.status_payload())
                        return
                    if parsed.path == "/logs":
                        daemon.handle_logs_request(self, parse_qs(parsed.query))
                        return
                    self._send_json({"error": "not found"}, status=404)
                except Exception as exc:  # pragma: no cover - defensive control path
                    self._send_json({"error": str(exc)}, status=500)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                payload = self._read_json()
                try:
                    if parsed.path == "/stop":
                        daemon.request_stop()
                        self._send_json({"status": "stopping"})
                        return
                    if parsed.path == "/restart":
                        daemon.update_config(payload)
                        daemon.restart_all(reason="control restart")
                        self._send_json(daemon.status_payload())
                        return
                    if parsed.path == "/restart/backend":
                        daemon.restart_backend(reason="control restart")
                        self._send_json(daemon.status_payload())
                        return
                    if parsed.path == "/restart/webui":
                        daemon.update_config(payload)
                        daemon.restart_webui(
                            reason="control restart",
                            force_frontend_build=bool(payload.get("force_frontend_build")),
                        )
                        self._send_json(daemon.status_payload())
                        return
                    if parsed.path == "/stop/webui":
                        daemon.stop_webui(reason="control stop")
                        self._send_json(daemon.status_payload())
                        return
                    if parsed.path == "/upgrade/prepare":
                        daemon.prepare_upgrade(reason="control upgrade prepare")
                        self._send_json(daemon.status_payload())
                        return
                    if parsed.path == "/upgrade/resume":
                        daemon.update_config(payload)
                        daemon.resume_upgrade(reason="control upgrade resume")
                        self._send_json(daemon.status_payload())
                        return
                    self._send_json({"error": "not found"}, status=404)
                except Exception as exc:  # pragma: no cover - defensive control path
                    self._send_json({"error": str(exc)}, status=500)

        return ControlHandler

    def update_config(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.config = service_config_from_payload(payload, self.config)
            self.backend.host = self.config.backend_host
            self.backend.port = self.config.backend_port
            self.webui.host = self.config.frontend_host
            self.webui.port = self.config.frontend_port

    def request_stop(self) -> None:
        self._shutdown_requested.set()

    def status_payload(self) -> dict[str, object]:
        try:
            from flocks import __version__
        except Exception:  # pragma: no cover - defensive
            __version__ = "unknown"
        with self._lock:
            return {
                "daemon": {
                    "pid": os.getpid(),
                    "uptime": time.time() - self.started_at,
                    "version": __version__,
                    "state": "stopping" if self._shutdown_requested.is_set() else "running",
                    "log_path": str(supervisor_log_path(self.paths)),
                },
                "backend": _service_payload(self.backend, paused=self._backend_paused),
                "webui": _service_payload(self.webui, paused=self._webui_paused),
                "config": service_config_payload(self.config),
            }

    def handle_logs_request(self, handler: BaseHTTPRequestHandler, query: dict[str, list[str]]) -> None:
        from flocks.cli.service_manager import FOLLOW_POLL_INTERVAL, _coerce_positive_int, tail_lines

        service_name = (query.get("service") or ["backend"])[0]
        lines = _coerce_positive_int((query.get("lines") or ["50"])[0]) or 50
        follow = (query.get("follow") or ["false"])[0].lower() == "true"
        selections = self._log_paths_for_service(service_name)
        if not selections:
            body = json.dumps({"error": "unknown service"}, ensure_ascii=False).encode("utf-8")
            handler.send_response(400)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            return

        for _prefix, log_path in selections:
            log_path.touch(exist_ok=True)
        if not follow:
            body = json.dumps(
                {
                    "service": service_name,
                    "logs": {
                        prefix: {
                            "path": str(log_path),
                            "lines": tail_lines(log_path, lines),
                        }
                        for prefix, log_path in selections
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8")
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            return

        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.end_headers()
        for prefix, log_path in selections:
            handler.wfile.write((f"[{prefix}] --- {log_path} ---\n").encode("utf-8", errors="replace"))
            for line in tail_lines(log_path, lines):
                handler.wfile.write((f"[{prefix}] {line}\n").encode("utf-8", errors="replace"))
        handler.wfile.flush()
        handles = {}
        try:
            for prefix, log_path in selections:
                handle = log_path.open("r", encoding="utf-8", errors="replace")
                handle.seek(0, os.SEEK_END)
                handles[prefix] = handle
            while not self._shutdown_requested.is_set():
                emitted = False
                for prefix, handle in handles.items():
                    while True:
                        line = handle.readline()
                        if not line:
                            break
                        emitted = True
                        handler.wfile.write((f"[{prefix}] {line}").encode("utf-8", errors="replace"))
                if emitted:
                    handler.wfile.flush()
                else:
                    time.sleep(FOLLOW_POLL_INTERVAL)
        finally:
            for handle in handles.values():
                handle.close()

    def _log_paths_for_service(self, service_name: str) -> list[tuple[str, Path]]:
        if service_name == "backend":
            return [("backend", self.paths.backend_log)]
        if service_name == "webui":
            return [("webui", self.paths.frontend_log)]
        if service_name in {"daemon", "supervisor"}:
            return [("daemon", supervisor_log_path(self.paths))]
        if service_name == "all":
            return [
                ("backend", self.paths.backend_log),
                ("webui", self.paths.frontend_log),
                ("daemon", supervisor_log_path(self.paths)),
            ]
        return []

    def restart_all(self, *, reason: str) -> None:
        with self._lock:
            self._backend_paused = False
            self._webui_paused = False
            self._restart_service(self.webui, reason=reason, immediate=True)
            self._restart_service(self.backend, reason=reason, immediate=True)
            self._start_backend_locked(immediate=True)
            self._start_webui_locked(immediate=True)

    def restart_backend(self, *, reason: str) -> None:
        with self._lock:
            self._backend_paused = False
            self._restart_service(self.backend, reason=reason, immediate=True)
            self._start_backend_locked(immediate=True)

    def restart_webui(self, *, reason: str, force_frontend_build: bool = False) -> None:
        with self._lock:
            self._webui_paused = False
            if force_frontend_build:
                self.webui.built_once = False
            self._restart_service(self.webui, reason=reason, immediate=True)
            self._start_webui_locked(immediate=True)

    def stop_webui(self, *, reason: str) -> None:
        with self._lock:
            self._webui_paused = True
            _daemon_log("service_pause", {"service": "webui", "reason": reason})
            self._stop_service(self.webui)
            self.webui.last_error = reason

    def prepare_upgrade(self, *, reason: str) -> None:
        with self._lock:
            self._backend_paused = True
            self._webui_paused = True
            _daemon_log("service_pause", {"service": "backend", "reason": reason})
            _daemon_log("service_pause", {"service": "webui", "reason": reason})
            self.backend.last_error = reason
            self.webui.last_error = reason
            self._stop_service(self.webui)

    def resume_upgrade(self, *, reason: str) -> None:
        with self._lock:
            self._backend_paused = False
            self._webui_paused = False
            _daemon_log("service_resume", {"service": "backend", "reason": reason})
            _daemon_log("service_resume", {"service": "webui", "reason": reason})
            self._probe_backend_locked()
            self._probe_webui_locked()
            self._start_backend_locked(immediate=True)
            self._start_webui_locked(immediate=True)

    def shutdown_children(self) -> None:
        with self._lock:
            self._stop_service(self.webui)
            self._stop_service(self.backend)

    def tick(self) -> None:
        with self._lock:
            if not self._backend_paused:
                self._probe_backend_locked()
            if not self._webui_paused:
                self._probe_webui_locked()
            if not self._backend_paused:
                self._start_backend_locked(immediate=False)
            if not self._webui_paused:
                self._start_webui_locked(immediate=False)

    def _restart_service(self, service: ManagedService, *, reason: str, immediate: bool) -> None:
        _daemon_log("service_restart", {"service": service.name, "reason": reason})
        self._stop_service(service)
        service.state = "restarting"
        service.last_error = reason
        service.health_failure_count = 0
        service.restart_count += 1
        service.last_restart_at = time.time()
        service.next_restart_at = time.monotonic() if immediate else self._next_restart_time(service.restart_count)

    def _stop_service(self, service: ManagedService) -> None:
        adapter = self._adapter_for(service)
        adapter.stop(service.process)
        service.process = None
        service.command = ()
        service.state = "stopped"

    def _start_backend_locked(self, *, immediate: bool) -> None:
        if self.backend.process is not None and self.backend.process.poll() is None:
            return
        if not immediate and time.monotonic() < self.backend.next_restart_at:
            return
        self.backend.state = "starting"
        try:
            process = self.backend_adapter.start(self.config, self.paths)
        except Exception as exc:
            self._mark_start_failed(self.backend, exc)
            return
        self.backend.process = process
        self.backend.command = tuple(str(item) for item in process.args)
        self.backend.state = "healthy"
        self.backend.last_error = None
        self.backend.health_failure_count = 0

    def _start_webui_locked(self, *, immediate: bool) -> None:
        if self.webui.process is not None and self.webui.process.poll() is None:
            return
        if not immediate and time.monotonic() < self.webui.next_restart_at:
            return
        self.webui.state = "starting"
        try:
            process = self.webui_adapter.start(self.config, self.paths, built_once=self.webui.built_once)
        except Exception as exc:
            self._mark_start_failed(self.webui, exc)
            return
        self.webui.process = process
        self.webui.command = tuple(str(item) for item in process.args)
        self.webui.state = "healthy"
        self.webui.last_error = None
        self.webui.health_failure_count = 0
        self.webui.built_once = True

    def _mark_start_failed(self, service: ManagedService, error: Exception) -> None:
        service.process = None
        service.state = "degraded"
        service.last_error = str(error)
        service.next_restart_at = self._next_restart_time(service.restart_count)
        _daemon_log(
            "service_start_failed",
            {"service": service.name, "error": str(error), "retry_at": service.next_restart_at},
        )

    def _next_restart_time(self, restart_count: int) -> float:
        index = min(max(restart_count, 1) - 1, len(SUPERVISOR_BACKOFF_SECONDS) - 1)
        return time.monotonic() + SUPERVISOR_BACKOFF_SECONDS[index]

    def _probe_backend_locked(self) -> None:
        result = self.backend_adapter.probe(self.backend.process, self.backend.host, self.backend.port)
        if self.backend.process is None:
            self.backend.state = "stopped"
            return
        if result.restart:
            self._restart_service(self.backend, reason=result.reason or "backend probe failed", immediate=True)
            return
        if result.healthy:
            self.backend.state = "healthy"
            self.backend.health_failure_count = 0
            self.backend.last_error = None
            return

        self.backend.health_failure_count += 1
        self.backend.state = "degraded"
        self.backend.last_error = result.reason
        if self.backend.health_failure_count >= self.failure_threshold:
            self._restart_service(self.backend, reason=result.reason or "backend health failed", immediate=True)

    def _probe_webui_locked(self) -> None:
        result = self.webui_adapter.probe(self.webui.process, self.webui.host, self.webui.port)
        if self.webui.process is None:
            self.webui.state = "stopped"
            return
        if result.restart:
            self._restart_service(self.webui, reason=result.reason or "webui probe failed", immediate=True)
            return
        self.webui.state = "healthy"
        self.webui.health_failure_count = 0
        self.webui.last_error = None

    def _adapter_for(self, service: ManagedService) -> ProcessAdapter:
        return self.backend_adapter if service.name == "backend" else self.webui_adapter


def run_service_daemon(
    config,
    *,
    interval: float = SUPERVISOR_CHECK_INTERVAL_SECONDS,
    failure_threshold: int = SUPERVISOR_HEALTH_FAILURE_THRESHOLD,
) -> None:
    """Run the local supervisor daemon."""
    _daemon_log(
        "started",
        {
            "backend_host": config.backend_host,
            "backend_port": config.backend_port,
            "frontend_host": config.frontend_host,
            "frontend_port": config.frontend_port,
        },
    )
    SupervisorDaemon(config, interval=interval, failure_threshold=failure_threshold).run()
