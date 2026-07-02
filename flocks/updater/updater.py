"""
Core updater logic

Checks for updates via GitHub / Gitee / GitLab Releases API, downloads
the source archive (zip / tar.gz), backs up the current installation,
extracts the new source over it, re-syncs dependencies, and restarts the
process in-place.

No git binary is required at runtime — all code fetching is done via HTTP.

The ``sources`` list in UpdaterConfig controls the priority order.
The updater tries each source in turn and falls back to the next on failure.
"""

import asyncio
import contextlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, AsyncGenerator, Awaitable, Callable
from urllib.parse import quote, urlparse

import httpx

from flocks.updater.models import UpdateProgress, VersionInfo
from flocks.utils.log import Log

_DEFAULT_REPO = "AgentFlocks/Flocks"
_BACKUP_DIR = Path.home() / ".flocks" / "version"
_UPGRADE_PAGE_MARKER = "flocks-upgrade-in-progress"
_UPGRADE_PHASE_HANDOVER_PREPARING = "handover_preparing"
_UPGRADE_PHASE_TEMP_PAGE_ACTIVE = "temporary_page_active"
_UPGRADE_PHASE_CUTOVER_APPLIED = "cutover_applied"
_UPGRADE_PHASE_ROLLBACK_IN_PROGRESS = "rollback_in_progress"
_UPGRADE_PHASE_ROLLBACK_FAILED = "rollback_failed"
_STATE_FIELD_UNSET = object()
_UPDATE_REGION_CN = "cn"
_CN_NPM_REGISTRY = "https://registry.npmmirror.com/"
_CN_UV_DEFAULT_INDEX = "https://mirrors.aliyun.com/pypi/simple"
_CN_PIP_INDEX_URL = _CN_UV_DEFAULT_INDEX
_CURL_USER_AGENT = "curl/8.7.1"
_FRONTEND_DEPENDENCY_INSTALL_TIMEOUT_SECONDS = 300
_FRONTEND_BUILD_TIMEOUT_SECONDS = 300
_DEPENDENCY_SYNC_TIMEOUT_SECONDS = 180
_WINDOWS_DEPENDENCY_SYNC_TIMEOUT_SECONDS = 300
_CANCELLATION_RETRY_DELAY_SECONDS = 0.1

_PRESERVE_NAMES: set[str] = {
    ".venv",
    "node_modules",
    "logs",
    ".env",
    ".git",
    "__pycache__",
}

log = Log.create(service="updater")


@dataclass(frozen=True)
class ConsoleManifestRelease:
    version: str
    release_notes: str | None
    release_url: str | None
    bundle_url: str
    bundle_sha256: str | None
    bundle_format: str
    manifest: dict[str, Any]
    console_session_token: str | None = None
    release_id: str | None = None
    bundle_release_id: str | None = None


@dataclass(frozen=True)
class ProVersionState:
    bundle_version: str
    core_version: str
    pro_component_version: str | None


def _record_update_journal(message: str) -> None:
    """Append a human-readable upgrade line to today's errors log."""
    from flocks.utils.log import append_upgrade_text_log

    append_upgrade_text_log(message)


@dataclass(frozen=True)
class UpdateMirrorProfile:
    """Resolved download/runtime mirror settings for a single upgrade request."""

    region: str | None
    sources: list[str]
    npm_registry: str | None = None
    uv_default_index: str | None = None
    pip_index_url: str | None = None


@dataclass(frozen=True)
class _FrontendNpmCandidate:
    """A single npm launcher candidate for frontend rebuilds."""

    npm: str
    env: dict[str, str] | None
    source: str


@dataclass(frozen=True)
class _ProComponentSnapshot:
    installed: bool
    version: str | None = None


# ------------------------------------------------------------------ #
# Install root
# ------------------------------------------------------------------ #


def _clean_process_output(value: str | bytes | None) -> str:
    """Normalize subprocess stdout/stderr so Windows commands can't crash upgrade flow."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace").strip()
    return value.strip()


def _windows_path_suffix(value: str) -> str:
    """Return a Windows-style suffix even when running tests on non-Windows hosts."""
    return PureWindowsPath(value).suffix.lower()


def _windows_path_name(value: str) -> str:
    """Return a Windows-style basename even when running tests on non-Windows hosts."""
    return PureWindowsPath(value).name


def _windows_path_stem(value: str) -> str:
    """Return a Windows-style stem even when running tests on non-Windows hosts."""
    return PureWindowsPath(value).stem.lower()


def _windows_command_candidates(name: str) -> list[str]:
    """Return Windows launcher candidates for *name* preserving explicit suffixes."""
    if _windows_path_suffix(name) in {".exe", ".cmd", ".bat"}:
        return [name]
    return [name, f"{name}.exe", f"{name}.cmd", f"{name}.bat"]


def _running_from_legacy_uv_tool_install() -> bool:
    """Return True when the current interpreter still comes from ``uv tool``."""
    executable = (sys.executable or "").replace("\\", "/").lower()
    return "/uv/tools/flocks/" in executable


def _bundled_node_install_dir() -> Path | None:
    """Return bundled Node.js install dir when Windows installer env vars are set."""
    candidates: list[str] = []
    node_home = os.getenv("FLOCKS_NODE_HOME")
    if node_home:
        candidates.append(node_home)

    install_root = os.getenv("FLOCKS_INSTALL_ROOT")
    if install_root:
        candidates.append(str(Path(install_root).expanduser() / "tools" / "node"))

    for candidate in candidates:
        node_dir = Path(candidate).expanduser()
        node_executable = node_dir / ("node.exe" if sys.platform == "win32" else "bin/node")
        if node_executable.exists():
            return node_dir.resolve()
    return None


def _windows_paths_match(left: str, right: str) -> bool:
    """Return True when two Windows paths likely point to the same launcher/script."""
    if not left or not right:
        return False
    left_path = PureWindowsPath(left)
    right_path = PureWindowsPath(right)
    return left_path == right_path or left_path.name == right_path.name


def _looks_like_windows_python_launcher(entry: str) -> bool:
    """Return True when *entry* looks like a Windows Python launcher."""
    return _windows_path_stem(entry) in {"python", "pythonw", "py"}


def _is_windows_file_in_use_error(exc: BaseException) -> bool:
    """Return True when *exc* looks like a Windows file-lock failure."""
    if sys.platform != "win32":
        return False
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 32:
        return True

    text = str(exc).lower()
    return "winerror 32" in text or "used by another process" in text


def _is_uv_managed_python_runtime_error(text: str) -> bool:
    """Return True when uv reports a broken managed Python runtime cache."""
    if not text:
        return False
    return bool(
        re.search(
            (
                r"Failed to inspect Python interpreter from managed installations|"
                r"Failed to query Python interpreter|"
                r"failed to query metadata of file|"
                r"Failed to create temporary virtualenv|"
                r"Could not find a suitable Python executable for the virtual "
                r"environment based on the interpreter"
            ),
            text,
            re.IGNORECASE,
        )
    )


def _uv_managed_python_install_dir_from_text(text: str) -> Path | None:
    """Extract the cached uv-managed Python install directory from error text."""
    if not text:
        return None
    match = re.search(
        r"([A-Z]:\\[^'\"\r\n]+\\uv\\python\\[^'\"\r\n]+\\python\.exe)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return Path(str(PureWindowsPath(match.group(1)).parent))


def _repair_windows_uv_managed_python_install(text: str) -> Path | None:
    """Remove the broken cached uv-managed Python install directory when possible."""
    install_dir = _uv_managed_python_install_dir_from_text(text)
    if install_dir is None:
        return None

    target = _resolve_windows_long_path(install_dir)
    if not target.exists():
        return target

    _safe_rmtree(target)
    return target


def _resolve_windows_long_path(path: Path) -> Path:
    """Resolve Windows 8.3 short path names (e.g. THREAT~1) to their full long form.

    ``tempfile.mkdtemp`` may return paths containing 8.3 short names when the
    user profile directory exceeds eight characters.  Passing such a path as
    ``cwd`` to a Node.js subprocess causes ``process.cwd()`` to return the
    short form while Vite internally resolves files to the long form, breaking
    ``path.relative()`` in the ``build-html`` plugin.
    """
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetLongPathNameW(str(path), buf, 32768)
        if length > 0:
            return Path(buf.value)
    except Exception:
        pass
    return path


def _get_repo_root() -> Path:
    """
    Return the root of the flocks installation (the directory that owns
    pyproject.toml).  Git is *not* required.
    """
    p = Path(__file__).resolve().parent
    for _ in range(10):
        if (p / "pyproject.toml").exists() and (p / ".git").exists():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent

    p = Path(__file__).resolve().parent
    for _ in range(10):
        if (p / "pyproject.toml").exists():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent

    return Path(__file__).parent.parent.parent


def _upgrade_python_path(install_root: Path) -> Path:
    """Return the project virtualenv interpreter used for upgrade restarts."""
    if sys.platform == "win32":
        return install_root / ".venv" / "Scripts" / "python.exe"
    return install_root / ".venv" / "bin" / "python"


async def _validate_restart_runtime(
    install_root: Path,
) -> str | None:
    """Validate that the project runtime path exists for restart."""
    python_exe = _upgrade_python_path(install_root)
    if not python_exe.exists():
        return f"Restart runtime is missing: {python_exe}"
    return None


def _build_uv_sync_env() -> dict[str, str] | None:
    """Build supplementary env vars for ``uv sync``.

    On Linux/macOS under systemd or other minimal-PATH environments, the
    inherited PATH may not include directories where ``uv`` or managed
    Python interpreters live.  We augment PATH with well-known locations
    so that ``uv sync`` can reliably locate its own toolchain.
    """
    if sys.platform == "win32":
        return None

    home = str(Path.home())
    extra_dirs = [
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".cargo", "bin"),
        "/usr/local/bin",
    ]
    current_path = os.environ.get("PATH", "")
    current_entries = set(current_path.split(os.pathsep))
    missing = [d for d in extra_dirs if d not in current_entries]
    if not missing:
        return None
    return {"PATH": os.pathsep.join([current_path] + missing)}


def _build_frontend_subprocess_env_for_node_dir(
    node_dir: Path | None,
    *,
    npm_registry: str | None = None,
) -> dict[str, str] | None:
    """Build supplemental env vars for frontend npm commands."""
    env: dict[str, str] = {}
    if npm_registry:
        env["npm_config_registry"] = npm_registry

    if node_dir is not None:
        node_bin = str(node_dir if sys.platform == "win32" else node_dir / "bin")
        current_path = os.environ.get("PATH", "")
        path_entries = current_path.split(os.pathsep) if current_path else []
        if node_bin not in path_entries:
            env["PATH"] = node_bin if not current_path else node_bin + os.pathsep + current_path

    return env or None


def _build_frontend_subprocess_env(*, npm_registry: str | None = None) -> dict[str, str] | None:
    """Build supplemental env vars for frontend npm commands."""
    return _build_frontend_subprocess_env_for_node_dir(
        _bundled_node_install_dir(),
        npm_registry=npm_registry,
    )


def _reset_frontend_workspace(webui_dir: Path) -> None:
    """Remove transient frontend build artifacts before retrying another npm candidate."""
    for name in ("node_modules", "dist"):
        target = webui_dir / name
        if target.exists():
            _safe_remove(target)


async def _build_frontend_workspace(
    webui_dir: Path,
    *,
    npm_registry: str | None = None,
) -> str | None:
    """Install dependencies and build the frontend in the active install tree."""
    npm_candidates = _resolve_frontend_npm_candidates(npm_registry=npm_registry)
    if not npm_candidates:
        log.warning(
            "updater.frontend.npm_not_found",
            {
                "hint": ("Cannot build frontend during restart because npm was not found"),
            },
        )
        return "Frontend dependency install failed: npm was not found."

    has_package_lock = (webui_dir / "package-lock.json").exists()
    install_subcommands = ["install"]
    if has_package_lock:
        install_subcommands.append("ci")
    final_frontend_error: str | None = None

    for index, candidate in enumerate(npm_candidates):
        attempt_source = candidate.source
        is_last_attempt = index == len(npm_candidates) - 1
        install_succeeded = False
        for install_attempt_index, install_subcommand in enumerate(install_subcommands):
            install_label = f"npm {install_subcommand}"
            install_cmd = [candidate.npm, install_subcommand]
            try:
                code, _, err = await _run_async(
                    install_cmd,
                    cwd=webui_dir,
                    timeout=_FRONTEND_DEPENDENCY_INSTALL_TIMEOUT_SECONDS,
                    env=candidate.env,
                )
            except subprocess.TimeoutExpired:
                final_frontend_error = (
                    "Frontend dependency install timed out after "
                    f"{_FRONTEND_DEPENDENCY_INSTALL_TIMEOUT_SECONDS}s while running {install_label}."
                )
            else:
                if code == 0:
                    install_succeeded = True
                    final_frontend_error = None
                    break
                final_frontend_error = f"Frontend dependency install failed ({install_label}): {err}"

            has_same_candidate_fallback = install_attempt_index < len(install_subcommands) - 1
            if has_same_candidate_fallback:
                next_install_label = f"npm {install_subcommands[install_attempt_index + 1]}"
                _record_update_journal(
                    "WARN "
                    f"{final_frontend_error} Retrying {next_install_label} "
                    f"with the same npm/node after {attempt_source} {install_label} attempt."
                )

        if not install_succeeded:
            if is_last_attempt:
                break
            _reset_frontend_workspace(webui_dir)
            _record_update_journal(
                "WARN "
                f"{final_frontend_error} Cleaned frontend workspace and retrying with fallback "
                f"npm/node after {attempt_source} attempt."
            )
            continue

        try:
            code, out, err = await _run_async(
                [candidate.npm, "run", "build"],
                cwd=webui_dir,
                timeout=_FRONTEND_BUILD_TIMEOUT_SECONDS,
                env=candidate.env,
            )
        except subprocess.TimeoutExpired:
            final_frontend_error = (
                f"Frontend build timed out after {_FRONTEND_BUILD_TIMEOUT_SECONDS}s while running npm run build."
            )
            if is_last_attempt:
                break
            _reset_frontend_workspace(webui_dir)
            _record_update_journal(
                "WARN "
                f"{final_frontend_error} Cleaned frontend workspace and retrying with fallback "
                f"npm/node after {attempt_source} attempt."
            )
            continue

        if code != 0:
            from flocks.cli import service_manager

            build_output = "\n".join(value for value in (out, err) if value)
            if service_manager.windows_frontend_build_assertion_is_recoverable(webui_dir, build_output):
                final_frontend_error = None
                break
            final_frontend_error = f"Frontend build failed: {err}"
            if is_last_attempt:
                break
            _reset_frontend_workspace(webui_dir)
            _record_update_journal(
                "WARN "
                f"{final_frontend_error} Cleaned frontend workspace and retrying with fallback "
                f"npm/node after {attempt_source} attempt."
            )
            continue

        final_frontend_error = None
        break

    if final_frontend_error is not None:
        return final_frontend_error

    dist_index = webui_dir / "dist" / "index.html"
    if not dist_index.exists():
        return "Frontend build output is missing after npm run build."

    return None


async def build_updated_frontend(
    *,
    locale: str | None = None,
    region: str | None = None,
) -> None:
    """Build the active install tree frontend after a non-restarting update."""
    ucfg = await _get_updater_config()
    profile = _resolve_update_mirror_profile(
        ucfg.sources,
        region=region,
        locale=locale,
    )
    install_root = _get_repo_root()
    webui_dir = install_root / "webui"
    if not webui_dir.is_dir() or not (webui_dir / "package.json").exists():
        return

    frontend_error = await _build_frontend_workspace(
        webui_dir,
        npm_registry=profile.npm_registry,
    )
    if frontend_error is not None:
        raise RuntimeError(frontend_error)


async def _await_ignoring_cancellation(awaitable):
    """Finish a post-handover critical step even if the SSE client disconnects."""
    task = asyncio.create_task(awaitable)
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            log.warning("updater.restart.critical_step_cancelled_ignored")
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(_CANCELLATION_RETRY_DELAY_SECONDS)


def _dependency_sync_timeout_seconds() -> int:
    """Return the timeout budget for ``uv sync`` during self-update."""
    if sys.platform == "win32":
        return _WINDOWS_DEPENDENCY_SYNC_TIMEOUT_SECONDS
    return _DEPENDENCY_SYNC_TIMEOUT_SECONDS


def _build_dependency_sync_command(uv_path: str, *, uv_default_index: str | None = None) -> list[str]:
    """Build the ``uv sync`` command used by the self-updater."""
    cmd = [uv_path, "sync", "--frozen", "--no-python-downloads"]
    if uv_default_index:
        cmd.extend(["--default-index", uv_default_index])
    return cmd


async def _sync_project_dependencies(
    *,
    uv_path: str,
    install_root: Path,
    uv_default_index: str | None = None,
    sync_timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> str | None:
    """Synchronize backend dependencies in the active project environment."""
    effective_timeout = sync_timeout or _dependency_sync_timeout_seconds()
    uv_cmd = _build_dependency_sync_command(uv_path, uv_default_index=uv_default_index)
    retried_after_managed_python_repair = False
    log.info("updater.dependencies.sync", {"tool": "uv sync", "path": uv_path})

    async def _run_uv_sync(cmd: list[str]) -> tuple[int, str, str]:
        return await _run_async(
            cmd,
            cwd=install_root,
            timeout=effective_timeout,
            env=env,
        )

    def _timeout_message() -> str:
        return f"Dependency sync timed out after {effective_timeout}s while running uv sync."

    try:
        code, _, err = await _run_uv_sync(uv_cmd)
    except subprocess.TimeoutExpired:
        return _timeout_message()

    if (
        code != 0
        and sys.platform == "win32"
        and not retried_after_managed_python_repair
        and _is_uv_managed_python_runtime_error(err)
    ):
        retried_after_managed_python_repair = True
        repaired_dir = await asyncio.to_thread(_repair_windows_uv_managed_python_install, err)
        if repaired_dir is not None:
            log.warning(
                "updater.dependencies.sync_repair_uv_python",
                {"path": str(repaired_dir)},
            )
        else:
            log.warning(
                "updater.dependencies.sync_repair_uv_python_missing_path",
                {"error": err},
            )
        await asyncio.sleep(2)
        try:
            code, _, err = await _run_uv_sync(uv_cmd)
        except subprocess.TimeoutExpired:
            return _timeout_message()

    if code != 0 and uv_default_index:
        log.warning(
            "updater.dependencies.sync_retry_default_index",
            {
                "first_error": err,
                "default_index": uv_default_index,
            },
        )
        await asyncio.sleep(3)
        uv_cmd = _build_dependency_sync_command(uv_path)
        try:
            code, _, err = await _run_uv_sync(uv_cmd)
        except subprocess.TimeoutExpired:
            return _timeout_message()

    if code != 0:
        log.warning("updater.dependencies.sync_retry", {"first_error": err})
        await asyncio.sleep(3)
        try:
            code, _, err = await _run_uv_sync(uv_cmd)
        except subprocess.TimeoutExpired:
            return _timeout_message()

    if code != 0:
        return f"Dependency sync failed: {err}"

    return None


# ------------------------------------------------------------------ #
# Async subprocess helpers
# ------------------------------------------------------------------ #


async def _run_async(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = 60,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess in a thread pool so the async event loop stays free."""
    merged_env = None
    if env:
        merged_env = os.environ.copy()
        merged_env.update(env)
    result = await asyncio.to_thread(
        subprocess.run,
        cmd,
        cwd=cwd or _get_repo_root(),
        capture_output=True,
        text=False,
        timeout=timeout,
        env=merged_env,
    )
    return (
        result.returncode,
        _clean_process_output(result.stdout),
        _clean_process_output(result.stderr),
    )


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    result = subprocess.run(
        cmd,
        cwd=cwd or _get_repo_root(),
        capture_output=True,
        text=False,
    )
    return (
        result.returncode,
        _clean_process_output(result.stdout),
        _clean_process_output(result.stderr),
    )


