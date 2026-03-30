"""
Service lifecycle helpers for local Flocks daemon commands.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import webbrowser
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Iterable, Sequence

import httpx

MIN_NODE_MAJOR = 22
BACKEND_HEALTH_PATHS = ("/api/health", "/health")
FOLLOW_POLL_INTERVAL = 0.5


class ServiceError(RuntimeError):
    """Raised when a service lifecycle action fails."""


@dataclass(frozen=True)
class ServiceConfig:
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_host: str = "127.0.0.1"
    frontend_port: int = 5173
    no_browser: bool = False
    skip_frontend_build: bool = False

    @property
    def backend_urls(self) -> list[str]:
        base = f"http://{_loopback_host(self.backend_host)}:{self.backend_port}"
        return [f"{base}{path}" for path in BACKEND_HEALTH_PATHS]

    @property
    def frontend_url(self) -> str:
        return f"http://{_loopback_host(self.frontend_host)}:{self.frontend_port}"


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    run_dir: Path
    log_dir: Path
    backend_pid: Path
    frontend_pid: Path
    backend_log: Path
    frontend_log: Path


def repo_root() -> Path:
    """Return the installed repository root."""
    override = os.getenv("FLOCKS_REPO_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def flocks_root() -> Path:
    """Return the user-level Flocks state directory."""
    override = os.getenv("FLOCKS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".flocks"


def runtime_paths() -> RuntimePaths:
    """Resolve runtime pid/log locations."""
    root = flocks_root()
    run_dir = root / "run"
    log_dir = root / "logs"
    return RuntimePaths(
        root=root,
        run_dir=run_dir,
        log_dir=log_dir,
        backend_pid=run_dir / "backend.pid",
        frontend_pid=run_dir / "webui.pid",
        backend_log=log_dir / "backend.log",
        frontend_log=log_dir / "webui.log",
    )


def ensure_runtime_dirs(paths: RuntimePaths | None = None) -> RuntimePaths:
    """Create runtime directories if needed."""
    current = paths or runtime_paths()
    current.run_dir.mkdir(parents=True, exist_ok=True)
    current.log_dir.mkdir(parents=True, exist_ok=True)
    return current


def ensure_install_layout(root: Path | None = None) -> Path:
    """Validate that the installed repo still contains backend and WebUI code."""
    current = root or repo_root()
    if not (current / "pyproject.toml").exists():
        raise ServiceError(f"未找到安装目录中的 pyproject.toml: {current}")
    if not (current / "webui" / "package.json").exists():
        raise ServiceError("未找到 WebUI 源码，请重新安装 Flocks，或设置 FLOCKS_REPO_ROOT 指向有效安装目录。")
    return current


def get_node_major_version() -> int | None:
    """Return the detected Node.js major version."""
    node = which("node")
    if not node:
        return None

    try:
        completed = subprocess.run(
            [node, "-v"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None

    version = completed.stdout.strip().lstrip("v")
    if not version:
        return None
    major = version.split(".", 1)[0]
    return int(major) if major.isdigit() else None


def node_version_satisfies_requirement() -> bool:
    """Return True if Node.js is present and meets the minimum version."""
    major = get_node_major_version()
    return major is not None and major >= MIN_NODE_MAJOR


def read_pid(pid_file: Path) -> int | None:
    """Read a pid file if it exists and contains a valid integer."""
    if not pid_file.exists():
        return None
    raw = pid_file.read_text(encoding="utf-8").strip()
    return int(raw) if raw.isdigit() else None


def write_pid(pid_file: Path, pid: int) -> None:
    """Persist a process id."""
    pid_file.write_text(str(pid), encoding="utf-8")


def pid_is_running(pid: int | None) -> bool:
    """Return True if a pid exists and is still alive."""
    if pid is None:
        return False

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def cleanup_stale_pid_file(pid_file: Path) -> None:
    """Remove pid files that no longer point to running processes."""
    pid = read_pid(pid_file)
    if pid is not None and not pid_is_running(pid):
        pid_file.unlink(missing_ok=True)


def backend_is_running(config: ServiceConfig, paths: RuntimePaths | None = None) -> bool:
    """Return True if the tracked backend process is running."""
    current = paths or runtime_paths()
    cleanup_stale_pid_file(current.backend_pid)
    return pid_is_running(read_pid(current.backend_pid)) or bool(port_owner_pids(config.backend_port))


def frontend_is_running(config: ServiceConfig, paths: RuntimePaths | None = None) -> bool:
    """Return True if the tracked frontend process is running."""
    current = paths or runtime_paths()
    cleanup_stale_pid_file(current.frontend_pid)
    return pid_is_running(read_pid(current.frontend_pid)) or bool(port_owner_pids(config.frontend_port))


def port_owner_pids(port: int) -> list[int]:
    """Return pids listening on the given TCP port."""
    if sys.platform == "win32":
        return _parse_windows_netstat_output(_run_windows_netstat(port))

    if which("lsof"):
        completed = subprocess.run(
            ["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"],
            check=False,
            capture_output=True,
            text=True,
        )
        pids = [int(line) for line in completed.stdout.splitlines() if line.strip().isdigit()]
        return sorted(dict.fromkeys(pids))

    if which("fuser"):
        completed = subprocess.run(
            ["fuser", f"{port}/tcp"],
            check=False,
            capture_output=True,
            text=True,
        )
        values = completed.stdout.split() or completed.stderr.split()
        pids = [int(value) for value in values if value.isdigit()]
        return sorted(dict.fromkeys(pids))

    raise ServiceError("未检测到 lsof 或 fuser，无法检查端口占用。")


def wait_for_http(urls: Sequence[str], name: str, attempts: int = 30, delay: float = 1.0) -> None:
    """Wait until any URL becomes reachable."""
    with httpx.Client(timeout=2.0) as client:
        for _ in range(attempts):
            for url in urls:
                try:
                    response = client.get(url)
                    if response.status_code < 500:
                        return
                except Exception:
                    pass
            time.sleep(delay)
    raise ServiceError(f"{name} 启动超时，请检查日志。")


def start_backend(config: ServiceConfig, console) -> None:
    """Start the backend API service if needed."""
    root = ensure_install_layout()
    paths = ensure_runtime_dirs()
    cleanup_stale_pid_file(paths.backend_pid)

    tracked_pid = read_pid(paths.backend_pid)
    listeners = port_owner_pids(config.backend_port)
    if listeners:
        if tracked_pid and tracked_pid in listeners:
            console.print(f"[flocks] 后端已在运行，PID={tracked_pid}")
        else:
            console.print(f"[flocks] 后端端口已被占用，视为已运行 (PID: {_join_pids(listeners)})")
        return

    if tracked_pid is not None:
        paths.backend_pid.unlink(missing_ok=True)

    console.print("[flocks] 启动后端服务...")
    process = _spawn_process(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "flocks.server.app:app",
            "--host",
            config.backend_host,
            "--port",
            str(config.backend_port),
        ],
        cwd=root,
        log_path=paths.backend_log,
    )
    write_pid(paths.backend_pid, process.pid)

    try:
        wait_for_http(config.backend_urls, "后端服务")
    except ServiceError:
        stop_one(config.backend_port, paths.backend_pid, "后端", console)
        raise

    console.print(f"[flocks] 后端已启动，日志: {paths.backend_log}")


def start_frontend(config: ServiceConfig, console) -> None:
    """Build and start the WebUI preview service if needed."""
    root = ensure_install_layout()
    paths = ensure_runtime_dirs()
    cleanup_stale_pid_file(paths.frontend_pid)

    tracked_pid = read_pid(paths.frontend_pid)
    listeners = port_owner_pids(config.frontend_port)
    if listeners:
        if tracked_pid and tracked_pid in listeners:
            console.print(f"[flocks] WebUI 已在运行，PID={tracked_pid}")
        else:
            console.print(f"[flocks] WebUI 端口已被占用，视为已运行 (PID: {_join_pids(listeners)})")
        return

    if tracked_pid is not None:
        paths.frontend_pid.unlink(missing_ok=True)

    npm = which("npm") or which("npm.cmd")
    if not npm:
        raise ServiceError("未检测到 npm，请先安装 Node.js 22+（包含 npm）后重试。")
    if not node_version_satisfies_requirement():
        raise ServiceError(f"检测到的 Node.js 版本过低。启动 WebUI 至少需要 Node.js {MIN_NODE_MAJOR}+。")

    webui_dir = root / "webui"
    if not config.skip_frontend_build:
        console.print("[flocks] 构建 WebUI...")
        completed = subprocess.run(
            [npm, "run", "build"],
            cwd=webui_dir,
            check=False,
        )
        if completed.returncode != 0:
            raise ServiceError("WebUI 构建失败。")

    console.print("[flocks] 启动 WebUI...")
    process = _spawn_process(
        [
            npm,
            "run",
            "preview",
            "--",
            "--host",
            config.frontend_host,
            "--port",
            str(config.frontend_port),
        ],
        cwd=webui_dir,
        log_path=paths.frontend_log,
    )
    write_pid(paths.frontend_pid, process.pid)

    try:
        wait_for_http([config.frontend_url], "WebUI")
    except ServiceError:
        stop_one(config.frontend_port, paths.frontend_pid, "WebUI", console)
        raise

    console.print(f"[flocks] WebUI 已启动，日志: {paths.frontend_log}")


def stop_one(port: int, pid_file: Path, name: str, console) -> None:
    """Stop a single service by tracked pid and/or listening port."""
    cleanup_stale_pid_file(pid_file)
    tracked_pid = read_pid(pid_file)
    listeners = port_owner_pids(port)

    target_pids: list[int] = []
    if tracked_pid is not None:
        target_pids = append_unique_pids(target_pids, collect_process_tree_pids(tracked_pid))
    target_pids = append_unique_pids(target_pids, listeners)

    if not target_pids:
        pid_file.unlink(missing_ok=True)
        console.print(f"[flocks] {name} 未运行。")
        return

    console.print(f"[flocks] 停止 {name}（端口 {port}，PID: {_join_pids(target_pids)}）...")

    if sys.platform == "win32":
        for pid in target_pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
    else:
        signal_pid_list(signal.SIGTERM, target_pids)
        for _ in range(10):
            if not port_owner_pids(port) and not any(pid_is_running(pid) for pid in target_pids):
                pid_file.unlink(missing_ok=True)
                console.print(f"[flocks] {name} 已停止。")
                return
            time.sleep(1)

        console.print(f"[flocks] {name} 未在预期时间内退出，强制终止...")
        signal_pid_list(signal.SIGKILL, target_pids)

    for _ in range(10):
        if not port_owner_pids(port):
            pid_file.unlink(missing_ok=True)
            console.print(f"[flocks] {name} 已停止。")
            return
        time.sleep(1)

    pid_file.unlink(missing_ok=True)
    raise ServiceError(f"{name} 未在预期时间内退出，请手动检查端口 {port}。")


def stop_all(config: ServiceConfig, console) -> None:
    """Stop frontend then backend."""
    paths = ensure_runtime_dirs()
    stop_one(config.frontend_port, paths.frontend_pid, "WebUI", console)
    stop_one(config.backend_port, paths.backend_pid, "后端", console)


def _start_all_without_stop(config: ServiceConfig, console) -> None:
    """Start backend and frontend, then print access summary."""
    ensure_runtime_dirs()
    start_backend(config, console)
    start_frontend(config, console)
    show_start_summary(config, console)
    if not config.no_browser:
        open_default_browser(config.frontend_url, console)


def start_all(config: ServiceConfig, console) -> None:
    """Ensure backend and frontend are restarted with a clean state."""
    stop_all(config, console)
    _start_all_without_stop(config, console)


def restart_all(config: ServiceConfig, console) -> None:
    """Restart backend and frontend."""
    start_all(config, console)


def build_status_lines(config: ServiceConfig, paths: RuntimePaths | None = None) -> list[str]:
    """Return a human-readable status summary."""
    current = paths or runtime_paths()
    cleanup_stale_pid_file(current.backend_pid)
    cleanup_stale_pid_file(current.frontend_pid)

    backend_pid = read_pid(current.backend_pid)
    frontend_pid = read_pid(current.frontend_pid)
    backend_listeners = port_owner_pids(config.backend_port)
    frontend_listeners = port_owner_pids(config.frontend_port)

    lines: list[str] = []
    if backend_listeners:
        lines.append(
            f"[flocks] 后端运行中: PID={_join_pids(backend_listeners)} URL=http://{_loopback_host(config.backend_host)}:{config.backend_port}"
        )
    elif pid_is_running(backend_pid):
        lines.append(f"[flocks] 后端主进程仍在运行，但端口 {config.backend_port} 未监听: PID={backend_pid}")
    else:
        lines.append("[flocks] 后端未运行")

    if frontend_listeners:
        lines.append(
            f"[flocks] WebUI 运行中: PID={_join_pids(frontend_listeners)} URL=http://{_loopback_host(config.frontend_host)}:{config.frontend_port}"
        )
    elif pid_is_running(frontend_pid):
        lines.append(f"[flocks] WebUI 主进程仍在运行，但端口 {config.frontend_port} 未监听: PID={frontend_pid}")
    else:
        lines.append("[flocks] WebUI 未运行")

    lines.append(f"[flocks] 后端日志: {current.backend_log}")
    lines.append(f"[flocks] WebUI 日志: {current.frontend_log}")
    return lines


def show_status(config: ServiceConfig, console) -> None:
    """Print service status."""
    for line in build_status_lines(config):
        console.print(line)


def show_start_summary(config: ServiceConfig, console) -> None:
    """Print URLs and log locations after startup."""
    paths = ensure_runtime_dirs()
    console.print()
    console.print("[flocks] 日志:")
    console.print(f"[flocks]   后端: {paths.backend_log}")
    console.print(f"[flocks]   WebUI: {paths.frontend_log}")
    console.print()
    console.print("[flocks] 后端接口:")
    console.print(f"[flocks]   http://{_loopback_host(config.backend_host)}:{config.backend_port}")
    console.print()
    console.print("[flocks] 打开浏览器访问:")
    console.print(f"[flocks]   {config.frontend_url}")


def show_logs(
    console,
    *,
    backend: bool = False,
    webui: bool = False,
    follow: bool = True,
    lines: int = 50,
) -> None:
    """Print recent service logs and optionally follow them."""
    paths = ensure_runtime_dirs()
    selections = selected_log_paths(paths, backend=backend, webui=webui)
    prefixes = {paths.backend_log: "backend", paths.frontend_log: "webui"}

    for path in selections:
        path.touch(exist_ok=True)
        console.print(f"[{prefixes[path]}] --- {path} ---")
        for line in tail_lines(path, lines):
            console.print(f"[{prefixes[path]}] {line}")

    if not follow:
        return

    console.print("[flocks] 按 Ctrl+C 退出日志跟随。")
    handles = {}
    try:
        for path in selections:
            handle = path.open("r", encoding="utf-8", errors="replace")
            handle.seek(0, os.SEEK_END)
            handles[path] = handle

        while True:
            emitted = False
            for path, handle in handles.items():
                while True:
                    line = handle.readline()
                    if not line:
                        break
                    emitted = True
                    console.print(f"[{prefixes[path]}] {line.rstrip()}")
            if not emitted:
                time.sleep(FOLLOW_POLL_INTERVAL)
    finally:
        for handle in handles.values():
            handle.close()


def selected_log_paths(
    paths: RuntimePaths,
    *,
    backend: bool = False,
    webui: bool = False,
) -> list[Path]:
    """Return the log files selected by CLI flags."""
    if backend and not webui:
        return [paths.backend_log]
    if webui and not backend:
        return [paths.frontend_log]
    return [paths.backend_log, paths.frontend_log]


def tail_lines(path: Path, lines: int) -> list[str]:
    """Read the last N lines from a text file."""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=max(lines, 0))]


def append_unique_pids(existing: Iterable[int], additions: Iterable[int]) -> list[int]:
    """Return a deduplicated pid list preserving order."""
    result: list[int] = []
    seen: set[int] = set()
    for pid in list(existing) + list(additions):
        if pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        result.append(pid)
    return result


def collect_process_tree_pids(root_pid: int) -> list[int]:
    """Collect a process tree for Unix systems; Windows uses taskkill /T."""
    if root_pid <= 0:
        return []
    if sys.platform == "win32":
        return [root_pid]

    result: list[int] = []
    for child in child_pids(root_pid):
        result = append_unique_pids(result, collect_process_tree_pids(child))
        result = append_unique_pids(result, [child])
    return append_unique_pids(result, [root_pid])


def child_pids(pid: int) -> list[int]:
    """Return the direct children of a pid on Unix."""
    if which("pgrep"):
        completed = subprocess.run(
            ["pgrep", "-P", str(pid)],
            check=False,
            capture_output=True,
            text=True,
        )
        return [int(line) for line in completed.stdout.splitlines() if line.strip().isdigit()]

    completed = subprocess.run(
        ["ps", "-eo", "pid=,ppid="],
        check=False,
        capture_output=True,
        text=True,
    )
    result: list[int] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]) == pid:
            result.append(int(parts[0]))
    return result


def signal_pid_list(sig: signal.Signals, pids: Iterable[int]) -> None:
    """Signal all pids in the provided iterable."""
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def open_default_browser(url: str, console) -> None:
    """Best-effort browser open."""
    try:
        if webbrowser.open(url):
            console.print(f"[flocks] 已使用默认浏览器打开: {url}")
            return
    except Exception:
        pass
    console.print(f"[flocks] 未检测到可用的浏览器打开命令，请手动访问: {url}")


def _spawn_process(command: Sequence[str], *, cwd: Path, log_path: Path) -> subprocess.Popen:
    """Spawn a detached child process and redirect output to a log file."""
    creationflags = 0
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    else:
        kwargs["start_new_session"] = True

    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    try:
        return subprocess.Popen(
            list(command),
            cwd=cwd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            **kwargs,
        )
    finally:
        handle.close()


def _run_windows_netstat(port: int) -> str:
    completed = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return ""
    target = f":{port}"
    lines = []
    for line in completed.stdout.splitlines():
        if "LISTENING" not in line.upper():
            continue
        if target not in line:
            continue
        lines.append(line)
    return "\n".join(lines)


def _parse_windows_netstat_output(output: str) -> list[int]:
    pids: list[int] = []
    for line in output.splitlines():
        parts = line.split()
        if not parts:
            continue
        pid = parts[-1]
        if pid.isdigit():
            pids.append(int(pid))
    return sorted(dict.fromkeys(pids))


def _join_pids(pids: Iterable[int]) -> str:
    return ",".join(str(pid) for pid in pids)


def _loopback_host(host: str) -> str:
    return "127.0.0.1" if host in {"0.0.0.0", "::"} else host
