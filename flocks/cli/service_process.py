"""Process adapters used by the service supervisor."""

from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass
from typing import Protocol

import httpx

from flocks.cli.service_config import ServiceConfig


@dataclass(frozen=True)
class ServiceProbeResult:
    healthy: bool
    reason: str | None = None
    restart: bool = False


class ProcessAdapter(Protocol):
    name: str
    label: str

    def start(self, config: ServiceConfig, paths, *, built_once: bool = False) -> subprocess.Popen:
        """Start the service process."""

    def stop(self, process: subprocess.Popen | None) -> None:
        """Stop the service process group."""

    def probe(self, process: subprocess.Popen | None, host: str, port: int) -> ServiceProbeResult:
        """Probe service process and listener health."""


class BackendProcessAdapter:
    name = "backend"
    label = "后端"

    def start(self, config: ServiceConfig, paths, *, built_once: bool = False) -> subprocess.Popen:
        del built_once
        from flocks.cli.service_manager import _StdoutConsole, _start_backend_process

        return _start_backend_process(config, _StdoutConsole(), paths=paths)

    def stop(self, process: subprocess.Popen | None) -> None:
        from flocks.cli.service_manager import _StdoutConsole, _terminate_process

        _terminate_process(process, self.label, _StdoutConsole())

    def probe(self, process: subprocess.Popen | None, host: str, port: int) -> ServiceProbeResult:
        if process is None:
            return ServiceProbeResult(healthy=False, reason="stopped")
        if process.poll() is not None:
            return ServiceProbeResult(
                healthy=False,
                reason=f"process exited with code {process.returncode}",
                restart=True,
            )
        if not tcp_port_accepts_connections(host, port):
            return ServiceProbeResult(healthy=False, reason=f"port {port} is not listening", restart=True)

        from flocks.cli.service_manager import _backend_health_url, _is_healthy_status_response, backend_access_base_url

        url = _backend_health_url(host, port)
        try:
            with httpx.Client(timeout=2.0, trust_env=False) as client:
                response = client.get(url)
                root_response = client.get(
                    backend_access_base_url(ServiceConfig(backend_host=host, backend_port=port)),
                    headers={"Accept": "text/html"},
                )
            healthy = _is_healthy_status_response(response) and _is_static_webui_response(root_response)
            reason = f"health status={response.status_code}, root status={root_response.status_code}"
        except Exception as exc:
            healthy = False
            reason = f"health failed: {exc}"
        return ServiceProbeResult(healthy=healthy, reason=reason)


def _is_static_webui_response(response: httpx.Response) -> bool:
    """Return True only when the unified service serves the SPA index."""
    content_type = response.headers.get("content-type", "").lower()
    return response.status_code == 200 and "text/html" in content_type


def tcp_port_accepts_connections(host: str, port: int) -> bool:
    """Return True when a local service accepts TCP connections."""
    from flocks.cli.service_manager import access_host

    try:
        with socket.create_connection((access_host(host), port), timeout=1.0):
            return True
    except OSError:
        return False
