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

import os
import re
import sys
import shutil
import asyncio
import tarfile
import tempfile
import zipfile
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import quote

import httpx

from flocks.updater.models import VersionInfo, UpdateProgress
from flocks.utils.log import Log

_DEFAULT_REPO = "AgentFlocks/Flocks"
_BACKUP_DIR = Path.home() / ".flocks" / "version"

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


# ------------------------------------------------------------------ #
# Async subprocess helpers
# ------------------------------------------------------------------ #

async def _run_async(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = 60,
) -> tuple[int, str, str]:
    """Run a subprocess in a thread pool so the async event loop stays free."""
    result = await asyncio.to_thread(
        subprocess.run,
        cmd,
        cwd=cwd or _get_repo_root(),
        capture_output=True,
        text=True,
        timeout=timeout,
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
        text=True,
    )
    return (
        result.returncode,
        _clean_process_output(result.stdout),
        _clean_process_output(result.stderr),
    )


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

    zip_url = f"https://gitee.com/api/v5/repos/{repo}/zipball?ref={raw_tag}"
    tar_url = f"https://gitee.com/api/v5/repos/{repo}/tarball?ref={raw_tag}"
    if token:
        zip_url += f"&access_token={token}"
        tar_url += f"&access_token={token}"
    return tag, notes, html_url, zip_url, tar_url


def _gitee_archive_url(repo: str, tag: str, fmt: str, gitee_token: str | None = None) -> str:
    """Gitee archive download via API endpoint."""
    raw_tag = tag if tag.startswith("v") else f"v{tag}"
    kind = "zipball" if fmt == "zip" else "tarball"
    url = f"https://gitee.com/api/v5/repos/{repo}/{kind}?ref={raw_tag}"
    if gitee_token:
        url += f"&access_token={gitee_token}"
    return url


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
                link: str | None = (
                    latest.get("_links", {}).get("self")
                    or f"{base}/{repo}/-/releases/{raw_tag}"
                )
                proj = repo.split("/")[-1]
                zip_url = f"{base}/{repo}/-/archive/{raw_tag}/{proj}-{raw_tag}.zip"
                tar_url = f"{base}/{repo}/-/archive/{raw_tag}/{proj}-{raw_tag}.tar.gz"
                return tag, notes, link, zip_url, tar_url

        tags_url = (
            f"{base}/api/v4/projects/{encoded}/repository/tags"
            "?order_by=version&sort=desc&per_page=1"
        )
        tags_resp = await client.get(tags_url, headers=headers, follow_redirects=True)

        if tags_resp.status_code == 200:
            tags = tags_resp.json()
            if tags:
                latest_tag_obj = tags[0]
                tag = latest_tag_obj.get("name", "").lstrip("v")
                raw_tag = latest_tag_obj.get("name", "")
                notes = (
                    latest_tag_obj.get("release", {}).get("description")
                    or latest_tag_obj.get("message")
                    or None
                )
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
        names = [
            line.split("\t")[1].removeprefix("refs/tags/")
            for line in out.splitlines()
            if "\t" in line
        ]
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
    dest = dest_dir / filename

    headers: dict[str, str] = {}
    if token and "gitee.com" not in url:
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
            log.warning("updater.download.source_failed", {
                "source": source_name,
                "url": url,
                "error": str(exc),
            })

    summary = "; ".join(errors) if errors else "No download sources configured"
    raise RuntimeError(summary)