def _flocks_root() -> Path:
    override = os.getenv("FLOCKS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".flocks"


def _upgrade_run_dir() -> Path:
    return _flocks_root() / "run"


def _upgrade_log_dir() -> Path:
    return _flocks_root() / "logs"


def _upgrade_state_path() -> Path:
    return _upgrade_run_dir() / "upgrade-state.json"


def _upgrade_server_pid_path() -> Path:
    return _upgrade_run_dir() / "upgrade_server.pid"


def _upgrade_page_dir() -> Path:
    return _upgrade_run_dir() / "upgrade-page"


def _upgrade_page_log_path() -> Path:
    return _upgrade_log_dir() / "upgrade-page.log"


def _read_upgrade_state() -> dict[str, Any] | None:
    path = _upgrade_state_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_upgrade_state(payload: dict[str, Any]) -> None:
    path = _upgrade_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")


def _clear_upgrade_state() -> None:
    _upgrade_state_path().unlink(missing_ok=True)


def _persist_upgrade_state(
    payload: dict[str, Any],
    *,
    phase: str | None = None,
    last_error: str | None | object = _STATE_FIELD_UNSET,
) -> dict[str, Any]:
    if phase is not None:
        payload["phase"] = phase
    if last_error is not _STATE_FIELD_UNSET:
        if last_error:
            payload["last_error"] = last_error
        else:
            payload.pop("last_error", None)
    _write_upgrade_state(payload)
    return payload


def _upgrade_page_html(version: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Flocks 升级中</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #fff7ed;
      --panel: rgba(255, 255, 255, 0.92);
      --text: #7c2d12;
      --muted: #9a3412;
      --accent: #f59e0b;
      --border: rgba(251, 191, 36, 0.32);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(251, 191, 36, 0.22), transparent 32%),
        radial-gradient(circle at bottom right, rgba(249, 115, 22, 0.18), transparent 28%),
        var(--bg);
      color: var(--text);
    }}
    .panel {{
      width: min(100%, 520px);
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 28px;
      box-shadow: 0 24px 60px rgba(124, 45, 18, 0.12);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(251, 191, 36, 0.14);
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
    }}
    h1 {{
      margin: 18px 0 12px;
      font-size: 32px;
      line-height: 1.2;
    }}
    p {{
      margin: 0;
      line-height: 1.7;
      color: var(--muted);
      font-size: 15px;
    }}
    .version {{
      margin-top: 18px;
      font-size: 28px;
      font-weight: 800;
    }}
    .status {{
      margin-top: 20px;
      display: flex;
      align-items: center;
      gap: 12px;
      font-size: 14px;
      color: var(--text);
    }}
    .spinner {{
      width: 18px;
      height: 18px;
      border-radius: 999px;
      border: 2px solid rgba(245, 158, 11, 0.24);
      border-top-color: var(--accent);
      animation: spin 0.9s linear infinite;
      flex: 0 0 auto;
    }}
    .tips {{
      margin-top: 22px;
      padding: 14px 16px;
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.82);
      border: 1px solid rgba(251, 191, 36, 0.24);
      font-size: 13px;
      color: var(--muted);
    }}
    @keyframes spin {{
      to {{ transform: rotate(360deg); }}
    }}
  </style>
</head>
<body data-upgrade-marker="{_UPGRADE_PAGE_MARKER}">
  <main class="panel">
    <div class="badge">Flocks 正在升级</div>
    <h1>系统升级中，请稍候</h1>
    <p>升级期间服务会短暂重启。当前页面会在新版本恢复后自动刷新。</p>
    <div class="version">v{version}</div>
    <div class="status">
      <div class="spinner" aria-hidden="true"></div>
      <span>正在切换到新版本并恢复服务...</span>
    </div>
    <div class="tips">如果页面长时间没有恢复，请稍后手动刷新一次。</div>
  </main>
  <script>
    const marker = "{_UPGRADE_PAGE_MARKER}";
    const reloadWhenReady = async () => {{
      try {{
        const rootResp = await fetch("/", {{ cache: "no-store" }});
        const rootText = await rootResp.text();
        if (!rootText.includes(marker)) {{
          window.location.reload();
          return;
        }}
      }} catch (error) {{
      }}
      window.setTimeout(reloadWhenReady, 3000);
    }};
    window.setTimeout(reloadWhenReady, 2500);
  </script>
