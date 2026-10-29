"""
The way from this open install to the enterprise edition: what the Flocks Pro page asks.

The enterprise edition is an install of its own: ``flockspro-<release>.run`` puts its core, its
runtime and its own PostgreSQL in /opt/flockspro, takes over this install's data (moved into
PostgreSQL on its first start) and the licence the machine has, and is run with the same
``flocks start|stop|status|restart|logs|update``. The Python enterprise package installed into this
runtime before (``flockspro`` 2026.10 and earlier) gets no more releases: a machine that has it keeps
it running on this open release until that installer moves the machine.

For the page this module says where the machine stands, asks Console for the installer built for
the machine's licence (``installers`` in the licence's manifest, one ``{platform, url, sha256,
release_id, version}`` each), downloads and checks it, and starts it when this account may run it:
an install of its own, run by its own supervisor (``flocks start``). A machine run by systemd (the
open edition's offline package) is switched by root; the page gives the command.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urlparse

import httpx

from flocks.updater.deploy import declared_managed, detect_deploy_mode
from flocks.utils.log import Log

log = Log.create(service="updater.enterprise")

#: Where the enterprise installer puts the release (deploy/release/installer.sh in flockspro).
INSTALL_ROOT = Path("/opt/flockspro")
#: The Python enterprise package of before.
PREVIOUS_DISTRIBUTION = "flockspro"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")
_LOG_TAIL_LINES = 40


class EnterpriseUpgradeError(Exception):
    """A refusal the page shows as it is."""


@dataclass(frozen=True)
class InstallerOffer:
    """The installer Console offers for this machine's licence and platform."""

    url: str
    sha256: str
    release_id: str
    version: str
    size: int | None = None

    @property
    def filename(self) -> str:
        return f"flockspro-{self.release_id}.run"


def runtime_root() -> Path:
    override = (os.environ.get("FLOCKS_ROOT") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".flocks"


def work_dir() -> Path:
    """Where the installer is downloaded to and its run recorded."""
    return runtime_root() / "run" / "enterprise-upgrade"


def platform_id() -> str:
    """This machine as the installers are named: linux-arm64, linux-amd64, darwin-arm64, ..."""
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)
    return f"{sys.platform}-{arch}"


def available() -> bool:
    """A Unix machine (the installer is a shell one) not run by the enterprise core already,
    which has its own page and upgrades."""
    return os.name == "posix" and not declared_managed()


def _euid() -> int:
    return os.geteuid() if hasattr(os, "geteuid") else -1


def _account() -> tuple[str, str]:
    try:
        import grp
        import pwd
    except ImportError:  # not Unix
        name = os.environ.get("USERNAME") or "user"
        return name, name
    try:
        user = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        user = str(os.geteuid())
    try:
        group = grp.getgrgid(os.getegid()).gr_name
    except KeyError:
        group = str(os.getegid())
    return user, group


def run_by_systemd() -> bool:
    """This open install is a systemd service (the offline package's flocks.service)."""
    return bool(os.environ.get("INVOCATION_ID")) or detect_deploy_mode() == "offline"


def _previous_licence_files() -> list[Path]:
    """Where the Python enterprise package kept its licence: under FLOCKS_ROOT (its cloud
    checker), or under the home's .flocks whatever FLOCKS_ROOT says (its offline checker)."""
    files = [runtime_root() / "flockspro" / "license.json"]
    home = Path.home() / ".flocks" / "flockspro" / "license.json"
    if home not in files:
        files.append(home)
    return files


def previous_licence_id(status: dict[str, Any] | None = None) -> str:
    """The licence id the Python enterprise package recorded on this machine, if any: what the
    page shows and what Console is asked for, one answer for both."""
    override = (os.environ.get("FLOCKSPRO_LICENSE_ID") or "").strip()
    if override:
        return override
    for path in _previous_licence_files():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        found = str(data.get("license_id") or payload.get("license_id") or "").strip()
        if found:
            return found
    return str((status or {}).get("license_id") or "").strip()


