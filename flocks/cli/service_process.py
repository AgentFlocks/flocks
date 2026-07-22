"""Process adapters used by the service supervisor."""

from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass
from typing import Protocol


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
        return ServiceProbeResult(healthy=True, reason="liveness check passed")


def tcp_port_accepts_connections(host: str, port: int) -> bool:
    """Return True when a local service accepts TCP connections."""
    from flocks.cli.service_manager import access_host

    try:
        with socket.create_connection((access_host(host), port), timeout=1.0):
            return True
    except OSError:
        return False
