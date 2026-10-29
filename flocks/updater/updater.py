"""Core updater logic.

The active process downloads and validates source archives.  A detached
handoff stops the old service, backs up and replaces the source tree, repairs
the installation, and restores the captured service state.

No git binary is required at runtime — all code fetching is done via HTTP.

The ``sources`` list in UpdaterConfig controls the priority order.
The updater tries each source in turn and falls back to the next on failure.
"""

import asyncio
import json
import os
import re
import secrets
import shutil
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
from typing import Any, AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from urllib.parse import quote, urljoin, urlparse

import httpx

from flocks.updater.channels import ChannelRelease, InstallPlan, InstalledReleaseChannel, get_update_channel
from flocks.updater.handoff_progress import UPGRADE_PROGRESS_TOKEN_ENV
from flocks.updater.models import UpdateProgress, VersionInfo
from flocks.utils.log import Log

_DEFAULT_REPO = "AgentFlocks/Flocks"
# Backup/version-marker locations follow the runtime root (``FLOCKS_ROOT``) at call
# time; tests may pin these to a fixed path.  ``None`` = ``<FLOCKS_ROOT>/version``.
_BACKUP_DIR: Path | None = None
_UPDATE_REGION_CN = "cn"
_CN_NPM_REGISTRY = "https://registry.npmmirror.com/"
_CN_UV_DEFAULT_INDEX = "https://mirrors.aliyun.com/pypi/simple"
_CN_PIP_INDEX_URL = _CN_UV_DEFAULT_INDEX
_CURL_USER_AGENT = "curl/8.7.1"
_FRONTEND_DEPENDENCY_INSTALL_TIMEOUT_SECONDS = 300
_FRONTEND_BUILD_TIMEOUT_SECONDS = 300
_DEPENDENCY_SYNC_TIMEOUT_SECONDS = 300
_WINDOWS_DEPENDENCY_SYNC_TIMEOUT_SECONDS = 300

# Deploy modes where a network-based in-place upgrade must never run
# (``update_allowed_for_mode`` in ``flocks.updater.deploy`` is the source of truth).
_NO_INPLACE_UPGRADE_MODES = ("docker", "managed", "offline")
from flocks.updater.deploy import OFFLINE_UPGRADE_REFUSAL as OFFLINE_UPGRADE_REFUSED_MESSAGE  # noqa: E402

_PROC_ROOT = Path("/proc")
SYSTEMD_MAIN_PROCESS_REFUSED_MESSAGE = (
    "后端由 systemd 直接以 flocks serve 运行，不支持页面内就地升级或降级：这一步要先退出当前进程，"
    "systemd 会随之停掉整个服务，接手升级的进程也会被结束，服务就停了。"
    "请用部署流程更新版本，或像离线安装包那样改用 flocks start 托管服务。"
)

_PRESERVE_NAMES: set[str] = {
    ".venv",
    "node_modules",
    "logs",
    ".env",
    ".git",
    "__pycache__",
}

_ROOT_RUNTIME_NAMES: set[str] = {
    ".secret.json",
    "config.json",
    "data",
    "flocks.json",
    "flocks.jsonc",
    "mcp_list.json",
    "run",
    "storage",
}

log = Log.create(service="updater")


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
class ServiceSnapshot:
    """Service state captured once before the detached upgrade starts."""

    config: Any
    daemon_pid: int | None
    was_running: bool


@dataclass(frozen=True)
class _FrontendNpmCandidate:
    """A single npm launcher candidate for frontend rebuilds."""

    npm: str
    env: dict[str, str] | None
    source: str


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


def _running_as_systemd_service_main_process() -> bool:
    """Return whether this process is the main process of a systemd service.

    The upgrade restart handoff relies on this process exiting while a detached
    helper finishes the work. When systemd runs ``flocks serve`` directly, that
    exit stops the unit and systemd kills the helper with the rest of the cgroup.
    systemd exports ``INVOCATION_ID`` to the processes it starts for a unit, and
    a unit's main process is a direct child of the service manager (PID 1, or the
    user's ``systemd --user``). Under ``flocks start`` the backend is a child of
    the Flocks supervisor instead.
    """
    if not sys.platform.startswith("linux") or not os.environ.get("INVOCATION_ID"):
        return False
    parent_pid = os.getppid()
    if parent_pid == 1:
        return True
    try:
        return (_PROC_ROOT / str(parent_pid) / "comm").read_text(encoding="utf-8").strip() == "systemd"
    except OSError:
        return False


def _install_write_blocker(*paths: Path) -> str | None:
    """Return a refusal when an existing install path is not writable by this process."""
    for path in paths:
        if path.exists() and not os.access(path, os.W_OK | os.X_OK):
            return (
                f"安装目录 {path} 当前服务用户写不了（只读挂载或属主不对），无法就地升级。"
                "请用部署流程更新版本，或给服务用户写权限后再升级。"
            )
    return None


def _inplace_upgrade_blocker(install_root: Path, *, restart: bool, replaces_core: bool) -> str | None:
    """Return why an in-place upgrade cannot run here, before anything is downloaded.

    Whether an update channel's release replaces the source is only known once its
    plan has looked at the extracted release, so channel releases pass
    ``replaces_core=False`` here and check the install root again then.
    """
    if restart and _running_as_systemd_service_main_process():
        return SYSTEMD_MAIN_PROCESS_REFUSED_MESSAGE
    venv = install_root / ".venv"
    return _install_write_blocker(install_root, venv) if replaces_core else _install_write_blocker(venv)


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


def _dependency_sync_timeout_seconds() -> int:
    """Return the timeout budget for ``uv sync`` during self-update."""
    if sys.platform == "win32":
        return _WINDOWS_DEPENDENCY_SYNC_TIMEOUT_SECONDS
    return _DEPENDENCY_SYNC_TIMEOUT_SECONDS


def _build_dependency_sync_command(uv_path: str, *, uv_default_index: str | None = None) -> list[str]:
    """Build the ``uv sync`` command used by the self-updater.

    ``--inexact`` keeps packages that are not in the lock file: the upgrade reuses
    the existing ``.venv``, and libraries the user (or Rex) installed into it for
    tools and workflows must survive the update. A plain ``uv sync`` removes them.
    """
    cmd = [uv_path, "sync", "--inexact", "--no-python-downloads"]
    if uv_default_index:
        cmd.extend(["--default-index", uv_default_index])
    return cmd