def _previous_installed() -> bool:
    try:
        importlib.metadata.version(PREVIOUS_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def _previous_status() -> dict[str, Any]:
    """The Python enterprise package's own word on its licence, when it is installed here."""
    try:
        from flockspro.license.runtime import get_license_status  # type: ignore[import-not-found]

        status = get_license_status()
    except Exception as exc:  # an old package that does not load on this release
        log.warning("enterprise.previous_status_failed", {"error": str(exc)})
        return {}
    return status if isinstance(status, dict) else {}


def previous_edition() -> dict[str, Any] | None:
    """The Python enterprise package of before on this machine: its version and licence."""
    if not available():
        return None
    try:
        version: str | None = importlib.metadata.version(PREVIOUS_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        version = None
    status = _previous_status() if version is not None else {}
    licence_id = previous_licence_id(status)
    if version is None and not licence_id:
        return None
    return {
        "version": version,
        "license_id": licence_id or None,
        "license_status": status.get("license_status"),
        "pro_enabled": bool(status.get("pro_enabled")),
        "expires_at": status.get("expires_at"),
    }


def install_root_ready() -> bool:
    """This account may make or write the install root, as the installer needs."""
    if _euid() == 0:
        return True
    return INSTALL_ROOT.is_dir() and os.access(INSTALL_ROOT, os.W_OK | os.X_OK)


def prepare_command() -> str | None:
    """What root runs once so this account may install (installer.sh says the same)."""
    if _euid() == 0 or run_by_systemd() or install_root_ready():
        return None
    user, group = _account()
    return f"sudo install -d -o {shlex.quote(user)} -g {shlex.quote(group)} -m 0755 {INSTALL_ROOT}"


def installer_command(path_or_name: str, *, quote: bool = True) -> str:
    """How the installer is run by hand: as root on a systemd machine, as this account otherwise."""
    run = f"bash {shlex.quote(path_or_name) if quote else path_or_name}"
    return f"sudo {run}" if run_by_systemd() and _euid() != 0 else run


def situation() -> dict[str, Any]:
    """Where this machine stands, without asking anyone: what the page draws first."""
    user, _ = _account()
    systemd = run_by_systemd()
    return {
        "available": available(),
        "previous": previous_edition(),
        "platform": platform_id(),
        "account": user,
        "root": _euid() == 0,
        "systemd": systemd,
        "install_root": str(INSTALL_ROOT),
        "install_root_ready": install_root_ready(),
        "prepare_command": prepare_command(),
        # The page starts the installer itself only where this account runs the install.
        "can_start": available() and not systemd and install_root_ready(),
        # The installer is a shell one: nothing to run by hand on another system.
        "manual_command": installer_command(f"flockspro-<版本>-{platform_id()}.run", quote=False) if os.name == "posix" else "",
        "upgrade": read_state(),
    }


def _console_base_url() -> str:
    raw = (os.environ.get("FLOCKS_CONSOLE_BASE_URL") or "").strip().rstrip("/")
    if raw and not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw


async def _console_session_token() -> str:
    try:
        from flocks.console.login import ConsoleLoginService

        session = await ConsoleLoginService.get_console_session()
    except Exception:
        return ""
    return str((session or {}).get("console_session_token") or "").strip()


def offered_installer(manifest: dict[str, Any], base_url: str) -> InstallerOffer | None:
    """This platform's entry of the manifest's ``installers``; None when it has none."""
    entries = manifest.get("installers")
    if not isinstance(entries, list):
        return None
    wanted = platform_id()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("platform") != wanted:
            continue
        url = str(entry.get("url") or "").strip()
        offer = InstallerOffer(
            url=url,
            sha256=str(entry.get("sha256") or "").strip().lower(),
            release_id=str(entry.get("release_id") or "").strip(),
            version=str(entry.get("version") or "").strip(),
            size=entry.get("size") if isinstance(entry.get("size"), int) else None,
        )
        scheme = urlparse(url).scheme
        # Over https; plain http only from a Console that is itself reached that way (a test one).
        if scheme != "https" and not (scheme == "http" and base_url.startswith("http://")):
            raise EnterpriseUpgradeError(f"Console 给的 {wanted} 安装包地址不是 https：{url or '（空）'}")
        if not (_SHA256_RE.match(offer.sha256) and _RELEASE_ID_RE.match(offer.release_id) and _VERSION_RE.match(offer.version)):
            raise EnterpriseUpgradeError(f"Console 给的 {wanted} 安装包信息不完整")
        return offer
    return None


async def fetch_offer() -> InstallerOffer:
    """Ask Console for the installer built for this machine's licence."""
    base = _console_base_url()
    if not base:
        raise EnterpriseUpgradeError("这台机器没有配置 Console（FLOCKS_CONSOLE_BASE_URL），请用手动的方法升级")
    licence_id = previous_licence_id()
    if not licence_id and _previous_installed():
        licence_id = previous_licence_id(await asyncio.to_thread(_previous_status))
    if not licence_id:
        raise EnterpriseUpgradeError("这台机器上没有企业版授权，Console 不知道该给哪个安装包；请联系我们获取授权和安装包")
    channel = (os.environ.get("FLOCKS_UPDATE_CHANNEL") or "flockspro").strip() or "flockspro"
    url = f"{base}/v1/manifest/latest?{urlencode({'channel': channel, 'license_id': licence_id})}"
    headers = {"x-license-id": licence_id}
    token = await _console_session_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise EnterpriseUpgradeError(f"连不上 Console：{exc}") from exc
    if resp.status_code in (401, 403):
        raise EnterpriseUpgradeError("Console 要先登录才给安装包：点「登录 Console」后再检查")
    if resp.status_code >= 400:
        raise EnterpriseUpgradeError(f"Console 回答 HTTP {resp.status_code}")
    try:
        manifest = resp.json()
    except ValueError as exc:
        raise EnterpriseUpgradeError("Console 的回答不是 JSON") from exc
    if not isinstance(manifest, dict):
        raise EnterpriseUpgradeError("Console 的回答格式不对")
    offer = offered_installer(manifest, base)
    if offer is None:
        raise EnterpriseUpgradeError(
            f"Console 还没有给授权 {licence_id} 的新企业版安装包（{platform_id()}）；请联系我们，或用手动的方法升级"
        )
    return offer


# --- the run --------------------------------------------------------------------------------

def _state_file() -> Path:
    return work_dir() / "state.json"


def _write_state(state: dict[str, Any]) -> None:
    work = work_dir()
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)
    tmp = work / "state.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _state_file())


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _log_tail(path: Path) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 64 * 1024))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-_LOG_TAIL_LINES:]


