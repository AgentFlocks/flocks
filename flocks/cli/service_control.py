"""Local supervisor control API client helpers."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import httpx

from flocks.cli.service_config import ServiceConfig, service_config_from_status_payload, service_config_payload

SUPERVISOR_CONTROL_PORT = 48765
SUPERVISOR_LOG_FILENAME = "daemon.log"
SUPERVISOR_SOCKET_FILENAME = "service-daemon.sock"


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


def supervisor_control_port() -> int:
    """Return the local TCP control port used on Windows."""
    raw = os.getenv("FLOCKS_CONTROL_PORT")
    if raw and raw.isdigit():
        value = int(raw)
        if 0 < value < 65536:
            return value
    return SUPERVISOR_CONTROL_PORT


def supervisor_control_client(paths=None, timeout: float | None = 2.0) -> httpx.Client:
    """Create a client for the local daemon control API."""
    if sys.platform == "win32":
        return httpx.Client(
            base_url=f"http://127.0.0.1:{supervisor_control_port()}",
            timeout=timeout,
            trust_env=False,
        )
    current = paths or _default_runtime_paths()
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


def request_stop_webui(*, paths=None, timeout: float | None = 30.0) -> SupervisorStatus:
    """Ask the supervisor daemon to stop WebUI only."""
    payload = _post_control_json("/stop/webui", paths=paths, timeout=timeout)
    return parse_supervisor_status(payload)


def request_prepare_upgrade(*, paths=None, timeout: float | None = 30.0) -> SupervisorStatus:
    """Ask the supervisor daemon to pause managed services for upgrade handoff."""
    payload = _post_control_json("/upgrade/prepare", paths=paths, timeout=timeout)
    return parse_supervisor_status(payload)


def request_resume_upgrade(
    config: ServiceConfig,
    *,
    paths=None,
    timeout: float | None = 180.0,
) -> SupervisorStatus:
    """Ask the supervisor daemon to resume managed services after upgrade handoff."""
    payload = _post_control_json("/upgrade/resume", payload=service_config_payload(config), paths=paths, timeout=timeout)
    return parse_supervisor_status(payload)


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
