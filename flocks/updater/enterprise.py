"""
The way from this open install to the enterprise edition: what the Flocks Pro page asks.

The enterprise edition is an install of its own: ``flockspro-<release>.run`` puts its core, its
runtime and its own PostgreSQL in /opt/flockspro, takes over this install's data (moved into
PostgreSQL on its first start) and the licence the machine has, and is run with the same
``flocks start|stop|status|restart|logs|update``. On Windows ``flockspro-<release>.exe`` puts it in
Program Files and runs it as the FlocksPro Core service; it copies this install's data and asks
Windows for an administrator itself. The Python enterprise package installed into this
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
import base64
import contextlib
import functools
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
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode, urlparse

import httpx

from flocks.updater.deploy import declared_managed, detect_deploy_mode
from flocks.updater.handoff_progress import summarize_error
from flocks.updater.installation_status import (
    installation_result_id,
    installation_result_path,
    read_installation_result,
    scrub_credentials,
)
from flocks.utils.log import Log

log = Log.create(service="updater.enterprise")

_WINDOWS = os.name == "nt"
#: Where the enterprise installer puts the release (deploy/release/installer.sh in flockspro; on
#: Windows its install_managed_windows.go: Program Files).
INSTALL_ROOT = (
    Path(os.environ.get("ProgramFiles") or r"C:\Program Files") / "FlocksPro" if _WINDOWS else Path("/opt/flockspro")
)
#: The one-file installer: a shell one on Unix, a program on Windows.
INSTALLER_SUFFIX = ".exe" if _WINDOWS else ".run"
#: The Python enterprise package of before.
PREVIOUS_DISTRIBUTION = "flockspro"
#: Under the data directory's run/: what Console says of the release the installer puts in place, for
#: that edition's "what's new" notice (flockspro.updater.managed reads this file, in this format).
RELEASE_NOTES_FILE = "managed-release-notes.json"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")
_LOG_TAIL_LINES = 40
#: What is shown of the installer's output once it has failed: the page and the dialog scroll it.
_FAILED_LOG_LINES = 200
#: The line start_installer writes in the log ahead of each run.
_RUN_HEADER = "== "
_TERMINAL_CODES = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ERROR_LINE = re.compile(
    r"失败|错误|无法|建不了|取不出|error|fail|fatal|panic|traceback|exception|cannot|can't|could not|couldn't|did not|not found|no such|denied|refus|no space",
    re.IGNORECASE,
)


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
    #: What Console tells customers of this release; None when it says nothing (an older Console).
    release_notes: str | None = None

    @property
    def filename(self) -> str:
        return f"flockspro-{self.release_id}{INSTALLER_SUFFIX}"


def runtime_root() -> Path:
    override = (os.environ.get("FLOCKS_ROOT") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".flocks"


def work_dir() -> Path:
    """Where the installer is downloaded to and its run recorded."""
    return runtime_root() / "run" / "enterprise-upgrade"


def platform_id() -> str:
    """This machine as the installers are named: linux-arm64, linux-amd64, darwin-arm64,
    windows-amd64, ..."""
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)
    system = "windows" if sys.platform == "win32" else sys.platform
    return f"{system}-{arch}"


def available() -> bool:
    """A Unix machine (the installer is a shell one) or a Windows one (a program), not run by the
    enterprise core already, which has its own page and upgrades."""
    return os.name in ("posix", "nt") and not declared_managed()


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
    """This open install is a systemd service that would take an installer started from here down
    with it (the offline package's flocks.service): root has to run the installer."""
    if detect_deploy_mode() == "offline":
        return True
    return bool(os.environ.get("INVOCATION_ID")) and not _installer_outlives_service()


@functools.lru_cache(maxsize=1)
def _installer_outlives_service() -> bool:
    """Whether an installer started from this service runs on when the service stops, as it does in
    a service of KillMode=none or process, or one that stays active with its processes gone
    (a `flocks start` run by a oneshot unit). install-managed stops the service; systemd then ends
    what is left in its control group, the installer among it, unless told otherwise. Not knowing
    counts as no."""
    service = _own_service()
    if not service:
        return False
    try:
        answer = subprocess.run(
            ["systemctl", "show", service, "--property=KillMode,Type,RemainAfterExit"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if answer.returncode != 0:
        return False
    properties = dict(line.split("=", 1) for line in answer.stdout.splitlines() if "=" in line)
    if properties.get("KillMode") in ("none", "process"):
        return True
    # Stopped by name it takes the installer along: install-managed does that to this one unit.
    return service != "flocks.service" and properties.get("Type") == "oneshot" and properties.get("RemainAfterExit") == "yes"


def _own_service() -> str | None:
    """The systemd service this process runs in, by its control group."""
    try:
        groups = Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in groups.splitlines():
        for part in reversed(line.rsplit(":", 1)[-1].split("/")):
            if part.endswith(".service"):
                return part
    return None


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


def _licence_file_terms(licence_id: str) -> dict[str, Any]:
    """What the licence file of *licence_id* says of itself, for the page: a licence taken here has
    no Python package to answer for it. Read, not verified; the enterprise core judges the licence
    when the machine moves."""
    for path in _previous_licence_files():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        terms = dict(data["payload"]) if isinstance(data.get("payload"), dict) else {}
        if str(data.get("license_id") or terms.get("license_id") or "").strip() != licence_id:
            continue
        for field in ("key", "token", "activate_key"):
            parts = str(data.get(field) or "").split(".")
            if len(parts) != 3:
                continue
            try:
                claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(claims, dict):
                terms = {**claims, **terms}
            break
        return {
            **terms,
            "activated_at": data.get("activated_at"),
            "install_id": data.get("install_id"),
            "fingerprint": data.get("fingerprint"),
        }
    return {}


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
    terms = _licence_file_terms(licence_id) if licence_id else {}
    # The package's word on another licence than the one named here says nothing of this one.
    if status.get("license_id") and status.get("license_id") != licence_id:
        status = {"pro_enabled": status.get("pro_enabled")}
    return {
        "version": version,
        "license_id": licence_id or None,
        "license_status": status.get("license_status") or terms.get("status"),
        "pro_enabled": bool(status.get("pro_enabled")),
        "expires_at": status.get("expires_at") or terms.get("expires_at"),
        "max_admins": status.get("max_admins") or terms.get("max_admins"),
        "max_members": status.get("max_members") or terms.get("max_members"),
        "last_sync_at": status.get("last_sync_at") or status.get("last_heartbeat_ok_at") or terms.get("activated_at"),
        "install_id": status.get("install_id") or terms.get("install_id"),
        "fingerprint": status.get("fingerprint") or terms.get("fingerprint"),
    }


def install_dir() -> Path:
    """Where the installer puts the release for this account (deploy/release/installer.sh in
    flockspro chooses the same): the default where this account may write it or runs from it, else
    a directory of its own, so a machine set up without root needs none for this either."""
    if _WINDOWS or _euid() == 0 or (INSTALL_ROOT / "current").exists():
        return INSTALL_ROOT
    if INSTALL_ROOT.is_dir() and os.access(INSTALL_ROOT, os.W_OK | os.X_OK):
        return INSTALL_ROOT
    return Path.home() / "flockspro"


def install_root_ready() -> bool:
    """This account may make or write the directory the install goes to, as the installer needs. On
    Windows the installer asks for an administrator itself (the system's confirmation), so it
    always may."""
    if _WINDOWS or _euid() == 0:
        return True
    target = install_dir()
    return os.access(target if target.is_dir() else target.parent, os.W_OK | os.X_OK)


def _default_root_ready() -> bool:
    """This account may make or write the default directory."""
    if _WINDOWS or _euid() == 0:
        return True
    return os.access(INSTALL_ROOT if INSTALL_ROOT.is_dir() else INSTALL_ROOT.parent, os.W_OK | os.X_OK)


def installer_relocatable(path: Path) -> bool:
    """What the installer says of itself (make_installer.sh, from the signed release): one for a
    customer, or from before this, goes to the default directory and nowhere else."""
    try:
        with path.open("rb") as installer:
            return b"\nrelocatable=1\n" in installer.read(1 << 16)
    except OSError:
        return False


def prepare_command(*, default_root: bool = False) -> str | None:
    """What root runs once so this account may install (installer.sh says the same); default_root
    when the installer goes nowhere but the default directory."""
    if _euid() == 0 or run_by_systemd() or (_default_root_ready() if default_root else install_root_ready()):
        return None
    user, group = _account()
    return f"sudo install -d -o {shlex.quote(user)} -g {shlex.quote(group)} -m 0755 {INSTALL_ROOT}"


def installer_command(path_or_name: str) -> str:
    """How root runs the installer on a machine the page cannot run it from (a systemd one, whose
    service stopping would take the installer with it); on Windows from PowerShell, as an
    administrator (Windows asks)."""
    if _WINDOWS:
        # This install's data directory and flocks command go with it, as when the page starts it:
        # the administrator who confirms may have another profile, FLOCKS_ROOT another directory.
        arguments = subprocess.list2cmdline(_windows_open_edition_args())
        return f"Start-Process -Verb RunAs -FilePath {_powershell_quote(path_or_name)} -ArgumentList {_powershell_quote(arguments)}"
    run = f"bash {shlex.quote(path_or_name)}"
    return f"sudo {run}" if run_by_systemd() and _euid() != 0 else run


def _powershell_quote(value: str) -> str:
    """A PowerShell single-quoted string: every character it takes for a single quote doubled
    (the ASCII one and the typographic ones, U+2018 to U+201B)."""
    return "'" + re.sub("['\u2018\u2019\u201a\u201b]", lambda match: match.group(0) * 2, value) + "'"


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
        "install_root": str(install_dir()),
        "install_root_ready": install_root_ready(),
        "prepare_command": prepare_command(),
        # The page starts the installer itself only where this account runs the install.
        "can_start": available() and not systemd and install_root_ready(),
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


async def _forget_console_session(refused_token: str) -> None:
    try:
        from flocks.console.login import ConsoleLoginService

        await ConsoleLoginService.forget_console_session(refused_token)
    except Exception as exc:  # the refusal is said all the same
        log.warning("enterprise.console_session_not_forgotten", {"error": str(exc)})


def _detail(resp: httpx.Response) -> str:
    """What Console said about a refusal (its ``detail``), when it said anything readable."""
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        return ""
    return str(detail).strip() if detail else ""


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
            release_notes=entry.get("release_notes") if isinstance(entry.get("release_notes"), str) else None,
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
        raise EnterpriseUpgradeError("这台机器没有配置 Console（FLOCKS_CONSOLE_BASE_URL），查不到新企业版")
    licence_id = previous_licence_id()
    if not licence_id and _previous_installed():
        licence_id = previous_licence_id(await asyncio.to_thread(_previous_status))
    if not licence_id:
        raise EnterpriseUpgradeError("这台机器上没有企业版授权，Console 不知道该给哪个安装包；请联系我们获取授权和安装包")
    channel = (os.environ.get("FLOCKS_UPDATE_CHANNEL") or "flockspro").strip() or "flockspro"
    url = f"{base}/v1/manifest/latest?{urlencode({'channel': channel, 'license_id': licence_id})}"
    # Licence ids are free-form and a header only carries ASCII: the query above names it either way.
    headers = {"x-license-id": licence_id} if licence_id.isascii() else {}
    token = await _console_session_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise EnterpriseUpgradeError(f"连不上 Console：{exc}") from exc
    if resp.status_code == 401:
        if token:
            # Console no longer takes this sign-in: forget it, so the page offers to sign in again.
            await _forget_console_session(token)
        raise EnterpriseUpgradeError("Console 要先登录才给安装包：点「云账号登录」后再检查")
    if resp.status_code == 403:
        # A sign-in Console takes, refused this (a licence of another account, say): it stays.
        raise EnterpriseUpgradeError(f"Console 不给这台机器安装包：{_detail(resp) or 'HTTP 403'}")
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
            f"Console 还没有给授权 {licence_id} 的新企业版安装包（{platform_id()}）；请联系我们"
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
    if _WINDOWS:
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_alive(pid: int) -> bool:
    """Whether a process runs, asked of Windows: os.kill there ends the process it names."""
    if pid <= 0:
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    process_query_limited_information, error_access_denied, still_active = 0x1000, 5, 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        # There, but not this account's to look at (an administrator's).
        return ctypes.get_last_error() == error_access_denied
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _log_tail(path: Path, lines: int = _LOG_TAIL_LINES) -> list[str]:
    """The end of the installer's log, of its last run only: the log is added to run after run."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 64 * 1024))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    # Credentials go first, from the whole text: a value can be on the line after its name.
    found = scrub_credentials(_TERMINAL_CODES.sub("", text)).splitlines()
    for index in range(len(found) - 1, -1, -1):
        if found[index].startswith(_RUN_HEADER):
            found = found[index:]
            break
    return found[-lines:]


def _installer_reason(lines: list[str]) -> str | None:
    """Why the installer stopped, where it says: its refusal, else the last line that reads as an
    error. Not simply the last line, which may be it cleaning up: no reason is better than a wrong one."""
    found = [line.strip() for line in lines if line.strip() and not line.startswith(_RUN_HEADER)]
    for index in range(len(found) - 1, -1, -1):
        if found[index].startswith("安装失败："):
            # All of it: a refusal can say what to run on the lines after, and it exits right after saying it.
            return summarize_error(" ".join(found[index:]))
    for text in reversed(found):
        if _ERROR_LINE.search(text):
            return summarize_error(text)
    return None


def _prior_installation_result() -> str | None:
    """The record there is as a run begins: what it records after that is another."""
    try:
        return installation_result_id()
    except (OSError, ValueError):
        return "unreadable"


def _installation_outcome(state: dict[str, Any]) -> dict[str, Any] | None:
    """What the installer recorded of this run: it records that it is switching the machine over
    before it stops anything, and how far the machine was put back if that fails. None when it has
    recorded nothing since the run began; recovery unknown when nobody can tell."""
    # Not noted, or not readable when it was: another id does not show this run wrote it.
    if state.get("prior_result", "unreadable") == "unreadable":
        return {"recovery": "unknown"}
    try:
        if installation_result_id() == state["prior_result"]:
            return None
    except (OSError, ValueError):
        return {"recovery": "unknown"}
    # A record of this run that is no failure's: it stopped in the middle of switching the machine over.
    return read_installation_result() or {"recovery": "unknown"}


def _explain_failure(state: dict[str, Any]) -> None:
    """Where a failed upgrade stopped and why, for whoever reads it: the step, the reason in the
    installer's words, how the machine was left and the end of the log."""
    installing = bool(state.get("log") or state.get("pid"))
    step = state.setdefault("failed_step", "install" if installing else "download")
    log_file = state.get("log")
    lines = _log_tail(Path(log_file), _FAILED_LOG_LINES) if isinstance(log_file, str) and log_file else []
    if lines:
        state["log_tail"] = lines
    message = state.get("message")
    if isinstance(message, str):
        message = state["message"] = scrub_credentials(message)
    if step != "install":
        # Nothing of the machine is touched before the installer runs.
        state["recovery"] = "unchanged"
        state["reason"] = message
        return
    ended = "exit_code" in state or bool(message)
    reason = message
    if reason is None:
        if state.get("from") == "terminal":
            reason = "这次是在终端里运行的，原因在终端的输出里"
        elif "exit_code" in state:
            reason = _installer_reason(lines)
        else:
            reason = "安装程序中途退出了，没有留下退出码（机器或服务可能重启过）"
    if not state.get("recovery"):
        outcome = _installation_outcome(state)
        if outcome is None:
            # Nothing recorded: it never got as far as stopping anything, if it said how it ended.
            state["recovery"] = "unchanged" if ended else "unknown"
        else:
            state["recovery"] = outcome.get("recovery") or "unknown"
            reason = outcome.get("detail") or reason
    state["reason"] = reason


@contextlib.contextmanager
def upgrade_lock():
    """One upgrade of this machine at a time, from the page or the terminal: an exclusive lock on
    work_dir()/upgrade.lock held while Console is asked, the installer downloaded and started."""
    work = work_dir()
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)
    fd = os.open(work / "upgrade.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if _WINDOWS:
            import msvcrt

            # A byte range lock, released when the descriptor closes.
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                raise EnterpriseUpgradeError("已经在升级了：另一个页面或终端正在给这台机器换装企业版") from None
        else:
            import fcntl

            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise EnterpriseUpgradeError("已经在升级了：另一个页面或终端正在给这台机器换装企业版") from None
        yield
    finally:
        os.close(fd)


def _upgrade_lock_held() -> bool:
    """Someone holds upgrade_lock: an upgrade is under way, here or in another process."""
    try:
        with upgrade_lock():
            return False
    except EnterpriseUpgradeError:
        return True
    except OSError:
        return False


#: What the enterprise edition's downgrade (flockspro-go) writes into the runtime root each time
#: it goes back to this edition: the list of what it kept aside.
DOWNGRADE_RECORD = "flockspro-downgrade.json"


def _downgraded_since(*marks: Path) -> bool:
    """Whether the enterprise edition went back to this one after the first of *marks* there is."""
    for mark in marks:
        try:
            written = mark.stat().st_mtime
        except OSError:
            continue
        try:
            return (runtime_root() / DOWNGRADE_RECORD).stat().st_mtime > written
        except OSError:
            return False
    return False


_KEPT_STAMP = re.compile(r"\.managed-(\d{8}T\d{6}Z)-")


def _tree_size(path: Path) -> int | None:
    """Bytes under *path* (links not followed); None when it cannot be read whole."""
    try:
        if not path.is_dir() or path.is_symlink():
            return path.lstat().st_size
        total = 0
        for folder, _dirs, files in os.walk(path, onerror=_raise):
            for name in files:
                total += os.lstat(os.path.join(folder, name)).st_size
        return total
    except OSError:
        return None


def _raise(error: OSError) -> None:
    raise error


def kept_after_downgrades() -> list[dict[str, Any]]:
    """What the enterprise edition's downgrades kept aside, from its record in the runtime root:
    each path, when it was kept, whether it is still there and its size.

    Nothing is removed here. They hold what the enterprise edition wrote while it ran (its database,
    settings, keys); neither this edition nor a later upgrade uses them, and only the administrator
    can say they are not wanted.
    """
    try:
        record = json.loads((runtime_root() / DOWNGRADE_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    paths = record.get("kept") if isinstance(record, dict) else None
    if not isinstance(paths, list):
        return []
    items = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        path = Path(raw)
        stamp = _KEPT_STAMP.search(path.name)
        try:
            os.lstat(path)
            present: bool | None = True
        except FileNotFoundError:
            present = False
        except OSError:
            present = None  # cannot tell from this account (a directory on the way it may not enter)
        items.append({
            "path": raw,
            "kept_at": stamp.group(1) if stamp else None,
            "present": present,
            "size": _tree_size(path) if present else None,
        })
    return items


def read_state() -> dict[str, Any] | None:
    """The last upgrade started from the page or the terminal: where it is now (None once the
    enterprise edition it installed has gone back to this one)."""
    try:
        state = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    if state.get("phase") == "downloading" and not _run_lock.locked() and not _upgrade_lock_held():
        # The process that downloaded it is gone (the server restarted under it).
        state["phase"] = "failed"
        state["failed_step"] = "download"
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
    if state.get("phase") == "finished" and _downgraded_since(work_dir() / "exit-status", _state_file()):
        # The enterprise edition it installed went back to this one since: nothing is installed
        # to refresh the page into any more.
        return None
    log_file = state.get("log")
    if state.get("phase") == "failed":
        _explain_failure(state)
    elif isinstance(log_file, str) and log_file:
        state["log_tail"] = _log_tail(Path(log_file))
    return state


def _keep_release_notes(offer: InstallerOffer) -> None:
    """Leave what Console says of the release in the data the enterprise edition takes over: it may
    start without the Console sign-in this edition has. Not worth stopping an upgrade for."""
    if offer.release_notes is None:
        return
    path = runtime_root() / "run" / RELEASE_NOTES_FILE
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps({"release_id": offer.release_id, "version": offer.version, "release_notes": offer.release_notes}, ensure_ascii=False),
            encoding="utf-8",
        )
        # Whoever the edition runs as reads it; the notes are what customers read anyway.
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        log.warning("enterprise.release_notes_not_kept", {"release_id": offer.release_id, "error": str(exc)})


def _megabytes(count: int) -> int:
    """Rounded as the progress bar beside it rounds: half up."""
    return (count + (1 << 19)) >> 20


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
        # Some of these say nothing of themselves (a connection reset has no message).
        said = str(exc) or type(exc).__name__
        got = f"，已下载 {_megabytes(received)} MB" + (f" / {_megabytes(offer.size)} MB" if offer.size else "") if received else ""
        raise EnterpriseUpgradeError(f"下载安装包失败：{said}{got}") from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if digest.hexdigest() != offer.sha256:
        part.unlink(missing_ok=True)
        raise EnterpriseUpgradeError("下载的安装包校验不对（sha256 和 Console 给的不一样），没有运行")
    os.replace(part, final)
    os.chmod(final, 0o700)
    _keep_release_notes(offer)
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


def can_run_installer_here() -> bool:
    """This account runs the installer itself: not a machine run by systemd unless root."""
    return available() and (not run_by_systemd() or _euid() == 0) and install_root_ready()


_START_AGAIN = Path(__file__).with_name("start_again.py")


def _write_start_again_plan() -> Path:
    """How to start this service as it runs now, for start_again.py. Empty when it does not run or
    nobody can tell: that file then says so and does nothing."""
    plan_file = work_dir() / "start-again.json"
    plan: dict[str, Any] = {}
    try:
        from flocks.updater.updater import _capture_service_snapshot

        snapshot = _capture_service_snapshot()
        if snapshot.was_running:
            config = snapshot.config
            command = [sys.executable, "-m", "flocks.cli.main", "start", "--host", config.frontend_host, "--port", str(config.frontend_port)]
            if config.no_browser:
                command.append("--no-browser")
            if config.skip_frontend_build:
                command.append("--skip-webui-build")
            if config.legacy_backend_host is not None:
                command += ["--server-host", config.legacy_backend_host]
            if config.legacy_backend_port is not None:
                command += ["--server-port", str(config.legacy_backend_port)]
            plan = {"host": config.frontend_host, "port": config.frontend_port, "argv": command, "cwd": str(Path(__file__).resolve().parents[2]),
                    "result_file": str(installation_result_path()), "prior_result": _prior_installation_result()}
    except Exception as exc:
        log.warning("enterprise.start_again_plan_failed", {"error": str(exc)})
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    return plan_file


def run_installer(path: Path, offer: InstallerOffer) -> int:
    """Run the installer in the foreground (flocks enterprise upgrade), its output the terminal's;
    recorded as the page records the run it starts (state.json, exit-status), so the page sees it
    too. Its exit status."""
    exit_file = work_dir() / "exit-status"
    exit_file.unlink(missing_ok=True)
    # Windows: the installer asks for an administrator and runs in the window that opens; it tells
    # how it ended there, not here.
    argv = [str(path), "--no-pause", "--exit-status", str(exit_file), *_windows_open_edition_args()] if _WINDOWS else ["bash", str(path)]
    prior_result = _prior_installation_result()
    start_again_plan = None if _WINDOWS else _write_start_again_plan()
    process = subprocess.Popen(argv, cwd=str(path.parent), env=_installer_environment())
    _write_state({
        "phase": "installing",
        "pid": process.pid,
        "release_id": offer.release_id,
        "version": offer.version,
        "installer": str(path),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "prior_result": prior_result,
        "from": "terminal",
    })
    code = process.wait()
    if not (_WINDOWS and exit_file.exists()):
        exit_file.write_text(f"{code}\n", encoding="utf-8")
    if code != 0 and start_again_plan is not None:
        subprocess.run([sys.executable, "-I", str(_START_AGAIN), str(start_again_plan)], env=_installer_environment(), check=False)
    return code


def _windows_open_edition_args() -> list[str]:
    """What the Windows installer is told of this open install: its data directory and its flocks
    command, so it finds them as this account has them whichever administrator confirms."""
    args = ["--open-data", str(runtime_root())]
    launcher = Path.home() / ".local" / "bin" / "flocks.cmd"
    if launcher.is_file():
        args += ["--open-cli", str(launcher)]
    return args


#: Run by this server's interpreter, it starts the Windows installer and exits at once, saying the
#: installer's pid: the installer is then no descendant of this server, whose whole process tree
#: `flocks stop` takes -- and install-managed, inside the installer, stops the open edition with it.
#: 0x208: CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS (no console of the server's, no Ctrl+C of its group).
_WINDOWS_DETACH = (
    "import subprocess, sys\n"
    "log = open(sys.argv[1], 'ab')\n"
    "process = subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, creationflags=0x208)\n"
    "print(process.pid)\n"
)


def _start_windows_installer(path: Path, offer: InstallerOffer, attempt: str | None = None) -> dict[str, Any]:
    """start_installer on Windows: the installer, apart from this server, asks Windows for an
    administrator and writes its log and exit status where the page reads them."""
    prior_result = _prior_installation_result()
    work = work_dir()
    log_file = work / "install.log"
    exit_file = work / "exit-status"
    exit_file.unlink(missing_ok=True)
    with log_file.open("ab") as out:
        out.write(f"\n== {time.strftime('%Y-%m-%dT%H:%M:%S%z')} {path.name}\n".encode())
    argv = [str(path), "--no-pause", "--log", str(log_file), "--exit-status", str(exit_file), *_windows_open_edition_args()]
    launcher = subprocess.Popen(
        [sys.executable, "-I", "-c", _WINDOWS_DETACH, str(log_file), *argv],
        cwd=str(work),
        env=_installer_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        close_fds=True,
    )
    started, _ = launcher.communicate(timeout=30)
    try:
        pid = int(started.strip())
    except ValueError:
        raise EnterpriseUpgradeError(f"安装包没有起来，看 {log_file}") from None
    state = {
        "phase": "installing",
        "pid": pid,
        "release_id": offer.release_id,
        "version": offer.version,
        "installer": str(path),
        "log": str(log_file),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "prior_result": prior_result,
        **({"attempt": attempt} if attempt else {}),
    }
    _write_state(state)
    log.info("enterprise.installer_started", {"pid": pid, "release_id": offer.release_id})
    return state


def start_installer(path: Path, offer: InstallerOffer, attempt: str | None = None) -> dict[str, Any]:
    """Run the installer apart from this server: install-managed stops this open install's
    supervisor, and this process with it, before the enterprise edition starts."""
    if not available():
        raise EnterpriseUpgradeError("这台机器已经是企业版")
    if _WINDOWS:
        return _start_windows_installer(path, offer, attempt)
    if run_by_systemd():
        raise EnterpriseUpgradeError(f"这台机器由 systemd 管，要以 root 执行：{installer_command(str(path))}")
    if not install_root_ready():
        raise EnterpriseUpgradeError(f"先用 root 执行一次：{prepare_command()}")
    prior_result = _prior_installation_result()
    work = work_dir()
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)
    log_file = work / "install.log"
    exit_file = work / "exit-status"
    exit_file.unlink(missing_ok=True)
    with log_file.open("ab") as out:
        os.chmod(log_file, 0o600)
        out.write(f"\n== {time.strftime('%Y-%m-%dT%H:%M:%S%z')} bash {path.name}\n".encode())
    # The exit status is written by a shell of the installer's own session, which outlives this one.
    # The shell started here puts it in the background and exits at once, so it is no child of this
    # server: `flocks stop` takes the server's whole process tree, and install-managed stops the open
    # edition before the enterprise edition starts.
    # When the installer fails it starts this service again itself; killed or out of memory it
    # cannot, so the shell sees to it (start_again.py).
    launcher = subprocess.Popen(
        ["/bin/sh", "-c",
         '(bash "$1"; code=$?; echo "$code" > "$2"; if [ "$code" != 0 ]; then "$4" -I "$5" "$6" >"$6.log" 2>&1; fi) '
         '</dev/null >>"$3" 2>&1 & echo "$!"',
         "sh", str(path), str(exit_file), str(log_file), sys.executable, str(_START_AGAIN), str(_write_start_again_plan())],
        cwd=str(work),
        env=_installer_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    started, _ = launcher.communicate(timeout=30)
    try:
        pid = int(started.strip())
    except ValueError:
        raise EnterpriseUpgradeError(f"安装包没有起来，看 {log_file}") from None
    state = {
        "phase": "installing",
        "pid": pid,
        "release_id": offer.release_id,
        "version": offer.version,
        "installer": str(path),
        "log": str(log_file),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "prior_result": prior_result,
        **({"attempt": attempt} if attempt else {}),
    }
    _write_state(state)
    log.info("enterprise.installer_started", {"pid": pid, "release_id": offer.release_id})
    return state


_run_lock = asyncio.Lock()


async def upgrade(progress: Callable[[dict[str, Any]], Awaitable[None]]) -> dict[str, Any]:
    """Ask Console, download, check and start the installer; ``progress`` hears each step."""
    if _run_lock.locked():
        raise EnterpriseUpgradeError("已经在升级了")
    async with _run_lock:
        with upgrade_lock():
            return await _upgrade(progress)


async def _upgrade(progress: Callable[[dict[str, Any]], Awaitable[None]]) -> dict[str, Any]:
    """upgrade() with the locks held."""
    current = read_state()
    if current is not None and current.get("phase") == "installing":
        raise EnterpriseUpgradeError("安装包已经在运行了")
    # What this attempt records goes by this: the page must not take an earlier failure for this one's.
    attempt = uuid.uuid4().hex
    await progress({"stage": "started", "attempt": attempt})
    offer = await fetch_offer()
    base = {"attempt": attempt, "release_id": offer.release_id, "version": offer.version, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
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
        _write_state({**base, "phase": "failed", "failed_step": "download", "message": str(exc) or "下载没有完成"})
        raise
    await progress({"stage": "downloaded", "installer": str(path)})
    fixed_root = not _WINDOWS and not installer_relocatable(path) and not _default_root_ready()
    if run_by_systemd() or not install_root_ready() or fixed_root:
        state = {
            "phase": "command",
            "release_id": offer.release_id,
            "version": offer.version,
            "installer": str(path),
            "command": installer_command(str(path)),
            "prepare_command": prepare_command(default_root=fixed_root),
        }
        _write_state(state)
        return state
    try:
        state = start_installer(path, offer, attempt)
    except BaseException as exc:
        # Its own refusal, or the shell that starts it saying nothing: nothing ran. Anything else, it may have.
        recovery = "unchanged" if isinstance(exc, EnterpriseUpgradeError) else "unknown"
        _write_state({**base, "phase": "failed", "failed_step": "install", "installer": str(path), "recovery": recovery, "message": str(exc) or "安装包没有起来"})
        raise
    await progress({"stage": "installing", **state})
    return state


def offer_dict(offer: InstallerOffer) -> dict[str, Any]:
    return {**asdict(offer), "filename": offer.filename}
