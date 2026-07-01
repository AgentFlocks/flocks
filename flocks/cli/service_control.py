"""Local supervisor control API client helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx

SUPERVISOR_CONTROL_PORT = 48765
SUPERVISOR_LOG_FILENAME = "supervisor.log"
SUPERVISOR_SOCKET_FILENAME = "service-daemon.sock"


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


def read_control_json(path: str, *, paths=None, timeout: float | None = 2.0) -> dict[str, Any]:
    response = control_api_request("GET", path, paths=paths, timeout=timeout)
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("daemon control API returned an invalid response.")
    return payload


def read_supervisor_status(paths=None, timeout: float | None = 2.0) -> dict[str, Any]:
    """Read the current supervisor status from the local control API."""
    return read_control_json("/status", paths=paths, timeout=timeout)


def post_control_json(
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


def service_config_payload(config) -> dict[str, object]:
    """Serialize a ServiceConfig-like object for the supervisor control API."""
    return {
        "backend_host": config.backend_host,
        "backend_port": config.backend_port,
        "frontend_host": config.frontend_host,
        "frontend_port": config.frontend_port,
        "no_browser": config.no_browser,
        "skip_frontend_build": config.skip_frontend_build,
    }
