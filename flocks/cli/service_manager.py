"""
Service lifecycle helpers for local Flocks daemon commands.
"""

from __future__ import annotations

import contextlib
import ctypes
import datetime
import importlib.util
import json
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

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows
    fcntl = None

MIN_NODE_MAJOR = 22
FOLLOW_POLL_INTERVAL = 0.5
WEBUI_DIRECT_BACKEND_URLS_ENV = "FLOCKS_WEBUI_DIRECT_BACKEND_URLS"


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


@dataclass(frozen=True)
class RuntimeRecord:
    pid: int
    pgid: int | None = None
    host: str | None = None
    port: int | None = None
    command: tuple[str, ...] = ()
    started_at: float | None = None


@dataclass(frozen=True)
class UpgradeRuntimeInfo:
    payload_present: bool = False
    pid_file_present: bool = False
    upgrade_pid: int | None = None
    frontend_host: str | None = None
    frontend_port: int | None = None
    listener_pids: tuple[int, ...] = ()
    page_active: bool = False

    @property
    def has_artifacts(self) -> bool:
        return self.payload_present or self.pid_file_present


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


def _python_executable_from_env_root(env_root: Path) -> str | None:
    """Return the Python executable inside a virtual environment root."""
    candidates = [
        env_root / "Scripts" / "python.exe",
        env_root / "Scripts" / "python",
        env_root / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return None


def _python_env_root_from_module(module_name: str) -> Path | None:
    """Infer the owning Python environment root from an importable module."""
    spec = importlib.util.find_spec(module_name)
    origin = getattr(spec, "origin", None)
    if not origin or origin in {"built-in", "frozen"}:
        return None

    module_path = Path(origin).resolve()
    site_packages = next(
        (parent for parent in (module_path, *module_path.parents) if parent.name.lower() == "site-packages"),
        None,
    )
    if site_packages is None:
        return None

    lib_parent = site_packages.parent
    lib_name = lib_parent.name.lower()
    if lib_name in {"lib", "lib64"}:
        return lib_parent.parent
    if lib_name.startswith("python") and lib_parent.parent.name.lower() in {"lib", "lib64"}:
        return lib_parent.parent.parent
    return None


def resolve_python_subprocess_command(
    root: Path | None = None,
    *,
    preferred_modules: Sequence[str] = ("uvicorn", "flocks"),
) -> list[str]:
    """Resolve a Python executable for child processes.

    Priority:
    1. Project/install ``.venv``.
    2. Current runtime environment inferred from installed modules.
    3. Current ``sys.executable``.
    """
    current_root = root or repo_root()
    venv_python = _python_executable_from_env_root(current_root / ".venv")
    if venv_python:
        return [venv_python]

    for module_name in preferred_modules:
        env_root = _python_env_root_from_module(module_name)
        if env_root is None:
            continue
        resolved = _python_executable_from_env_root(env_root)
        if resolved:
            return [resolved]

    return [sys.executable]


def _flocks_executable_from_venv(venv_root: Path) -> str | None:
    """Return the flocks CLI entry point inside a virtual environment."""
    candidates = [
        venv_root / "Scripts" / "flocks.exe",
        venv_root / "Scripts" / "flocks.cmd",
        venv_root / "bin" / "flocks",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return None


def resolve_flocks_cli_command(root: Path | None = None) -> list[str]:
    """Resolve a command prefix that launches the ``flocks`` CLI reliably.

    On Windows, always uses ``python.exe -m flocks.cli.main`` instead of
    ``flocks.exe`` to avoid locking the console-script entry point, which
    would prevent ``uv sync`` from replacing it during live upgrades.
    """
    current_root = root or repo_root()

    if sys.platform == "win32":
        venv_python = _python_executable_from_env_root(current_root / ".venv")
        if venv_python:
            return [venv_python, "-m", "flocks.cli.main"]
    else:
        venv_flocks = _flocks_executable_from_venv(current_root / ".venv")
        if venv_flocks:
            return [venv_flocks]

    launcher = which("flocks") or which("flocks.exe") or which("flocks.cmd")
    if launcher and not launcher.startswith("/mnt/"):
        return [launcher]

    return resolve_python_subprocess_command(root) + ["-m", "flocks.cli.main"]


def _bundled_node_install_dir() -> Path | None:
    """Return the bundled Node.js installation directory when available."""
    candidates: list[str] = []
    node_home = os.getenv("FLOCKS_NODE_HOME")
    if node_home:
        candidates.append(node_home)

    install_root = os.getenv("FLOCKS_INSTALL_ROOT")
    if install_root:
        candidates.append(str(Path(install_root).expanduser() / "tools" / "node"))

    for candidate in candidates:
        node_dir = Path(candidate).expanduser()
        if sys.platform == "win32":
            node_executable = node_dir / "node.exe"
        else:
            node_executable = node_dir / "bin" / "node"
        if node_executable.exists():
            return node_dir.resolve()
    return None


def resolve_node_executable() -> str | None:
    """Resolve node executable from bundled toolchain first, then PATH."""
    node_dir = _bundled_node_install_dir()
    if node_dir is not None:
        node_executable = node_dir / ("node.exe" if sys.platform == "win32" else "bin/node")
        return str(node_executable)
    return which("node")


def resolve_npm_executable() -> str | None:
    """Resolve npm from bundled toolchain first, then PATH."""
    node_dir = _bundled_node_install_dir()
    if node_dir is not None:
        candidates = (
            [node_dir / "npm.cmd", node_dir / "npm", node_dir / "bin/npm"]
            if sys.platform == "win32"
            else [node_dir / "bin/npm", node_dir / "npm"]
        )
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

    if sys.platform == "win32":
        return which("npm.cmd") or which("npm")
    return which("npm") or which("npm.cmd")


def get_node_major_version() -> int | None:
    """Return the detected Node.js major version."""
    node = resolve_node_executable()
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


def _coerce_positive_int(value: object) -> int | None:
    """Return a positive integer when the value can be safely coerced."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _parse_runtime_record(raw: str) -> RuntimeRecord | None:
    """Parse either legacy pid-only files or JSON runtime metadata."""
    text = raw.strip()
    if not text:
        return None
    if text.isdigit():
        return RuntimeRecord(pid=int(text))

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    pid = _coerce_positive_int(payload.get("pid"))
    if pid is None:
        return None

    command_payload = payload.get("command")
    command: tuple[str, ...] = ()
    if isinstance(command_payload, list) and all(isinstance(item, str) for item in command_payload):
        command = tuple(command_payload)

    started_at = payload.get("started_at")
    started_value = float(started_at) if isinstance(started_at, (int, float)) and not isinstance(started_at, bool) else None

    return RuntimeRecord(
        pid=pid,
        pgid=_coerce_positive_int(payload.get("pgid")),
        host=payload.get("host") if isinstance(payload.get("host"), str) and payload.get("host") else None,
        port=_coerce_positive_int(payload.get("port")),
        command=command,
        started_at=started_value,
    )


def read_runtime_record(pid_file: Path) -> RuntimeRecord | None:
    """Read runtime metadata from a pid file, supporting legacy formats."""
    if not pid_file.exists():
        return None
    raw = pid_file.read_text(encoding="utf-8").strip()
    return _parse_runtime_record(raw)


def write_runtime_record(pid_file: Path, record: RuntimeRecord) -> None:
    """Persist runtime metadata in a backward-compatible JSON format."""
    payload: dict[str, object] = {"pid": record.pid}
    if record.pgid is not None:
        payload["pgid"] = record.pgid
    if record.host is not None:
        payload["host"] = record.host
    if record.port is not None:
        payload["port"] = record.port
    if record.command:
        payload["command"] = list(record.command)
    if record.started_at is not None:
        payload["started_at"] = record.started_at
    pid_file.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")


def process_runtime_record(
    process: subprocess.Popen,
    *,
    host: str,
    port: int,
    command: Sequence[str],
) -> RuntimeRecord:
    """Build runtime metadata for a freshly started service process."""
    pgid = None
    if sys.platform != "win32":
        try:
            pgid = os.getpgid(process.pid)
        except OSError:
            pgid = None
    return RuntimeRecord(
        pid=process.pid,
        pgid=pgid,
        host=host,
        port=port,
        command=tuple(command),
        started_at=time.time(),
    )


def read_pid(pid_file: Path) -> int | None:
    """Read a pid file if it exists and contains a valid integer."""
    record = read_runtime_record(pid_file)
    return record.pid if record else None


def write_pid(pid_file: Path, pid: int) -> None:
    """Persist a process id."""
    write_runtime_record(pid_file, RuntimeRecord(pid=pid))


def _unix_process_stat(pid: int) -> str | None:
    """Return the Unix process status code for a pid, if available."""
    if sys.platform == "win32" or pid <= 0:
        return None
    completed = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    return lines[0]


def _unix_pid_is_zombie(pid: int | None) -> bool:
    """Return True when a Unix pid is a zombie/defunct process."""
    if pid is None or pid <= 0 or sys.platform == "win32":
        return False
    stat = _unix_process_stat(pid)
    return bool(stat and stat.startswith("Z"))


def _windows_pid_is_running(pid: int) -> bool:
    """Return True when a Windows process id is still alive."""
    if sys.platform != "win32" or pid <= 0:
        return False

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    get_exit_code_process.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5

    try:
        exit_code = ctypes.c_uint32()
        if not get_exit_code_process(handle, ctypes.byref(exit_code)):
            return ctypes.get_last_error() == 5
        return exit_code.value == still_active
    finally:
        close_handle(handle)


def pid_is_running(pid: int | None) -> bool:
    """Return True if a pid exists and is still alive."""
    if pid is None or pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_is_running(pid)

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    if _unix_pid_is_zombie(pid):
        return False
    return True


def _process_group_member_pids(pgid: int) -> list[int]:
    """Return pids that belong to a Unix process group."""
    if sys.platform == "win32" or pgid <= 0:
        return []
    if which("pgrep"):
        completed = subprocess.run(
            ["pgrep", "-g", str(pgid)],
            check=False,
            capture_output=True,
            text=True,
        )
        return [int(line) for line in completed.stdout.splitlines() if line.strip().isdigit()]

    completed = subprocess.run(
        ["ps", "-eo", "pid=,pgid="],
        check=False,
        capture_output=True,
        text=True,
    )
    result: list[int] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]) == pgid:
            result.append(int(parts[0]))
    return result


def process_group_is_running(pgid: int | None) -> bool:
    """Return True when a Unix process group is still alive."""
    if sys.platform == "win32" or pgid is None or pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    members = _process_group_member_pids(pgid)
    if not members:
        return False
    alive_members = [pid for pid in members if pid_is_running(pid)]
    if alive_members:
        return True
    # macOS can raise EPERM for a defunct process-group leader even when no
    # runnable members remain, so rely on member liveness as the source of truth.
    return False


def runtime_record_is_running(record: RuntimeRecord | None) -> bool:
    """Return True if the tracked pid or process group is still alive."""
    if record is None:
        return False
    return pid_is_running(record.pid) or process_group_is_running(record.pgid)


def _console_print(console, message: str) -> None:
    if console is None:
        return
    console.print(message)


def _read_upgrade_runtime_info(frontend_port: int | None = None) -> UpgradeRuntimeInfo:
    try:
        from flocks.updater import updater as updater_module

        payload = updater_module.read_upgrade_runtime_state(frontend_port=frontend_port)
    except Exception:
        return UpgradeRuntimeInfo(frontend_port=frontend_port)

    listener_pids = tuple(int(pid) for pid in payload.get("listener_pids", []) if isinstance(pid, int))
    return UpgradeRuntimeInfo(
        payload_present=bool(payload.get("payload_present")),
        pid_file_present=bool(payload.get("pid_file_present")),
        upgrade_pid=payload.get("upgrade_pid") if isinstance(payload.get("upgrade_pid"), int) else None,
        frontend_host=payload.get("frontend_host") if isinstance(payload.get("frontend_host"), str) else None,
        frontend_port=payload.get("frontend_port") if isinstance(payload.get("frontend_port"), int) else frontend_port,
        listener_pids=listener_pids,
        page_active=bool(payload.get("page_active")),
    )


def _resolve_upgrade_runtime(console, *, frontend_port: int, attempt_recover: bool) -> dict[str, object]:
    upgrade_info = _read_upgrade_runtime_info(frontend_port)
    if not upgrade_info.has_artifacts:
        return {"action": "noop", "error": None}

    from flocks.updater import updater as updater_module

    _console_print(console, "[flocks] 检测到升级临时页残留，正在尝试恢复或清理...")
    result = updater_module.resolve_upgrade_runtime_state(
        attempt_recover=attempt_recover,
        frontend_port=upgrade_info.frontend_port or frontend_port,
    )

    action = str(result.get("action") or "noop")
    error = result.get("error")
    if action == "recovered":
        _console_print(console, "[flocks] 已恢复未完成升级，正式 WebUI 将继续接管端口。")
    elif action != "noop":
        _console_print(console, "[flocks] 已清理升级临时页残留。")

    if isinstance(error, str) and error:
        _console_print(console, f"[flocks] 未完成升级的自动恢复失败，已清理临时升级页: {error}")
    return result


def _effective_frontend_port(paths: RuntimePaths, default: int) -> int:
    recorded_port = _recorded_port(paths.frontend_pid, default)
    upgrade_info = _read_upgrade_runtime_info(recorded_port)
    return upgrade_info.frontend_port or recorded_port


def cleanup_stale_pid_file(pid_file: Path) -> None:
    """Remove pid files that no longer point to running processes."""
    if not pid_file.exists():
        return

    raw = pid_file.read_text(encoding="utf-8").strip()
    if not raw:
        pid_file.unlink(missing_ok=True)
        return

    record = _parse_runtime_record(raw)
    if record is None or not runtime_record_is_running(record):
        pid_file.unlink(missing_ok=True)


def backend_is_running(config: ServiceConfig, paths: RuntimePaths | None = None) -> bool:
    """Return True if the tracked backend process is running."""
    current = paths or runtime_paths()
    cleanup_stale_pid_file(current.backend_pid)
    return runtime_record_is_running(read_runtime_record(current.backend_pid)) or bool(port_owner_pids(config.backend_port))


def frontend_is_running(config: ServiceConfig, paths: RuntimePaths | None = None) -> bool:
    """Return True if the tracked frontend process is running."""
    current = paths or runtime_paths()
    cleanup_stale_pid_file(current.frontend_pid)
    return runtime_record_is_running(read_runtime_record(current.frontend_pid)) or bool(port_owner_pids(config.frontend_port))


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


def _is_reachable_response(response: httpx.Response) -> bool:
    """Return True when an HTTP endpoint is reachable enough for startup checks."""
    return response.status_code < 500


def _is_running_status_response(response: httpx.Response) -> bool:
    """Return True when the backend root endpoint reports a running status."""
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("status") == "running"


def wait_for_http(
    urls: Sequence[str],
    name: str,
    attempts: int = 30,
    delay: float = 1.0,
    validator=None,
) -> None:
    """Wait until any URL passes the provided startup validator."""
    response_validator = validator or _is_reachable_response
    # Local startup probes must never be routed through system proxy settings;
    # otherwise localhost/127.0.0.1 checks can time out even when the service
    # is already healthy.
    with httpx.Client(timeout=2.0, trust_env=False) as client:
        for _ in range(attempts):
            for url in urls:
                try:
                    response = client.get(url)
                    if response_validator(response):
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

    runtime_record = read_runtime_record(paths.backend_pid)
    tracked_pid = runtime_record.pid if runtime_record else None
    listeners = port_owner_pids(config.backend_port)
    if listeners:
        if tracked_pid and tracked_pid in listeners:
            console.print(f"[flocks] 后端已在运行，PID={tracked_pid}")
            return
        raise ServiceError(
            f"后端端口 {config.backend_port} 已被占用 (PID: {_join_pids(listeners)})，"
            "与当前运行时记录不一致，请先执行 `flocks stop` 或手动清理残留进程。"
        )

    if runtime_record is not None and runtime_record_is_running(runtime_record):
        raise ServiceError(
            "后端运行记录仍存活，但端口未监听；请先执行 `flocks stop` 清理异常状态后重试。"
        )

    if runtime_record is not None:
        paths.backend_pid.unlink(missing_ok=True)

    command = resolve_flocks_cli_command(root) + [
        "serve",
        "--host",
        config.backend_host,
        "--port",
        str(config.backend_port),
    ]

    backend_env = os.environ.copy()
    backend_env["_FLOCKS_WEBUI_HOST"] = config.frontend_host
    backend_env["_FLOCKS_WEBUI_PORT"] = str(config.frontend_port)
    backend_env["PYTHONUNBUFFERED"] = "1"

    console.print("[flocks] 启动后端服务...")
    process = _spawn_process(
        command,
        cwd=root,
        log_path=paths.backend_log,
        env=backend_env,
    )
    write_runtime_record(
        paths.backend_pid,
        process_runtime_record(
            process,
            host=config.backend_host,
            port=config.backend_port,
            command=command,
        ),
    )
    _log_startup_config(paths.backend_log, "backend", config.backend_host, config.backend_port, read_runtime_record(paths.backend_pid))

    try:
        wait_for_http(
            [backend_access_base_url(config)],
            "后端服务",
            delay=3.0,
            validator=_is_running_status_response,
        )
    except ServiceError:
        _emit_service_log_tail(console, paths.backend_log, "后端")
        stop_one(config.backend_port, paths.backend_pid, "后端", console)
        raise

    console.print(f"[flocks] 后端已启动，日志: {paths.backend_log}")


def start_frontend(config: ServiceConfig, console) -> None:
    """Build and start the WebUI preview service if needed."""
    root = ensure_install_layout()
    paths = ensure_runtime_dirs()
    cleanup_stale_pid_file(paths.frontend_pid)

    runtime_record = read_runtime_record(paths.frontend_pid)
    tracked_pid = runtime_record.pid if runtime_record else None
    listeners = port_owner_pids(config.frontend_port)
    if listeners:
        if tracked_pid and tracked_pid in listeners:
            console.print(f"[flocks] WebUI 已在运行，PID={tracked_pid}")
            return

        upgrade_info = _read_upgrade_runtime_info(config.frontend_port)
        if upgrade_info.page_active:
            _resolve_upgrade_runtime(
                console,
                frontend_port=upgrade_info.frontend_port or config.frontend_port,
                attempt_recover=False,
            )
            cleanup_stale_pid_file(paths.frontend_pid)
            runtime_record = read_runtime_record(paths.frontend_pid)
            tracked_pid = runtime_record.pid if runtime_record else None
            listeners = port_owner_pids(config.frontend_port)
            if tracked_pid and tracked_pid in listeners:
                console.print(f"[flocks] WebUI 已在运行，PID={tracked_pid}")
                return
            if not listeners:
                tracked_pid = runtime_record.pid if runtime_record else None
            else:
                raise ServiceError(
                    f"WebUI 端口 {config.frontend_port} 已被占用 (PID: {_join_pids(listeners)})，"
                    "与当前运行时记录不一致，请先执行 `flocks stop` 或手动清理残留进程。"
                )

        else:
            raise ServiceError(
                f"WebUI 端口 {config.frontend_port} 已被占用 (PID: {_join_pids(listeners)})，"
                "与当前运行时记录不一致，请先执行 `flocks stop` 或手动清理残留进程。"
            )

    if runtime_record is not None and runtime_record_is_running(runtime_record):
        raise ServiceError(
            "WebUI 运行记录仍存活，但端口未监听；请先执行 `flocks stop` 清理异常状态后重试。"
        )

    if runtime_record is not None:
        paths.frontend_pid.unlink(missing_ok=True)

    npm = resolve_npm_executable()
    if not npm:
        raise ServiceError("未检测到 npm，请先安装 Node.js 22+（包含 npm）后重试。")
    if not node_version_satisfies_requirement():
        raise ServiceError(f"检测到的 Node.js 版本过低。启动 WebUI 至少需要 Node.js {MIN_NODE_MAJOR}+。")

    webui_dir = root / "webui"
    frontend_env = build_frontend_env(config)
    if not config.skip_frontend_build:
        console.print("[flocks] 构建 WebUI...")
        completed = subprocess.run(
            [npm, "run", "build"],
            cwd=webui_dir,
            check=False,
            env=frontend_env,
        )
        if completed.returncode != 0:
            raise ServiceError("WebUI 构建失败。")

    command = [
        npm,
        "run",
        "preview",
        "--",
        "--host",
        config.frontend_host,
        "--port",
        str(config.frontend_port),
    ]

    console.print("[flocks] 启动 WebUI...")
    process = _spawn_process(
        command,
        cwd=webui_dir,
        log_path=paths.frontend_log,
        env=frontend_env,
    )
    write_runtime_record(
        paths.frontend_pid,
        process_runtime_record(
            process,
            host=config.frontend_host,
            port=config.frontend_port,
            command=command,
        ),
    )
    _log_startup_config(paths.frontend_log, "webui", config.frontend_host, config.frontend_port, read_runtime_record(paths.frontend_pid))

    try:
        wait_for_http([config.frontend_url], "WebUI")
    except ServiceError:
        _emit_service_log_tail(console, paths.frontend_log, "WebUI")
        stop_one(config.frontend_port, paths.frontend_pid, "WebUI", console)
        raise

    console.print(f"[flocks] WebUI 已启动，日志: {paths.frontend_log}")


def _tracked_processes_stopped(
    port: int,
    record: RuntimeRecord | None,
    tracked_pids: Iterable[int],
) -> bool:
    """Return True when the tracked service no longer has running processes."""
    listeners = port_owner_pids(port)
    if listeners:
        return False
    if runtime_record_is_running(record):
        return False
    return not any(pid_is_running(pid) for pid in tracked_pids)


def _runtime_record_pids(record: RuntimeRecord | None) -> list[int]:
    """Collect the latest pids implied by a runtime record."""
    if record is None:
        return []

    result: list[int] = []
    if record.pid > 0:
        result = append_unique_pids(result, collect_process_tree_pids(record.pid))
    if record.pgid is not None and sys.platform != "win32":
        result = append_unique_pids(result, _process_group_member_pids(record.pgid))
    return result


def _current_stop_targets(
    port: int,
    record: RuntimeRecord | None,
    tracked_pids: Iterable[int],
) -> list[int]:
    """Refresh the pid list that stop_one() should verify or force kill."""
    result = append_unique_pids([], tracked_pids)
    result = append_unique_pids(result, _runtime_record_pids(record))
    return append_unique_pids(result, port_owner_pids(port))


def signal_process_group(sig: signal.Signals, pgid: int | None) -> None:
    """Signal an entire Unix process group when it exists."""
    if sys.platform == "win32" or pgid is None or pgid <= 0:
        return
    try:
        os.killpg(pgid, sig)
    except OSError:
        pass


def stop_one(port: int, pid_file: Path, name: str, console) -> None:
    """Stop a single service by tracked pid and/or listening port."""
    cleanup_stale_pid_file(pid_file)
    runtime_record = read_runtime_record(pid_file)
    tracked_pid = runtime_record.pid if runtime_record else None
    listeners = port_owner_pids(port)

    target_pids: list[int] = []
    if tracked_pid is not None:
        target_pids = append_unique_pids(target_pids, collect_process_tree_pids(tracked_pid))
    target_pids = append_unique_pids(target_pids, listeners)

    group_running = process_group_is_running(runtime_record.pgid if runtime_record else None)
    if not target_pids and not group_running:
        pid_file.unlink(missing_ok=True)
        console.print(f"[flocks] {name} 未运行。")
        return

    details = _join_pids(target_pids) if target_pids else "none"
    if runtime_record and runtime_record.pgid is not None and sys.platform != "win32":
        details = f"{details}; PGID={runtime_record.pgid}"
    console.print(f"[flocks] 停止 {name}（端口 {port}，PID: {details}）...")

    if sys.platform == "win32":
        for pid in target_pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
    else:
        if runtime_record and runtime_record.pgid is not None:
            signal_process_group(signal.SIGTERM, runtime_record.pgid)
        else:
            signal_pid_list(signal.SIGTERM, target_pids)
        for _ in range(10):
            current_targets = _current_stop_targets(port, runtime_record, target_pids)
            if _tracked_processes_stopped(port, runtime_record, current_targets):
                pid_file.unlink(missing_ok=True)
                console.print(f"[flocks] {name} 已停止。")
                return
            time.sleep(1)

        console.print(f"[flocks] {name} 未在预期时间内退出，强制终止...")
        force_targets = _current_stop_targets(port, runtime_record, target_pids)
        if runtime_record and runtime_record.pgid is not None:
            signal_process_group(signal.SIGKILL, runtime_record.pgid)
        signal_pid_list(signal.SIGKILL, force_targets)

    for _ in range(10):
        force_targets = _current_stop_targets(port, runtime_record, target_pids)
        if _tracked_processes_stopped(port, runtime_record, force_targets):
            pid_file.unlink(missing_ok=True)
            console.print(f"[flocks] {name} 已停止。")
            return
        if sys.platform == "win32":
            for pid in force_targets:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        else:
            if runtime_record and runtime_record.pgid is not None:
                signal_process_group(signal.SIGKILL, runtime_record.pgid)
            signal_pid_list(signal.SIGKILL, force_targets)
        time.sleep(1)

    raise ServiceError(f"{name} 未在预期时间内退出，请手动检查端口 {port}。")


def _recorded_port(pid_file: Path, default: int) -> int:
    """Return the port from a runtime record, falling back to *default*."""
    record = read_runtime_record(pid_file)
    if record is not None and record.port is not None:
        return record.port
    return default


def _recorded_host(pid_file: Path, default: str) -> str:
    """Return the host from a runtime record, falling back to *default*."""
    record = read_runtime_record(pid_file)
    if record is not None and record.host:
        return record.host
    return default


@contextlib.contextmanager
def service_lock(paths: RuntimePaths):
    """Serialize lifecycle commands with a cross-process lock file."""
    lock_path = paths.run_dir / "service.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    unlock_windows = None
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                handle.write("0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                unlock_windows = msvcrt
            else:
                if fcntl is None:  # pragma: no cover - defensive
                    raise OSError("fcntl unavailable")
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ServiceError("另一个 flocks 命令正在执行，请稍后重试。") from error
        yield
    finally:
        try:
            if unlock_windows is not None:
                handle.seek(0)
                unlock_windows.locking(handle.fileno(), unlock_windows.LK_UNLCK, 1)
            elif fcntl is not None and sys.platform != "win32":
                fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _log_startup_config(
    log_path: Path,
    name: str,
    host: str,
    port: int,
    record: RuntimeRecord | None,
) -> None:
    """Append a startup summary to the service log."""
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    pid = record.pid if record is not None else "unknown"
    pgid = record.pgid if record is not None else None
    pgid_info = f" pgid={pgid}" if pgid is not None else ""
    line = f"[{timestamp}] {name} starting: host={host} port={port} pid={pid}{pgid_info}\n"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _resolve_stop_ports(
    paths: RuntimePaths,
    config: ServiceConfig | None = None,
) -> tuple[int, int]:
    """Resolve frontend/backend ports for stop flows.

    When a runtime record is missing or uses the legacy pid-only format,
    ``start`` and ``restart`` should fall back to the current CLI config
    rather than the static default ports.
    """
    frontend_default = config.frontend_port if config is not None else ServiceConfig.frontend_port
    backend_default = config.backend_port if config is not None else ServiceConfig.backend_port
    return (
        _effective_frontend_port(paths, frontend_default),
        _recorded_port(paths.backend_pid, backend_default),
    )


def _stop_all_locked(
    paths: RuntimePaths,
    console,
    *,
    config: ServiceConfig | None = None,
) -> None:
    """Stop frontend then backend while reusing the caller's lock."""
    fe_port, be_port = _resolve_stop_ports(paths, config)
    _resolve_upgrade_runtime(console, frontend_port=fe_port, attempt_recover=False)
    stop_one(fe_port, paths.frontend_pid, "WebUI", console)
    stop_one(be_port, paths.backend_pid, "后端", console)


def stop_all(console) -> None:
    """Stop frontend then backend using ports persisted in runtime records."""
    paths = ensure_runtime_dirs()
    with service_lock(paths):
        _stop_all_locked(paths, console)


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
    paths = ensure_runtime_dirs()
    with service_lock(paths):
        _stop_all_locked(paths, console, config=config)
        _start_all_without_stop(config, console)


def restart_all(config: ServiceConfig, console) -> None:
    """Restart backend and frontend."""
    paths = ensure_runtime_dirs()
    with service_lock(paths):
        _stop_all_locked(paths, console, config=config)
        _start_all_without_stop(config, console)


def build_status_lines(paths: RuntimePaths | None = None) -> list[str]:
    """Return a human-readable status summary."""
    current = paths or runtime_paths()
    cleanup_stale_pid_file(current.backend_pid)
    cleanup_stale_pid_file(current.frontend_pid)

    backend_record = read_runtime_record(current.backend_pid)
    frontend_record = read_runtime_record(current.frontend_pid)
    backend_port = _recorded_port(current.backend_pid, ServiceConfig.backend_port)
    frontend_port = _recorded_port(current.frontend_pid, ServiceConfig.frontend_port)
    backend_host = _loopback_host(_recorded_host(current.backend_pid, ServiceConfig.backend_host))
    frontend_host = _loopback_host(_recorded_host(current.frontend_pid, ServiceConfig.frontend_host))
    upgrade_info = _read_upgrade_runtime_info(frontend_port)
    if frontend_record is None and upgrade_info.frontend_port is not None:
        frontend_port = upgrade_info.frontend_port
    if frontend_record is None and upgrade_info.frontend_host:
        frontend_host = _loopback_host(upgrade_info.frontend_host)
    backend_pid = backend_record.pid if backend_record else None
    frontend_pid = frontend_record.pid if frontend_record else None
    backend_listeners = port_owner_pids(backend_port)
    frontend_listeners = port_owner_pids(frontend_port)

    lines: list[str] = []
    if backend_listeners:
        lines.append(
            f"[flocks] 后端运行中: PID={_join_pids(backend_listeners)} URL=http://{backend_host}:{backend_port}"
        )
    elif pid_is_running(backend_pid):
        lines.append(f"[flocks] 后端主进程仍在运行，但端口 {backend_port} 未监听: PID={backend_pid}")
    elif process_group_is_running(backend_record.pgid if backend_record else None):
        lines.append(f"[flocks] 后端进程组仍在运行，但端口 {backend_port} 未监听: PGID={backend_record.pgid}")
    else:
        lines.append("[flocks] 后端未运行")

    if upgrade_info.page_active:
        lines.append(
            f"[flocks] WebUI 临时升级页运行中: PID={_join_pids(upgrade_info.listener_pids)} URL=http://{frontend_host}:{frontend_port}"
        )
    elif frontend_listeners:
        lines.append(
            f"[flocks] WebUI 运行中: PID={_join_pids(frontend_listeners)} URL=http://{frontend_host}:{frontend_port}"
        )
    elif pid_is_running(frontend_pid):
        lines.append(f"[flocks] WebUI 主进程仍在运行，但端口 {frontend_port} 未监听: PID={frontend_pid}")
    elif process_group_is_running(frontend_record.pgid if frontend_record else None):
        lines.append(f"[flocks] WebUI 进程组仍在运行，但端口 {frontend_port} 未监听: PGID={frontend_record.pgid}")
    else:
        lines.append("[flocks] WebUI 未运行")

    if upgrade_info.payload_present:
        lines.append("[flocks] 检测到未完成的升级恢复状态")

    lines.append(f"[flocks] 后端日志: {current.backend_log}")
    lines.append(f"[flocks] WebUI 日志: {current.frontend_log}")
    return lines


def show_status(console) -> None:
    """Print service status."""
    for line in build_status_lines():
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


def _emit_service_log_tail(console, log_path: Path, service_label: str, lines: int = 40) -> None:
    """Print the last *lines* lines of *log_path* to help diagnose failed daemon startups."""
    if lines <= 0:
        return
    if not log_path.exists():
        console.print(
            f"[dim][flocks] {service_label} 日志文件尚不存在（{log_path}），"
            "子进程可能启动即退出。[/dim]",
        )
        return
    try:
        excerpt = tail_lines(log_path, lines)
    except OSError as exc:
        console.print(f"[dim][flocks] 无法读取 {service_label} 日志: {exc}[/dim]")
        return
    if not excerpt:
        return
    console.print(f"[yellow][flocks] {service_label} 近期日志（最后 {len(excerpt)} 行）:[/yellow]")
    for line in excerpt:
        console.print(f"[dim]{line}[/dim]")


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


def access_host(host: str) -> str:
    """Return the host that local health checks and browser requests should use."""
    return _loopback_host(host)


def _format_host_for_url(host: str) -> str:
    """Wrap IPv6 literals in brackets before composing URLs."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def backend_access_base_url(config: ServiceConfig) -> str:
    """Return the backend base URL that the local WebUI should connect to."""
    return f"http://{_format_host_for_url(access_host(config.backend_host))}:{config.backend_port}"


def websocket_access_base_url(config: ServiceConfig) -> str:
    """Return the websocket base URL matching ``backend_access_base_url()``."""
    return _http_to_ws_url(backend_access_base_url(config))


def build_frontend_env(config: ServiceConfig) -> dict[str, str]:
    """Build frontend proxy environment variables from backend service settings."""
    env = os.environ.copy()
    backend_url = backend_access_base_url(config)
    env["FLOCKS_API_PROXY_TARGET"] = backend_url

    # Avoid leaking a stale Vite API target from the parent shell into a new
    # build/start cycle.  WebUI now defaults to same-origin proxy mode for all
    # backend hosts so that reverse-proxy / remote access deployments keep a
    # single browser origin and cookie scope.  Direct backend URLs remain
    # available as an explicit opt-in via WEBUI_DIRECT_BACKEND_URLS_ENV.
    env.pop("VITE_API_BASE_URL", None)
    env.pop("VITE_WS_BASE_URL", None)
    if _should_inject_direct_backend_urls(config.backend_host):
        env["VITE_API_BASE_URL"] = backend_url
        env["VITE_WS_BASE_URL"] = websocket_access_base_url(config)

    # When using the bundled toolchain (Windows installer), npm/node spawned by
    # `npm run build/preview` must be able to locate the bundled node.exe via
    # PATH — npm itself does not always inherit the caller's resolved executable
    # location.  Prepend the bundled node directory when present.
    node_dir = _bundled_node_install_dir()
    if node_dir is not None:
        if sys.platform == "win32":
            node_bin = str(node_dir)
        else:
            node_bin = str(node_dir / "bin")
        path_sep = os.pathsep
        current_path = env.get("PATH", "")
        if node_bin not in current_path.split(path_sep):
            env["PATH"] = node_bin + path_sep + current_path

    return env


def _spawn_process(
    command: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    """Spawn a detached child process and redirect output to a log file."""
    creationflags = 0
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
        if startupinfo_cls is not None:
            startupinfo = startupinfo_cls()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
            startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
            kwargs["startupinfo"] = startupinfo
    else:
        kwargs["start_new_session"] = True

    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    try:
        return subprocess.Popen(
            list(command),
            cwd=cwd,
            env=env,
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


def _http_to_ws_url(url: str) -> str:
    if url.startswith("https://"):
        return url.replace("https://", "wss://", 1)
    if url.startswith("http://"):
        return url.replace("http://", "ws://", 1)
    return url


def _env_flag_enabled(name: str) -> bool:
    value = os.getenv(name)
    if not value:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _should_inject_direct_backend_urls(host: str) -> bool:
    if host in {"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}:
        return False
    return _env_flag_enabled(WEBUI_DIRECT_BACKEND_URLS_ENV)
