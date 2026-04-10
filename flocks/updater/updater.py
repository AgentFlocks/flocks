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
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, AsyncGenerator
from urllib.parse import quote

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

_PRESERVE_NAMES: set[str] = {
    ".venv",
    "node_modules",
    "logs",
    ".env",
    "flocks.json",
    "__pycache__",
    ".flocks",
}

log = Log.create(service="updater")


@dataclass(frozen=True)
class UpdateMirrorProfile:
    """Resolved download/runtime mirror settings for a single upgrade request."""

    region: str | None
    sources: list[str]
    npm_registry: str | None = None
    uv_default_index: str | None = None
    pip_index_url: str | None = None


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


def _windows_upgrade_python_path(install_root: Path) -> Path:
    """Return the Windows project virtualenv interpreter for upgrade restarts."""
    return install_root / ".venv" / "Scripts" / "python.exe"


async def _validate_windows_restart_runtime(
    install_root: Path,
    *,
    max_attempts: int = 2,
    timeout: int = 60,
    retry_delay: float = 3.0,
) -> str | None:
    """Validate the Windows project runtime that will be used for restart.

    Retries up to *max_attempts* times to tolerate transient delays caused by
    antivirus scanning or filesystem cache warm-up after ``uv sync``.
    """
    python_exe = _windows_upgrade_python_path(install_root)
    if not python_exe.exists():
        return f"Windows restart runtime is missing: {python_exe}"

    last_error: str = ""
    for attempt in range(max_attempts):
        try:
            code, _, err = await _run_async(
                [str(python_exe), "-c", "import flocks; import uvicorn"],
                cwd=install_root,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            last_error = (
                f"Validation timed out ({timeout}s) — "
                "antivirus or filesystem cache may still be warming up."
            )
            log.warning(
                "updater.validate_runtime.timeout",
                {"attempt": attempt + 1, "timeout": timeout},
            )
        except Exception as exc:
            last_error = str(exc)
            log.warning(
                "updater.validate_runtime.error",
                {"attempt": attempt + 1, "error": last_error},
            )
        else:
            if code == 0:
                return None
            last_error = err or "unknown error"
            log.warning(
                "updater.validate_runtime.nonzero",
                {"attempt": attempt + 1, "code": code, "error": last_error},
            )

        if attempt < max_attempts - 1:
            await asyncio.sleep(retry_delay)

    return f"Windows restart runtime validation failed: {last_error}"


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
    for seg in v.lstrip("v").split("."):
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
    return candidates[0][1].lstrip("v")


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
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)

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
    Heavy directories (.venv, node_modules, ...) are excluded.
    """
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup_name = f"flocks-{current_version}-{ts}"
    backup_path = _BACKUP_DIR / f"{backup_name}.tar.gz"

    exclude = {".venv", "node_modules", "__pycache__", ".git", "logs"}

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = info.name.split("/")
        for index, part in enumerate(parts):
            if part in exclude:
                return None
            if part == "dist" and parts[max(index - 1, 0)] != "webui":
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


class _NullConsole:
    def print(self, *args, **kwargs) -> None:
        return None


def _current_service_config():
    from flocks.cli import service_manager

    paths = service_manager.ensure_runtime_dirs()
    return service_manager.ServiceConfig(
        backend_host=service_manager._recorded_host(paths.backend_pid, service_manager.ServiceConfig.backend_host),
        backend_port=service_manager._recorded_port(paths.backend_pid, service_manager.ServiceConfig.backend_port),
        frontend_host=service_manager._recorded_host(paths.frontend_pid, service_manager.ServiceConfig.frontend_host),
        frontend_port=service_manager._recorded_port(paths.frontend_pid, service_manager.ServiceConfig.frontend_port),
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
    paths = service_manager.ensure_runtime_dirs()
    frontend_port = service_manager._recorded_port(paths.frontend_pid, config.frontend_port)
    service_manager.stop_one(frontend_port, paths.frontend_pid, "WebUI", console)

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
            service_manager.start_frontend(config, console)
        except Exception as restart_error:
            log.error("updater.frontend.restore_failed", {"error": str(restart_error)})
        raise

    return payload


def _service_config_from_payload(
    payload: dict[str, Any],
    *,
    skip_frontend_build: bool | None = None,
):
    from flocks.cli import service_manager

    resolved_skip_frontend_build = (
        bool(payload.get("skip_frontend_build", True)) if skip_frontend_build is None else skip_frontend_build
    )
    return service_manager.ServiceConfig(
        backend_host=str(payload.get("backend_host") or service_manager.ServiceConfig.backend_host),
        backend_port=int(payload.get("backend_port") or service_manager.ServiceConfig.backend_port),
        frontend_host=str(payload.get("frontend_host") or service_manager.ServiceConfig.frontend_host),
        frontend_port=int(payload.get("frontend_port") or service_manager.ServiceConfig.frontend_port),
        no_browser=True,
        skip_frontend_build=resolved_skip_frontend_build,
    )


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
    from flocks.cli import service_manager

    try:
        service_manager.start_frontend(config, console)
        return
    except Exception:
        if not allow_build_fallback or not config.skip_frontend_build:
            raise

    rebuilt_config = service_manager.ServiceConfig(
        backend_host=config.backend_host,
        backend_port=config.backend_port,
        frontend_host=config.frontend_host,
        frontend_port=config.frontend_port,
        no_browser=config.no_browser,
        skip_frontend_build=False,
    )
    service_manager.start_frontend(rebuilt_config, console)


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
        (path for path in install_root.rglob("*") if ".flocks_old_" in path.name),
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


def get_current_version() -> str:
    """
    Return the running version.
    Priority:
      1. Version marker at ~/.flocks/version/.current_version — always
         accurate after a download-based upgrade since local git tags
         are no longer updated.
      2. git describe --tags (works when installed from a git checkout).
         If found, also persist it to the marker for future lookups.
      3. importlib.metadata / pyproject.toml via flocks.__version__.
    """
    try:
        if _VERSION_MARKER_PATH.is_file():
            ver = _VERSION_MARKER_PATH.read_text(encoding="utf-8").strip()
            if ver:
                return ver.lstrip("v")
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=_get_repo_root(),
            capture_output=True,
            text=False,
            timeout=3,
        )
        stdout = _clean_process_output(result.stdout)
        if result.returncode == 0 and stdout:
            ver = stdout.lstrip("v")
            _write_version_marker(ver)
            return ver
    except Exception:
        pass

    from flocks import __version__

    return __version__


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
    sources = list(sources_override or ucfg.sources)

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


async def check_update(*, locale: str | None = None, region: str | None = None) -> VersionInfo:
    """Return version comparison info without performing any upgrade."""
    from flocks.updater.deploy import detect_deploy_mode

    current = get_current_version()
    mode = detect_deploy_mode()
    ucfg = await _get_updater_config()
    profile = _resolve_update_mirror_profile(
        ucfg.sources,
        region=region,
        locale=locale,
    )

    if not ucfg.enabled:
        return VersionInfo(
            current_version=current,
            deploy_mode=mode,
            update_allowed=(mode != "docker"),
        )

    try:
        tag, notes, url, zipball, tarball = await get_latest_release(
            repo=ucfg.repo,
            token=ucfg.token,
            sources_override=profile.sources,
        )
    except Exception as exc:
        log.warning("updater.check_failed", {"error": str(exc)})
        return VersionInfo(
            current_version=current,
            error="Failed to check for updates. Please check your network connection.",
            deploy_mode=mode,
            update_allowed=(mode != "docker"),
        )

    has_update = _parse_version(tag) > _parse_version(current)
    return VersionInfo(
        current_version=current,
        latest_version=tag,
        has_update=has_update,
        release_notes=notes,
        release_url=url,
        zipball_url=zipball,
        tarball_url=tarball,
        deploy_mode=mode,
        update_allowed=(mode != "docker"),
    )


# ------------------------------------------------------------------ #
# Perform upgrade
# ------------------------------------------------------------------ #


async def perform_update(
    latest_tag: str,
    *,
    zipball_url: str | None = None,
    tarball_url: str | None = None,
    restart: bool = True,
    locale: str | None = None,
    region: str | None = None,
) -> AsyncGenerator[UpdateProgress, None]:
    """
    Async generator that executes the upgrade steps and yields progress events.

    If *zipball_url* / *tarball_url* are provided (e.g. from a prior
    ``check_update`` call), the redundant Releases API round-trip is skipped.

    Sources are tried in the order configured in ``updater.sources``.
    If one source fails the download, the next source is tried automatically.
    """
    ucfg = await _get_updater_config()
    profile = _resolve_update_mirror_profile(
        ucfg.sources,
        region=region,
        locale=locale,
    )
    install_root = _get_repo_root()
    current_version = get_current_version()
    handover_prepared = False

    fmt = _choose_archive_format(ucfg.archive_format)

    # ------------------------------------------------------------------ #
    # Step 1 – download source archive
    # ------------------------------------------------------------------ #
    sources_desc = " → ".join(profile.sources)
    yield UpdateProgress(stage="fetching", message=f"Downloading source archive (sources: {sources_desc})...")

    tmp_dir = Path(tempfile.mkdtemp(prefix="flocks-update-"))
    if sys.platform == "win32":
        tmp_dir = _resolve_windows_long_path(tmp_dir)
    archive_filename = f"flocks-{latest_tag}.{fmt}"
    try:
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
        yield UpdateProgress(
            stage="error",
            message="Failed to download the update. Please check your network connection.",
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
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        msg = f"Failed to extract files: {exc}"
        if backup_path:
            msg += f"\nRestore from backup: {backup_path}"
        yield UpdateProgress(stage="error", message=msg, success=False)
        return

    # ------------------------------------------------------------------ #
    # Step 3 – prepare staged frontend
    # ------------------------------------------------------------------ #
    staged_webui_dir = content_root / "webui"
    if staged_webui_dir.is_dir() and (staged_webui_dir / "package.json").exists():
        npm = _find_executable("npm.cmd") or _find_executable("npm")
        if npm:
            yield UpdateProgress(stage="building", message="Installing frontend dependencies...")
            npm_env = {"npm_config_registry": profile.npm_registry} if profile.npm_registry else None
            code, _, err = await _run_async(
                [npm, "install"],
                cwd=staged_webui_dir,
                timeout=180,
                env=npm_env,
            )
            if code != 0:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                yield UpdateProgress(
                    stage="error",
                    message=f"Frontend dependency install failed: {err}",
                    success=False,
                )
                return

            yield UpdateProgress(stage="building", message="Building frontend...")
            code, _, err = await _run_async(
                [npm, "run", "build"],
                cwd=staged_webui_dir,
                timeout=300,
                env=npm_env,
            )
            if code != 0:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                yield UpdateProgress(
                    stage="error",
                    message=f"Frontend build failed: {err}",
                    success=False,
                )
                return
        else:
            log.warning(
                "updater.frontend.npm_not_found",
                {
                    "hint": ("Cannot prebuild frontend during upgrade because npm was not found"),
                },
            )
        dist_index = staged_webui_dir / "dist" / "index.html"
        if not dist_index.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            yield UpdateProgress(
                stage="error",
                message="Frontend build output is missing; upgrade aborted before cutover.",
                success=False,
            )
            return

    # ------------------------------------------------------------------ #
    # Step 4 – determine whether frontend handover is needed
    # ------------------------------------------------------------------ #
    needs_handover = staged_webui_dir.is_dir() and (staged_webui_dir / "package.json").exists()

    # ------------------------------------------------------------------ #
    # Step 5 – replace install tree
    # (Frontend proxy is still alive so the SSE stream keeps flowing.)
    # ------------------------------------------------------------------ #
    yield UpdateProgress(
        stage="applying",
        message=f"Applying v{latest_tag}...",
    )
    try:
        await asyncio.to_thread(
            _replace_install_dir,
            content_root,
            install_root,
        )
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if backup_path is not None:
            await asyncio.to_thread(
                _restore_backup_if_possible,
                backup_path,
                install_root,
                current_version,
            )
        msg = f"Failed to replace files: {exc}"
        if backup_path:
            msg += f"\nRestore from backup: {backup_path}"
        yield UpdateProgress(stage="error", message=msg, success=False)
        return

    # ------------------------------------------------------------------ #
    # Step 6 – sync dependencies
    # ------------------------------------------------------------------ #
    yield UpdateProgress(stage="syncing", message="Syncing dependencies...")

    uv_path = _find_executable("uv")
    if not uv_path:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if backup_path is not None:
            await asyncio.to_thread(
                _restore_backup_if_possible,
                backup_path,
                install_root,
                current_version,
            )
        hint = (
            "Dependency sync failed: uv is required but was not found. "
            "Please install uv (https://docs.astral.sh/uv/) and ensure it "
            "is in PATH (e.g. add ~/.local/bin to PATH for systemd services)."
        )
        if sys.platform == "win32":
            hint = "Dependency sync failed: uv is required to refresh the Windows project runtime."
        yield UpdateProgress(stage="error", message=hint, success=False)
        return

    log.info("updater.dependencies.sync", {"tool": "uv sync", "path": uv_path})
    uv_cmd = [uv_path, "sync"]
    if profile.uv_default_index:
        uv_cmd.extend(["--default-index", profile.uv_default_index])

    sync_env = _build_uv_sync_env()
    code, _, err = await _run_async(
        uv_cmd, cwd=install_root, timeout=180, env=sync_env,
    )
    if code != 0:
        log.warning("updater.dependencies.sync_retry", {"first_error": err})
        await asyncio.sleep(3)
        code, _, err = await _run_async(
            uv_cmd, cwd=install_root, timeout=180, env=sync_env,
        )

    if code != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if backup_path is not None:
            await asyncio.to_thread(
                _restore_backup_if_possible,
                backup_path,
                install_root,
                current_version,
            )
        yield UpdateProgress(stage="error", message=f"Dependency sync failed: {err}", success=False)
        return

    if sys.platform == "win32":
        validation_error = await _validate_windows_restart_runtime(install_root)
        if validation_error:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            if backup_path is not None:
                await asyncio.to_thread(
                    _restore_backup_if_possible,
                    backup_path,
                    install_root,
                    current_version,
                )
            yield UpdateProgress(stage="error", message=validation_error, success=False)
            return

    shutil.rmtree(tmp_dir, ignore_errors=True)
    _write_version_marker(latest_tag.lstrip("v"))

    try:
        _refresh_global_cli_entry(install_root)
    except Exception as exc:
        log.warning("updater.refresh_cli.failed", {"error": str(exc)})

    # ------------------------------------------------------------------ #
    # Step 7 – restart in-place (skipped when restart=False, e.g. CLI)
    # Send the "restarting" event while the proxy is still alive, then
    # perform the handover (kill frontend + start temp page) right
    # before os.execv so the SSE stream is not broken prematurely.
    #
    # CRITICAL: handover + execv run SYNCHRONOUSLY (no await) so that
    # CancelledError from client disconnect cannot interrupt between
    # killing the Vite proxy and calling os.execv.
    # ------------------------------------------------------------------ #
    if not restart:
        yield UpdateProgress(
            stage="done",
            message=f"Upgraded to v{latest_tag}",
            success=True,
        )
        return

    yield UpdateProgress(stage="restarting", message="Restarting service...")

    log.info(
        "updater.restart",
        {
            "tag": latest_tag,
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
        yield UpdateProgress(
            stage="error",
            message=f"Failed to build restart command: {exc}",
            success=False,
        )
        return

    if needs_handover:
        try:
            _prepare_upgrade_handover(latest_tag)
        except Exception as exc:
            log.error("updater.handover.failed", {"error": str(exc)})

    if sys.platform == "win32":
        log.info("updater.restart.spawn", {"argv": restart_argv})
        try:
            subprocess.Popen(
                restart_argv,
                cwd=install_root,
                close_fds=True,
            )
            os._exit(0)
        except OSError as exc:
            log.error("updater.restart.spawn_failed", {"error": str(exc)})
            if needs_handover:
                try:
                    rollback_upgrade_handover()
                except Exception:
                    pass
            yield UpdateProgress(
                stage="error",
                message=f"Failed to restart service: {exc}",
                success=False,
            )
            return

    log.info("updater.restart.execv", {"argv": restart_argv})
    try:
        os.execv(restart_argv[0], restart_argv)
    except OSError as exc:
        log.error("updater.restart.execv_failed", {"error": str(exc)})
        if needs_handover:
            try:
                rollback_upgrade_handover()
            except Exception:
                pass
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
        wrapper.write_text(
            f'@echo off\r\n"{venv_python}" -m flocks.cli.main %*',
            encoding="ascii",
        )
        stale_exe = link_dir / "flocks.exe"
        if stale_exe.exists():
            stale_exe.unlink(missing_ok=True)
    else:
        target = install_root / ".venv" / "bin" / "flocks"
        if not target.exists():
            return
        link = link_dir / "flocks"
        link.unlink(missing_ok=True)
        link.symlink_to(target)

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
    """Reconstruct the argv for ``os.execv`` so the process restarts correctly.

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