def _downgrade_refusal(target_tag: str, *, allow_downgrade: bool = False) -> str | None:
    """Reason a *target_tag* older than the installed version is refused, else ``None``.

    Equal versions stay allowed (``flocks update --force`` reinstalls the current
    release); only a strictly older target is a downgrade.
    """
    if allow_downgrade:
        return None
    current = get_current_version()
    target = str(target_tag or "").strip()
    if not current or not target:
        return None
    if _parse_version(target) < _parse_version(current):
        return (
            f"Target version {_version_label(target)} is older than the installed "
            f"{_version_label(current)}; downgrades are not applied by the built-in updater. "
            "Restore the previous release from its backup archive instead, or pass allow_downgrade explicitly."
        )
    return None


def _install_modification_refusal() -> str | None:
    """Reason the built-in updater must not touch this installation (venv/source), else ``None``.

    Docker images are replaced by pulling a new image and managed release packages
    are upgraded by the supervisor that manages them; in both cases ``uv sync`` / source
    replacement from inside the runtime would corrupt the deployment. Offline
    installations are allowed through: a release shipped with the package is
    installed from disk (see ``perform_update(release=...)``).
    """
    from flocks.updater.deploy import install_modification_refusal

    return install_modification_refusal()


async def _sync_project_dependencies(
    *,
    uv_path: str,
    install_root: Path,
    uv_default_index: str | None = None,
    sync_timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> str | None:
    """Synchronize backend dependencies in the active project environment."""
    refusal = _install_modification_refusal()
    if refusal is not None:
        log.warning("updater.dependencies.sync_refused", {"path": str(install_root), "reason": refusal})
        return refusal
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

    def _log_timeout(exc: subprocess.TimeoutExpired, *, retry_without_default_index: bool) -> None:
        log.warning(
            "updater.dependencies.sync_timeout",
            {
                "command": list(exc.cmd) if not isinstance(exc.cmd, str) else exc.cmd,
                "timeout": effective_timeout,
                "stdout": _clean_process_output(exc.stdout),
                "stderr": _clean_process_output(exc.stderr),
                "retry_without_default_index": retry_without_default_index,
            },
        )

    try:
        code, _, err = await _run_uv_sync(uv_cmd)
    except subprocess.TimeoutExpired as exc:
        _log_timeout(exc, retry_without_default_index=bool(uv_default_index))
        if not uv_default_index:
            return _timeout_message()
        uv_cmd = _build_dependency_sync_command(uv_path)
        uv_default_index = None
        try:
            code, _, err = await _run_uv_sync(uv_cmd)
        except subprocess.TimeoutExpired as fallback_exc:
            _log_timeout(fallback_exc, retry_without_default_index=False)
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
        except subprocess.TimeoutExpired as exc:
            _log_timeout(exc, retry_without_default_index=False)
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
        except subprocess.TimeoutExpired as exc:
            _log_timeout(exc, retry_without_default_index=False)
            return _timeout_message()

    if code != 0:
        log.warning("updater.dependencies.sync_retry", {"first_error": err})
        await asyncio.sleep(3)
        try:
            code, _, err = await _run_uv_sync(uv_cmd)
        except subprocess.TimeoutExpired as exc:
            _log_timeout(exc, retry_without_default_index=False)
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


def _upgrade_result_path() -> Path:
    return _upgrade_run_dir() / "upgrade-result.json"


def _write_upgrade_result_state(payload: dict[str, Any]) -> None:
    """Persist diagnostics from the detached upgrade helper."""
    path = _upgrade_result_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    result = dict(payload)
    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(result, ensure_ascii=True, sort_keys=True), encoding="utf-8")


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


_BUILTIN_RELEASE_SOURCES = ("github", "gitee", "gitlab")


def _release_sources(configured_sources: list[str]) -> list[str]:
    """The configured sources this updater reads itself.

    A release an extension ships comes through its update channel
    (flocks.updater.channels), never through this list; anything else in it
    is ignored.
    """
    return [source for source in configured_sources if source in _BUILTIN_RELEASE_SOURCES]


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


def _version_label(version: str | None) -> str:
    normalized = str(version or "").strip()
    if not normalized:
        return ""
    return normalized if normalized.startswith(("v", "V")) else f"v{normalized}"


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
    normalized = v.strip().lower().removeprefix("refs/tags/")
    # A channel may tag its releases under its own name (``<edition>-v<number>``).
    normalized = re.sub(r"^[a-z]+-(?=v?\d)", "", normalized).removeprefix("v")
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


def _url_origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    default = {"http": 80, "https": 443}.get(scheme)
    return scheme, (parsed.hostname or "").lower(), parsed.port or default


