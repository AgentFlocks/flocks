"""Local supervisor control API client helpers."""

from __future__ import annotations

import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import httpx

from flocks.cli.service_config import ServiceConfig, service_config_from_status_payload, service_config_payload

# Fixed TCP control port daemons used before each runtime root recorded its own; clients
# still try it when a runtime has no port record (a daemon started by an older version).
SUPERVISOR_CONTROL_PORT = 48765
SUPERVISOR_LOG_FILENAME = "daemon.log"
SUPERVISOR_SOCKET_FILENAME = "service-daemon.sock"
SUPERVISOR_PORT_FILENAME = "service-daemon.port"
CONTROL_OWNER_PROBE_TIMEOUT_SECONDS = 2.0
_PORT_RECORD_REPLACE_ATTEMPTS = 20
_PORT_RECORD_REPLACE_RETRY_SECONDS = 0.05


class ForeignSupervisorError(httpx.ConnectError):
    """The TCP control port is answered by the daemon of another runtime root."""


@dataclass(frozen=True)
class DaemonStatus:
    pid: int | None
    uptime: float | None
    version: str | None
    state: str
    log_path: str | None


@dataclass(frozen=True)
class ManagedServiceStatus:
    pid: int | None
    host: str
    port: int | None
    state: str
    health: str
    last_error: str | None
    restart_count: int
    last_restart_at: float | None
    log_path: str | None
    command: tuple[str, ...]
    paused: bool = False


@dataclass(frozen=True)
class SupervisorStatus:
    daemon: DaemonStatus
    backend: ManagedServiceStatus
    webui: ManagedServiceStatus
    config: ServiceConfig
    raw: dict[str, Any]


def _default_runtime_paths():
    from flocks.cli.service_manager import runtime_paths

    return runtime_paths()


def supervisor_log_path(paths) -> Path:
    """Return the supervisor daemon log path."""
    return paths.log_dir / SUPERVISOR_LOG_FILENAME


def supervisor_socket_path(paths) -> Path:
    """Return the Unix control socket path for the supervisor daemon."""
    return paths.run_dir / SUPERVISOR_SOCKET_FILENAME


def supervisor_port_path(paths) -> Path:
    """Return the file where a TCP-control daemon records its port for this runtime root."""
    return paths.run_dir / SUPERVISOR_PORT_FILENAME


def configured_control_port() -> int | None:
    """Return the TCP control port pinned by FLOCKS_CONTROL_PORT, if any."""
    raw = os.getenv("FLOCKS_CONTROL_PORT")
    if raw and raw.isdigit():
        value = int(raw)
        if 0 < value < 65536:
            return value
    return None


def read_recorded_control_port(paths) -> int | None:
    """Return the TCP control port this runtime root's daemon recorded, if readable."""
    try:
        raw = supervisor_port_path(paths).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw.isdigit() and 0 < int(raw) < 65536:
        return int(raw)
    return None


def record_control_port(paths, port: int) -> None:
    """Record the daemon's TCP control port where this runtime root's clients look for it."""
    path = supervisor_port_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{port}\n", encoding="utf-8")
    for attempt in range(_PORT_RECORD_REPLACE_ATTEMPTS):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            # Windows will not replace a file another process has open; clients only read it briefly.
            if attempt == _PORT_RECORD_REPLACE_ATTEMPTS - 1:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(_PORT_RECORD_REPLACE_RETRY_SECONDS)


def clear_recorded_control_port(paths, port: int) -> None:
    """Remove the port record while it still names *port*; a newer daemon's record stays."""
    if read_recorded_control_port(paths) != port:
        return
    try:
        supervisor_port_path(paths).unlink(missing_ok=True)
    except OSError:
        # A client reading the record at this moment keeps it open on Windows; a stale
        # record is harmless because clients check who answers the port.
        pass


