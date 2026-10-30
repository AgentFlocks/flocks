"""Live progress of the upgrade handoff while the Flocks service is offline.

The web upgrade ends with the backend exiting and a detached handoff stopping the
service, replacing the source, installing dependencies, building the WebUI and
starting the service again. Nothing answers on the service port during those minutes,
so the upgrade dialog could only wait for ``/api/health``. The handoff now serves a
small status document on that port until it starts the new service.

Anyone reaching the port sees the step names and states. The error text is only
returned to requests carrying the per-upgrade token that the authenticated upgrade
stream handed to the browser.
"""

from __future__ import annotations

import hmac
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Sequence

UPGRADE_PROGRESS_TOKEN_ENV = "FLOCKS_UPGRADE_PROGRESS_TOKEN"
UPGRADE_PROGRESS_TOKEN_HEADER = "X-Flocks-Upgrade-Token"
ERROR_MAX_CHARS = 600
CLIENT_CLOSE_WAIT_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 5.0
STOP_WAIT_SECONDS = 3.0
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]+@")


def redact_credentials(text: str) -> str:
    """Mask the user:password part of URLs (registry and proxy URLs end up in tool errors)."""
    return _URL_USERINFO.sub(r"\1***@", text)


def summarize_error(error: str) -> str:
    """Keep an error readable in the dialog without credentials embedded in URLs."""
    text = redact_credentials(" ".join(str(error).split()))
    if len(text) > ERROR_MAX_CHARS:
        text = text[: ERROR_MAX_CHARS - 3] + "..."
    return text


class UpgradeProgress:
    """Thread-safe step tracker shared by the handoff and its status responder."""

    def __init__(self, planned_steps: Sequence[str], on_change: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._lock = threading.Lock()
        self._steps: list[dict[str, Any]] = [{"id": step, "status": "pending"} for step in planned_steps]
        self._phase = "running"
        self._step: str | None = None
        self._failed_step: str | None = None
        self._error: str | None = None
        self._on_change = on_change

    @property
    def current_step(self) -> str | None:
        with self._lock:
            return self._step

    def _entry(self, step: str) -> dict[str, Any]:
        for entry in self._steps:
            if entry["id"] == step:
                return entry
        entry = {"id": step, "status": "pending"}
        self._steps.append(entry)
        return entry

    def start(self, step: str) -> None:
        """Mark *step* running; earlier unfinished steps are done or were skipped."""
        now = time.time()
        with self._lock:
            target = self._entry(step)
            for entry in self._steps:
                if entry is target:
                    break
                if entry["status"] == "running":
                    entry["status"] = "done"
                    entry["finished_at"] = now
                elif entry["status"] == "pending":
                    entry["status"] = "skipped"
            target["status"] = "running"
            target["started_at"] = now
            self._step = step
        self._changed()

    def fail(self, error: str, step: str | None = None) -> None:
        now = time.time()
        with self._lock:
            failed = step or self._step or "unknown"
            entry = self._entry(failed)
            entry["status"] = "failed"
            entry["finished_at"] = now
            self._phase = "failed"
            self._failed_step = failed
            self._step = failed
            self._error = summarize_error(error)
        self._changed()

    def finish(self) -> None:
        now = time.time()
        with self._lock:
            for entry in self._steps:
                if entry["status"] == "running":
                    entry["status"] = "done"
                    entry["finished_at"] = now
            self._phase = "done"
        self._changed()

    def snapshot(self, *, include_error: bool = True) -> dict[str, Any]:
        with self._lock:
            payload: dict[str, Any] = {
                "phase": self._phase,
                "step": self._step,
                "steps": [dict(entry) for entry in self._steps],
            }
            if self._failed_step is not None:
                payload["failed_step"] = self._failed_step
            if include_error and self._error:
                payload["error"] = self._error
            return payload

    def _changed(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(self.snapshot())
        except Exception:
            pass


class ProgressResponder:
    """Answer every request on the service port with the handoff progress (HTTP 503)."""

    def __init__(self, progress: UpgradeProgress, host: str, port: int, token: str | None) -> None:
        self._progress = progress
        self._host = host
        self._port = port
        self._token = token or ""
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.failure_delivered = threading.Event()

    def start(self) -> bool:
        """Bind the service port; False when it is unavailable (the upgrade goes on without it)."""
        responder = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"
            # Idle connections end on their own; stop() also cuts off slow ones.
            timeout = REQUEST_TIMEOUT_SECONDS

            def log_message(self, _format, *_args) -> None:
                return

            def _reply(self, with_body: bool) -> None:
                trusted = responder._token_matches(self.headers.get(UPGRADE_PROGRESS_TOKEN_HEADER))
                snapshot = responder._progress.snapshot(include_error=trusted)
                body = json.dumps({"status": "upgrading", "upgrade": snapshot}, ensure_ascii=False).encode("utf-8")
                try:
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Retry-After", "2")
                    self.send_header("Connection", "close")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if with_body:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                if snapshot["phase"] == "failed" and (trusted or not responder._token):
                    responder.failure_delivered.set()

            def do_GET(self) -> None:
                self._reply(True)

            def do_POST(self) -> None:
                self._reply(True)

            def do_HEAD(self) -> None:
                self._reply(False)

        class Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6 if ":" in responder._host else socket.AF_INET
            # stop() waits for open connections itself, with an upper bound.
            daemon_threads = True

            def __init__(self, *args, **kwargs) -> None:
                self.connections: set[socket.socket] = set()
                self.connections_changed = threading.Condition()
                super().__init__(*args, **kwargs)

            def process_request(self, request, client_address) -> None:
                with self.connections_changed:
                    self.connections.add(request)
                super().process_request(request, client_address)

            def shutdown_request(self, request) -> None:
                # Let the client close first. A connection closed from this side stays in
                # TIME_WAIT on the service port, and `flocks start` probes that port without
                # SO_REUSEADDR on Windows (and where lsof is missing): it would see the port
                # as busy for minutes after the upgrade.
                deadline = time.monotonic() + CLIENT_CLOSE_WAIT_SECONDS
                try:
                    while (remaining := deadline - time.monotonic()) > 0:
                        request.settimeout(remaining)
                        if not request.recv(4096):
                            break
                except OSError:
                    pass
                self.close_request(request)
                with self.connections_changed:
                    self.connections.discard(request)
                    self.connections_changed.notify_all()

        try:
            server = Server((self._host, self._port), Handler)
        except OSError:
            return False
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="flocks-upgrade-progress", daemon=True)
        self._thread.start()
        return True

    def _token_matches(self, value: str | None) -> bool:
        return bool(self._token) and value is not None and hmac.compare_digest(value, self._token)

    def stop(self) -> None:
        """Release the service port so the upgraded service can bind it."""
        server = self._server
        self._server = None
        if server is None:
            return
        server.shutdown()
        # Open connections still hold the port: give them a bounded time to finish, then
        # cut off the ones that never complete a request.
        deadline = time.monotonic() + STOP_WAIT_SECONDS
        with server.connections_changed:
            server.connections_changed.wait_for(
                lambda: not server.connections,
                timeout=max(0.0, deadline - time.monotonic()),
            )
            stragglers = list(server.connections)
        for connection in stragglers:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if stragglers:
            with server.connections_changed:
                server.connections_changed.wait_for(lambda: not server.connections, timeout=1.0)
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def linger_for_failure(self, *, timeout: float, grace: float = 3.0) -> None:
        """Keep serving a failure until the upgrade dialog has read it, at most *timeout* seconds."""
        if self._server is None:
            return
        if self.failure_delivered.wait(timeout):
            time.sleep(grace)
        self.stop()