</body>
</html>
"""


# ------------------------------------------------------------------ #
# Config helper
# ------------------------------------------------------------------ #


async def _get_updater_config():
    """Return UpdaterConfig from flocks.json, or defaults."""
    try:
        from flocks.config.config import Config, UpdaterConfig

        cfg = await Config.get()
        return cfg.updater or UpdaterConfig()
    except Exception:
        from flocks.config.config import UpdaterConfig

        return UpdaterConfig()


def _normalize_update_region(region: str | None, locale: str | None = None) -> str | None:
    """Resolve an explicit region or locale hint into a known mirror profile."""
    raw_region = region
    normalized_region = (region or "").strip().lower().replace("_", "-")
    if raw_region is not None and normalized_region in {"", "auto", "default", "global"}:
        return None
    if normalized_region in {"cn", "china", "zh", "zh-cn"}:
        return _UPDATE_REGION_CN
    if normalized_region:
        return None

    normalized_locale = (locale or "").strip().lower().replace("_", "-")
    if normalized_locale.startswith("zh"):
        return _UPDATE_REGION_CN
    return None


def _prioritize_sources_for_region(sources: list[str], region: str | None) -> list[str]:
    """Reorder sources for a region without changing the configured source set."""
    prioritized = list(sources)
    if region != _UPDATE_REGION_CN:
        return prioritized

    def sort_key(source: str) -> tuple[int, int]:
        if source == "gitee":
            return (0, 0)
        if source == "github":
            return (1, 0)
        return (2, prioritized.index(source))

    return sorted(prioritized, key=sort_key)


def _resolve_update_mirror_profile(
    configured_sources: list[str],
    *,
    region: str | None = None,
    locale: str | None = None,
) -> UpdateMirrorProfile:
    """Return the effective upgrade source order and package mirrors."""
    resolved_region = _normalize_update_region(region, locale)
    sources = _prioritize_sources_for_region(configured_sources, resolved_region)
    if resolved_region == _UPDATE_REGION_CN:
        return UpdateMirrorProfile(
            region=resolved_region,
            sources=sources,
            npm_registry=_CN_NPM_REGISTRY,
            uv_default_index=_CN_UV_DEFAULT_INDEX,
            pip_index_url=_CN_PIP_INDEX_URL,
        )
    return UpdateMirrorProfile(region=None, sources=sources)


async def _resolve_sources_for_edition(configured_sources: list[str]) -> list[str]:
    """
    Resolve effective sources by runtime edition state.

    The generic Flocks update endpoint checks OSS releases. Pro bundle upgrades
    opt into the Console manifest explicitly via ``force_console_manifest``.
    """
    return [source for source in configured_sources if source != "console-manifest"]


def _is_flockspro_license_active() -> bool:
    try:
        if importlib.util.find_spec("flockspro") is None:
            return False
        from flockspro.license.runtime import is_pro_feature_enabled  # type: ignore[import-not-found]

        return bool(is_pro_feature_enabled())
    except Exception:
        return False


# ------------------------------------------------------------------ #
# Release API — GitHub
# ------------------------------------------------------------------ #


def _github_api_url(base_url: str | None, repo: str) -> str:
    base = (base_url or "https://api.github.com").rstrip("/")
    if base == "https://api.github.com":
        return f"{base}/repos/{repo}/releases/latest"
    return f"{base}/api/v3/repos/{repo}/releases/latest"


async def _fetch_github_release(
    repo: str,
    token: str | None,
    base_url: str | None = None,
) -> tuple[str, str | None, str | None, str | None, str | None]:
    """Fetch the latest GitHub release.  Returns (tag, notes, html_url, zipball_url, tarball_url)."""
    url = _github_api_url(base_url, repo)
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()

    tag: str = data.get("tag_name", "").lstrip("v")
    notes: str | None = data.get("body") or None
    html_url: str | None = data.get("html_url") or None
    zipball_url: str | None = data.get("zipball_url") or None
    tarball_url: str | None = data.get("tarball_url") or None
    return tag, notes, html_url, zipball_url, tarball_url


def _github_archive_url(repo: str, tag: str, fmt: str, base_url: str | None = None) -> str:
    """Direct archive URL for GitHub (public repos, no auth needed)."""
    raw_tag = tag if tag.startswith("v") else f"v{tag}"
    base = (base_url or "https://github.com").rstrip("/")
    return f"{base}/{repo}/archive/refs/tags/{raw_tag}.{fmt}"


# ------------------------------------------------------------------ #
# Release API — Gitee
# ------------------------------------------------------------------ #


async def _fetch_gitee_release(
    repo: str,
    token: str | None,
) -> tuple[str, str | None, str | None, str | None, str | None]:
    """Fetch the latest Gitee release.  Returns (tag, notes, html_url, zipball_url, tarball_url)."""
    api_url = f"https://gitee.com/api/v5/repos/{repo}/releases/latest"
    params: dict[str, str] = {}
    if token:
        params["access_token"] = token

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(api_url, params=params, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()

    tag: str = data.get("tag_name", "").lstrip("v")
    raw_tag: str = data.get("tag_name", "")
    notes: str | None = data.get("body") or None
    html_url: str | None = data.get("html_url") or None

    zip_url = _gitee_archive_url(repo, raw_tag, "zip")
    tar_url = zip_url
    return tag, notes, html_url, zip_url, tar_url


def _gitee_archive_url(repo: str, tag: str, fmt: str, gitee_token: str | None = None) -> str:
    """Gitee archive download via the webpage archive endpoint."""
    raw_tag = tag if tag.startswith("v") else f"v{tag}"
    return f"https://gitee.com/{repo}/archive/refs/tags/{raw_tag}.zip"


def _is_gitee_tag_archive_url(url: str) -> bool:
    normalized = url.split("?", 1)[0]
    return normalized.startswith("https://gitee.com/") and "/archive/refs/tags/" in normalized and normalized.endswith(".zip")


def _download_filename_for_url(url: str, filename: str) -> str:
    if not _is_gitee_tag_archive_url(url):
        return filename
    if filename.endswith(".zip"):
        return filename
    if filename.endswith(".tar.gz"):
        return f"{filename[:-7]}.zip"
    return str(Path(filename).with_suffix(".zip"))


def _archive_format_for_url(url: str, manifest_format: str | None = None) -> str:
    normalized = (manifest_format or "").strip().lower().replace("tgz", "tar.gz")
    if normalized in {"zip", "tar.gz"}:
        return normalized

    path = urlparse(url).path.lower()
    if path.endswith(".zip"):
        return "zip"
    if path.endswith(".tar.gz") or path.endswith(".tgz"):
        return "tar.gz"
    return "tar.gz"


def _console_manifest_display_version(data: dict[str, Any]) -> str:
    display_version = str(data.get("display_version") or data.get("version") or data.get("latest_version") or "").strip()
    if display_version:
        return display_version
    core_version = str(data.get("core_version") or "").strip()
    if core_version:
        return core_version
    oss_version = str(data.get("oss_version") or "").strip()
    if oss_version:
        return oss_version
    compare_version = str(data.get("compare_version") or "").strip()
    return f"v{compare_version}" if compare_version and not compare_version.startswith(("v", "V")) else compare_version


def _pro_bundle_core_version(data: dict[str, Any]) -> str:
    core_version = str(data.get("core_version") or "").strip()
    if core_version:
        return core_version
    oss_version = str(data.get("oss_version") or "").strip()
    if oss_version:
        return oss_version
    return _console_manifest_display_version(data)


def _pro_bundle_oss_version(data: dict[str, Any]) -> str:
    return _pro_bundle_core_version(data)


def _version_label(version: str | None) -> str:
    normalized = str(version or "").strip()
    if not normalized:
        return ""
    return normalized if normalized.startswith(("v", "V")) else f"v{normalized}"


def _is_pro_bundle_oss_older_than_local(manifest: dict[str, Any], current_version: str | None = None) -> bool:
    bundle_oss_version = _pro_bundle_oss_version(manifest)
    if not bundle_oss_version:
        return False
    local_version = str(current_version or get_current_version() or "").strip()
    if not local_version:
        return False
    return _parse_version(bundle_oss_version) < _parse_version(local_version)


def _effective_pro_bundle_manifest(manifest: dict[str, Any], effective_oss_version: str) -> dict[str, Any]:
    payload = dict(manifest)
    effective_label = _version_label(effective_oss_version)
    if effective_label:
        payload["core_version"] = effective_label
        payload["oss_version"] = effective_label
    return payload


def _archive_filename_for_format(latest_tag: str, fmt: str) -> str:
    return f"flocks-{latest_tag}.{'zip' if fmt == 'zip' else 'tar.gz'}"


def _sha256_file(path: Path) -> str:
    digest = __import__("hashlib").sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_download_sha256(path: Path, expected_sha256: str | None) -> None:
    expected = (expected_sha256 or "").strip().lower()
    if not expected:
        return
    actual = _sha256_file(path).lower()
    if actual != expected:
        raise ValueError(f"bundle sha256 mismatch: expected {expected}, got {actual}")


async def _raise_download_status_error(resp: httpx.Response) -> None:
    body = (await resp.aread()).decode("utf-8", errors="replace").strip()
    detail = f"HTTP {resp.status_code} {resp.reason_phrase}".strip()
    if body:
        detail = f"{detail}: {body[:500]}"
    raise RuntimeError(detail)


# ------------------------------------------------------------------ #
# Release API — GitLab
# ------------------------------------------------------------------ #


async def _fetch_gitlab_release(
    repo: str,
    token: str | None,
    base_url: str | None = None,
) -> tuple[str, str | None, str | None, str | None, str | None]:
    """Fetch latest GitLab release; return (tag, notes, url, zipball, tarball)."""
    base = (base_url or "https://gitlab.com").rstrip("/")
    encoded = quote(repo, safe="")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["PRIVATE-TOKEN"] = token

    async with httpx.AsyncClient(timeout=15) as client:
        releases_url = f"{base}/api/v4/projects/{encoded}/releases"
        resp = await client.get(releases_url, headers=headers, follow_redirects=True)

        if resp.status_code == 200:
            releases = resp.json()
            if releases:
                latest = releases[0]
                tag: str = latest.get("tag_name", "").lstrip("v")
                raw_tag: str = latest.get("tag_name", "")
                notes: str | None = latest.get("description") or None
                link: str | None = latest.get("_links", {}).get("self") or f"{base}/{repo}/-/releases/{raw_tag}"
                proj = repo.split("/")[-1]
                zip_url = f"{base}/{repo}/-/archive/{raw_tag}/{proj}-{raw_tag}.zip"
                tar_url = f"{base}/{repo}/-/archive/{raw_tag}/{proj}-{raw_tag}.tar.gz"
                return tag, notes, link, zip_url, tar_url

        tags_url = f"{base}/api/v4/projects/{encoded}/repository/tags?order_by=version&sort=desc&per_page=1"
        tags_resp = await client.get(tags_url, headers=headers, follow_redirects=True)

        if tags_resp.status_code == 200:
            tags = tags_resp.json()
            if tags:
                latest_tag_obj = tags[0]
                tag = latest_tag_obj.get("name", "").lstrip("v")
                raw_tag = latest_tag_obj.get("name", "")
                notes = latest_tag_obj.get("release", {}).get("description") or latest_tag_obj.get("message") or None
                link = f"{base}/{repo}/-/tags/{raw_tag}"
                proj = repo.split("/")[-1]
                zip_url = f"{base}/{repo}/-/archive/{raw_tag}/{proj}-{raw_tag}.zip"
                tar_url = f"{base}/{repo}/-/archive/{raw_tag}/{proj}-{raw_tag}.tar.gz"
                return tag, notes, link, zip_url, tar_url

    raise ValueError(
        f"Failed to fetch version info (releases={resp.status_code}, "
        f"tags={tags_resp.status_code}). "
        "Verify the repo path or configure a token in flocks.json."
    )


async def _load_console_session_token() -> str | None:
    def _token_from_payload(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        token = str(payload.get("console_session_token") or "").strip()
        if not token:
            return None
        expires_at_raw = str(payload.get("expires_at") or "").strip()
        if expires_at_raw:
            try:
                expires_at = datetime.fromisoformat(expires_at_raw)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= datetime.now(timezone.utc):
                    return None
            except ValueError:
                return None
        return token

    shared_session_path = _flocks_root() / "run" / "console-session.json"
    try:
        shared_session = json.loads(shared_session_path.read_text(encoding="utf-8"))
    except Exception:
        shared_session = None

    token = _token_from_payload(shared_session)
    if token:
        return token

    try:
        from flocks.storage.storage import Storage

        session = await Storage.get("console:session")
    except Exception:
        session = None

    token = _token_from_payload(session)
    if token:
        return token
    return None


async def _fetch_console_manifest_release_info(console_session_token: str | None = None) -> ConsoleManifestRelease:
    """
    Fetch latest Pro bundle manifest from console.
    """
    manifest_base = os.getenv("FLOCKS_CONSOLE_BASE_URL", "").rstrip("/")
    if not manifest_base:
        raise ValueError("FLOCKS_CONSOLE_BASE_URL 未配置，无法使用 console-manifest 源")

    channel = (os.getenv("FLOCKS_UPDATE_CHANNEL") or "flockspro").strip() or "flockspro"
    url = f"{manifest_base}/v1/manifest/latest?channel={channel}"
    headers: dict[str, str] = {}
    token = str(console_session_token or "").strip() or await _load_console_session_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("manifest 响应格式无效")

    if bool(data.get("frozen")):
        raise ValueError("console manifest channel is frozen")
    frozen_until_raw = str(data.get("frozen_until") or "").strip()
    if frozen_until_raw:
        text = frozen_until_raw[:-1] + "+00:00" if frozen_until_raw.endswith("Z") else frozen_until_raw
        frozen_until = datetime.fromisoformat(text)
        if frozen_until.tzinfo is None:
            frozen_until = frozen_until.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < frozen_until:
            raise ValueError("console manifest channel frozen_until not reached")

    latest = _console_manifest_display_version(data)
    if not latest:
        raise ValueError("manifest 响应缺少 compare_version/display_version")
    bundle_url = str(
        data.get("bundle_url")
        or data.get("url")
        or data.get("zipball_url")
        or data.get("tarball_url")
        or ""
    ).strip()
    if not bundle_url:
        raise ValueError("manifest 响应缺少 bundle_url")
    bundle_format = _archive_format_for_url(bundle_url, str(data.get("bundle_format") or data.get("archive_format") or ""))
    release_id = str(data.get("release_id") or data.get("bundle_release_id") or "").strip() or None
    bundle_release_id = str(data.get("bundle_release_id") or data.get("release_id") or "").strip() or None
    return ConsoleManifestRelease(
        version=latest,
        release_notes=data.get("release_notes") or data.get("notes"),
        release_url=data.get("release_url") or bundle_url,
        bundle_url=bundle_url,
        bundle_sha256=str(data.get("bundle_sha256") or "").strip() or None,
        bundle_format=bundle_format,
        manifest=data,
        console_session_token=token,
        release_id=release_id,
        bundle_release_id=bundle_release_id,
    )


async def _fetch_console_manifest_release() -> tuple[str, str | None, str | None, str | None, str | None]:
    info = await _fetch_console_manifest_release_info()
    if info.bundle_format == "zip":
        return info.version, info.release_notes, info.release_url, info.bundle_url, None
    return info.version, info.release_notes, info.release_url, None, info.bundle_url


# ------------------------------------------------------------------ #
# Multi-source dispatcher
# ------------------------------------------------------------------ #


async def _fetch_release_from_source(
    source: str,
    repo: str,
    token: str | None,
    gitee_token: str | None,
    base_url: str | None = None,
    gitee_repo: str | None = None,
) -> tuple[str, str | None, str | None, str | None, str | None]:
    """Fetch release info from a single source.  Raises on failure."""
    if source == "github":
        return await _fetch_github_release(repo, token, base_url)
    if source == "gitee":
        return await _fetch_gitee_release(gitee_repo or repo, gitee_token)
    if source == "gitlab":
        return await _fetch_gitlab_release(repo, token, base_url)
    if source == "console-manifest":
        return await _fetch_console_manifest_release()
    raise ValueError(f"Unknown source: {source}")


def _archive_url_for_source(
    source: str,
    repo: str,
    tag: str,
    fmt: str,
    base_url: str | None = None,
    gitee_repo: str | None = None,
    gitee_token: str | None = None,
) -> str:
    """Build a direct archive download URL for the given source."""
    if source == "github":
        return _github_archive_url(repo, tag, fmt, base_url)
    if source == "gitee":
        return _gitee_archive_url(gitee_repo or repo, tag, fmt, gitee_token)
    if source == "gitlab":
        raw_tag = tag if tag.startswith("v") else f"v{tag}"
        base = (base_url or "https://gitlab.com").rstrip("/")
        proj = repo.split("/")[-1]
        return f"{base}/{repo}/-/archive/{raw_tag}/{proj}-{raw_tag}.{'zip' if fmt == 'zip' else 'tar.gz'}"
    if source == "console-manifest":
        manifest_base = os.getenv("FLOCKS_CONSOLE_BASE_URL", "").rstrip("/")
        if not manifest_base:
            raise ValueError("FLOCKS_CONSOLE_BASE_URL 未配置")
        raw_tag = tag if tag.startswith("v") else f"v{tag}"
        ext = "zip" if fmt == "zip" else "tar.gz"
        return f"{manifest_base}/v1/manifest/archive/{raw_tag}.{ext}"
    raise ValueError(f"Unknown source: {source}")


def _token_for_source(source: str, token: str | None, gitee_token: str | None) -> str | None:
    if source == "gitee":
        return gitee_token
    return token


# ------------------------------------------------------------------ #
# Version helpers
# ------------------------------------------------------------------ #


def _parse_version(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    normalized = v.strip().lower().removeprefix("refs/tags/").removeprefix("pro-").removeprefix("v")
    normalized = normalized.replace("-", ".").replace("_", ".")
    for seg in normalized.split("."):
        m = re.match(r"(\d+)", seg)
        if not m:
            m = re.search(r"(\d+)", seg)
        parts.append(int(m.group(1)) if m else 0)
    return tuple(parts) if parts else (0,)


def _pick_best_tag(names: list[str]) -> str:
    """Return the tag name with the highest version, stripped of leading 'v'."""
    candidates: list[tuple[tuple[int, ...], str]] = []
    for name in names:
        name = name.strip()
        if name:
            candidates.append((_parse_version(name), name))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1].removeprefix("v")


async def _latest_tag_from_git_remote_async(
    remote: str | None = None,
) -> tuple[str, None, None]:
    r = remote or "origin"
    code, out, _ = await _run_async(
        ["git", "ls-remote", "--tags", "--refs", r],
        timeout=15,
    )
    if code == 0:
        names = [line.split("\t")[1].removeprefix("refs/tags/") for line in out.splitlines() if "\t" in line]
        tag = _pick_best_tag(names)
        if tag:
            return tag, None, None

    code2, out2, _ = await _run_async(["git", "tag"])
    if code2 == 0:
        tag = _pick_best_tag(out2.splitlines())
        if tag:
            return tag, None, None

    return "", None, None


# ------------------------------------------------------------------ #
# Archive helpers — download / backup / extract
# ------------------------------------------------------------------ #


def _choose_archive_format(configured: str) -> str:
    """Return 'zip' or 'tar.gz' based on config and platform."""
    if configured != "auto":
        return configured
    return "zip" if sys.platform == "win32" else "tar.gz"


async def _download_archive(
    url: str,
    token: str | None,
    dest_dir: Path,
    filename: str,
) -> Path:
    """Stream-download an archive from *url* into *dest_dir/filename*."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / _download_filename_for_url(url, filename)

    headers: dict[str, str] = {}
    if _is_gitee_tag_archive_url(url):
        headers["User-Agent"] = _CURL_USER_AGENT
    elif token and "gitee.com" not in url:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=300), follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                await _raise_download_status_error(resp)
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)

    return dest