def supervisor_control_port(paths=None) -> int:
    """Return the local TCP control port used on Windows.

    FLOCKS_CONTROL_PORT wins; otherwise the port this runtime root's daemon recorded, and
    the legacy fixed port when nothing is recorded.
    """
    configured = configured_control_port()
    if configured is not None:
        return configured
    recorded = read_recorded_control_port(paths or _default_runtime_paths())
    return recorded if recorded is not None else SUPERVISOR_CONTROL_PORT


def supervisor_uses_tcp_control() -> bool:
    """Return True when the daemon control API should use localhost TCP."""
    return sys.platform == "win32" or not hasattr(socket, "AF_UNIX")


def _same_path(left: str, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(str(right)))


def status_belongs_to_runtime(payload: Any, paths) -> bool:
    """Return True when a /status payload comes from the daemon of this runtime root.

    Every daemon reports its own ``daemon.log_path``, which lives under its runtime root.
    """
    daemon = payload.get("daemon") if isinstance(payload, dict) else None
    log_path = daemon.get("log_path") if isinstance(daemon, dict) else None
    return isinstance(log_path, str) and bool(log_path) and _same_path(log_path, supervisor_log_path(paths))


def _ensure_control_port_owner(port: int, paths, timeout: float | None) -> None:
    """Refuse a TCP control port answered by another runtime root's daemon.

    A localhost port is shared by every runtime on the host, unlike the Unix socket in each
    runtime's run directory; without this check one instance's start/stop/restart acted on
    whichever daemon held the port.
    """
    probe_timeout = CONTROL_OWNER_PROBE_TIMEOUT_SECONDS if timeout is None else min(timeout, CONTROL_OWNER_PROBE_TIMEOUT_SECONDS)
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=probe_timeout, trust_env=False) as probe:
        response = probe.get("/status")
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPStatusError, ValueError):
        payload = None
    if not status_belongs_to_runtime(payload, paths):
        raise ForeignSupervisorError(
            f"control port {port} is not served by this runtime's Flocks daemon",
            request=response.request,
        )


def supervisor_control_client(paths=None, timeout: float | None = 2.0) -> httpx.Client:
    """Create a client for the local daemon control API.

    On TCP control the daemon behind the port is checked first; a port answered for another
    runtime root raises ForeignSupervisorError, which callers treat like an unreachable daemon.
    """
    current = paths or _default_runtime_paths()
    if supervisor_uses_tcp_control():
        port = supervisor_control_port(current)
        _ensure_control_port_owner(port, current, timeout)
        return httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=timeout, trust_env=False)
    transport = httpx.HTTPTransport(uds=str(supervisor_socket_path(current)))
    return httpx.Client(base_url="http://flocks.local", timeout=timeout, trust_env=False, transport=transport)


def control_api_request(
    method: str,
    path: str,
    *,
    paths=None,
    timeout: float | None = 2.0,
    **kwargs,
) -> httpx.Response:
    """Send one local control API request."""
    with supervisor_control_client(paths, timeout=timeout) as client:
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response


def supervisor_is_running(paths=None) -> bool:
    """Return True when the local supervisor control API responds."""
    try:
        control_api_request("GET", "/status", paths=paths, timeout=0.75)
        return True
    except Exception:
        return False


def _read_control_json(path: str, *, paths=None, timeout: float | None = 2.0) -> dict[str, Any]:
    response = control_api_request("GET", path, paths=paths, timeout=timeout)
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("daemon control API returned an invalid response.")
    return payload


def _post_control_json(
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    paths=None,
    timeout: float | None = 5.0,
) -> dict[str, Any]:
    response = control_api_request("POST", path, paths=paths, timeout=timeout, json=payload or {})
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("daemon control API returned an invalid response.")
    return data


def read_supervisor_status(paths=None, timeout: float | None = 2.0) -> SupervisorStatus:
    """Read and parse the current supervisor status."""
    return parse_supervisor_status(_read_control_json("/status", paths=paths, timeout=timeout))


def request_stop(paths=None, timeout: float | None = 2.0) -> dict[str, Any]:
    """Ask the supervisor daemon to stop itself and its children."""
    return _post_control_json("/stop", paths=paths, timeout=timeout)


