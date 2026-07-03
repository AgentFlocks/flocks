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
import socket
import subprocess
import sys
import time
import webbrowser
import warnings
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Any, Iterable, Sequence

import httpx

from flocks.browser.admin import stop_all_daemons as stop_all_browser_daemons
from flocks.cli.service_config import ServiceConfig, loopback_host
from flocks.cli.service_control import (
    read_logs,
    read_supervisor_status,
    request_restart,
    request_stop,
    stream_logs,
    supervisor_is_running,
    supervisor_log_path,
    supervisor_socket_path,
    supervisor_uses_tcp_control,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows
    fcntl = None

MIN_NODE_MAJOR = 22
FOLLOW_POLL_INTERVAL = 0.5
MAX_SERVICE_LOG_BYTES = 1024 * 1024 * 1024
LOG_TRIM_CHUNK_BYTES = 1024 * 1024
WEBUI_DIRECT_BACKEND_URLS_ENV = "FLOCKS_WEBUI_DIRECT_BACKEND_URLS"
DEFAULT_FLOCKS_CONSOLE_BASE_URL = "https://portalflocks.threatbook.cn"
DEFAULT_VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS = "portalflocks.threatbook.cn"
MISSING_PORT_OWNER_TOOLS_WARNING = (
    "未检测到 lsof 或 fuser，无法解析端口占用 PID；将退回到 bind 检查。"
    "可尝试安装：apt/yum install lsof -y"
)
WINDOWS_FRONTEND_BUILD_ASSERTION_MARKERS = (
    "UV_HANDLE_CLOSING",
    "src\\win\\async.c",
    "src/win/async.c",
)
WATCHDOG_PID_FILENAME = "watchdog.pid"
SUPERVISOR_START_TIMEOUT_SECONDS = 180.0


class ServiceError(RuntimeError):
    """Raised when a service lifecycle action fails."""


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


def watchdog_pid_path(paths: RuntimePaths) -> Path:
    """Return the watchdog runtime record path."""
    return paths.run_dir / WATCHDOG_PID_FILENAME


def ensure_install_layout(root: Path | None = None) -> Path:
    """Validate that the installed repo still contains backend and WebUI code."""
    current = root or repo_root()
    from flocks.server.static_webui import resolve_webui_dist_dir

    if not (current / "pyproject.toml").exists():
        if resolve_webui_dist_dir() is None:
            raise ServiceError(f"未找到安装目录中的 pyproject.toml 或 WebUI 静态资源: {current}")
        return current
    if not (current / "webui" / "package.json").exists():
        if resolve_webui_dist_dir() is None:
            raise ServiceError("未找到 WebUI 静态资源，请重新安装 Flocks，或设置 FLOCKS_REPO_ROOT 指向有效安装目录。")
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


def process_runtime_record(
    process: subprocess.Popen,
    *,
    host: str | None,
    port: int | None,
    command: Sequence[str],
) -> RuntimeRecord:
    """Build runtime metadata for a freshly started service process."""
    pgid = _process_group_id(process)
    return RuntimeRecord(
        pid=process.pid,
        pgid=pgid,
        host=host,
        port=port,
        command=tuple(command),
        started_at=time.time(),
    )


def _process_group_id(process: subprocess.Popen) -> int | None:
    """Return a cached or live Unix process group id for a managed process."""
    if sys.platform == "win32":
        return None
    cached = getattr(process, "_flocks_pgid", None)
    if isinstance(cached, int) and cached > 0:
        return cached
    try:
        pgid = os.getpgid(process.pid)
    except OSError:
        return None
    try:
        setattr(process, "_flocks_pgid", pgid)
    except Exception:
        pass
    return pgid


def read_pid(pid_file: Path) -> int | None:
    """Read a pid file if it exists and contains a valid integer."""
    record = read_runtime_record(pid_file)
    return record.pid if record else None


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


def _windows_tasklist_process_name(pid: int) -> str | None:
    """Return the Windows image name for a pid via tasklist when possible."""
    if sys.platform != "win32" or pid <= 0:
        return None
    completed = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    line = completed.stdout.strip()
    if not line or line.startswith("INFO:"):
        return None
    with contextlib.suppress(Exception):
        import csv

        rows = list(csv.reader([line]))
        if rows and rows[0]:
            value = rows[0][0].strip()
            return value or None
    return None


def _windows_process_snapshot(pid: int) -> dict[str, str] | None:
    """Return lightweight process details for Windows pid identity checks."""
    if sys.platform != "win32" or pid <= 0:
        return None

    powershell = which("powershell") or which("powershell.exe")
    if powershell:
        script = (
            f'$p = Get-CimInstance Win32_Process -Filter "ProcessId = {pid}"; '
            'if ($null -eq $p) { exit 1 }; '
            '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; '
            '[PSCustomObject]@{'
            'Name = $p.Name; '
            'CommandLine = $p.CommandLine; '
            'ExecutablePath = $p.ExecutablePath'
            '} | ConvertTo-Json -Compress'
        )
        completed = subprocess.run(
            [powershell, "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode == 0:
            with contextlib.suppress(json.JSONDecodeError):
                payload = json.loads((completed.stdout or "").strip() or "{}")
                if isinstance(payload, dict):
                    return {
                        "name": str(payload.get("Name") or ""),
                        "command_line": str(payload.get("CommandLine") or ""),
                        "executable_path": str(payload.get("ExecutablePath") or ""),
                    }

    name = _windows_tasklist_process_name(pid)
    if name is None:
        return None
    return {"name": name, "command_line": "", "executable_path": ""}


def _expected_windows_images(record: RuntimeRecord) -> set[str]:
    """Return plausible Windows image names for a runtime record."""
    if not record.command:
        return set()

    executable = Path(record.command[0]).name.lower()
    result = {executable} if executable else set()
    if executable.endswith((".cmd", ".bat")):
        result.add("cmd.exe")
    if executable.startswith("python"):
        result.update({"python.exe", "python"})
    if executable.startswith("npm"):
        result.update({"cmd.exe", "node.exe", "npm.cmd", "npm.exe"})
    return result


def _windows_identity_clauses(record: RuntimeRecord) -> list[tuple[str, ...]]:
    """Return command-line token clauses that strongly identify a service pid."""
    payload = " ".join(record.command).lower()
    clauses: list[tuple[str, ...]] = []
    if "flocks.cli.main" in payload:
        clauses.append(("flocks.cli.main", "serve"))
    elif record.command and Path(record.command[0]).name.lower().startswith("flocks"):
        clauses.append(("flocks", "serve"))

    if record.command and Path(record.command[0]).name.lower().startswith("npm"):
        clauses.append(("npm", "preview"))
    return clauses


def _windows_runtime_record_matches_pid(
    record: RuntimeRecord,
    pid: int,
    listeners: Iterable[int] | None = None,
) -> bool:
    """Return True when a Windows pid still looks like the recorded service."""
    if sys.platform != "win32":
        return False
    if pid <= 0 or not pid_is_running(pid):
        return False

    listener_set = set(listeners or [])
    if listener_set and pid in listener_set:
        return True
    if not record.command:
        return True

    snapshot = _windows_process_snapshot(pid)
    if snapshot is None:
        # If Windows refuses to provide identity details, keep the record to
        # avoid tearing down a healthy service based on incomplete evidence.
        return True

    name = snapshot.get("name", "").strip().lower()
    command_line = snapshot.get("command_line", "").strip().lower()
    executable_path = snapshot.get("executable_path", "").strip().lower()

    expected_images = _expected_windows_images(record)
    actual_image = Path(executable_path).name.lower() if executable_path else name
    if actual_image and actual_image in expected_images:
        return True

    if command_line:
        for clause in _windows_identity_clauses(record):
            if all(token in command_line for token in clause):
                return True

    if not name and not command_line and not executable_path:
        return True
    return False


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
    if sys.platform == "win32":
        listeners = port_owner_pids(record.port) if record.port is not None else []
        return _windows_runtime_record_matches_pid(record, record.pid, listeners)
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


def _port_owner_lookup_available() -> bool:
    """Return True when the current platform can resolve listener pids."""
    return sys.platform == "win32" or bool(which("lsof") or which("fuser"))


def _bind_port_available(port: int) -> bool:
    """Return True when the TCP port can be bound on any local IPv4 interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def _warn_missing_port_owner_tools() -> None:
    """Warn when pid-based port inspection is unavailable."""
    warnings.warn(MISSING_PORT_OWNER_TOOLS_WARNING, RuntimeWarning, stacklevel=2)


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

    _warn_missing_port_owner_tools()
    return []


def port_is_in_use(port: int, listeners: Sequence[int] | None = None) -> bool:
    """Return True when the TCP port is already occupied."""
    current_listeners = list(listeners) if listeners is not None else port_owner_pids(port)
    if current_listeners:
        return True
    if _port_owner_lookup_available():
        return False
    return not _bind_port_available(port)


def _process_command_line(pid: int) -> str:
    """Return a process command line for best-effort orphan detection."""
    if pid <= 0:
        return ""
    if sys.platform == "win32":
        snapshot = _windows_process_snapshot(pid)
        return str(snapshot.get("command_line") or "") if snapshot else ""
    completed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _trusted_flocks_port_owner(pid: int, *, service: str, root: Path) -> bool:
    """Return True only for port owners that look like Flocks leftovers."""
    command_line = _process_command_line(pid).lower()
    if not command_line:
        return False
    root_text = str(root).lower()
    webui_text = str(root / "webui").lower()
    if service == "backend":
        looks_like_uvicorn_backend = "uvicorn" in command_line and "flocks.server.app:app" in command_line
        return (
            looks_like_uvicorn_backend
            or ("flocks.cli.main" in command_line and "serve" in command_line)
            or ("flocks" in command_line and "serve" in command_line and root_text in command_line)
        )
    if service == "webui":
        looks_like_vite = "vite" in command_line and (
            "preview" in command_line or "--host" in command_line or "--port" in command_line
        )
        looks_like_flocks_webui = (
            webui_text in command_line
            or root_text in command_line
            or "/flocks/webui/" in command_line
            or "\\flocks\\webui\\" in command_line
        )
        return looks_like_vite and looks_like_flocks_webui
    return False


def _terminate_orphan_pid(pid: int, label: str, console, *, timeout: float = 5.0) -> None:
    """Terminate a trusted orphan process tree by pid."""
    console.print(f"[flocks] 清理残留 {label} 进程（PID={pid}）...")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        return

    pgid: int | None = None
    try:
        candidate_pgid = os.getpgid(pid)
        if candidate_pgid != os.getpgrp():
            pgid = candidate_pgid
    except OSError:
        pgid = None

    targets = collect_process_tree_pids(pid)
    signal_process_group(signal.SIGTERM, pgid)
    signal_pid_list(signal.SIGTERM, targets)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(pid_is_running(target) for target in targets) and not process_group_is_running(pgid):
            return
        time.sleep(0.25)
    signal_process_group(signal.SIGKILL, pgid)
    signal_pid_list(signal.SIGKILL, targets)


def cleanup_trusted_port_owners(port: int, *, service: str, label: str, console, root: Path | None = None) -> list[int]:
    """Clean Flocks-owned orphan processes that are still occupying a service port."""
    current_root = root or ensure_install_layout()
    listeners = port_owner_pids(port)
    trusted = [pid for pid in listeners if _trusted_flocks_port_owner(pid, service=service, root=current_root)]
    for pid in trusted:
        _terminate_orphan_pid(pid, label, console)
    if trusted:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            current = port_owner_pids(port)
            if not any(pid in trusted for pid in current):
                break
            time.sleep(0.25)
    return trusted


def _process_list_pids() -> list[int]:
    """Return process ids for best-effort trusted orphan cleanup."""
    if sys.platform == "win32":
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | ForEach-Object { $_.ProcessId }",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        completed = subprocess.run(
            ["ps", "-eo", "pid="],
            check=False,
            capture_output=True,
            text=True,
        )
    if completed.returncode != 0:
        return []
    pids = []
    for line in completed.stdout.splitlines():
        value = line.strip()
        if value.isdigit():
            pids.append(int(value))
    return sorted(dict.fromkeys(pids))


def _windows_trusted_daemon_process_pids(*, root: Path) -> list[int]:
    """Return trusted Windows daemon pids with a single process query."""
    if sys.platform != "win32":
        return []
    root_text = str(root).lower()
    env = os.environ.copy()
    env["FLOCKS_DAEMON_ROOT_MATCH"] = root_text
    env["FLOCKS_DAEMON_CURRENT_PID"] = str(os.getpid())
    powershell = which("powershell") or which("powershell.exe")
    if not powershell:
        return []
    script = (
        "$root = [Environment]::GetEnvironmentVariable('FLOCKS_DAEMON_ROOT_MATCH'); "
        "$currentPid = [int][Environment]::GetEnvironmentVariable('FLOCKS_DAEMON_CURRENT_PID'); "
        "Get-CimInstance Win32_Process | Where-Object { "
        "$_.ProcessId -ne $currentPid -and $_.CommandLine -and "
        "$_.CommandLine.ToLowerInvariant().Contains('service-daemon') -and "
        "$_.CommandLine.ToLowerInvariant().Contains('flocks') -and "
        "$_.CommandLine.ToLowerInvariant().Contains($root) "
        "} | ForEach-Object { $_.ProcessId }"
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if completed.returncode != 0:
        return []
    return sorted(
        dict.fromkeys(int(line.strip()) for line in completed.stdout.splitlines() if line.strip().isdigit())
    )


def _trusted_flocks_daemon_owner(pid: int, *, root: Path) -> bool:
    """Return True only for daemon processes that belong to this Flocks install."""
    if pid <= 0 or pid == os.getpid():
        return False
    command_line = _process_command_line(pid).lower()
    if not command_line:
        return False
    root_text = str(root).lower()
    return "service-daemon" in command_line and "flocks" in command_line and root_text in command_line


def trusted_daemon_process_pids(*, root: Path | None = None) -> list[int]:
    """Return trusted daemon pids for the current Flocks install."""
    current_root = root or ensure_install_layout()
    if sys.platform == "win32":
        return _windows_trusted_daemon_process_pids(root=current_root)
    return [pid for pid in _process_list_pids() if _trusted_flocks_daemon_owner(pid, root=current_root)]


def cleanup_trusted_daemon_processes(*, console, root: Path | None = None) -> list[int]:
    """Clean trusted Flocks daemon processes whose control API is unavailable."""
    trusted = trusted_daemon_process_pids(root=root)
    for pid in trusted:
        _terminate_orphan_pid(pid, "daemon", console)
    return trusted


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


def _is_healthy_status_response(response: httpx.Response) -> bool:
    """Return True when the backend health endpoint reports healthy."""
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("status") == "healthy"


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


class _StdoutConsole:
    """Console adapter for daemon logs redirected to a file."""

    def print(self, *args, **_kwargs) -> None:
        sys.stdout.write(" ".join(str(arg) for arg in args) + "\n")
        sys.stdout.flush()


def _backend_health_url(host: str, port: int) -> str:
    return f"http://{_format_host_for_url(access_host(host))}:{port}/api/health"


def _terminate_process(
    process: subprocess.Popen | None,
    name: str,
    console,
    *,
    timeout: float = 10.0,
) -> None:
    """Terminate a process and its process group without scanning service ports."""
    if process is None:
        return

    record = process_runtime_record(process, host=None, port=None, command=())
    if process.poll() is not None and not process_group_is_running(record.pgid):
        return

    console.print(f"[flocks] 停止 {name}（PID={process.pid}）...")
    if sys.platform == "win32":
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, capture_output=True)
    else:
        if record.pgid is not None:
            signal_process_group(signal.SIGTERM, record.pgid)
        else:
            signal_pid_list(signal.SIGTERM, collect_process_tree_pids(process.pid))

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None and not process_group_is_running(record.pgid):
            return
        time.sleep(0.25)

    console.print(f"[flocks] {name} 未在预期时间内退出，强制终止...")
    if sys.platform == "win32":
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, capture_output=True)
    else:
        if record.pgid is not None:
            signal_process_group(signal.SIGKILL, record.pgid)
        signal_pid_list(signal.SIGKILL, collect_process_tree_pids(process.pid))


def _backend_command_and_env(root: Path, config: ServiceConfig) -> tuple[list[str], dict[str, str]]:
    """Build the backend service command and environment."""
    command = resolve_flocks_cli_command(root) + [
        "serve",
        "--host",
        config.backend_host,
        "--port",
        str(config.backend_port),
    ]
    env = os.environ.copy()
    env["_FLOCKS_WEBUI_HOST"] = config.frontend_host
    env["_FLOCKS_WEBUI_PORT"] = str(config.frontend_port)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("FLOCKS_CONSOLE_BASE_URL", DEFAULT_FLOCKS_CONSOLE_BASE_URL)
    return command, env


def _build_webui_dist(root: Path, config: ServiceConfig, console) -> None:
    """Build the production WebUI static bundle."""
    npm = resolve_npm_executable()
    if not npm:
        raise ServiceError("WebUI dist 不存在，且未检测到 npm；请先安装 Node.js 22+（包含 npm）后重试。")
    if not node_version_satisfies_requirement():
        raise ServiceError(f"检测到的 Node.js 版本过低。构建 WebUI 至少需要 Node.js {MIN_NODE_MAJOR}+。")

    webui_dir = root / "webui"
    if not (webui_dir / "package.json").exists():
        raise ServiceError("未找到 WebUI 源码，无法构建静态资源。")

    console.print("[flocks] 准备 Flocks 静态资源...")
    frontend_env = build_frontend_env(config)
    run_kwargs: dict[str, object] = {"cwd": webui_dir, "check": False, "env": frontend_env}
    if sys.platform == "win32":
        run_kwargs.update({"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"})
    completed = subprocess.run([npm, "run", "build"], **run_kwargs)
    if completed.returncode != 0:
        output = "\n".join(
            value for value in (getattr(completed, "stdout", None), getattr(completed, "stderr", None)) if value
        )
        if windows_frontend_build_assertion_is_recoverable(webui_dir, output):
            console.print("[flocks] WebUI 构建产物已生成，忽略 Windows Node.js 退出断言。")
        else:
            if output:
                console.print(output)
            raise ServiceError("WebUI 构建失败。")


def _ensure_webui_dist(root: Path, config: ServiceConfig, console) -> None:
    """Ensure the FastAPI process can serve the production WebUI bundle."""
    from flocks.server.static_webui import WebUIDistMissingError, ensure_webui_dist_dir

    try:
        ensure_webui_dist_dir()
        return
    except WebUIDistMissingError:
        if config.skip_frontend_build:
            raise

    _build_webui_dist(root, config, console)
    ensure_webui_dist_dir()


def _cleanup_backend_start_port(port: int, console, *, root: Path) -> list[int]:
    """Clean trusted leftovers that can occupy the unified public service port."""
    cleaned: list[int] = []
    cleaned.extend(
        cleanup_trusted_port_owners(
            port,
            service="backend",
            label="后端",
            console=console,
            root=root,
        )
    )
    cleaned.extend(
        cleanup_trusted_port_owners(
            port,
            service="webui",
            label="WebUI",
            console=console,
            root=root,
        )
    )
    return sorted(dict.fromkeys(cleaned))


def _start_backend_process(
    config: ServiceConfig,
    console,
    *,
    paths: RuntimePaths | None = None,
) -> subprocess.Popen:
    """Start the backend child process for the supervisor."""
    root = ensure_install_layout()
    current = paths if paths is not None else ensure_runtime_dirs()
    _ensure_webui_dist(root, config, console)

    listeners = port_owner_pids(config.backend_port)
    if listeners:
        _cleanup_backend_start_port(config.backend_port, console, root=root)
        listeners = port_owner_pids(config.backend_port)
        if listeners:
            raise ServiceError(
                f"server 端口 {config.backend_port} 已被占用 (PID: {_join_pids(listeners)})，"
                "请先执行 `flocks stop` 或手动清理残留进程。"
            )
    if port_is_in_use(config.backend_port, listeners):
        raise ServiceError(
            f"server 端口 {config.backend_port} 已被占用，但当前环境无法识别占用 PID；"
            "请先安装 lsof 或手动清理残留进程。"
        )

    command, env = _backend_command_and_env(root, config)
    process = _spawn_process(command, cwd=root, log_path=current.backend_log, env=env)
    record = process_runtime_record(
        process,
        host=config.backend_host,
        port=config.backend_port,
        command=command,
    )
    _log_startup_config(current.backend_log, "backend", config.backend_host, config.backend_port, record)

    try:
        wait_for_http(
            [backend_access_base_url(config)],
            "后端服务",
            delay=3.0,
            validator=_is_running_status_response,
        )
    except ServiceError:
        _emit_service_log_tail(console, current.backend_log, "后端")
        _terminate_process(process, "后端", console)
        raise
    return process


def stop_runtime_record_process(pid_file: Path, name: str, console) -> None:
    """Stop a legacy pid/runtime record without scanning ports."""
    cleanup_stale_pid_file(pid_file)
    record = read_runtime_record(pid_file)
    if record is None:
        pid_file.unlink(missing_ok=True)
        return

    targets = collect_process_tree_pids(record.pid)
    console.print(f"[flocks] 清理旧 {name} 进程（PID={record.pid}）...")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(record.pid), "/T", "/F"], check=False, capture_output=True)
    else:
        if record.pgid is not None:
            signal_process_group(signal.SIGTERM, record.pgid)
        else:
            signal_pid_list(signal.SIGTERM, targets)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not runtime_record_is_running(record):
                pid_file.unlink(missing_ok=True)
                return
            time.sleep(0.25)
        if record.pgid is not None:
            signal_process_group(signal.SIGKILL, record.pgid)
        signal_pid_list(signal.SIGKILL, targets)

    pid_file.unlink(missing_ok=True)


def signal_process_group(sig: signal.Signals, pgid: int | None) -> None:
    """Signal an entire Unix process group when it exists."""
    if sys.platform == "win32" or pgid is None or pgid <= 0:
        return
    try:
        os.killpg(pgid, sig)
    except OSError:
        pass


def _recorded_port(pid_file: Path, default: int) -> int:
    """Return the port from a legacy runtime record, falling back to *default*."""
    record = read_runtime_record(pid_file)
    if record is not None and record.port is not None:
        return record.port
    return default


def _recorded_host(pid_file: Path, default: str) -> str:
    """Return the host from a legacy runtime record, falling back to *default*."""
    record = read_runtime_record(pid_file)
    if record is not None and record.host:
        return record.host
    return default


@contextlib.contextmanager
def service_lock(paths: RuntimePaths):
    """Serialize CLI lifecycle commands while starting/stopping the daemon."""
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


def _wait_for_supervisor_ready(
    paths: RuntimePaths,
    *,
    process: subprocess.Popen | None = None,
    timeout: float = SUPERVISOR_START_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Wait for the supervisor control API and managed services to become ready."""
    deadline = time.monotonic() + timeout
    last_payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise ServiceError(f"Flocks daemon 启动失败，退出码: {process.returncode}")
        try:
            status = read_supervisor_status(paths=paths, timeout=1.0)
            last_payload = status.raw
            backend_state = status.backend.state
            webui_state = status.webui.state
            if backend_state == "healthy" and webui_state in {"healthy", "static"}:
                return status.raw
            if backend_state == "degraded" or webui_state == "degraded":
                return status.raw
        except Exception:
            pass
        time.sleep(0.5)
    if last_payload is not None:
        return last_payload
    raise ServiceError("Flocks daemon 启动超时，请检查日志。")


def _start_supervisor_process(config: ServiceConfig, paths: RuntimePaths, console) -> subprocess.Popen:
    """Spawn the detached service supervisor daemon."""
    root = ensure_install_layout()
    log_path = supervisor_log_path(paths)
    if not supervisor_uses_tcp_control():
        supervisor_socket_path(paths).unlink(missing_ok=True)
    command = resolve_flocks_cli_command(root) + [
        "service-daemon",
        "--server-host",
        config.backend_host,
        "--server-port",
        str(config.backend_port),
        "--webui-host",
        config.frontend_host,
        "--webui-port",
        str(config.frontend_port),
    ]
    if config.legacy_backend_host is not None:
        command.extend(["--legacy-server-host", config.legacy_backend_host])
    if config.legacy_backend_port is not None:
        command.extend(["--legacy-server-port", str(config.legacy_backend_port)])
    if config.server_port_migration_hint:
        command.append("--server-port-migration-hint")
    if config.skip_frontend_build:
        command.append("--skip-webui-build")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    return _spawn_process(command, cwd=root, log_path=log_path, env=env)


def _service_config_matches(left: ServiceConfig, right: ServiceConfig) -> bool:
    """Return True when two configs manage the same service endpoints."""
    return (
        left.backend_host == right.backend_host
        and left.backend_port == right.backend_port
        and left.frontend_host == right.frontend_host
        and left.frontend_port == right.frontend_port
    )


def _supervisor_backend_is_healthy(status) -> bool:
    """Return whether a supervisor status represents an accessible Flocks service."""
    return (
        not status.backend.paused
        and status.backend.state.lower() == "healthy"
        and status.backend.health.lower() == "healthy"
    )


def _legacy_runtime_config(paths: RuntimePaths, fallback: ServiceConfig) -> ServiceConfig:
    """Build cleanup config from legacy runtime records when present."""
    return ServiceConfig(
        backend_host=_recorded_host(paths.backend_pid, fallback.backend_host),
        backend_port=_recorded_port(paths.backend_pid, fallback.backend_port),
        frontend_host=_recorded_host(paths.frontend_pid, fallback.frontend_host),
        frontend_port=_recorded_port(paths.frontend_pid, fallback.frontend_port),
        legacy_backend_host=fallback.legacy_backend_host,
        legacy_backend_port=fallback.legacy_backend_port,
        no_browser=fallback.no_browser,
        skip_frontend_build=fallback.skip_frontend_build,
    )


def _unique_cleanup_configs(*configs: ServiceConfig) -> list[ServiceConfig]:
    """Deduplicate cleanup configs by backend/WebUI ports."""
    result: list[ServiceConfig] = []
    seen: set[tuple[int, int, int | None]] = set()
    for config in configs:
        key = (config.backend_port, config.frontend_port, config.legacy_backend_port)
        if key in seen:
            continue
        seen.add(key)
        result.append(config)
    return result


def cleanup_legacy_runtime_processes(paths: RuntimePaths, console) -> None:
    """Clean legacy pid/runtime records left by pre-daemon service starts."""
    for pid_file, name in (
        (watchdog_pid_path(paths), "watchdog"),
        (paths.frontend_pid, "WebUI"),
        (paths.backend_pid, "后端"),
    ):
        stop_runtime_record_process(pid_file, name, console)


def _stop_all_unlocked(console, *, paths: RuntimePaths) -> None:
    """Stop managed services; caller must hold the lifecycle lock."""
    cleanup_config = ServiceConfig()
    legacy_config = _legacy_runtime_config(paths, cleanup_config)
    stop_status = None
    if not supervisor_is_running(paths):
        console.print("[flocks] Flocks daemon 未运行。")
        cleanup_legacy_runtime_processes(paths, console)
        cleanup_orphan_service_ports(cleanup_config, console, extra_configs=[legacy_config])
        stop_all_browser_daemons()
        return
    try:
        stop_status = read_supervisor_status(paths=paths, timeout=1.0)
        cleanup_config = stop_status.config
        legacy_config = _legacy_runtime_config(paths, cleanup_config)
    except Exception:
        pass
    try:
        request_stop(paths=paths, timeout=2.0)
    except Exception as exc:
        raise ServiceError(f"无法请求 Flocks daemon 停止: {exc}") from exc

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if not supervisor_is_running(paths):
            cleanup_legacy_runtime_processes(paths, console)
            cleanup_orphan_service_ports(cleanup_config, console, extra_configs=[legacy_config])
            stop_all_browser_daemons()
            _print_stop_summary(console, stop_status)
            return
        time.sleep(0.5)
    raise ServiceError("Flocks daemon 未在预期时间内退出。")


def stop_all(console) -> None:
    """Stop managed services through the supervisor control API."""
    paths = ensure_runtime_dirs()
    with service_lock(paths):
        _stop_all_unlocked(console, paths=paths)


def _start_all_without_stop(config: ServiceConfig, console) -> None:
    """Start the supervisor daemon, then print access summary."""
    paths = ensure_runtime_dirs()
    _print_static_port_migration_hint(config, console)
    console.print("[flocks] Flocks daemon 启动中...")
    cleanup_legacy_runtime_processes(paths, console)
    cleanup_orphan_service_ports(config, console)
    _ensure_webui_dist(ensure_install_layout(), config, console)
    process = _start_supervisor_process(config, paths, console)
    console.print("[flocks] Flocks daemon 已启动。")
    payload = _wait_for_supervisor_ready(paths, process=process)
    _print_status_payload(payload, console, include_daemon_step=False)
    if not config.no_browser:
        open_default_browser(config.frontend_url, console)


def _start_all_unlocked(config: ServiceConfig, console, *, paths: RuntimePaths) -> None:
    """Ensure the supervisor daemon is running; caller must hold lifecycle lock."""
    _resolve_upgrade_runtime(console, frontend_port=config.frontend_port, attempt_recover=False)
    if supervisor_is_running(paths):
        status = None
        try:
            status = read_supervisor_status(paths=paths, timeout=1.0)
        except Exception:
            status = None
        if status is not None and not _service_config_matches(config, status.config):
            console.print("[flocks] Flocks daemon 已在运行，但配置已变化，正在按新配置重启...")
            _stop_all_unlocked(console, paths=paths)
            _start_all_without_stop(config, console)
            return
        if status is not None and (status.backend.paused or status.backend.state.lower() == "paused"):
            console.print("[flocks] Flocks daemon 已在运行，但 Flocks service 处于暂停状态，正在重新启动...")
            _stop_all_unlocked(console, paths=paths)
            _start_all_without_stop(config, console)
            return
        if status is not None and not _supervisor_backend_is_healthy(status):
            console.print("[flocks] Flocks daemon 已在运行，但 Flocks service 不可用，正在重启...")
            status = request_restart(config, paths=paths)
            _print_status_payload(status.raw, console, include_daemon_step=False)
            if not config.no_browser and _supervisor_backend_is_healthy(status):
                open_default_browser(_frontend_url_from_status(status, config.frontend_url), console)
            return
        console.print("[flocks] Flocks daemon 已在运行。")
        show_status(console)
        if status is not None and not config.no_browser and _supervisor_backend_is_healthy(status):
            try:
                url = _frontend_url_from_status(status, config.frontend_url)
            except Exception:
                url = config.frontend_url
            open_default_browser(url, console)
        return
    _start_all_without_stop(config, console)


def start_all(config: ServiceConfig, console) -> None:
    """Ensure the supervisor daemon is running."""
    paths = ensure_runtime_dirs()
    with service_lock(paths):
        _start_all_unlocked(config, console, paths=paths)


def restart_all(config: ServiceConfig, console) -> None:
    """Restart by stopping the daemon first, then starting a fresh daemon."""
    paths = ensure_runtime_dirs()
    with service_lock(paths):
        _stop_all_unlocked(console, paths=paths)
        _start_all_unlocked(config, console, paths=paths)


def _print_static_port_migration_hint(config: ServiceConfig, console) -> None:
    """Explain legacy server-port behavior when it differs from public WebUI port."""
    if (
        not config.server_port_migration_hint
        or config.legacy_backend_port is None
        or config.legacy_backend_port == config.backend_port
    ):
        return
    console.print(
        "[flocks] API 已与 WebUI 同源，"
        f"当前统一监听端口为 {config.backend_port}；旧 server 端口 {config.legacy_backend_port} 仅用于残留清理。"
    )


def _print_stop_summary(console, status) -> None:
    """Print stopped services from the last available supervisor status."""
    if status is not None:
        if status.backend.pid is not None:
            console.print(f"[flocks] flocks 已停止（PID={status.backend.pid}）。")
    console.print("[flocks] daemon 已停止。")


def cleanup_orphan_service_ports(config: ServiceConfig, console, *, extra_configs: Sequence[ServiceConfig] = ()) -> None:
    """Clean trusted Flocks leftovers on configured backend/WebUI ports."""
    root = ensure_install_layout()
    cleanup_trusted_daemon_processes(console=console, root=root)
    candidates: list[ServiceConfig] = []
    for candidate in (config, config.legacy_cleanup_config, *extra_configs):
        candidates.append(candidate)
        candidates.append(candidate.legacy_cleanup_config)
    for cleanup_config in _unique_cleanup_configs(*candidates):
        cleanup_trusted_port_owners(
            cleanup_config.backend_port,
            service="backend",
            label="后端",
            console=console,
            root=root,
        )
        cleanup_trusted_port_owners(
            cleanup_config.backend_port,
            service="webui",
            label="WebUI",
            console=console,
            root=root,
        )
        cleanup_trusted_port_owners(
            cleanup_config.frontend_port,
            service="webui",
            label="WebUI",
            console=console,
            root=root,
        )
        cleanup_trusted_port_owners(
            cleanup_config.frontend_port,
            service="backend",
            label="后端",
            console=console,
            root=root,
        )


def build_status_lines(paths: RuntimePaths | None = None) -> list[str]:
    """Return a human-readable status summary from the supervisor control API."""
    current = paths or runtime_paths()
    try:
        status = read_supervisor_status(paths=current)
    except Exception:
        residual_daemons = []
        try:
            residual_daemons = trusted_daemon_process_pids(root=ensure_install_layout())
        except Exception:
            residual_daemons = []
        if residual_daemons:
            return [
                "[flocks] Flocks daemon control API 未运行",
                f"[flocks] 检测到残留 daemon 进程: PID={_join_pids(residual_daemons)}",
                f"[flocks] 日志: {supervisor_log_path(current)}",
                "[flocks] 可执行 `flocks stop` 清理残留进程。",
            ]
        return [
            "[flocks] Flocks daemon 未运行",
            f"[flocks] 日志: {supervisor_log_path(current)}",
        ]
    return _status_lines_from_payload(status.raw)


def _status_lines_from_payload(payload: dict[str, Any]) -> list[str]:
    daemon = payload.get("daemon") if isinstance(payload.get("daemon"), dict) else {}
    backend = payload.get("backend") if isinstance(payload.get("backend"), dict) else {}
    lines = [
        "[flocks] 服务",
        _daemon_status_line(daemon),
        _service_status_line("flocks", backend),
        "",
        "[flocks] 日志",
        f"[flocks]   daemon: {daemon.get('log_path')}",
    ]
    log_path = backend.get("log_path")
    if log_path:
        lines.append(f"[flocks]   flocks: {log_path}")
    return lines


def _service_status_line(label: str, payload: dict[str, Any]) -> str:
    host = _loopback_host(str(payload.get("host") or "127.0.0.1"))
    port = payload.get("port")
    pid = payload.get("pid")
    state = payload.get("state") or "unknown"
    error = payload.get("last_error")
    suffix = f" last_error={error}" if error else ""
    pid_part = f" PID={pid}" if pid is not None else ""
    return f"[flocks]   {label}: state={state}{pid_part} URL=http://{host}:{port}{suffix}"


def _daemon_status_line(payload: dict[str, Any]) -> str:
    pid = payload.get("pid")
    state = payload.get("state") or "unknown"
    error = payload.get("last_error")
    suffix = f" last_error={error}" if error else ""
    return f"[flocks]   daemon: state={state} PID={pid}{suffix}"


def _startup_step_status(state: object, *, ready_states: set[str]) -> str:
    return "已启动" if str(state or "").lower() in ready_states else "启动异常"


def _startup_status_lines_from_payload(payload: dict[str, Any], *, include_daemon_step: bool = True) -> list[str]:
    daemon = payload.get("daemon") if isinstance(payload.get("daemon"), dict) else {}
    backend = payload.get("backend") if isinstance(payload.get("backend"), dict) else {}
    lines = []
    if include_daemon_step:
        lines.append(f"[flocks] Flocks daemon {_startup_step_status(daemon.get('state'), ready_states={'running'})}。")
    lines.extend([
        f"[flocks] Flocks service {_startup_step_status(backend.get('state'), ready_states={'healthy'})}。",
        "",
        "[flocks] 服务",
        _daemon_status_line(daemon),
        _service_status_line("flocks", backend),
        "",
        "[flocks] 日志",
        f"[flocks]   daemon: {daemon.get('log_path')}",
    ])
    log_path = backend.get("log_path")
    if log_path:
        lines.append(f"[flocks]   flocks: {log_path}")
    return lines


def _frontend_url_from_status(status, fallback: str) -> str:
    if status.backend.port is not None:
        return f"http://{_format_host_for_url(_loopback_host(status.backend.host))}:{status.backend.port}"
    return fallback


def _print_status_payload(payload: dict[str, Any], console, *, include_daemon_step: bool = True) -> None:
    for line in _startup_status_lines_from_payload(payload, include_daemon_step=include_daemon_step):
        console.print(line)


def show_status(console) -> None:
    """Print service status."""
    for line in build_status_lines():
        console.print(line)


def show_logs(
    console,
    *,
    backend: bool = False,
    webui: bool = False,
    follow: bool = True,
    lines: int = 50,
) -> None:
    """Print recent service logs through the supervisor control API."""
    paths = ensure_runtime_dirs()
    service = "all"
    if backend and not webui:
        service = "backend"
    elif webui and not backend:
        service = "webui"
    if not follow:
        try:
            payload = read_logs(service=service, lines=lines, paths=paths, timeout=5.0)
        except Exception as exc:
            console.print(f"[flocks] Flocks daemon 日志接口不可用，改为读取本地日志文件: {exc}")
            _show_local_logs(console, paths, backend=backend, webui=webui, follow=False, lines=lines)
            return
        logs = payload.get("logs") if isinstance(payload.get("logs"), dict) else {}
        for prefix, entry in logs.items():
            if not isinstance(entry, dict):
                continue
            console.print(f"[{prefix}] --- {entry.get('path')} ---")
            for line in entry.get("lines") or []:
                console.print(f"[{prefix}] {line}")
        return

    console.print("[flocks] 按 Ctrl+C 退出日志跟随。")
    try:
        for line in stream_logs(service=service, lines=lines, paths=paths, timeout=None):
            console.print(line)
    except KeyboardInterrupt:
        return
    except Exception as exc:
        console.print(f"[flocks] Flocks daemon 日志接口不可用，改为跟随本地日志文件: {exc}")
        _show_local_logs(console, paths, backend=backend, webui=webui, follow=True, lines=lines)


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
        return [paths.backend_log]
    return [paths.backend_log]


def _selected_log_entries(paths: RuntimePaths, *, backend: bool = False, webui: bool = False) -> list[tuple[str, Path]]:
    """Return local log files selected by CLI flags."""
    if backend and not webui:
        return [("flocks", paths.backend_log)]
    if webui and not backend:
        return [("flocks", paths.backend_log)]
    return [
        ("flocks", paths.backend_log),
        ("daemon", supervisor_log_path(paths)),
    ]


def _show_local_logs(
    console,
    paths: RuntimePaths,
    *,
    backend: bool = False,
    webui: bool = False,
    follow: bool = True,
    lines: int = 50,
) -> None:
    """Print local log files when the daemon control API is unavailable."""
    selections = _selected_log_entries(paths, backend=backend, webui=webui)
    for prefix, path in selections:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        console.print(f"[{prefix}] --- {path} ---")
        for line in tail_lines(path, lines):
            console.print(f"[{prefix}] {line}")

    if not follow:
        return

    handles = {}
    try:
        for prefix, path in selections:
            handle = path.open("r", encoding="utf-8", errors="replace")
            handle.seek(0, os.SEEK_END)
            handles[prefix] = handle
        while True:
            emitted = False
            for prefix, handle in handles.items():
                while True:
                    line = handle.readline()
                    if not line:
                        break
                    emitted = True
                    console.print(f"[{prefix}] {line.rstrip()}")
            if not emitted:
                time.sleep(FOLLOW_POLL_INTERVAL)
    except KeyboardInterrupt:
        return
    finally:
        for handle in handles.values():
            handle.close()


def tail_lines(path: Path, lines: int) -> list[str]:
    """Read the last N lines from a text file."""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=max(lines, 0))]


def _emit_service_log_tail(console, log_path: Path, service_label: str, lines: int = 10) -> None:
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
    if sys.platform == "win32":
        startfile = getattr(os, "startfile", None)
        if startfile is not None:
            try:
                startfile(url)
                console.print(f"[flocks] 浏览器已打开: {url}")
                return
            except Exception:
                pass
    try:
        if webbrowser.open(url):
            console.print(f"[flocks] 浏览器已打开: {url}")
            return
    except Exception:
        pass
    console.print(f"[flocks] 未检测到可用的浏览器打开命令，请手动访问: {url}")


def access_host(host: str) -> str:
    """Return the host that local health checks and browser requests should use."""
    return loopback_host(host)


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


def windows_frontend_build_assertion_is_recoverable(webui_dir: Path, output: str) -> bool:
    """Return True when Windows npm crashed after producing a usable build."""
    if sys.platform != "win32":
        return False
    if not (webui_dir / "dist" / "index.html").exists():
        return False
    return any(marker in output for marker in WINDOWS_FRONTEND_BUILD_ASSERTION_MARKERS)


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

    # Provide portal defaults for plain `flocks start`, while still allowing
    # callers to override via explicit environment variables.
    env.setdefault("FLOCKS_CONSOLE_BASE_URL", DEFAULT_FLOCKS_CONSOLE_BASE_URL)
    env.setdefault(
        "__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS",
        DEFAULT_VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS,
    )

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
    _cap_service_log_file(log_path, MAX_SERVICE_LOG_BYTES)
    handle = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            **kwargs,
        )
        _process_group_id(process)
        return process
    finally:
        handle.close()


def _cap_service_log_file(log_path: Path, max_bytes: int = MAX_SERVICE_LOG_BYTES) -> bool:
    """Keep service logs under *max_bytes* without deleting or renaming them."""
    if max_bytes <= 0:
        return False
    try:
        size = log_path.stat().st_size
    except FileNotFoundError:
        return False
    except OSError:
        return False
    if size <= max_bytes:
        return False

    read_offset = size - max_bytes
    write_offset = 0
    try:
        with log_path.open("r+b") as handle:
            while read_offset < size:
                handle.seek(read_offset)
                chunk = handle.read(min(LOG_TRIM_CHUNK_BYTES, size - read_offset))
                if not chunk:
                    break
                handle.seek(write_offset)
                handle.write(chunk)
                read_offset += len(chunk)
                write_offset += len(chunk)
            handle.truncate(write_offset)
        return True
    except OSError:
        # Logging must not make daemon startup fail.  If Windows still has a
        # transient lock, leave the file untouched and continue with append.
        return False


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
    return loopback_host(host)


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