def read_state() -> dict[str, Any] | None:
    """The last upgrade started from the page: where it is now."""
    try:
        state = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    if state.get("phase") == "downloading" and not _run_lock.locked():
        # The process that downloaded it is gone (the server restarted under it).
        state["phase"] = "failed"
        state["message"] = "下载没有完成（服务中途重启了），请重新点升级"
    if state.get("phase") == "installing":
        exit_file = work_dir() / "exit-status"
        try:
            code = int(exit_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            code = None
        if code is not None:
            state["phase"] = "finished" if code == 0 else "failed"
            state["exit_code"] = code
        elif not _pid_alive(int(state.get("pid") or 0)):
            # Gone without saying how it ended (the machine restarted under it).
            state["phase"] = "failed"
    log_file = state.get("log")
    if isinstance(log_file, str) and log_file:
        state["log_tail"] = _log_tail(Path(log_file))
    return state


async def download(offer: InstallerOffer, progress: Callable[[int, int | None], Awaitable[None]] | None = None) -> Path:
    """Download the installer into work_dir and check its digest; the path of the checked file."""
    work = work_dir()
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)
    final = work / offer.filename
    part = work / (offer.filename + ".part")
    digest = hashlib.sha256()
    received = 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as client:
            async with client.stream("GET", offer.url, follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    raise EnterpriseUpgradeError(f"下载安装包失败：HTTP {resp.status_code}")
                total = offer.size or int(resp.headers.get("content-length") or 0) or None
                with part.open("wb") as out:
                    os.chmod(part, 0o600)
                    async for chunk in resp.aiter_bytes(1 << 20):
                        out.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if progress is not None:
                            await progress(received, total)
    except httpx.HTTPError as exc:
        part.unlink(missing_ok=True)
        raise EnterpriseUpgradeError(f"下载安装包失败：{exc}") from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if digest.hexdigest() != offer.sha256:
        part.unlink(missing_ok=True)
        raise EnterpriseUpgradeError("下载的安装包校验不对（sha256 和 Console 给的不一样），没有运行")
    os.replace(part, final)
    os.chmod(final, 0o700)
    return final


def _installer_environment() -> dict[str, str]:
    """This process's settings for the installer (it keeps the FLOCKS* ones), without what
    points Python at this runtime."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "UV_")) and key not in ("VIRTUAL_ENV", "FLOCKSPRO_RUN_DIR")
    }
    env.setdefault("HOME", str(Path.home()))
    return env


def start_installer(path: Path, offer: InstallerOffer) -> dict[str, Any]:
    """Run the installer apart from this server: install-managed stops this open install's
    supervisor, and this process with it, before the enterprise edition starts."""
    if not available():
        raise EnterpriseUpgradeError("这台机器已经是企业版")
    if run_by_systemd():
        raise EnterpriseUpgradeError(f"这台机器由 systemd 管，要以 root 执行：{installer_command(str(path))}")
    if not install_root_ready():
        raise EnterpriseUpgradeError(f"先用 root 执行一次：{prepare_command()}")
    work = work_dir()
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)
    log_file = work / "install.log"
    exit_file = work / "exit-status"
    exit_file.unlink(missing_ok=True)
    with log_file.open("ab") as out:
        os.chmod(log_file, 0o600)
        out.write(f"\n== {time.strftime('%Y-%m-%dT%H:%M:%S%z')} bash {path.name}\n".encode())
        out.flush()
        # The exit status is written by a shell of the installer's own session, which outlives this one.
        process = subprocess.Popen(
            ["/bin/sh", "-c", 'bash "$1"; echo "$?" > "$2"', "sh", str(path), str(exit_file)],
            cwd=str(work),
            env=_installer_environment(),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    state = {
        "phase": "installing",
        "pid": process.pid,
        "release_id": offer.release_id,
        "version": offer.version,
        "installer": str(path),
        "log": str(log_file),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _write_state(state)
    log.info("enterprise.installer_started", {"pid": process.pid, "release_id": offer.release_id})
    return state


_run_lock = asyncio.Lock()


async def upgrade(progress: Callable[[dict[str, Any]], Awaitable[None]]) -> dict[str, Any]:
    """Ask Console, download, check and start the installer; ``progress`` hears each step."""
    if _run_lock.locked():
        raise EnterpriseUpgradeError("已经在升级了")
    async with _run_lock:
        current = read_state()
        if current is not None and current.get("phase") == "installing":
            raise EnterpriseUpgradeError("安装包已经在运行了")
        offer = await fetch_offer()
        base = {"release_id": offer.release_id, "version": offer.version, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        _write_state({**base, "phase": "downloading", "received": 0, "total": offer.size})
        await progress({"stage": "downloading", "version": offer.version, "release_id": offer.release_id})
        recorded = 0.0

        async def downloaded(received: int, total: int | None) -> None:
            nonlocal recorded
            await progress({"stage": "downloading", "received": received, "total": total})
            if time.monotonic() - recorded >= 1.0:
                recorded = time.monotonic()
                _write_state({**base, "phase": "downloading", "received": received, "total": total})

        try:
            path = await download(offer, downloaded)
        except BaseException as exc:
            _write_state({**base, "phase": "failed", "message": str(exc) or "下载没有完成"})
            raise
        await progress({"stage": "downloaded", "installer": str(path)})
        if run_by_systemd() or not install_root_ready():
            state = {
                "phase": "command",
                "release_id": offer.release_id,
                "version": offer.version,
                "installer": str(path),
                "command": installer_command(str(path)),
                "prepare_command": prepare_command(),
            }
            _write_state(state)
            return state
        state = start_installer(path, offer)
        await progress({"stage": "installing", **state})
        return state


def offer_dict(offer: InstallerOffer) -> dict[str, Any]:
    return {**asdict(offer), "filename": offer.filename}