def request_restart(
    config: ServiceConfig,
    *,
    paths=None,
    timeout: float | None = 180.0,
) -> SupervisorStatus:
    """Ask the supervisor daemon to restart all managed services."""
    payload = _post_control_json("/restart", payload=service_config_payload(config), paths=paths, timeout=timeout)
    return parse_supervisor_status(payload)


def request_restart_backend(*, paths=None, timeout: float | None = 180.0) -> SupervisorStatus:
    """Ask the supervisor daemon to restart backend."""
    payload = _post_control_json("/restart/backend", paths=paths, timeout=timeout)
    return parse_supervisor_status(payload)


def request_restart_webui(
    config: ServiceConfig,
    *,
    force_frontend_build: bool = False,
    paths=None,
    timeout: float | None = 180.0,
) -> SupervisorStatus:
    """Ask the supervisor daemon to restart WebUI."""
    payload = service_config_payload(config)
    if force_frontend_build:
        payload["force_frontend_build"] = True
    data = _post_control_json("/restart/webui", payload=payload, paths=paths, timeout=timeout)
    return parse_supervisor_status(data)


def read_logs(
    *,
    service: str,
    lines: int,
    paths=None,
    timeout: float | None = 5.0,
) -> dict[str, Any]:
    """Read recent service logs through the supervisor control API."""
    return _read_control_json(
        f"/logs?service={service}&lines={lines}&follow=false",
        paths=paths,
        timeout=timeout,
    )


def stream_logs(
    *,
    service: str,
    lines: int,
    paths=None,
    timeout: float | None = None,
) -> Iterator[str]:
    """Stream service logs through the supervisor control API."""
    params = {"service": service, "lines": str(lines), "follow": "true"}
    with supervisor_control_client(paths, timeout=timeout) as client:
        with client.stream("GET", "/logs", params=params) as response:
            response.raise_for_status()
            yield from response.iter_lines()


def parse_supervisor_status(payload: dict[str, Any]) -> SupervisorStatus:
    """Parse a supervisor status payload into typed status objects."""
    daemon = payload.get("daemon") if isinstance(payload.get("daemon"), dict) else {}
    backend = payload.get("backend") if isinstance(payload.get("backend"), dict) else {}
    webui = payload.get("webui") if isinstance(payload.get("webui"), dict) else {}
    return SupervisorStatus(
        daemon=_parse_daemon_status(daemon),
        backend=_parse_service_status(backend),
        webui=_parse_service_status(webui),
        config=service_config_from_status_payload(payload),
        raw=payload,
    )


def _parse_daemon_status(payload: dict[str, Any]) -> DaemonStatus:
    return DaemonStatus(
        pid=_optional_int(payload.get("pid")),
        uptime=_optional_float(payload.get("uptime")),
        version=str(payload["version"]) if payload.get("version") is not None else None,
        state=str(payload.get("state") or "unknown"),
        log_path=str(payload["log_path"]) if payload.get("log_path") is not None else None,
    )


def _parse_service_status(payload: dict[str, Any]) -> ManagedServiceStatus:
    command = payload.get("command") if isinstance(payload.get("command"), list) else []
    return ManagedServiceStatus(
        pid=_optional_int(payload.get("pid")),
        host=str(payload.get("host") or "127.0.0.1"),
        port=_optional_int(payload.get("port")),
        state=str(payload.get("state") or "unknown"),
        health=str(payload.get("health") or payload.get("state") or "unknown"),
        last_error=str(payload["last_error"]) if payload.get("last_error") is not None else None,
        restart_count=_optional_int(payload.get("restart_count")) or 0,
        last_restart_at=_optional_float(payload.get("last_restart_at")),
        log_path=str(payload["log_path"]) if payload.get("log_path") is not None else None,
        command=tuple(str(item) for item in command),
        paused=bool(payload.get("paused")),
    )


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (float, int)):
        return float(value)
    return None