async def _download_console_bundle(
    url: str,
    token: str | None,
    dest_dir: Path,
    filename: str,
    progress_callback: Callable[[UpdateProgress], Awaitable[None]] | None = None,
) -> Path:
    """Download a Pro bundle, sending the console session token only to Console origin."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / _download_filename_for_url(url, filename)
    headers = (
        {
            "Authorization": f"Bearer {token}",
            "x-console-session-token": token,
        }
        if token and _is_console_origin_url(url)
        else {}
    )
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=300), follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                await _raise_download_status_error(resp)
            total_bytes = None
            content_length = getattr(resp, "headers", {}).get("content-length")
            if content_length:
                try:
                    parsed_content_length = int(content_length)
                    total_bytes = parsed_content_length if parsed_content_length > 0 else None
                except ValueError:
                    total_bytes = None
            downloaded_bytes = 0
            last_progress_emit = 0.0
            last_percent: int | None = None
            with dest.open("wb") as handle:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    handle.write(chunk)
                    downloaded_bytes += len(chunk)
                    if not progress_callback:
                        continue
                    percent = (
                        min(100, int(downloaded_bytes * 100 / total_bytes))
                        if total_bytes
                        else None
                    )
                    now = time.monotonic()
                    should_emit = (
                        downloaded_bytes == total_bytes
                        or now - last_progress_emit >= 0.25
                    )
                    if percent is not None and percent == last_percent and downloaded_bytes != total_bytes:
                        should_emit = False
                    if should_emit:
                        last_progress_emit = now
                        last_percent = percent
                        await progress_callback(
                            UpdateProgress(
                                stage="fetching",
                                message="Downloading Flocks Pro bundle...",
                                bundle_filename=dest.name,
                                downloaded_bytes=downloaded_bytes,
                                total_bytes=total_bytes,
                                percent=percent,
                            )
                        )
    return dest


async def _download_with_fallback(
    sources: list[str],
    repo: str,
    tag: str,
    fmt: str,
    token: str | None,
    gitee_token: str | None,
    primary_zipball: str | None,
    primary_tarball: str | None,
    dest_dir: Path,
    filename: str,
    base_url: str | None = None,
    gitee_repo: str | None = None,
) -> Path:
    """
    Try downloading the archive from multiple sources in priority order.
    First tries API-provided URLs, then constructs direct archive URLs
    for each source as fallback.  Collects all errors for reporting.
    """
    primary_url = primary_zipball if fmt == "zip" else primary_tarball
    attempts: list[tuple[str, str, str | None]] = []

    if primary_url:
        is_gitee = "gitee.com" in primary_url
        label = "gitee-api" if is_gitee else "github-api"
        tk = gitee_token if is_gitee else token
        attempts.append((label, primary_url, tk))

    for source in sources:
        url = _archive_url_for_source(source, repo, tag, fmt, base_url, gitee_repo, gitee_token)
        tk = _token_for_source(source, token, gitee_token)
        if not any(u == url for _, u, _ in attempts):
            attempts.append((source, url, tk))

    errors: list[str] = []
    for source_name, url, tk in attempts:
        try:
            log.info("updater.download.trying", {"source": source_name, "url": url})
            return await _download_archive(url, tk, dest_dir, filename)
        except Exception as exc:
            err_msg = f"[{source_name}] {exc}"
            errors.append(err_msg)
            log.warning(
                "updater.download.source_failed",
                {
                    "source": source_name,
                    "url": url,
                    "error": str(exc),
                },
            )

    summary = "; ".join(errors) if errors else "No download sources configured"
    raise RuntimeError(summary)


def _backup_current_version(
    install_root: Path,
    current_version: str,
    retain_count: int = 1,
) -> Path | None:
    """
    Compress the current install directory into ~/.flocks/version/ .
    Returns the backup path on success, None on failure.
    Preserved runtime/user directories are excluded.
    """
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup_name = f"flocks-{current_version}-{ts}"
    backup_path = _BACKUP_DIR / f"{backup_name}.tar.gz"

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = info.name.split("/")
        for index, part in enumerate(parts):
            if part in _PRESERVE_NAMES:
                return None
            if part == "dist":
                return None
        return info

    try:
        with tarfile.open(backup_path, "w:gz") as tar:
            tar.add(str(install_root), arcname="flocks", filter=_filter)
    except Exception as exc:
        log.warning("updater.backup.failed", {"error": str(exc)})
        return None

    _cleanup_old_backups(retain_count)
    return backup_path


def _cleanup_old_backups(retain_count: int) -> None:
    """Remove the oldest backups beyond *retain_count*."""
    if not _BACKUP_DIR.is_dir():
        return
    effective_retain_count = max(1, retain_count)
    backups = sorted(
        [p for p in _BACKUP_DIR.iterdir() if p.name.startswith("flocks-") and p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[effective_retain_count:]:
        try:
            old.unlink()
            log.info("updater.backup.purged", {"file": old.name})
        except OSError:
            pass


def _detect_archive_root(extracted_dir: Path) -> Path:
    """
    GitHub/Gitee archives contain a single top-level directory.
    Return that directory, or *extracted_dir* itself if no single root found.
    """
    children = [c for c in extracted_dir.iterdir() if not c.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extracted_dir


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_bundle_member_path(bundle_root: Path, relative_path: str) -> Path | None:
    raw_path = Path(relative_path)
    if not relative_path or raw_path.is_absolute() or ".." in raw_path.parts:
        raise ValueError("Pro bundle manifest contains an unsafe wheel path")
    return bundle_root / raw_path


def _resolve_pro_bundle_content(content_root: Path) -> tuple[Path, Path | None, dict[str, Any]]:
    """
    Return the OSS source root and optional flockspro wheel when an archive is a
    Pro bundle. Plain OSS archives are returned unchanged.
    """
    manifest_path = content_root / "manifest.json"
    flocks_dir = content_root / "flocks"
    if not manifest_path.is_file() or not flocks_dir.is_dir():
        return content_root, None, {}

    manifest = _load_json_file(manifest_path)
    wheel_value = str(manifest.get("flockspro_wheel") or "").strip()
    wheel_path = _resolve_bundle_member_path(content_root, wheel_value) if wheel_value else None
    if wheel_path is None or not wheel_path.is_file():
        wheels = sorted((content_root / "wheels").glob("*.whl"))
        wheel_path = wheels[0] if wheels else None
    return flocks_dir, wheel_path, manifest


def _resolve_pro_bundle_wheel(content_root: Path) -> tuple[Path, dict[str, Any]]:
    manifest_path = content_root / "manifest.json"
    manifest = _load_json_file(manifest_path) if manifest_path.is_file() else {}
    wheel_value = str(manifest.get("flockspro_wheel") or "").strip()
    wheel_path = _resolve_bundle_member_path(content_root, wheel_value) if wheel_value else None
    if wheel_path is None or not wheel_path.is_file():
        wheels = sorted((content_root / "wheels").glob("*.whl"))
        wheel_path = wheels[0] if wheels else None
    if wheel_path is None or not wheel_path.is_file():
        raise ValueError("Pro bundle 中未找到 flockspro wheel")
    return wheel_path, manifest


def _is_pro_component_installed() -> bool:
    return importlib.util.find_spec("flockspro") is not None


def _venv_python_path(install_root: Path) -> Path:
    if sys.platform == "win32":
        return install_root / ".venv" / "Scripts" / "python.exe"
    return install_root / ".venv" / "bin" / "python"


async def _snapshot_pro_component(install_root: Path) -> _ProComponentSnapshot:
    python_path = _venv_python_path(install_root)
    if not python_path.exists():
        return _ProComponentSnapshot(installed=False)
    code, out, _ = await _run_async(
        [
            str(python_path),
            "-c",
            "import importlib.metadata as m\n"
            "import sys\n"
            "try:\n"
            " print(m.version('flockspro'))\n"
            "except m.PackageNotFoundError:\n"
            " sys.exit(2)\n",
        ],
        cwd=install_root,
        timeout=30,
    )
    if code == 0 and out.strip():
        return _ProComponentSnapshot(installed=True, version=out.strip())
    return _ProComponentSnapshot(installed=False)


async def _restore_pro_component_snapshot(
    snapshot: _ProComponentSnapshot,
    *,
    uv_path: str,
    install_root: Path,
    env: dict[str, str] | None,
) -> str | None:
    python_path = _venv_python_path(install_root)
    if not python_path.exists():
        return f"Python environment may need manual repair: missing {python_path}"
    if snapshot.installed and snapshot.version:
        cmd = [uv_path, "pip", "install", "--python", str(python_path), "--no-deps", f"flockspro=={snapshot.version}"]
    else:
        cmd = [uv_path, "pip", "uninstall", "--python", str(python_path), "-y", "flockspro"]
    code, _, err = await _run_async(cmd, cwd=install_root, timeout=180, env=env)
    if code != 0:
        return f"Python environment may need manual repair: {err}"
    return None


async def run_handoff_upgrade_tasks(
    *,
    install_root: Path,
    uv_path: str,
    version: str,
    uv_default_index: str | None = None,
    npm_registry: str | None = None,
    pro_wheel_path: Path | None = None,
    pro_bundle_manifest_path: Path | None = None,
    bundle_sha256: str | None = None,
    sync_timeout: int | None = None,
) -> str | None:
    """Run upgrade work that must happen after the old service exits."""
    sync_env = _build_uv_sync_env()
    sync_error = await _sync_project_dependencies(
        uv_path=uv_path,
        install_root=install_root,
        uv_default_index=uv_default_index,
        sync_timeout=sync_timeout,
        env=sync_env,
    )
    if sync_error is not None:
        return sync_error

    if pro_wheel_path is not None:
        python_path = _venv_python_path(install_root)
        install_cmd = [uv_path, "pip", "install", "--python", str(python_path), "--no-deps", str(pro_wheel_path)]
        code, _, err = await _run_async(
            install_cmd,
            cwd=install_root,
            timeout=180,
            env=sync_env,
        )
        if code != 0:
            return f"Flocks Pro component install failed: {err}"

    validation_error = await _validate_restart_runtime(install_root)
    if validation_error:
        return validation_error

    install_webui_dir = install_root / "webui"
    if install_webui_dir.is_dir() and (install_webui_dir / "package.json").exists():
        frontend_error = await _build_frontend_workspace(
            install_webui_dir,
            npm_registry=npm_registry,
        )
        if frontend_error is not None:
            return frontend_error

    _write_version_marker(version.lstrip("v"))
    if pro_bundle_manifest_path is not None and pro_bundle_manifest_path.is_file():
        _write_pro_bundle_install_marker(
            _load_json_file(pro_bundle_manifest_path),
            bundle_sha256=bundle_sha256,
        )

    try:
        _refresh_global_cli_entry(install_root)
    except Exception as exc:
        log.warning("updater.refresh_cli.failed", {"error": str(exc)})

    return None


def _merge_console_manifest_release_identity(
    bundle_manifest: dict[str, Any],
    console_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    if not console_manifest:
        return bundle_manifest
    merged = dict(bundle_manifest)
    release_id = str(console_manifest.get("release_id") or console_manifest.get("bundle_release_id") or "").strip()
    bundle_release_id = str(console_manifest.get("bundle_release_id") or console_manifest.get("release_id") or "").strip()
    if release_id and not merged.get("release_id"):
        merged["release_id"] = release_id
    if bundle_release_id and not merged.get("bundle_release_id"):
        merged["bundle_release_id"] = bundle_release_id
    for key in ("display_version", "version", "latest_version", "compare_version", "flockspro_component_version", "build_id"):
        if console_manifest.get(key):
            merged[key] = console_manifest.get(key)
    core_version = console_manifest.get("core_version") or console_manifest.get("oss_version") or merged.get("core_version") or merged.get("oss_version")
    if core_version:
        merged["core_version"] = core_version
        merged["oss_version"] = core_version
    return merged


def _write_pro_bundle_install_marker(manifest: dict[str, Any], *, bundle_sha256: str | None = None) -> None:
    marker = _flocks_root() / "run" / "pro-bundle-installed.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    release_id = manifest.get("release_id") or manifest.get("bundle_release_id")
    bundle_release_id = manifest.get("bundle_release_id") or manifest.get("release_id")
    display_version = _console_manifest_display_version(manifest)
    core_version = manifest.get("core_version") or manifest.get("oss_version")
    payload = {
        "release_id": release_id,
        "bundle_release_id": bundle_release_id,
        "bundle_version": display_version,
        "display_version": display_version,
        "installed_version": display_version,
        "core_version": core_version,
        "oss_version": core_version,
        "flockspro_component_version": manifest.get("flockspro_component_version"),
        "build_id": manifest.get("build_id"),
        "bundle_sha256": bundle_sha256 or manifest.get("bundle_sha256"),
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    marker.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")


class _NullConsole:
    def print(self, *args, **kwargs) -> None:
        return None


def _current_service_config():
    from flocks.cli import service_manager
    from flocks.cli.service_config import service_config_from_status_payload
    from flocks.cli.service_control import read_supervisor_status

    try:
        status = read_supervisor_status(paths=service_manager.runtime_paths(), timeout=1.0)
    except Exception as exc:
        raise RuntimeError("Supervisor control API is unavailable; cannot perform managed upgrade restart.") from exc
    return service_config_from_status_payload(
        status.raw,
        no_browser=True,
        skip_frontend_build=True,
    )


def _spawn_detached_process(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
) -> subprocess.Popen:
    creationflags = 0
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
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
            command,
            cwd=cwd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            **kwargs,
        )
    finally:
        handle.close()


def _write_upgrade_page(version: str) -> Path:
    page_dir = _upgrade_page_dir()
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "index.html").write_text(_upgrade_page_html(version), encoding="utf-8")
    return page_dir


def _upgrade_page_probe_urls(frontend_host: str, frontend_port: int) -> list[str]:
    from flocks.cli import service_manager

    if frontend_host == "::":
        return [
            f"http://[::1]:{frontend_port}",
            f"http://127.0.0.1:{frontend_port}",
        ]
    return [f"http://{service_manager.access_host(frontend_host)}:{frontend_port}"]


def _wait_for_upgrade_page(config) -> None:
    page_urls = _upgrade_page_probe_urls(config.frontend_host, config.frontend_port)
    with httpx.Client(timeout=1.5) as client:
        for _ in range(40):
            for page_url in page_urls:
                try:
                    response = client.get(page_url)
                    if response.status_code < 500:
                        return
                except Exception:
                    pass
            time.sleep(0.25)
    raise RuntimeError("Upgrade page server failed to start in time")


def _start_upgrade_page_server(config, version: str) -> dict[str, Any]:
    from flocks.cli import service_manager

    page_dir = _write_upgrade_page(version)
    page_dir_resolved = page_dir.resolve()
    process = _spawn_detached_process(
        service_manager.resolve_python_subprocess_command(_get_repo_root())
        + [
            "-m",
            "http.server",
            str(config.frontend_port),
            "--bind",
            config.frontend_host,
            "--directory",
            str(page_dir_resolved),
        ],
        cwd=page_dir_resolved,
        log_path=_upgrade_page_log_path(),
    )
    _upgrade_server_pid_path().write_text(str(process.pid), encoding="utf-8")
    _wait_for_upgrade_page(config)
    return {
        "page_dir": str(page_dir_resolved),
        "page_log": str(_upgrade_page_log_path().resolve()),
        "upgrade_server_pid": process.pid,
    }


def _stop_upgrade_page_server(*, frontend_port: int | None = None) -> None:
    pid_path = _upgrade_server_pid_path()
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = None
            pid_path.unlink(missing_ok=True)

        if pid is not None:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
                else:
                    os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

            for _ in range(40):
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.1)
            else:
                if sys.platform != "win32":
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                    time.sleep(0.1)

            pid_path.unlink(missing_ok=True)

    if frontend_port is None:
        return

    from flocks.cli import service_manager

    remaining = service_manager.port_owner_pids(frontend_port)
    if remaining:
        log.info(
            "updater.upgrade_page.port_fallback_kill",
            {
                "port": frontend_port,
                "pids": remaining,
            },
        )
        for rpid in remaining:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(rpid), "/T", "/F"], check=False, capture_output=True)
                else:
                    os.kill(rpid, signal.SIGKILL)
            except OSError:
                pass

    if sys.platform == "win32":
        wait_attempts = 40
        wait_interval = 0.25
        for _ in range(wait_attempts):
            if not service_manager.port_owner_pids(frontend_port):
                return
            time.sleep(wait_interval)
        return

    if remaining:
        time.sleep(0.3)


def _prepare_upgrade_handover(version: str) -> dict[str, Any]:
    from flocks.cli import service_manager
    from flocks.cli.service_control import request_prepare_upgrade

    config = _current_service_config()
    payload: dict[str, Any] = {
        "version": version,
        "backend_host": config.backend_host,
        "backend_port": config.backend_port,
        "frontend_host": config.frontend_host,
        "frontend_port": config.frontend_port,
        "skip_frontend_build": True,
        "phase": _UPGRADE_PHASE_HANDOVER_PREPARING,
    }
    _persist_upgrade_state(payload, last_error=None)

    console = _NullConsole()
    paths = service_manager.runtime_paths()
    request_prepare_upgrade(paths=paths, timeout=30.0)

    try:
        payload.update(_start_upgrade_page_server(config, version))
        _persist_upgrade_state(
            payload,
            phase=_UPGRADE_PHASE_TEMP_PAGE_ACTIVE,
            last_error=None,
        )
    except Exception:
        _stop_upgrade_page_server(frontend_port=config.frontend_port)
        _clear_upgrade_state()
        try:
            _start_frontend_with_fallback(config, console, allow_build_fallback=False)
        except Exception as restart_error:
            log.error("updater.frontend.restore_failed", {"error": str(restart_error)})
        raise

    return payload


def _spawn_restart_handoff(command: list[str], *, cwd: Path) -> subprocess.Popen:
    creationflags = 0
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
        if startupinfo_cls is not None:
            startupinfo = startupinfo_cls()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
            startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
            kwargs["startupinfo"] = startupinfo
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, cwd=cwd, close_fds=True, creationflags=creationflags, **kwargs)


def _service_config_from_payload(
    payload: dict[str, Any],
    *,
    skip_frontend_build: bool | None = None,
):
    from flocks.cli.service_config import service_config_from_payload

    resolved_skip_frontend_build = (
        bool(payload.get("skip_frontend_build", True)) if skip_frontend_build is None else skip_frontend_build
    )
    return service_config_from_payload(
        payload,
        no_browser=True,
        skip_frontend_build=resolved_skip_frontend_build,
    )


def _handoff_service_config():
    payload = _read_upgrade_state()
    if payload is not None:
        return _service_config_from_payload(payload, skip_frontend_build=True)
    return _current_service_config()


def _read_upgrade_server_pid() -> tuple[int | None, bool]:
    pid_path = _upgrade_server_pid_path()
    if not pid_path.exists():
        return None, False
    try:
        return int(pid_path.read_text(encoding="utf-8").strip()), True
    except (OSError, ValueError):
        pid_path.unlink(missing_ok=True)
        return None, True


def _payload_frontend_port(payload: dict[str, Any] | None) -> int | None:
    if not payload:
        return None
    value = payload.get("frontend_port")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def read_upgrade_runtime_state(frontend_port: int | None = None) -> dict[str, Any]:
    payload = _read_upgrade_state()
    upgrade_pid, pid_file_present = _read_upgrade_server_pid()
    resolved_port = _payload_frontend_port(payload) or frontend_port
    frontend_host = str(payload.get("frontend_host")) if payload and payload.get("frontend_host") else None

    listener_pids: list[int] = []
    if resolved_port is not None:
        try:
            from flocks.cli import service_manager

            listener_pids = service_manager.port_owner_pids(resolved_port)
        except Exception:
            listener_pids = []

    listener_matches_pid = upgrade_pid is not None and upgrade_pid in listener_pids
    return {
        "payload": payload,
        "payload_present": payload is not None,
        "pid_file_present": pid_file_present,
        "upgrade_pid": upgrade_pid,
        "frontend_host": frontend_host,
        "frontend_port": resolved_port,
        "listener_pids": listener_pids,
        "listener_matches_pid": listener_matches_pid,
        "page_active": listener_matches_pid,
        "has_artifacts": payload is not None or pid_file_present,
    }


def _start_frontend_with_fallback(config, console, *, allow_build_fallback: bool) -> None:
    from flocks.cli.service_config import with_frontend_build
    from flocks.cli.service_control import request_restart_webui, request_resume_upgrade

    try:
        status = request_resume_upgrade(
            config,
            paths=None,
            timeout=180.0,
        )
        if status.webui.state != "healthy":
            raise RuntimeError(status.webui.last_error or "WebUI restart did not become healthy")
        return
    except Exception:
        if not allow_build_fallback or not config.skip_frontend_build:
            raise

    rebuilt_config = with_frontend_build(config, skip_frontend_build=False)
    result = request_restart_webui(rebuilt_config, force_frontend_build=True, paths=None, timeout=180.0)
    if result.webui.state != "healthy":
        raise RuntimeError(result.webui.last_error or "WebUI restart did not become healthy")


def cleanup_orphan_upgrade_state(*, frontend_port: int | None = None) -> bool:
    state = read_upgrade_runtime_state(frontend_port=frontend_port)
    if not state["has_artifacts"]:
        return False

    resolved_port = state["frontend_port"]
    if resolved_port is None:
        _stop_upgrade_page_server()
    else:
        _stop_upgrade_page_server(frontend_port=resolved_port)

    _clear_upgrade_state()
    _upgrade_server_pid_path().unlink(missing_ok=True)
    shutil.rmtree(_upgrade_page_dir(), ignore_errors=True)
    return True


def resolve_upgrade_runtime_state(
    *,
    attempt_recover: bool = True,
    frontend_port: int | None = None,
) -> dict[str, Any]:
    state = read_upgrade_runtime_state(frontend_port=frontend_port)
    if not state["has_artifacts"]:
        return {
            **state,
            "action": "noop",
            "error": None,
        }

    resolved_port = state["frontend_port"]
    if attempt_recover and state["payload_present"]:
        try:
            recover_upgrade_state()
            return {
                **state,
                "action": "recovered",
                "error": None,
            }
        except Exception as exc:
            cleanup_orphan_upgrade_state(frontend_port=resolved_port)
            return {
                **state,
                "action": "cleanup_after_failed_recover",
                "error": str(exc),
            }

    cleanup_orphan_upgrade_state(frontend_port=resolved_port)
    return {
        **state,
        "action": "cleaned",
        "error": None,
    }


def _restore_backup_if_possible(
    backup_path: Path,
    install_root: Path,
    previous_version: str,
) -> None:
    """Restore install tree from backup without touching upgrade-page state."""
    try:
        _restore_backup_archive(backup_path, install_root)
        _write_version_marker(previous_version)
        log.info("updater.backup.restored", {"backup": str(backup_path)})
    except Exception as exc:
        log.error(
            "updater.backup.restore_failed",
            {
                "backup": str(backup_path),
                "error": str(exc),
            },
        )


def _restore_backup_archive(backup_path: Path, install_root: Path) -> None:
    restore_dir = Path(tempfile.mkdtemp(prefix="flocks-rollback-"))
    try:
        content_root = _extract_archive(backup_path, restore_dir)
        _replace_install_dir(content_root, install_root)
    finally:
        shutil.rmtree(restore_dir, ignore_errors=True)


def _rollback_failed_update(
    backup_path: Path | None,
    install_root: Path,
    previous_version: str,
) -> None:
    payload = _read_upgrade_state()
    if not payload:
        return

    _persist_upgrade_state(
        payload,
        phase=_UPGRADE_PHASE_ROLLBACK_IN_PROGRESS,
        last_error=None,
    )
    restored_backup = False
    restore_error: str | None = None
    if backup_path is not None:
        try:
            _restore_backup_archive(backup_path, install_root)
            _write_version_marker(previous_version.lstrip("v"))
            restored_backup = True
        except Exception as exc:
            restore_error = f"Failed to restore backup: {exc}"
            log.error("updater.backup.restore_failed", {"error": str(exc), "backup": str(backup_path)})

    console = _NullConsole()
    config = _service_config_from_payload(payload, skip_frontend_build=True)
    _stop_upgrade_page_server(frontend_port=config.frontend_port)
    try:
        _start_frontend_with_fallback(
            config,
            console,
            allow_build_fallback=restored_backup,
        )
    except Exception as exc:
        log.error(
            "updater.frontend.rollback_failed",
            {"error": str(exc), "restored_backup": restored_backup, "restore_error": restore_error},
        )
    finally:
        _clear_upgrade_state()
        shutil.rmtree(_upgrade_page_dir(), ignore_errors=True)


def recover_upgrade_state() -> None:
    payload = _read_upgrade_state()
    if not payload:
        return

    console = _NullConsole()
    config = _service_config_from_payload(payload)

    _stop_upgrade_page_server(frontend_port=config.frontend_port)
    try:
        _start_frontend_with_fallback(config, console, allow_build_fallback=True)
    except Exception as exc:
        log.error("updater.frontend.resume_failed", {"error": str(exc)})
        raise
    finally:
        _clear_upgrade_state()
        shutil.rmtree(_upgrade_page_dir(), ignore_errors=True)


def rollback_upgrade_handover() -> None:
    payload = _read_upgrade_state()
    if not payload:
        return

    console = _NullConsole()
    config = _service_config_from_payload(payload, skip_frontend_build=True)

    _stop_upgrade_page_server(frontend_port=config.frontend_port)
    try:
        _start_frontend_with_fallback(config, console, allow_build_fallback=False)
    except Exception as exc:
        log.error("updater.frontend.rollback_failed", {"error": str(exc)})
    finally:
        _clear_upgrade_state()
        shutil.rmtree(_upgrade_page_dir(), ignore_errors=True)


def cleanup_replaced_files(root: Path | None = None) -> None:
    install_root = root or _get_repo_root()
    leftovers = sorted(
        (
            path
            for path in install_root.rglob("*")
            if ".flocks_old_" in path.name
        ),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in leftovers:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=False)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            log.warning("updater.cleanup.leftover_failed", {"path": str(path)})


def _extract_archive(archive_path: Path, dest_dir: Path) -> Path:
    """
    Extract a zip or tar.gz archive into *dest_dir* and return the
    detected content root (handles the extra wrapper directory).
    """
    if archive_path.name.endswith(".tar.gz") or archive_path.suffixes[-2:] == [".tar", ".gz"]:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(dest_dir, filter="data")
    elif archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.infolist():
                member_path = Path(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError(f"Unsafe archive path: {member.filename}")
            zf.extractall(dest_dir)
    else:
        raise ValueError(f"Unsupported archive format: {archive_path.name}")

    return _detect_archive_root(dest_dir)


def _rmtree_onerror(func, path, exc_info):  # noqa: ANN001
    """Handle rmtree errors on Windows (read-only / locked files)."""
    import stat
    import time

    for attempt in range(5):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            time.sleep(0.1 * (2**attempt))
            func(path)
            return
        except OSError:
            continue
    log.warning("updater.rmtree.skip_locked", {"path": str(path)})


def _safe_rmtree(target: Path) -> None:
    """rmtree with Windows permission-error fallback."""
    if sys.platform == "win32":
        shutil.rmtree(target, onerror=_rmtree_onerror)
    else:
        shutil.rmtree(target)


def _renamed_lock_path(target: Path) -> Path:
    base = target.with_name(f"{target.name}.flocks_old_{os.getpid()}")
    candidate = base
    counter = 1
    while candidate.exists():
        candidate = target.with_name(f"{target.name}.flocks_old_{os.getpid()}_{counter}")
        counter += 1
    return candidate


def _safe_remove(target: Path) -> None:
    try:
        if target.is_dir() and not target.is_symlink():
            _safe_rmtree(target)
        else:
            target.unlink()
    except OSError:
        if sys.platform != "win32":
            raise
        if not target.exists():
            return
        renamed = _renamed_lock_path(target)
        target.rename(renamed)
        log.info("updater.rename_locked", {"from": str(target), "to": str(renamed)})


def _replace_install_dir(
    source_dir: Path,
    install_root: Path,
) -> None:
    """
    Overwrite *install_root* with the contents of *source_dir*, while
    preserving user/runtime directories listed in ``_PRESERVE_NAMES``
    at **any** directory depth (not only the top level).
    """
    install_root.mkdir(parents=True, exist_ok=True)
    source_names = {item.name for item in source_dir.iterdir()}

    for item in source_dir.iterdir():
        if item.name in _PRESERVE_NAMES:
            continue
        target = install_root / item.name
        if item.is_dir() and not item.is_symlink():
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    _replace_install_dir(item, target)
                    continue
                _safe_remove(target)
            shutil.copytree(item, target, symlinks=True)
        else:
            if target.exists() or target.is_symlink():
                _safe_remove(target)
            shutil.copy2(item, target)

    for child in install_root.iterdir():
        if child.name not in source_names and child.name not in _PRESERVE_NAMES:
            _safe_remove(child)


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #

_VERSION_MARKER_PATH = _BACKUP_DIR / ".current_version"


def _read_version_marker() -> str:
    """Read the persisted installed version marker."""
    try:
        if _VERSION_MARKER_PATH.is_file():
            return _VERSION_MARKER_PATH.read_text(encoding="utf-8").strip().lstrip("v")
    except Exception:
        pass
    return ""


def _read_pyproject_version() -> str:
    """Read the source version from the repository pyproject.toml."""
    try:
        pyproject_path = _get_repo_root() / "pyproject.toml"
        if not pyproject_path.is_file():
            return ""
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        version = str(payload.get("project", {}).get("version", "")).strip()
        return version.lstrip("v")
    except Exception:
        pass
    return ""


def _read_pro_bundle_install_marker() -> dict[str, Any]:
    marker = _flocks_root() / "run" / "pro-bundle-installed.json"
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_pro_bundle_installed_bundle_version() -> str:
    payload = _read_pro_bundle_install_marker()
    for key in ("bundle_version", "installed_version", "display_version"):
        version = str(payload.get(key) or "").strip()
        if version:
            return version
    return ""


def _read_pro_bundle_installed_core_version() -> str:
    payload = _read_pro_bundle_install_marker()
    for key in ("core_version", "oss_version"):
        version = str(payload.get(key) or "").strip()
        if version:
            return version
    return ""


def _read_pro_bundle_installed_component_version() -> str:
    payload = _read_pro_bundle_install_marker()
    return str(payload.get("flockspro_component_version") or "").strip()


def _current_pro_version_state(local_core_version: str) -> ProVersionState:
    core_version = _pick_newer_version(_read_pro_bundle_installed_core_version(), local_core_version)
    return ProVersionState(
        bundle_version=_version_label(_read_pro_bundle_installed_bundle_version()),
        core_version=_version_label(core_version),
        pro_component_version=_read_pro_bundle_installed_component_version() or None,
    )


def _latest_pro_version_state(manifest_info: ConsoleManifestRelease) -> ProVersionState:
    return ProVersionState(
        bundle_version=_version_label(manifest_info.version),
        core_version=_version_label(_pro_bundle_core_version(manifest_info.manifest)),
        pro_component_version=str(manifest_info.manifest.get("flockspro_component_version") or "").strip() or None,
    )


def _is_newer_version(latest: str | None, current: str | None) -> bool:
    latest_version = str(latest or "").strip()
    if not latest_version:
        return False
    current_version = str(current or "").strip()
    if not current_version:
        return True
    return _parse_version(latest_version) > _parse_version(current_version)


def _pick_newer_version(left: str | None, right: str | None) -> str:
    left_version = str(left or "").strip()
    right_version = str(right or "").strip()
    if not left_version:
        return right_version
    if not right_version:
        return left_version
    return left_version if _parse_version(left_version) >= _parse_version(right_version) else right_version


def get_current_version() -> str:
    """
    Return the running version.
    Compare the persisted marker at ~/.flocks/version/.current_version
    with the source version in pyproject.toml and return the higher one.
    """
    return _pick_best_tag([
        _read_version_marker(),
        _read_pyproject_version(),
    ])


def _write_version_marker(version: str) -> None:
    """Persist the installed version so get_current_version() picks it up after restart."""
    _VERSION_MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    _VERSION_MARKER_PATH.write_text(f"{version}\n", encoding="utf-8")


async def get_latest_release(
    provider: str | None = None,
    base_url: str | None = None,
    repo: str | None = None,
    token: str | None = None,
    sources_override: list[str] | None = None,
) -> tuple[str, str | None, str | None, str | None, str | None]:
    """
    Query Releases API using the configured sources list in priority order.
    Returns (tag, notes, html_url, zipball_url, tarball_url).
    Falls back to git ls-remote if all HTTP sources fail.
    """
    ucfg = await _get_updater_config()
    repo = repo or ucfg.repo
    token = token or ucfg.token
    base_url = base_url or ucfg.base_url
    sources = list(ucfg.sources if sources_override is None else sources_override)

    if provider:
        sources = [provider]

    last_error: Exception | None = None
    for source in sources:
        try:
            result = await _fetch_release_from_source(
                source,
                repo,
                token,
                ucfg.gitee_token,
                base_url,
                gitee_repo=ucfg.gitee_repo,
            )
            log.info("updater.release.fetched", {"source": source, "tag": result[0]})
            return result
        except Exception as exc:
            last_error = exc
            log.warning(
                "updater.release.source_failed",
                {
                    "source": source,
                    "error": str(exc),
                },
            )

    log.warning("updater.api_failed_fallback_git", {"error": str(last_error)})
    tag, _, _ = await _latest_tag_from_git_remote_async(ucfg.remote)
    if tag:
        return tag, None, None, None, None
    if last_error:
        raise last_error
    raise RuntimeError("No sources configured and git fallback failed")


async def check_update(
    *,
    locale: str | None = None,
    region: str | None = None,
    force_console_manifest: bool = False,
) -> VersionInfo:
    """Return version comparison info without performing any upgrade."""
    from flocks.updater.deploy import detect_deploy_mode

    mode = detect_deploy_mode()
    ucfg = await _get_updater_config()
    effective_sources = await _resolve_sources_for_edition(ucfg.sources)
    if force_console_manifest:
        effective_sources = ["console-manifest"]
    profile = _resolve_update_mirror_profile(
        effective_sources,
        region=region,
        locale=locale,
    )
    is_console_manifest = profile.sources == ["console-manifest"]
    local_core_version = get_current_version()
    current_pro_state = _current_pro_version_state(local_core_version) if is_console_manifest else None
    current = current_pro_state.bundle_version if current_pro_state else local_core_version
    if not current:
        current = local_core_version

    if not ucfg.enabled:
        return VersionInfo(
            current_version=current,
            current_core_version=current_pro_state.core_version if current_pro_state else None,
            current_bundle_version=current_pro_state.bundle_version if current_pro_state else None,
            current_pro_component_version=current_pro_state.pro_component_version if current_pro_state else None,
            deploy_mode=mode,
            update_allowed=(mode != "docker"),
        )

    bundle_sha256: str | None = None
    bundle_format: str | None = None
    latest_pro_state: ProVersionState | None = None
    try:
        if is_console_manifest:
            manifest_info = await _fetch_console_manifest_release_info()
            latest_pro_state = _latest_pro_version_state(manifest_info)
            tag = latest_pro_state.bundle_version
            notes = manifest_info.release_notes
            url = manifest_info.release_url
            bundle_sha256 = manifest_info.bundle_sha256
            bundle_format = manifest_info.bundle_format
            zipball = manifest_info.bundle_url if manifest_info.bundle_format == "zip" else None
            tarball = manifest_info.bundle_url if manifest_info.bundle_format != "zip" else None
        else:
            tag, notes, url, zipball, tarball = await get_latest_release(
                repo=ucfg.repo,
                token=ucfg.token,
                sources_override=profile.sources,
            )
    except Exception as exc:
        log.warning("updater.check_failed", {"error": str(exc)})
        return VersionInfo(
            current_version=current,
            current_core_version=current_pro_state.core_version if current_pro_state else None,
            current_bundle_version=current_pro_state.bundle_version if current_pro_state else None,
            current_pro_component_version=current_pro_state.pro_component_version if current_pro_state else None,
            error="Failed to check for updates. Please check your network connection.",
            deploy_mode=mode,
            update_allowed=(mode != "docker"),
        )

    bundle_has_update = _is_newer_version(tag, current)
    core_has_update = (
        _is_newer_version(latest_pro_state.core_version, current_pro_state.core_version)
        if latest_pro_state and current_pro_state
        else False
    )
    pro_component_has_update = (
        _is_newer_version(latest_pro_state.pro_component_version, current_pro_state.pro_component_version)
        if latest_pro_state and current_pro_state
        else False
    )
    has_update = bundle_has_update or core_has_update or pro_component_has_update
    log.info(
        "updater.check.result",
        {
            "current": current,
            "latest": tag,
            "has_update": has_update,
            "bundle_has_update": bundle_has_update,
            "core_has_update": core_has_update,
            "pro_component_has_update": pro_component_has_update,
            "sources": profile.sources,
            "region": profile.region,
        },
    )
    return VersionInfo(
        current_version=current,
        latest_version=tag,
        current_core_version=current_pro_state.core_version if current_pro_state else None,
        latest_core_version=latest_pro_state.core_version if latest_pro_state else None,
        current_bundle_version=current_pro_state.bundle_version if current_pro_state else None,
        latest_bundle_version=latest_pro_state.bundle_version if latest_pro_state else None,
        current_pro_component_version=current_pro_state.pro_component_version if current_pro_state else None,
        latest_pro_component_version=latest_pro_state.pro_component_version if latest_pro_state else None,
        edition="flockspro" if is_console_manifest else "flocks",
        has_update=has_update,
        release_notes=notes,
        release_url=url,
        zipball_url=zipball,
        tarball_url=tarball,
        bundle_sha256=bundle_sha256,
        bundle_format=bundle_format if bundle_format in {"zip", "tar.gz"} else None,
        deploy_mode=mode,
        update_allowed=(mode != "docker"),
    )


# ------------------------------------------------------------------ #
# Perform upgrade
# ------------------------------------------------------------------ #


def _absolute_console_url(url: str) -> str:
    manifest_base = os.getenv("FLOCKS_CONSOLE_BASE_URL", "").rstrip("/")
    if manifest_base and not manifest_base.startswith(("http://", "https://")):
        manifest_base = f"https://{manifest_base}"
    if url.startswith(("http://", "https://")):
        parsed_url = urlparse(url)
        parsed_base = urlparse(manifest_base) if manifest_base else None
        if parsed_base and (parsed_url.hostname or "").lower() == (parsed_base.hostname or "").lower():
            url_port = parsed_url.port or _default_port(parsed_url.scheme)
            base_port = parsed_base.port or _default_port(parsed_base.scheme)
            if url_port in {None, 80, 443} and base_port in {None, 80, 443}:
                return parsed_url._replace(scheme=parsed_base.scheme, netloc=parsed_base.netloc).geturl()
        return url
    if url.startswith("file://"):
        return url
    if not manifest_base:
        return url
    if url.startswith("/"):
        return f"{manifest_base}{url}"
    return f"{manifest_base}/{url}"


def _is_console_origin_url(url: str) -> bool:
    manifest_base = os.getenv("FLOCKS_CONSOLE_BASE_URL", "").strip().rstrip("/")
    if not manifest_base:
        return False
    if not manifest_base.startswith(("http://", "https://")):
        manifest_base = f"https://{manifest_base}"
    parsed_url = urlparse(url)
    parsed_base = urlparse(manifest_base)
    return (
        parsed_url.scheme.lower() == parsed_base.scheme.lower()
        and (parsed_url.hostname or "").lower() == (parsed_base.hostname or "").lower()
        and (parsed_url.port or _default_port(parsed_url.scheme)) == (parsed_base.port or _default_port(parsed_base.scheme))
    )


def _default_port(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def _ensure_pro_heartbeat_env_default() -> None:
    """Default Pro heartbeat endpoint to console when env is unset."""
    existing = os.getenv("FLOCKSPRO_HEARTBEAT_URL", "").strip()
    if existing:
        return
    console_base = os.getenv("FLOCKS_CONSOLE_BASE_URL", "").strip().rstrip("/")
    if not console_base:
        return
    if not console_base.startswith(("http://", "https://")):
        console_base = f"https://{console_base}"
    heartbeat_url = f"{console_base}/v1/heartbeats"
    os.environ["FLOCKSPRO_HEARTBEAT_URL"] = heartbeat_url
    log.info("updater.pro_bundle.heartbeat_url.defaulted", {"heartbeat_url": heartbeat_url})


async def perform_pro_bundle_install(
    *,
    restart: bool = True,
    console_session_token: str | None = None,
) -> AsyncGenerator[UpdateProgress, None]:
    """Apply the Console Pro bundle as a combined OSS core + Pro component upgrade."""
    try:
        if console_session_token:
            manifest_info = await _fetch_console_manifest_release_info(console_session_token)
        else:
            manifest_info = await _fetch_console_manifest_release_info()
    except Exception as exc:
        log.error("updater.pro_bundle.manifest_failed", {"error": str(exc)})
        yield UpdateProgress(
            stage="error",
            message="Failed to fetch the Flocks Pro bundle manifest.",
            success=False,
        )
        return

    zipball_url = manifest_info.bundle_url if manifest_info.bundle_format == "zip" else None
    tarball_url = manifest_info.bundle_url if manifest_info.bundle_format != "zip" else None
    async for progress in perform_update(
        manifest_info.version,
        zipball_url=zipball_url,
        tarball_url=tarball_url,
        bundle_sha256=manifest_info.bundle_sha256,
        bundle_format=manifest_info.bundle_format,
        console_session_token=manifest_info.console_session_token,
        console_manifest_payload=manifest_info.manifest,
        restart=restart,
        force_console_manifest=True,
    ):
        yield progress


async def perform_update(
    latest_tag: str,
    *,
    zipball_url: str | None = None,
    tarball_url: str | None = None,
    bundle_sha256: str | None = None,
    bundle_format: str | None = None,
    console_session_token: str | None = None,
    console_manifest_payload: dict[str, Any] | None = None,
    restart: bool = True,
    locale: str | None = None,
    region: str | None = None,
    force_console_manifest: bool = False,
) -> AsyncGenerator[UpdateProgress, None]:
    """
    Async generator that executes the upgrade steps and yields progress events.

    If *zipball_url* / *tarball_url* are provided (e.g. from a prior
    ``check_update`` call), the redundant Releases API round-trip is skipped.

    Sources are tried in the order configured in ``updater.sources``.
    If one source fails the download, the next source is tried automatically.
    """
    ucfg = await _get_updater_config()
    effective_sources = await _resolve_sources_for_edition(ucfg.sources)
    if force_console_manifest:
        effective_sources = ["console-manifest"]
    profile = _resolve_update_mirror_profile(
        effective_sources,
        region=region,
        locale=locale,
    )
    install_root = _get_repo_root()
    current_version = get_current_version()
    effective_update_version = current_version
    skip_core_replace = False
    handover_active = False
    console_manifest_info: ConsoleManifestRelease | None = None
    console_manifest_payload = console_manifest_payload if isinstance(console_manifest_payload, dict) else None
    fmt = _choose_archive_format(ucfg.archive_format)
    if profile.sources == ["console-manifest"]:
        if not (zipball_url or tarball_url) or console_session_token is None:
            try:
                console_manifest_info = await _fetch_console_manifest_release_info()
            except Exception as exc:
                log.error("updater.console_manifest.fetch_failed", {"error": str(exc)})
                yield UpdateProgress(
                    stage="error",
                    message="Failed to check the Pro bundle manifest. Please check your network connection.",
                    success=False,
                )
                return
            if _parse_version(console_manifest_info.version) != _parse_version(latest_tag):
                yield UpdateProgress(
                    stage="error",
                    message="Requested Pro bundle version does not match the latest approved manifest.",
                    success=False,
                )
                return
            if console_manifest_info.bundle_format == "zip":
                zipball_url = console_manifest_info.bundle_url
            else:
                tarball_url = console_manifest_info.bundle_url
            bundle_sha256 = console_manifest_info.bundle_sha256
            bundle_format = console_manifest_info.bundle_format
            console_session_token = console_manifest_info.console_session_token
            console_manifest_payload = console_manifest_info.manifest
        primary_bundle_url = zipball_url or tarball_url or ""
        fmt = _archive_format_for_url(primary_bundle_url, bundle_format)

    # ------------------------------------------------------------------ #
    # Step 1 – download source archive
    # ------------------------------------------------------------------ #
    sources_desc = " → ".join(profile.sources)
    archive_filename = _archive_filename_for_format(latest_tag, fmt)
    if profile.sources == ["console-manifest"]:
        archive_filename = _download_filename_for_url(
            _absolute_console_url(zipball_url or tarball_url or ""),
            f"flockspro-bundle-{Path(latest_tag).name}.{'zip' if fmt == 'zip' else 'tar.gz'}",
        )
    yield UpdateProgress(
        stage="fetching",
        message=f"Downloading {'Flocks Pro bundle' if profile.sources == ['console-manifest'] else 'source archive'} (sources: {sources_desc})...",
        bundle_filename=archive_filename if profile.sources == ["console-manifest"] else None,
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="flocks-update-"))
    if sys.platform == "win32":
        tmp_dir = _resolve_windows_long_path(tmp_dir)
    try:
        if profile.sources == ["console-manifest"]:
            primary_bundle_url = _absolute_console_url(zipball_url or tarball_url or "")
            if not primary_bundle_url:
                raise ValueError("Console manifest did not provide a bundle URL")
            progress_queue: asyncio.Queue[UpdateProgress] = asyncio.Queue()

            async def _queue_download_progress(progress: UpdateProgress) -> None:
                await progress_queue.put(progress)

            download_task = asyncio.create_task(
                _download_console_bundle(
                    primary_bundle_url,
                    console_session_token or await _load_console_session_token(),
                    tmp_dir,
                    archive_filename,
                    progress_callback=_queue_download_progress,
                )
            )
            while not download_task.done():
                try:
                    yield await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
            while not progress_queue.empty():
                yield progress_queue.get_nowait()
            archive_path = await download_task
            await asyncio.to_thread(_verify_download_sha256, archive_path, bundle_sha256)
        else:
            archive_path = await _download_with_fallback(
                sources=profile.sources,
                repo=ucfg.repo,
                tag=latest_tag,
                fmt=fmt,
                token=ucfg.token,
                gitee_token=ucfg.gitee_token,
                primary_zipball=zipball_url,
                primary_tarball=tarball_url,
                dest_dir=tmp_dir,
                filename=archive_filename,
                base_url=ucfg.base_url,
                gitee_repo=ucfg.gitee_repo,
            )
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.error("updater.download.all_failed", {"error": str(exc)})
        _dl_msg = f"Failed to download the update: {exc}"
        _record_update_journal(f"ERROR {_dl_msg} ({exc})")
        yield UpdateProgress(
            stage="error",
            message=_dl_msg,
            success=False,
        )
        return

    # ------------------------------------------------------------------ #
    # Step 2 – backup current version
    # ------------------------------------------------------------------ #
    yield UpdateProgress(stage="backing_up", message="Backing up current version...")

    backup_path = await asyncio.to_thread(
        _backup_current_version,
        install_root,
        current_version,
        ucfg.backup_retain_count,
    )
    if backup_path:
        yield UpdateProgress(
            stage="backing_up",
            message=f"Backup complete: {backup_path.name}",
        )
    else:
        yield UpdateProgress(
            stage="backing_up",
            message="Backup skipped (non-fatal, continuing upgrade)",
        )

    extract_dir = tmp_dir / "extracted"
    extract_dir.mkdir()
    try:
        content_root = await asyncio.to_thread(
            _extract_archive,
            archive_path,
            extract_dir,
        )
        content_root, pro_wheel_path, pro_bundle_manifest = _resolve_pro_bundle_content(content_root)
        if profile.sources == ["console-manifest"]:
            pro_bundle_manifest = _merge_console_manifest_release_identity(
                pro_bundle_manifest,
                console_manifest_payload,
            )
        if profile.sources == ["console-manifest"] and pro_wheel_path is None:
            raise ValueError("Pro bundle 中未找到 flockspro wheel")
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        msg = f"Failed to extract files: {exc}"
        if backup_path:
            msg += f"\nRestore from backup: {backup_path}"
        _record_update_journal(f"ERROR {msg}")
        yield UpdateProgress(stage="error", message=msg, success=False)
        return
    if profile.sources == ["console-manifest"]:
        skip_core_replace = _is_pro_bundle_oss_older_than_local(pro_bundle_manifest, current_version)
        if skip_core_replace:
            bundle_oss_version = _pro_bundle_oss_version(pro_bundle_manifest)
            pro_bundle_manifest = _effective_pro_bundle_manifest(pro_bundle_manifest, current_version)
            log.info(
                "updater.pro_bundle.keep_local_core",
                {"local_version": current_version, "bundle_oss_version": bundle_oss_version},
            )
        else:
            effective_update_version = latest_tag
    else:
        effective_update_version = latest_tag

    # ------------------------------------------------------------------ #
    # Step 3 – determine whether frontend handover is needed
    # ------------------------------------------------------------------ #
    staged_webui_dir = content_root / "webui"
    needs_handover = (
        not skip_core_replace
        and staged_webui_dir.is_dir()
        and (staged_webui_dir / "package.json").exists()
    )

    # ------------------------------------------------------------------ #
    # Step 4 – replace install tree
    # (Frontend proxy is still alive so the SSE stream keeps flowing.)
    # ------------------------------------------------------------------ #
    yield UpdateProgress(
        stage="applying",
        message=(
            f"Keeping local Flocks {_version_label(current_version)} and installing the Pro component..."
            if skip_core_replace
            else f"Applying v{latest_tag}..."
        ),
    )

    async def _restore_after_apply_failure() -> None:
        nonlocal handover_active
        if backup_path is None:
            if handover_active:
                await asyncio.to_thread(rollback_upgrade_handover)
                handover_active = False
            return
        if handover_active:
            await asyncio.to_thread(
                _rollback_failed_update,
                backup_path,
                install_root,
                current_version,
            )
            handover_active = False
            return
        await asyncio.to_thread(
            _restore_backup_if_possible,
            backup_path,
            install_root,
            current_version,
        )

    try:
        if not skip_core_replace:
            await asyncio.to_thread(
                _replace_install_dir,
                content_root,
                install_root,
            )
    except Exception as exc:
        final_replace_error: Exception | None = exc
        if (
            sys.platform == "win32"
            and restart
            and needs_handover
            and not handover_active
            and _is_windows_file_in_use_error(exc)
        ):
            log.warning("updater.replace.locked_retry_with_handover", {"error": str(exc)})
            try:
                _prepare_upgrade_handover(latest_tag)
                handover_active = True
                if not skip_core_replace:
                    await asyncio.to_thread(
                        _replace_install_dir,
                        content_root,
                        install_root,
                    )
            except Exception as retry_exc:
                final_replace_error = retry_exc
            else:
                final_replace_error = None

        if final_replace_error is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            await _restore_after_apply_failure()
            msg = f"Failed to replace files: {final_replace_error}"
            if backup_path:
                msg += f"\nRestore from backup: {backup_path}"
            yield UpdateProgress(stage="error", message=msg, success=False)
            return

    # ------------------------------------------------------------------ #
    # Step 5 – prepare dependency sync
    # ------------------------------------------------------------------ #
    yield UpdateProgress(stage="syncing", message="Preparing dependency sync...")

    uv_path = _find_executable("uv")
    if not uv_path:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        await _restore_after_apply_failure()
        hint = (
            "Dependency sync failed: uv is required but was not found. "
            "Please install uv (https://docs.astral.sh/uv/) and ensure it "
            "is in PATH (e.g. add ~/.local/bin to PATH for systemd services)."
        )
        if sys.platform == "win32":
            hint = "Dependency sync failed: uv is required to refresh the Windows project runtime."
        _record_update_journal(f"ERROR {hint}")
        yield UpdateProgress(stage="error", message=hint, success=False)
        return

    sync_timeout = _dependency_sync_timeout_seconds()
    pro_bundle_manifest_path: Path | None = None
    if pro_bundle_manifest:
        pro_bundle_manifest_path = tmp_dir / "pro-bundle-marker.json"
        pro_bundle_manifest_path.write_text(
            json.dumps(pro_bundle_manifest, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ #
    # Step 6 – run post-apply tasks or restart via handoff
    #
    # With restart=True, dependency sync, Pro install, frontend build, marker
    # writes, and CLI refresh happen in restart_handoff after the old backend
    # exits. This avoids mutating the active Python environment while the old
    # service is still running.
    # ------------------------------------------------------------------ #
    if not restart:
        task_error = await run_handoff_upgrade_tasks(
            install_root=install_root,
            uv_path=uv_path,
            version=effective_update_version,
            uv_default_index=profile.uv_default_index,
            npm_registry=profile.npm_registry,
            pro_wheel_path=pro_wheel_path,
            pro_bundle_manifest_path=pro_bundle_manifest_path,
            bundle_sha256=bundle_sha256,
            sync_timeout=sync_timeout,
        )
        if task_error is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            await _restore_after_apply_failure()
            _record_update_journal(f"ERROR {task_error}")
            yield UpdateProgress(stage="error", message=task_error, success=False)
            return

        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.info("updater.apply.done", {"version": effective_update_version, "restart": False, "region": profile.region})
        yield UpdateProgress(
            stage="done",
            message=f"Upgraded to {_version_label(effective_update_version)}",
            success=True,
        )
        return

    yield UpdateProgress(stage="restarting", message="Restarting service...")

    log.info(
        "updater.restart",
        {
            "tag": latest_tag,
            "effective_version": effective_update_version,
            "sources": profile.sources,
            "repo": ucfg.repo,
            "region": profile.region,
        },
    )
    await asyncio.sleep(0.8)

    if "--reload" in sys.argv:
        log.info("updater.restart.reload_exit3")
        sys.exit(3)

    try:
        restart_argv = _build_restart_argv(install_root)
    except Exception as exc:
        log.error("updater.restart.build_argv_failed", {"error": str(exc)})
        if handover_active:
            try:
                rollback_upgrade_handover()
            except Exception:
                pass
            handover_active = False
        yield UpdateProgress(
            stage="error",
            message=f"Failed to build restart command: {exc}",
            success=False,
        )
        return

    if needs_handover and not handover_active:
        try:
            _prepare_upgrade_handover(latest_tag)
            handover_active = True
        except Exception as exc:
            log.error("updater.handover.failed", {"error": str(exc)})
            await _restore_after_apply_failure()
            yield UpdateProgress(
                stage="error",
                message=f"Failed to prepare WebUI handover: {exc}",
                success=False,
            )
            return

    try:
        handoff_argv = _build_restart_handoff_argv(
            restart_argv,
            install_root,
            uv_path=uv_path,
            sync_timeout=sync_timeout,
            version=effective_update_version,
            current_version=current_version,
            backup_path=backup_path,
            uv_default_index=profile.uv_default_index,
            npm_registry=profile.npm_registry,
            pro_wheel_path=pro_wheel_path,
            pro_bundle_manifest_path=pro_bundle_manifest_path,
            bundle_sha256=bundle_sha256,
            cleanup_dir=tmp_dir,
        )
        log.info(
            "updater.restart.handoff_spawn",
            {
                "argv": handoff_argv,
                "restart_argv": restart_argv,
            },
        )
        _spawn_restart_handoff(handoff_argv, cwd=install_root)
        os._exit(0)
    except Exception as exc:
        log.error("updater.restart.handoff_spawn_failed", {"error": str(exc)})
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if handover_active:
            try:
                rollback_upgrade_handover()
            except Exception:
                pass
            handover_active = False
        yield UpdateProgress(
            stage="error",
            message=f"Failed to restart service: {exc}",
            success=False,
        )
        return


def _refresh_global_cli_entry(install_root: Path) -> None:
    """Ensure the global ``flocks`` command points to the project ``.venv``.

    Handles migration from the legacy uv-tool-based global command to a
    symlink (Unix) or .cmd wrapper (Windows).  Safe to call repeatedly.
    """
    link_dir = Path.home() / ".local" / "bin"
    link_dir.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        venv_python = install_root / ".venv" / "Scripts" / "python.exe"
        if not venv_python.exists():
            return
        wrapper = link_dir / "flocks.cmd"
        content = f'@echo off\r\n"{venv_python}" -m flocks.cli.main %*'
        try:
            wrapper.write_text(content, encoding="oem")
        except LookupError:
            wrapper.write_text(content, encoding="utf-8")
        stale_exe = link_dir / "flocks.exe"
        if stale_exe.exists():
            try:
                stale_exe.unlink(missing_ok=True)
            except OSError:
                try:
                    stale_exe.rename(stale_exe.with_suffix(".exe.bak"))
                except OSError:
                    pass
    else:
        target = install_root / ".venv" / "bin" / "flocks"
        if not target.exists():
            return
        link = link_dir / "flocks"
        link.unlink(missing_ok=True)
        link.symlink_to(target)

    if _running_from_legacy_uv_tool_install():
        log.info(
            "updater.refresh_cli.defer_legacy_uninstall",
            {"sys_executable": sys.executable},
        )
        return

    uv = shutil.which("uv")
    if uv:
        try:
            result = subprocess.run(
                [uv, "tool", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and re.search(r"^flocks ", result.stdout, re.MULTILINE):
                subprocess.run(
                    [uv, "tool", "uninstall", "flocks"],
                    capture_output=True,
                    timeout=30,
                )
        except Exception:
            pass


def _build_restart_argv(install_root: Path | None = None) -> list[str]:
    """Reconstruct the argv used by restart handoff to restart correctly.

    Always uses the project ``.venv`` Python to ensure the restarted process
    runs in the same environment that ``uv sync`` just updated.
    """
    rest = sys.argv[1:]

    clean_rest: list[str] = []
    skip_next = False
    for arg in rest:
        if skip_next:
            skip_next = False
            continue
        if arg in ("--reload",):
            continue
        if arg.startswith("--reload-"):
            if "=" not in arg:
                skip_next = True
            continue
        clean_rest.append(arg)

    repo_root = install_root or _get_repo_root()
    if sys.platform == "win32":
        venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = repo_root / ".venv" / "bin" / "python"

    if not venv_python.exists():
        raise FileNotFoundError(f"Restart runtime is missing: {venv_python}")

    log.info("updater.restart.force_venv", {"python": str(venv_python)})
    return [str(venv_python), "-m", "flocks.cli.main"] + clean_rest


def _build_restart_handoff_argv(
    restart_argv: list[str],
    install_root: Path,
    *,
    uv_path: str,
    sync_timeout: int,
    version: str,
    current_version: str,
    backup_path: Path | None = None,
    uv_default_index: str | None = None,
    npm_registry: str | None = None,
    pro_wheel_path: Path | None = None,
    pro_bundle_manifest_path: Path | None = None,
    bundle_sha256: str | None = None,
    cleanup_dir: Path | None = None,
) -> list[str]:
    """Wrap the real restart command in a helper that finishes upgrade work."""
    if not restart_argv:
        raise ValueError("restart command is empty")

    config = _handoff_service_config()
    managed_restart_argv = [
        restart_argv[0],
        "-m",
        "flocks.cli.main",
        "start",
        "--no-browser",
        "--skip-webui-build",
        "--server-host",
        str(config.backend_host),
        "--server-port",
        str(config.backend_port),
        "--webui-host",
        str(config.frontend_host),
        "--webui-port",
        str(config.frontend_port),
    ]
    argv = [
        restart_argv[0],
        "-m",
        "flocks.updater.restart_handoff",
        "--parent-pid",
        str(os.getpid()),
        "--backend-host",
        str(config.backend_host),
        "--backend-port",
        str(config.backend_port),
        "--frontend-host",
        str(config.frontend_host),
        "--frontend-port",
        str(config.frontend_port),
        "--install-root",
        str(install_root),
        "--uv-path",
        uv_path,
        "--sync-timeout",
        str(sync_timeout),
        "--version",
        version,
        "--current-version",
        current_version,
    ]
    if backup_path is not None:
        argv.extend(["--backup-path", str(backup_path)])
    if uv_default_index:
        argv.extend(["--uv-default-index", uv_default_index])
    if npm_registry:
        argv.extend(["--npm-registry", npm_registry])
    if pro_wheel_path is not None:
        argv.extend(["--pro-wheel-path", str(pro_wheel_path)])
    if pro_bundle_manifest_path is not None:
        argv.extend(["--pro-bundle-manifest-path", str(pro_bundle_manifest_path)])
    if bundle_sha256:
        argv.extend(["--bundle-sha256", bundle_sha256])
    if cleanup_dir is not None:
        argv.extend(["--cleanup-dir", str(cleanup_dir)])
    argv.extend(["--", *managed_restart_argv])
    return argv


def _resolve_windows_restart_command(argv0: str, orig_argv: list[str]) -> list[str] | None:
    """Recover the Windows launcher plus script or ``-m`` invocation from ``orig_argv``."""
    if not orig_argv:
        return None

    launcher = _resolve_windows_launcher_entry(orig_argv[0])
    if not launcher:
        return None

    tail = orig_argv[1:]
    if not tail:
        return launcher

    if not _looks_like_windows_python_launcher(orig_argv[0]):
        return launcher

    for index, value in enumerate(tail):
        if _windows_paths_match(value, argv0):
            return launcher + tail[: index + 1]

    for index, value in enumerate(tail[:-1]):
        if value == "-m":
            return launcher + tail[: index + 2]

    return None


def _resolve_windows_launcher_entry(entry: str) -> list[str] | None:
    """Resolve a single Windows launcher executable or cmd shim."""
    if not entry:
        return None

    candidates = _windows_command_candidates(entry)
    entry_name = _windows_path_name(entry)
    if entry_name and entry_name != entry:
        candidates.extend(_windows_command_candidates(entry_name))

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)

        resolved = shutil.which(candidate)
        if not resolved and Path(candidate).exists():
            resolved = candidate
        if not resolved:
            continue

        suffix = _windows_path_suffix(resolved)
        if suffix == ".exe":
            return [resolved]
        if suffix in {".cmd", ".bat"}:
            comspec = os.environ.get("COMSPEC") or "cmd.exe"
            return [comspec, "/c", resolved]

    return None


def _find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found and not found.startswith("/mnt/"):
        return found
    repo_root = _get_repo_root()
    venv_candidates = [repo_root / ".venv" / "bin" / name]
    if sys.platform == "win32":
        venv_candidates.extend(
            repo_root / ".venv" / "Scripts" / candidate for candidate in _windows_command_candidates(name)
        )
    else:
        venv_candidates.append(repo_root / ".venv" / "Scripts" / name)
    for candidate in venv_candidates:
        if candidate.exists():
            return str(candidate)

    # When running inside a uv-tool-managed environment (e.g. systemd service
    # where PATH is minimal), shutil.which may miss uv.  Probe well-known
    # user-level and system-level locations so the updater can still call
    # ``uv sync`` instead of falling back to pip (which doesn't exist in uv
    # tool venvs).
    if name == "uv" and sys.platform != "win32":
        extra_paths: list[Path] = []
        home = Path.home()
        extra_paths.append(home / ".local" / "bin" / "uv")
        extra_paths.append(home / ".cargo" / "bin" / "uv")
        # If the running interpreter lives inside a uv tools tree, the uv
        # binary that created it is usually in ~/.local/bin.  Also try the
        # bin dir adjacent to the tools dir.
        exe = Path(sys.executable).resolve()
        uv_tools_marker = os.sep + os.path.join(".local", "share", "uv", "tools") + os.sep
        if uv_tools_marker in str(exe):
            uv_share = str(exe).split(uv_tools_marker)[0]
            extra_paths.append(Path(uv_share) / ".local" / "bin" / "uv")
        extra_paths.append(Path("/usr/local/bin/uv"))
        for p in extra_paths:
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)

    return None


def _resolve_npm_executable() -> str | None:
    """Resolve npm from bundled Node first, then standard executable probing."""
    candidates = _resolve_frontend_npm_candidates()
    if candidates:
        return candidates[0].npm
    return None


def _resolve_bundled_npm_executable() -> tuple[str, Path] | None:
    """Resolve npm from the bundled Node.js install, if available."""
    node_dir = _bundled_node_install_dir()
    if node_dir is None:
        return None

    candidates = (
        [node_dir / "npm.cmd", node_dir / "npm", node_dir / "bin" / "npm"]
        if sys.platform == "win32"
        else [node_dir / "bin" / "npm", node_dir / "npm"]
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate), node_dir
    return None


def _resolve_system_npm_executable() -> str | None:
    """Resolve npm without relying on the bundled Node.js install."""
    if sys.platform == "win32":
        return _find_executable("npm.cmd") or _find_executable("npm")
    return _find_executable("npm") or _find_executable("npm.cmd")


def _resolve_frontend_npm_candidates(*, npm_registry: str | None = None) -> list[_FrontendNpmCandidate]:
    """Resolve npm candidates for frontend rebuilds."""
    candidates: list[_FrontendNpmCandidate] = []

    bundled = _resolve_bundled_npm_executable()
    if bundled is not None:
        bundled_npm, node_dir = bundled
        candidates.append(
            _FrontendNpmCandidate(
                npm=bundled_npm,
                env=_build_frontend_subprocess_env_for_node_dir(
                    node_dir,
                    npm_registry=npm_registry,
                ),
                source="bundled",
            )
        )

    system_npm = _resolve_system_npm_executable()
    if system_npm is not None and sys.platform == "win32":
        if sys.platform == "win32":
            normalized_system_npm = system_npm.replace("/", "\\").lower()
            duplicate = any(
                candidate.npm.replace("/", "\\").lower() == normalized_system_npm
                for candidate in candidates
            )
        if not duplicate:
            candidates.append(
                _FrontendNpmCandidate(
                    npm=system_npm,
                    env=_build_frontend_subprocess_env_for_node_dir(
                        None,
                        npm_registry=npm_registry,
                    ),
                    source="system",
                )
            )

    if candidates:
        return candidates

    if system_npm is not None:
        candidates.append(
            _FrontendNpmCandidate(
                npm=system_npm,
                env=_build_frontend_subprocess_env_for_node_dir(
                    None,
                    npm_registry=npm_registry,
                ),
                source="default",
            )
        )

    return candidates
