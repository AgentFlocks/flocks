"""Helpers for service supervisor integration-style tests."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from flocks.cli import service_control, service_manager, service_process, service_supervisor


class SleeperProcessAdapter:
    """Process adapter that starts a real, lightweight child process."""

    def __init__(self) -> None:
        self.started: list[subprocess.Popen] = []
        self.stopped: list[int] = []

    def start(self, _config, _paths, *, built_once: bool = False) -> subprocess.Popen:
        del built_once
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.started.append(process)
        return process

    def stop(self, process: subprocess.Popen | None) -> None:
        if process is None:
            return
        self.stopped.append(process.pid)
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def probe(self, process: subprocess.Popen | None, _host: str, _port: int) -> service_process.ServiceProbeResult:
        if process is None:
            return service_process.ServiceProbeResult(healthy=False, reason="stopped")
        if process.poll() is not None:
            return service_process.ServiceProbeResult(healthy=False, reason="process exited", restart=True)
        return service_process.ServiceProbeResult(healthy=True)


def make_short_runtime_root(prefix: str) -> Path:
    """Create a short runtime root so Unix domain socket paths fit on macOS."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))


def make_runtime_paths(root: Path) -> service_manager.RuntimePaths:
    return service_manager.RuntimePaths(
        root=root,
        run_dir=root / "run",
        log_dir=root / "logs",
        backend_pid=root / "run" / "backend.pid",
        frontend_pid=root / "run" / "webui.pid",
        backend_log=root / "logs" / "backend.log",
        frontend_log=root / "logs" / "webui.log",
    )


def wait_for_process_exit(process: subprocess.Popen, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.05)
    raise AssertionError(f"process {process.pid} did not exit")


def wait_for_supervisor(paths: service_manager.RuntimePaths, *, running: bool, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service_control.supervisor_is_running(paths) is running:
            return
        time.sleep(0.05)
    raise AssertionError(f"supervisor running={running} was not observed")


def start_supervisor(
    config: service_manager.ServiceConfig,
) -> tuple[service_supervisor.SupervisorDaemon, threading.Thread]:
    daemon = service_supervisor.SupervisorDaemon(
        config,
        interval=0.05,
        backend_adapter=SleeperProcessAdapter(),
        webui_adapter=SleeperProcessAdapter(),
    )
    thread = threading.Thread(target=daemon.run, daemon=True)
    thread.start()
    return daemon, thread


def stop_supervisor(daemon: service_supervisor.SupervisorDaemon, thread: threading.Thread) -> None:
    daemon.request_stop()
    thread.join(timeout=5)
    daemon.shutdown_children()
    daemon._stop_control_server()