async def _download_channel_archive(
    url: str,
    dest_dir: Path,
    filename: str,
    *,
    headers: Mapping[str, str] | None = None,
    label: str = "release",
    progress_callback: Callable[[UpdateProgress], Awaitable[None]] | None = None,
) -> Path:
    """Download an update channel's release archive, reporting progress.

    *headers* (the channel's credentials) go to *url*'s own origin only:
    redirects are followed here one hop at a time, and a hop to any other
    origin is made without them.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / _download_filename_for_url(url, filename)
    origin = _url_origin(url)
    current = url
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=300), follow_redirects=False) as client:
        for _hop in range(10):
            request_headers = dict(headers or {}) if _url_origin(current) == origin else {}
            async with client.stream("GET", current, headers=request_headers) as resp:
                response_headers = getattr(resp, "headers", None) or {}
                location = response_headers.get("location") if resp.status_code in {301, 302, 303, 307, 308} else None
                if location:
                    current = urljoin(current, location)
                    continue
                if resp.status_code != 200:
                    await _raise_download_status_error(resp)
                total_bytes = None
                content_length = response_headers.get("content-length")
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
                                    message=f"Downloading {label}...",
                                    bundle_filename=dest.name,
                                    downloaded_bytes=downloaded_bytes,
                                    total_bytes=total_bytes,
                                    percent=percent,
                                )
                            )
                return dest
    raise RuntimeError(f"too many redirects while downloading {label}")


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


def _backup_path_is_excluded(parts: tuple[str, ...]) -> bool:
    """Return whether a source-relative path should be excluded from backups."""
    if parts and parts[0] in _ROOT_RUNTIME_NAMES:
        return True
    return any(part in _PRESERVE_NAMES or part == "dist" for part in parts)


def _write_backup_archive(source_root: Path, archive_path: Path) -> None:
    """Write a source-only tar archive from *source_root*."""

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = info.name.split("/")
        relative_parts = tuple(parts[1:]) if parts and parts[0] == "flocks" else tuple(parts)
        if _backup_path_is_excluded(relative_parts):
            return None
        return info

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(str(source_root), arcname="flocks", filter=_filter)


def _copy_backup_snapshot(install_root: Path, snapshot_root: Path) -> None:
    """Copy backup-eligible source files into a stable temporary snapshot."""

    def _ignore(directory: str, names: list[str]) -> set[str]:
        relative_dir = Path(directory).relative_to(install_root)
        return {name for name in names if _backup_path_is_excluded((*relative_dir.parts, name))}

    shutil.copytree(
        install_root,
        snapshot_root,
        ignore=_ignore,
        ignore_dangling_symlinks=True,
    )


def _backup_current_version(
    install_root: Path,
    current_version: str,
    retain_count: int = 1,
) -> Path | None:
    """
    Compress the current source tree into <FLOCKS_ROOT>/version/ .
    Falls back to archiving a temporary source snapshot when direct archiving fails.
    Returns the backup path on success, None on failure.
    Preserved runtime/user directories are excluded.
    """
    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup_name = f"flocks-{current_version}-{ts}"
    backup_path = backup_dir / f"{backup_name}.tar.gz"
    partial_path = backup_dir / f"{backup_name}.tar.gz.partial"
    partial_path.unlink(missing_ok=True)

    try:
        _write_backup_archive(install_root, partial_path)
    except Exception as direct_exc:
        partial_path.unlink(missing_ok=True)
        log.warning("updater.backup.direct_failed", {"error": str(direct_exc)})
        snapshot_dir: Path | None = None
        try:
            snapshot_dir = Path(tempfile.mkdtemp(prefix="flocks-backup-"))
            snapshot_root = snapshot_dir / "flocks"
            _copy_backup_snapshot(install_root, snapshot_root)
            _write_backup_archive(snapshot_root, partial_path)
        except Exception as snapshot_exc:
            partial_path.unlink(missing_ok=True)
            log.warning(
                "updater.backup.failed",
                {
                    "direct_error": str(direct_exc),
                    "snapshot_error": str(snapshot_exc),
                },
            )
            return None
        finally:
            if snapshot_dir is not None:
                shutil.rmtree(snapshot_dir, ignore_errors=True)

    try:
        partial_path.replace(backup_path)
    except Exception as exc:
        partial_path.unlink(missing_ok=True)
        log.warning("updater.backup.failed", {"error": str(exc)})
        return None
    _cleanup_old_backups(retain_count)
    return backup_path


def _cleanup_old_backups(retain_count: int) -> None:
    """Remove the oldest backups beyond *retain_count*."""
    backup_dir = _backup_dir()
    if not backup_dir.is_dir():
        return
    effective_retain_count = max(1, retain_count)
    backups = sorted(
        [p for p in backup_dir.iterdir() if p.name.startswith("flocks-") and p.is_file()],
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


def _venv_python_path(install_root: Path) -> Path:
    if sys.platform == "win32":
        return install_root / ".venv" / "Scripts" / "python.exe"
    return install_root / ".venv" / "bin" / "python"


def _build_prebuilt_dependency_sync_command(uv_path: str, wheels_dir: Path) -> list[str]:
    """Build the offline ``uv sync`` used when a prebuilt bundle ships dependency wheels.

    ``--inexact`` for the same reason as the online sync: keep user-installed packages.
    """
    return [
        uv_path,
        "sync",
        "--inexact",
        "--frozen",
        "--offline",
        "--no-python-downloads",
        "--find-links",
        str(wheels_dir),
    ]


async def _sync_prebuilt_dependencies(
    *,
    uv_path: str,
    install_root: Path,
    wheels_dir: Path,
    sync_timeout: int,
    env: dict[str, str] | None,
) -> str | None:
    """Sync backend dependencies from bundled wheels without touching the network."""
    cmd = _build_prebuilt_dependency_sync_command(uv_path, wheels_dir)
    log.info("updater.dependencies.sync_prebuilt", {"tool": "uv sync", "path": uv_path, "wheels": str(wheels_dir)})
    try:
        code, _, err = await _run_async(cmd, cwd=install_root, timeout=sync_timeout, env=env)
    except subprocess.TimeoutExpired:
        return f"Dependency sync timed out after {sync_timeout}s while running offline uv sync."
    if code != 0:
        return f"Dependency sync failed: {err}"
    return None


async def install_or_repair_source(
    install_root: Path,
    *,
    version: str,
    uv_path: str,
    uv_default_index: str | None = None,
    npm_registry: str | None = None,
    sync_timeout: int = 300,
    extra_wheels: Sequence[Path] = (),
    success_files: Sequence[tuple[Path, Path]] = (),
    prebuilt: bool = False,
    dependency_wheels_dir: Path | None = None,
    on_step: Callable[[str], None] | None = None,
) -> None:
    """Install or repair the active source tree after managed services stop.

    ``prebuilt`` is set for releases that ship their built WebUI: the Python
    environment is either untouched (a change that keeps the local source) or
    refreshed from ``dependency_wheels_dir`` without network access. The default
    (``False``) is the online behaviour: ``uv sync`` against the index and
    ``npm run build``.  ``extra_wheels`` are installed (``--no-deps``) after the
    dependency sync, and ``success_files`` -- ``(staged, destination)`` pairs an
    update channel prepared -- are put in place once everything else succeeded.

    ``on_step`` is told the step about to run (``check_runtime``, ``sync_dependencies``,
    ``install_components``, ``build_webui``, ``finalize``) so the upgrade dialog can show it.
    """
    refusal = _install_modification_refusal()
    if refusal is not None:
        raise RuntimeError(refusal)

    def report(step: str) -> None:
        if on_step is None:
            return
        try:
            on_step(step)
        except Exception as exc:
            log.warning("updater.install.step_report_failed", {"step": step, "error": str(exc)})

    install_webui_dir = install_root / "webui"
    if not prebuilt and install_webui_dir.is_dir() and (install_webui_dir / "package.json").exists():
        from flocks.cli import service_manager

        report("check_runtime")

        install_script = "scripts/install.ps1" if sys.platform == "win32" else "scripts/install.sh"
        if service_manager.resolve_npm_executable() is None:
            raise RuntimeError(
                f"npm was not found. Run {install_script} to install Node.js and npm."
            )
        if not service_manager.node_version_satisfies_requirement():
            raise RuntimeError(
                f"Node.js {service_manager.MIN_NODE_MAJOR}+ is required. "
                f"Run {install_script} to install a compatible runtime."
            )

    sync_env = _build_uv_sync_env()
    if prebuilt:
        if dependency_wheels_dir is not None:
            report("sync_dependencies")
            sync_error = await _sync_prebuilt_dependencies(
                uv_path=uv_path,
                install_root=install_root,
                wheels_dir=dependency_wheels_dir,
                sync_timeout=sync_timeout,
                env=sync_env,
            )
            if sync_error is not None:
                raise RuntimeError(sync_error)
        else:
            log.info("updater.dependencies.sync_skipped_prebuilt", {"install_root": str(install_root)})
    else:
        report("sync_dependencies")
        sync_error = await _sync_project_dependencies(
            uv_path=uv_path,
            install_root=install_root,
            uv_default_index=uv_default_index,
            sync_timeout=sync_timeout,
            env=sync_env,
        )
        if sync_error is not None:
            raise RuntimeError(sync_error)

    if prebuilt:
        from flocks.server.static_webui import resolve_webui_dist_dir

        dist_index = install_webui_dir / "dist" / "index.html"
        if not dist_index.is_file() and resolve_webui_dist_dir() is None:
            raise RuntimeError("Prebuilt bundle is missing the WebUI bundle (webui/dist/index.html).")
        log.info("updater.frontend.build_skipped_prebuilt", {"dist": str(dist_index)})

    if extra_wheels:
        report("install_components")
    for wheel_path in extra_wheels:
        python_path = _venv_python_path(install_root)
        install_cmd = [uv_path, "pip", "install", "--python", str(python_path), "--no-deps", str(wheel_path)]
        code, _, err = await _run_async(
            install_cmd,
            cwd=install_root,
            timeout=180,
            env=sync_env,
        )
        if code != 0:
            raise RuntimeError(f"Component install failed ({Path(wheel_path).name}): {err}")

    if not prebuilt and install_webui_dir.is_dir() and (install_webui_dir / "package.json").exists():
        report("build_webui")
        frontend_error = await _build_frontend_workspace(
            install_webui_dir,
            npm_registry=npm_registry,
        )
        if frontend_error is not None:
            raise RuntimeError(frontend_error)

    report("finalize")
    validation_error = await _validate_restart_runtime(install_root)
    if validation_error:
        raise RuntimeError(validation_error)

    _refresh_global_cli_entry(install_root)

    _put_success_files(success_files)
    _write_version_marker(version.lstrip("v"))


async def _uninstall_packages(
    packages: Sequence[str],
    *,
    uv_path: str,
    install_root: Path,
    env: dict[str, str] | None = None,
) -> str | None:
    python_path = _venv_python_path(install_root)
    if not python_path.exists():
        return f"Python environment may need manual repair: missing {python_path}"
    cmd = [uv_path, "pip", "uninstall", "--python", str(python_path), *packages]
    code, _, err = await _run_async(cmd, cwd=install_root, timeout=180, env=env)
    if code != 0:
        return f"Uninstalling {', '.join(packages)} failed: {err}"
    return None


def _put_success_files(success_files: Sequence[tuple[Path, Path]]) -> None:
    """Move files staged by an update channel into place, each atomically."""
    for staged, destination in success_files:
        staged = Path(staged)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(f".{destination.name}.installing")
        shutil.copy2(staged, pending)
        os.replace(pending, destination)


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


def _capture_service_snapshot() -> ServiceSnapshot:
    """Capture restart configuration and liveness before the service is stopped."""
    from flocks.cli import service_manager
    from flocks.cli.service_config import ServiceConfig, service_config_from_status_payload
    from flocks.cli.service_control import read_supervisor_status

    paths = service_manager.runtime_paths()
    try:
        status = read_supervisor_status(paths=paths, timeout=1.0)
    except Exception as exc:
        backend_record = service_manager.read_runtime_record(paths.backend_pid)
        frontend_record = service_manager.read_runtime_record(paths.frontend_pid)
        backend_running = service_manager.runtime_record_is_running(backend_record)
        frontend_running = service_manager.runtime_record_is_running(frontend_record)
        if backend_running or frontend_running:
            backend_host = backend_record.host if backend_record and backend_record.host else "127.0.0.1"
            backend_port = backend_record.port if backend_record and backend_record.port else 5173
            frontend_host = frontend_record.host if frontend_record and frontend_record.host else backend_host
            frontend_port = frontend_record.port if frontend_record and frontend_record.port else backend_port
            config = ServiceConfig(
                backend_host=backend_host,
                backend_port=backend_port,
                frontend_host=frontend_host,
                frontend_port=frontend_port,
                no_browser=True,
                skip_frontend_build=True,
            )
            return ServiceSnapshot(config=config, daemon_pid=None, was_running=True)

        try:
            daemon_pids = service_manager.trusted_daemon_process_pids(root=_get_repo_root())
        except Exception:
            daemon_pids = []
        if daemon_pids:
            raise RuntimeError(
                "Supervisor is running but its control API is unavailable; "
                "cannot safely capture the service configuration."
            ) from exc
        return ServiceSnapshot(
            config=ServiceConfig(no_browser=True, skip_frontend_build=True),
            daemon_pid=None,
            was_running=False,
        )

    config = service_config_from_status_payload(
        status.raw,
    )
    backend_state = status.backend.state.lower()
    was_running = bool(status.backend.pid) or backend_state in {
        "healthy",
        "starting",
        "restarting",
        "paused",
        "static",
    }
    return ServiceSnapshot(
        config=config,
        daemon_pid=status.daemon.pid,
        was_running=was_running,
    )


def _spawn_detached_process(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    env: dict[str, str] | None = None,
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
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            **kwargs,
        )
    finally:
        handle.close()


def _spawn_restart_handoff(
    command: list[str],
    *,
    cwd: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen:
    return _spawn_detached_process(
        command,
        cwd=cwd,
        log_path=_flocks_root() / "logs" / "backend.log",
        env={**os.environ, **extra_env} if extra_env else None,
    )


def _handoff_service_config():
    """Read the current config for restart-only handoffs."""
    return _current_service_config()


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


def _stage_local_release(source: Path, tmp_dir: Path) -> Path:
    """Copy a release shipped on disk (directory or archive) into the update temp dir.

    The shipped release must survive the update (cleanup removes *tmp_dir*), so it
    is never used in place.
    """
    if source.is_dir():
        staged = tmp_dir / "local-release"
        shutil.copytree(source, staged, symlinks=True)
        return staged
    if not source.is_file():
        raise ValueError(f"Local release not found: {source}")
    staged_file = tmp_dir / source.name
    shutil.copy2(source, staged_file)
    return staged_file


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
    *,
    _is_root: bool = True,
) -> None:
    """
    Overwrite *install_root* with the contents of *source_dir*. Build caches
    are preserved at every depth; root-level configuration and runtime data
    are preserved only at the installation root.
    """
    install_root.mkdir(parents=True, exist_ok=True)
    source_names = {item.name for item in source_dir.iterdir()}
    preserve_names = _PRESERVE_NAMES | (_ROOT_RUNTIME_NAMES if _is_root else set())

    for item in source_dir.iterdir():
        if item.name in preserve_names:
            continue
        target = install_root / item.name
        if item.is_dir() and not item.is_symlink():
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    _replace_install_dir(item, target, _is_root=False)
                    continue
                _safe_remove(target)
            shutil.copytree(item, target, symlinks=True)
        else:
            if target.exists() or target.is_symlink():
                _safe_remove(target)
            # Keep symlinks as links at this level too (copytree above already does
            # so for nested ones); copy2 would silently dereference them.
            shutil.copy2(item, target, follow_symlinks=False)

    for child in install_root.iterdir():
        if child.name not in source_names and child.name not in preserve_names:
            _safe_remove(child)


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #

_VERSION_MARKER_PATH: Path | None = None


def _backup_dir() -> Path:
    """``<FLOCKS_ROOT>/version`` (or the pinned ``_BACKUP_DIR``)."""
    return _BACKUP_DIR if _BACKUP_DIR is not None else _flocks_root() / "version"


def _version_marker_path() -> Path:
    """``<FLOCKS_ROOT>/version/.current_version`` (or the pinned ``_VERSION_MARKER_PATH``)."""
    return _VERSION_MARKER_PATH if _VERSION_MARKER_PATH is not None else _backup_dir() / ".current_version"


def _read_version_marker() -> str:
    """Read the persisted installed version marker."""
    try:
        marker = _version_marker_path()
        if marker.is_file():
            return marker.read_text(encoding="utf-8").strip().lstrip("v")
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


def _is_newer_version(latest: str | None, current: str | None) -> bool:
    latest_version = str(latest or "").strip()
    if not latest_version:
        return False
    current_version = str(current or "").strip()
    if not current_version:
        return True
    return _parse_version(latest_version) > _parse_version(current_version)


def get_current_version() -> str:
    """
    Return the running version.
    Compare the persisted marker at <FLOCKS_ROOT>/version/.current_version
    (``~/.flocks`` by default) with the source version in pyproject.toml and
    return the higher one.
    """
    return _pick_best_tag([
        _read_version_marker(),
        _read_pyproject_version(),
    ])


def _write_version_marker(version: str) -> None:
    """Persist the installed version so get_current_version() picks it up after restart."""
    marker = _version_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{version}\n", encoding="utf-8")


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


async def _fetch_installed_release_source(
    client: httpx.AsyncClient,
    source: str,
    config: Any,
    encoded_tag: str,
) -> dict[str, Any] | None:
    if source == "github":
        url = f"{_github_api_url(config.base_url, config.repo).removesuffix('/latest')}/tags/{encoded_tag}"
        headers = {"Accept": "application/vnd.github+json"}
        if config.token:
            headers["Authorization"] = f"Bearer {config.token}"
        params = None
    elif source == "gitee":
        repo = config.gitee_repo or config.repo
        url = f"https://gitee.com/api/v5/repos/{repo}/releases/tags/{encoded_tag}"
        headers = {}
        params = {"access_token": config.gitee_token} if config.gitee_token else None
    else:
        return None

    try:
        response = await client.get(url, headers=headers, params=params, follow_redirects=True, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        log.warning("updater.installed_release.source_failed", {
            "source": source,
            "error_type": type(exc).__name__,
        })
        return None


async def get_installed_release(version: str) -> VersionInfo:
    """Read the published notes for an installed Core version, not the latest mirror tag."""
    current = version.strip().removeprefix("v")
    result = VersionInfo(current_version=current, latest_version=current or None)
    if not re.fullmatch(r"\d{4}\.\d{1,2}\.\d{1,2}", current):
        return result

    ucfg = await _get_updater_config()
    from flocks.updater.deploy import detect_deploy_mode

    if not ucfg.enabled or detect_deploy_mode() == "offline":
        return result

    tag = f"v{current}"
    encoded_tag = quote(tag, safe="")
    sources = [source for source in ucfg.sources if source in {"github", "gitee"}]
    async with httpx.AsyncClient(timeout=10) as client:
        tasks = [
            asyncio.create_task(_fetch_installed_release_source(client, source, ucfg, encoded_tag))
            for source in sources
        ]
        try:
            for completed in asyncio.as_completed(tasks):
                data = await completed
                if not isinstance(data, dict):
                    continue
                returned_tag = str(data.get("tag_name") or "").strip().removeprefix("v")
                notes = data.get("body")
                if returned_tag != current or not isinstance(notes, str) or not notes.strip():
                    continue
                return result.model_copy(update={
                    "release_notes": notes,
                    "release_url": data.get("html_url"),
                })
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    return result


async def get_channel_installed_release(update_channel: InstalledReleaseChannel) -> VersionInfo:
    """What *update_channel* installed here, with its notes when it has them.

    Remote lookups follow the updater switch, as in :func:`check_update`.
    """
    ucfg = await _get_updater_config()
    try:
        info = await update_channel.installed_release(remote=ucfg.enabled)
    except Exception as exc:
        log.warning("updater.channel_installed_release_failed", {"channel": update_channel.name, "error": str(exc)})
        info = VersionInfo(current_version=get_current_version())
    return info.model_copy(update={"channel": update_channel.name})


async def check_update(
    *,
    locale: str | None = None,
    region: str | None = None,
    channel: str | None = None,
) -> VersionInfo:
    """Return version comparison info without performing any upgrade.

    *channel* names an update channel an extension registered
    (:mod:`flocks.updater.channels`); unset means this project's own releases.
    """
    from flocks.updater.deploy import detect_deploy_mode, update_allowed_for_mode

    mode = detect_deploy_mode()
    ucfg = await _get_updater_config()
    if channel:
        update_channel = get_update_channel(channel)
        if update_channel is None:
            return VersionInfo(
                current_version=get_current_version(),
                channel=channel,
                error=f"Unknown update channel: {channel}",
                deploy_mode=mode,
                update_allowed=update_allowed_for_mode(mode),
            )
        try:
            # A channel answers for itself: an offline installation may still
            # reach the one endpoint its channel uses.  With the updater
            # switched off it only reports what is installed.
            info = await update_channel.check(locale=locale, remote=ucfg.enabled)
        except Exception as exc:
            log.warning("updater.channel_check_failed", {"channel": channel, "error": str(exc)})
            info = VersionInfo(
                current_version=get_current_version(),
                error="Failed to check for updates. Please check your network connection.",
            )
        return info.model_copy(
            update={"channel": channel, "deploy_mode": mode, "update_allowed": update_allowed_for_mode(mode)}
        )

    profile = _resolve_update_mirror_profile(
        _release_sources(ucfg.sources),
        region=region,
        locale=locale,
    )
    current = get_current_version()

    if not ucfg.enabled or mode == "offline":
        # An offline package deployment cannot reach GitHub / Gitee and does not upgrade the core
        # in place anyway (a new .run does that): report the local state without any network
        # round trips (each source would otherwise wait for its timeout, and the last fallback
        # even runs `git tag` in the CLI's cwd).
        return VersionInfo(
            current_version=current,
            deploy_mode=mode,
            update_allowed=update_allowed_for_mode(mode),
        )

    try:
        tag, notes, url, zipball, tarball = await get_latest_release(
            repo=ucfg.repo,
            token=ucfg.token,  # secret-guard: allow 误报：传递 token 字段
            sources_override=profile.sources,
        )
    except Exception as exc:
        log.warning("updater.check_failed", {"error": str(exc)})
        return VersionInfo(
            current_version=current,
            error="Failed to check for updates. Please check your network connection.",
            deploy_mode=mode,
            update_allowed=update_allowed_for_mode(mode),
        )

    has_update = _is_newer_version(tag, current)
    log.info(
        "updater.check.result",
        {
            "current": current,
            "latest": tag,
            "has_update": has_update,
            "sources": profile.sources,
            "region": profile.region,
        },
    )
    return VersionInfo(
        current_version=current,
        latest_version=tag,
        has_update=has_update,
        release_notes=notes,
        release_url=url,
        zipball_url=zipball,
        tarball_url=tarball,
        deploy_mode=mode,
        update_allowed=update_allowed_for_mode(mode),
    )


# ------------------------------------------------------------------ #
# Perform upgrade
# ------------------------------------------------------------------ #


async def remove_components(
    packages: Sequence[str],
    *,
    version: str | None,
    label: str = "components",
    restart: bool = True,
    after_removal: Callable[[], Awaitable[None]] | None = None,
    after_removal_message: str | None = None,
) -> AsyncGenerator[UpdateProgress, None]:
    """Uninstall *packages* from the runtime, record *version* and restart.

    For an extension taking its own component out again.  It checks first
    whether there is anything to remove; *after_removal* runs once the packages
    are gone, before the version marker and the restart, and an exception from
    it stops here with an error.
    """
    from flocks.updater.deploy import detect_deploy_mode

    refusal = _install_modification_refusal()
    if refusal is not None:
        yield UpdateProgress(stage="error", message=refusal, success=False)
        return

    install_root = _get_repo_root()
    current_version = get_current_version()
    yield UpdateProgress(stage="checking", message="Checking the local installation...")
    if restart and _running_as_systemd_service_main_process():
        # The restart below exits this process; refuse before anything is removed.
        yield UpdateProgress(stage="error", message=SYSTEMD_MAIN_PROCESS_REFUSED_MESSAGE, success=False)
        return
    uv_path = _find_executable("uv")
    if not uv_path:
        yield UpdateProgress(
            stage="error",
            message=f"Removing {label} failed: uv is required but was not found.",
            success=False,
        )
        return

    sync_env = _build_uv_sync_env()
    yield UpdateProgress(stage="downgrading", message=f"Removing {label}...")
    uninstall_error = await _uninstall_packages(packages, uv_path=uv_path, install_root=install_root, env=sync_env)
    if uninstall_error is not None:
        yield UpdateProgress(stage="error", message=uninstall_error, success=False)
        return

    if after_removal is not None:
        if after_removal_message:
            yield UpdateProgress(stage="reporting", message=after_removal_message)
        try:
            await after_removal()
        except Exception as exc:
            yield UpdateProgress(stage="error", message=f"{label} was removed, but {exc}", success=False)
            return

    try:
        if version:
            _write_version_marker(version.lstrip("v"))
    except Exception as exc:
        yield UpdateProgress(
            stage="error",
            message=f"{label} was removed, but the version marker update failed: {exc}",
            success=False,
        )
        return

    if not restart:
        try:
            _refresh_global_cli_entry(install_root)
        except Exception as exc:
            log.warning("updater.refresh_cli.failed", {"error": str(exc)})
        yield UpdateProgress(stage="done", message=f"Removed {label}.", success=True)
        return

    yield UpdateProgress(stage="restarting", message="Restarting service...")
    await asyncio.sleep(0.8)

    if "--reload" in sys.argv:
        log.info("updater.remove_components.reload_exit3")
        sys.exit(3)

    try:
        restart_argv = _build_restart_argv(install_root)
        sync_timeout = _dependency_sync_timeout_seconds()
        # Offline installations carry a prebuilt runtime: the handoff must not try to
        # uv sync / rebuild the WebUI (no index, no npm registry), only restart.
        offline_install = detect_deploy_mode() == "offline"
        handoff_argv = _build_restart_handoff_argv(
            restart_argv,
            install_root,
            uv_path=uv_path,
            sync_timeout=sync_timeout,
            version=version or current_version,
            current_version=current_version,
            prebuilt=offline_install,
        )
        log.info("updater.remove_components.restart_handoff_spawn", {"argv": handoff_argv, "prebuilt": offline_install})
        _spawn_restart_handoff(handoff_argv, cwd=install_root)
        os._exit(0)
    except Exception as exc:
        log.error("updater.remove_components.restart_failed", {"error": str(exc)})
        yield UpdateProgress(
            stage="error",
            message=f"Failed to restart service after removing {label}: {exc}",
            success=False,
        )
        return


async def perform_update(
    latest_tag: str,
    *,
    zipball_url: str | None = None,
    tarball_url: str | None = None,
    restart: bool = True,
    locale: str | None = None,
    region: str | None = None,
    wait_for_handoff: bool = False,
    allow_downgrade: bool = False,
    release: ChannelRelease | None = None,
) -> AsyncGenerator[UpdateProgress, None]:
    """
    Async generator that executes the upgrade steps and yields progress events.

    If *zipball_url* / *tarball_url* are provided (e.g. from a prior
    ``check_update`` call), the redundant Releases API round-trip is skipped.

    Sources are tried in the order configured in ``updater.sources``.
    If one source fails the download, the next source is tried automatically.

    A *latest_tag* older than the installed version is refused unless
    *allow_downgrade* is set: an explicit ``target_version`` must not silently
    roll the installation back; rollbacks go through the backup archive.

    *release* installs what an extension's update channel offers instead
    (:mod:`flocks.updater.channels`): its archive is downloaded (or staged from
    disk) and verified here, and the channel's plan says what in it to install.
    """
    from flocks.updater.deploy import detect_deploy_mode

    refusal = _install_modification_refusal()
    if refusal is not None:
        log.warning("updater.perform_update.refused", {"target": latest_tag, "reason": refusal})
        yield UpdateProgress(stage="error", message=refusal, success=False)
        return
    local_release = release.archive_path if release is not None else None
    if local_release is None and detect_deploy_mode() == "offline":
        # An offline installation has UV_OFFLINE / npm offline pinned; a download-based
        # upgrade would replace the source and then fail in dependency sync, leaving the
        # service down. Refuse before touching anything.
        log.warning("updater.offline_install.refused", {"tag": latest_tag})
        _record_update_journal(f"ERROR {OFFLINE_UPGRADE_REFUSED_MESSAGE}")
        yield UpdateProgress(stage="error", message=OFFLINE_UPGRADE_REFUSED_MESSAGE, success=False)
        return
    # A channel with its own version rules (e.g. a release whose source is older
    # keeps the newer local one) decides that in its plan; the guard covers the rest.
    downgrade_refusal = (
        None
        if release is not None and release.own_version_rules
        else _downgrade_refusal(latest_tag, allow_downgrade=allow_downgrade)
    )
    if downgrade_refusal is not None:
        log.warning("updater.perform_update.downgrade_refused", {"target": latest_tag, "current": get_current_version()})
        _record_update_journal(f"ERROR {downgrade_refusal}")
        yield UpdateProgress(stage="error", message=downgrade_refusal, success=False)
        return

    ucfg = await _get_updater_config()
    profile = _resolve_update_mirror_profile(
        _release_sources(ucfg.sources),
        region=region,
        locale=locale,
    )
    install_root = _get_repo_root()
    current_version = get_current_version()

    # Everything below needs a writable install, a restartable service and uv; check them
    # before downloading a release that can take minutes and would be thrown away.
    blocker = _inplace_upgrade_blocker(install_root, restart=restart, replaces_core=release is None)
    if blocker is not None:
        log.warning("updater.inplace_upgrade.refused", {"install_root": str(install_root), "reason": blocker})
        _record_update_journal(f"ERROR {blocker}")
        yield UpdateProgress(stage="error", message=blocker, success=False)
        return
    uv_path = _find_executable("uv")
    if not uv_path:
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

    fmt = _choose_archive_format(ucfg.archive_format)
    if local_release is not None:
        fmt = "dir" if local_release.is_dir() else _archive_format_for_url(local_release.name, release.archive_format)
    elif release is not None:
        if not release.archive_url:
            message = f"The {release.label} has no download address."
            _record_update_journal(f"ERROR {message}")
            yield UpdateProgress(stage="error", message=message, success=False)
            return
        fmt = _archive_format_for_url(release.archive_url, release.archive_format)

    # ------------------------------------------------------------------ #
    # Step 1 – download source archive
    # ------------------------------------------------------------------ #
    archive_filename = _archive_filename_for_format(latest_tag, fmt)
    if local_release is not None:
        archive_filename = local_release.name
        yield UpdateProgress(
            stage="fetching",
            message=f"Using local {release.label} {local_release}...",
            bundle_filename=archive_filename,
        )
    elif release is not None:
        archive_filename = _download_filename_for_url(
            release.archive_url,
            release.download_name or f"release-{Path(latest_tag).name}.{'zip' if fmt == 'zip' else 'tar.gz'}",
        )
        yield UpdateProgress(
            stage="fetching",
            message=f"Downloading {release.label}...",
            bundle_filename=archive_filename,
        )
    else:
        sources_desc = " → ".join(profile.sources)
        yield UpdateProgress(
            stage="fetching",
            message=f"Downloading source archive (sources: {sources_desc})...",
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="flocks-update-"))
    if sys.platform == "win32":
        tmp_dir = _resolve_windows_long_path(tmp_dir)
    local_release_copy: Path | None = None
    try:
        if local_release is not None:
            local_release_copy = await asyncio.to_thread(_stage_local_release, local_release, tmp_dir)
            archive_path = local_release_copy
            if local_release_copy.is_file():
                await asyncio.to_thread(_verify_download_sha256, archive_path, release.sha256)
        elif release is not None:
            progress_queue: asyncio.Queue[UpdateProgress] = asyncio.Queue()

            async def _queue_download_progress(progress: UpdateProgress) -> None:
                await progress_queue.put(progress)

            download_task = asyncio.create_task(
                _download_channel_archive(
                    release.archive_url,
                    tmp_dir,
                    archive_filename,
                    headers=release.download_headers,
                    label=release.label,
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
            await asyncio.to_thread(_verify_download_sha256, archive_path, release.sha256)
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
    # Step 2 – validate and extract the staged source, then decide what of it
    # gets installed (a channel's plan; this project's own release replaces
    # the source with all of it)
    # ------------------------------------------------------------------ #
    extract_dir = tmp_dir / "extracted"
    extract_dir.mkdir()
    try:
        if local_release_copy is not None and local_release_copy.is_dir():
            content_root = local_release_copy
        else:
            content_root = await asyncio.to_thread(
                _extract_archive,
                archive_path,
                extract_dir,
            )
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        msg = f"Failed to extract files: {exc}"
        _record_update_journal(f"ERROR {msg}")
        yield UpdateProgress(stage="error", message=msg, success=False)
        return
    if release is None:
        plan = InstallPlan(source_root=content_root, version=latest_tag)
    else:
        try:
            plan = await asyncio.to_thread(release.plan, content_root, current_version, tmp_dir)
        except Exception as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            msg = f"Cannot install this {release.label}: {exc}"
            _record_update_journal(f"ERROR {msg}")
            yield UpdateProgress(stage="error", message=msg, success=False)
            return
    skip_core_replace = plan.source_root is None
    effective_update_version = plan.version
    if release is not None and not skip_core_replace:
        # Only now is it known that this release replaces files in the install root.
        blocker = _install_write_blocker(install_root)
        if blocker is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            log.warning("updater.inplace_upgrade.refused", {"install_root": str(install_root), "reason": blocker})
            _record_update_journal(f"ERROR {blocker}")
            yield UpdateProgress(stage="error", message=blocker, success=False)
            return

    sync_timeout = _dependency_sync_timeout_seconds()

    # ------------------------------------------------------------------ #
    # Step 3 – changes that keep the local source may stay in process; source
    # changes are backed up here and then always use the detached handoff.
    # ------------------------------------------------------------------ #
    if skip_core_replace:
        yield UpdateProgress(
            stage="applying",
            message=f"Keeping local Flocks {_version_label(current_version)} and installing the {release.label}...",
        )

    if skip_core_replace and not restart:
        try:
            await install_or_repair_source(
                install_root=install_root,
                uv_path=uv_path,
                version=effective_update_version,
                uv_default_index=profile.uv_default_index,
                npm_registry=profile.npm_registry,
                extra_wheels=plan.extra_wheels,
                success_files=plan.success_files,
                sync_timeout=sync_timeout,
                prebuilt=plan.prebuilt,
                dependency_wheels_dir=plan.dependency_wheels_dir,
            )
        except RuntimeError as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            _record_update_journal(f"ERROR {exc}")
            yield UpdateProgress(stage="error", message=str(exc), success=False)
            return

        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.info("updater.apply.done", {"version": effective_update_version, "restart": False, "region": profile.region})
        yield UpdateProgress(
            stage="done",
            message=f"Upgraded to {_version_label(effective_update_version)}",
            success=True,
        )
        return

    if not restart:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        message = "Source upgrades require the detached handoff; restart=False is only valid for changes that keep the local source."
        _record_update_journal(f"ERROR {message}")
        yield UpdateProgress(stage="error", message=message, success=False)
        return

    backup_path: Path | None = None
    if not skip_core_replace:
        yield UpdateProgress(stage="backing_up", message="Backing up current version...")
        try:
            backup_path = await asyncio.to_thread(
                _backup_current_version,
                install_root,
                current_version,
                ucfg.backup_retain_count,
            )
        except Exception as exc:
            log.error("updater.backup.failed", {"error": str(exc)})
        if backup_path is None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            message = "Failed to back up the current source; the update was not applied."
            _record_update_journal(f"ERROR {message}")
            yield UpdateProgress(stage="error", message=message, success=False)
            return

        yield UpdateProgress(
            stage="applying",
            message=f"Staging v{latest_tag} for handoff...",
        )

    try:
        restart_argv = _build_restart_argv(install_root)
        service_snapshot = _capture_service_snapshot() if not skip_core_replace else None
        handoff_argv = _build_restart_handoff_argv(
            restart_argv,
            install_root,
            uv_path=uv_path,
            sync_timeout=sync_timeout,
            version=effective_update_version,
            current_version=current_version,
            content_root=plan.source_root,
            service_snapshot=service_snapshot,
            backup_path=backup_path,
            uv_default_index=profile.uv_default_index,
            npm_registry=profile.npm_registry,
            extra_wheels=plan.extra_wheels,
            success_files=plan.success_files,
            cleanup_dir=tmp_dir,
            wait_for_parent=not wait_for_handoff,
            prebuilt=plan.prebuilt,
            dependency_wheels_dir=plan.dependency_wheels_dir,
        )
    except Exception as exc:
        log.error("updater.restart.build_argv_failed", {"error": str(exc)})
        shutil.rmtree(tmp_dir, ignore_errors=True)
        yield UpdateProgress(
            stage="error",
            message=f"Failed to prepare restart handoff: {exc}",
            success=False,
        )
        return

    # The upgrade dialog sends this token to the handoff's progress responder to read the
    # error text of a failed step; only the CLI waits for the handoff itself.
    progress_token = None if wait_for_handoff else secrets.token_urlsafe(24)
    yield UpdateProgress(stage="restarting", message="Restarting service...", progress_token=progress_token)

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
        log.info(
            "updater.restart.handoff_spawn",
            {
                "argv": handoff_argv,
                "restart_argv": restart_argv,
            },
        )
        handoff_process = _spawn_restart_handoff(
            handoff_argv,
            cwd=install_root,
            extra_env={UPGRADE_PROGRESS_TOKEN_ENV: progress_token} if progress_token else None,
        )
        if wait_for_handoff:
            returncode = await asyncio.to_thread(handoff_process.wait)
            if returncode != 0:
                yield UpdateProgress(
                    stage="error",
                    message=f"Upgrade handoff failed with exit code {returncode}. See backend.log for details.",
                    success=False,
                )
                return
            yield UpdateProgress(
                stage="done",
                message=f"Upgraded to {_version_label(effective_update_version)}",
                success=True,
            )
            return
        os._exit(0)
    except Exception as exc:
        log.error("updater.restart.handoff_spawn_failed", {"error": str(exc)})
        shutil.rmtree(tmp_dir, ignore_errors=True)
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
    content_root: Path | None = None,
    service_snapshot: ServiceSnapshot | None = None,
    backup_path: Path | None = None,
    uv_default_index: str | None = None,
    npm_registry: str | None = None,
    extra_wheels: Sequence[Path] = (),
    success_files: Sequence[tuple[Path, Path]] = (),
    cleanup_dir: Path | None = None,
    wait_for_parent: bool = True,
    prebuilt: bool = False,
    dependency_wheels_dir: Path | None = None,
) -> list[str]:
    """Wrap the real restart command in a helper that finishes upgrade work."""
    if not restart_argv:
        raise ValueError("restart command is empty")

    source_upgrade = content_root is not None
    if source_upgrade:
        if service_snapshot is None:
            raise ValueError("source upgrade requires a captured service snapshot")
        if backup_path is None:
            raise ValueError("source upgrade requires a backup path")
        config = service_snapshot.config
    else:
        config = _handoff_service_config()

    from flocks.cli.service_config import service_config_payload

    if source_upgrade:
        # The upgrade helper reconstructs ``flocks start`` from the serialized
        # snapshot after the old source has been replaced.
        managed_restart_argv = [restart_argv[0]]
    else:
        managed_restart_argv = [
            restart_argv[0],
            "-m",
            "flocks.cli.main",
            "start",
            "--no-browser",
            "--skip-webui-build",
            "--host",
            str(config.frontend_host),
            "--port",
            str(config.frontend_port),
        ]
        if config.legacy_backend_host is not None:
            managed_restart_argv.extend(["--server-host", str(config.legacy_backend_host)])
        if config.legacy_backend_port is not None:
            managed_restart_argv.extend(["--server-port", str(config.legacy_backend_port)])
    argv = [
        restart_argv[0],
        "-m",
        "flocks.updater.restart_handoff",
    ]
    if wait_for_parent:
        argv.extend(["--parent-pid", str(os.getpid())])
    argv.extend(
        [
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
    )
    if source_upgrade:
        argv.extend(
            [
                "--mode",
                "upgrade",
                "--content-root",
                str(content_root),
                "--backup-path",
                str(backup_path),
                "--service-config-json",
                json.dumps(service_config_payload(config), ensure_ascii=True, sort_keys=True),
            ]
        )
        if service_snapshot.was_running:
            argv.append("--was-running")
        if service_snapshot.daemon_pid is not None:
            argv.extend(["--daemon-pid", str(service_snapshot.daemon_pid)])
    if uv_default_index:
        argv.extend(["--uv-default-index", uv_default_index])
    if npm_registry:
        argv.extend(["--npm-registry", npm_registry])
    for wheel_path in extra_wheels:
        argv.extend(["--extra-wheel", str(wheel_path)])
    for staged, destination in success_files:
        argv.extend(["--success-file", json.dumps([str(staged), str(destination)], ensure_ascii=True)])
    if cleanup_dir is not None:
        argv.extend(["--cleanup-dir", str(cleanup_dir)])
    if prebuilt:
        argv.append("--prebuilt")
    if dependency_wheels_dir is not None:
        argv.extend(["--dependency-wheels-dir", str(dependency_wheels_dir)])
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