def _backup_current_version(
    install_root: Path,
    current_version: str,
    retain_count: int = 5,
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

    exclude = {".venv", "node_modules", "__pycache__", ".git", "logs", "dist"}

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = info.name.split("/")
        for part in parts:
            if part in exclude:
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
    backups = sorted(
        [p for p in _BACKUP_DIR.iterdir() if p.name.startswith("flocks-") and p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[retain_count:]:
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

    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        time.sleep(0.05)
        func(path)
    except OSError:
        log.warning("updater.rmtree.skip_locked", {"path": str(path)})


def _safe_rmtree(target: Path) -> None:
    """rmtree with Windows permission-error fallback."""
    if sys.platform == "win32":
        shutil.rmtree(target, onerror=_rmtree_onerror)
    else:
        shutil.rmtree(target)


def _has_preserved_children(directory: Path) -> bool:
    """Check if *directory* directly contains any ``_PRESERVE_NAMES`` entries."""
    try:
        return any(child.name in _PRESERVE_NAMES for child in directory.iterdir())
    except OSError:
        return False


def _replace_install_dir(
    source_dir: Path,
    install_root: Path,
) -> None:
    """
    Overwrite *install_root* with the contents of *source_dir*, while
    preserving user/runtime directories listed in ``_PRESERVE_NAMES``
    at **any** directory depth (not only the top level).
    """
    for item in source_dir.iterdir():
        if item.name in _PRESERVE_NAMES:
            continue
        target = install_root / item.name
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                if item.is_dir() and _has_preserved_children(target):
                    _replace_install_dir(item, target)
                    source_names = {c.name for c in item.iterdir()}
                    for child in target.iterdir():
                        if child.name not in source_names and child.name not in _PRESERVE_NAMES:
                            if child.is_dir() and not child.is_symlink():
                                _safe_rmtree(child)
                            else:
                                child.unlink()
                    continue
                _safe_rmtree(target)
            else:
                target.unlink()
        if item.is_dir():
            shutil.copytree(item, target, symlinks=True)
        else:
            shutil.copy2(item, target)


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
            text=True,
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
    sources = ucfg.sources

    if provider:
        sources = [provider]

    last_error: Exception | None = None
    for source in sources:
        try:
            result = await _fetch_release_from_source(
                source, repo, token, ucfg.gitee_token, base_url,
                gitee_repo=ucfg.gitee_repo,
            )
            log.info("updater.release.fetched", {"source": source, "tag": result[0]})
            return result
        except Exception as exc:
            last_error = exc
            log.warning("updater.release.source_failed", {
                "source": source,
                "error": str(exc),
            })

    log.warning("updater.api_failed_fallback_git", {"error": str(last_error)})
    tag, _, _ = await _latest_tag_from_git_remote_async(ucfg.remote)
    if tag:
        return tag, None, None, None, None
    if last_error:
        raise last_error
    raise RuntimeError("No sources configured and git fallback failed")


async def check_update() -> VersionInfo:
    """Return version comparison info without performing any upgrade."""
    from flocks.updater.deploy import detect_deploy_mode

    current = get_current_version()
    mode = detect_deploy_mode()
    ucfg = await _get_updater_config()

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
) -> AsyncGenerator[UpdateProgress, None]:
    """
    Async generator that executes the upgrade steps and yields progress events.

    If *zipball_url* / *tarball_url* are provided (e.g. from a prior
    ``check_update`` call), the redundant Releases API round-trip is skipped.

    Sources are tried in the order configured in ``updater.sources``.
    If one source fails the download, the next source is tried automatically.
    """
    ucfg = await _get_updater_config()
    install_root = _get_repo_root()
    current_version = get_current_version()

    fmt = _choose_archive_format(ucfg.archive_format)

    # ------------------------------------------------------------------ #
    # Step 1 – download source archive
    # ------------------------------------------------------------------ #
    sources_desc = " → ".join(ucfg.sources)
    yield UpdateProgress(stage="fetching", message=f"Downloading {fmt} archive (sources: {sources_desc})...")

    tmp_dir = Path(tempfile.mkdtemp(prefix="flocks-update-"))
    archive_filename = f"flocks-{latest_tag}.{fmt}"
    try:
        archive_path = await _download_with_fallback(
            sources=ucfg.sources,
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

    # ------------------------------------------------------------------ #
    # Step 3 – extract and replace
    # ------------------------------------------------------------------ #
    yield UpdateProgress(
        stage="applying",
        message=f"Applying v{latest_tag}...",
    )

    extract_dir = tmp_dir / "extracted"
    extract_dir.mkdir()
    try:
        content_root = await asyncio.to_thread(
            _extract_archive, archive_path, extract_dir,
        )
        await asyncio.to_thread(
            _replace_install_dir, content_root, install_root,
        )
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        msg = f"Failed to replace files: {exc}"
        if backup_path:
            msg += f"\nRestore from backup: {backup_path}"
        yield UpdateProgress(stage="error", message=msg, success=False)
        return

    shutil.rmtree(tmp_dir, ignore_errors=True)

    _write_version_marker(latest_tag.lstrip("v"))

    # ------------------------------------------------------------------ #
    # Step 4 – sync dependencies
    # ------------------------------------------------------------------ #
    yield UpdateProgress(stage="syncing", message="Syncing dependencies...")

    uv_path = _find_executable("uv")
    if uv_path:
        code, _, err = await _run_async([uv_path, "sync"], cwd=install_root, timeout=120)
    else:
        code, _, err = await _run_async(
            [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
            cwd=install_root,
            timeout=120,
        )
    if code != 0:
        yield UpdateProgress(stage="error", message=f"Dependency sync failed: {err}", success=False)
        return

    # ------------------------------------------------------------------ #
    # Step 5 – build frontend
    # ------------------------------------------------------------------ #
    webui_dir = install_root / "webui"
    if webui_dir.is_dir() and (webui_dir / "package.json").exists():
        npm = _find_executable("npm.cmd") or _find_executable("npm")
        if npm:
            yield UpdateProgress(stage="building", message="Installing frontend dependencies...")
            code, _, err = await _run_async(
                [npm, "install"],
                cwd=webui_dir,
                timeout=180,
            )
            if code != 0:
                yield UpdateProgress(
                    stage="error",
                    message=f"Frontend dependency install failed: {err}",
                    success=False,
                )
                return

            yield UpdateProgress(stage="building", message="Building frontend...")
            code, _, err = await _run_async(
                [npm, "run", "build"],
                cwd=webui_dir,
                timeout=300,
            )
            if code != 0:
                yield UpdateProgress(
                    stage="error",
                    message=f"Frontend build failed: {err}",
                    success=False,
                )
                return
        else:
            log.warning("updater.frontend.npm_not_found", {
                "hint": "Skipping frontend build — npm not found; run 'npm install && npm run build' manually after upgrade",
            })

    # ------------------------------------------------------------------ #
    # Step 6 – restart in-place
    # ------------------------------------------------------------------ #
    yield UpdateProgress(stage="restarting", message="Restarting service...")

    log.info("updater.restart", {
        "tag": latest_tag,
        "sources": ucfg.sources,
        "repo": ucfg.repo,
    })
    await asyncio.sleep(0.8)

    if "--reload" in sys.argv:
        log.info("updater.restart.reload_exit3")
        sys.exit(3)
    else:
        restart_argv = _build_restart_argv()
        log.info("updater.restart.execv", {"argv": restart_argv})
        os.execv(restart_argv[0], restart_argv)


def _build_restart_argv() -> list[str]:
    """
    Reconstruct the argv for os.execv so the process restarts correctly.

    Handles two edge cases:
    1. __main__.py path → reconstruct ``-m module`` form
    2. --reload flags → strip them to avoid a second reloader
    """
    argv0 = sys.argv[0]
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

    if argv0.endswith("__main__.py"):
        pkg_dir = Path(argv0).parent
        parts: list[str] = []
        current = pkg_dir
        while (current / "__init__.py").exists():
            parts.insert(0, current.name)
            current = current.parent

        if parts:
            module = ".".join(parts)
            log.info("updater.restart.module_mode", {
                "module": module,
                "reload_stripped": len(rest) - len(clean_rest),
            })
            return [sys.executable, "-m", module] + clean_rest

    return [sys.executable, argv0] + clean_rest


def _find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    repo_root = _get_repo_root()
    venv_candidates = [
        repo_root / ".venv" / "bin" / name,
        repo_root / ".venv" / "Scripts" / name,
    ]
    for candidate in venv_candidates:
        if candidate.exists():
            return str(candidate)
    return None
