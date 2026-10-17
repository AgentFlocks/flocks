"""Components of an REA install tier, each reused when already in place.

- ``skill``: the reverse-engineer-anything skill from the guide's GitHub source,
  retried as is when the source is unreachable.
- ``rea``: the pinned CLI in the npx cache (``npx -y rea-agents@6.4.0``).
- ``jdk`` (full tier): JDK 21 from the distribution packages (``dnf``, root).
- ``ghidra`` (full tier): Ghidra 12.1.4 through the download mirror, checked
  against the official SHA-256 and unpacked next to the other ``/opt`` caches.

Nothing here asks the user; the ``rea_analysis`` tool gets the confirmation
before an install that writes system packages or platform configuration.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Optional

from flocks.rea import runtime
from flocks.utils.log import Log


log = Log.create(service="rea.components")

ComponentStatus = Literal["reused", "installed", "failed", "blocked"]

SKILL_ATTEMPTS = 3
GHIDRA_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 2.0
_REA_CLI_TIMEOUT_SECONDS = 900.0
_DNF_TIMEOUT_SECONDS = 1800.0
_GHIDRA_DOWNLOAD_TIMEOUT_SECONDS = 4 * 3600.0
_UNPACK_TIMEOUT_SECONDS = 1800.0


@dataclass
class ComponentResult:
    key: str
    status: ComponentStatus
    detail: Optional[str] = None
    # What the user has to do when the install cannot do it (permissions,
    # platform): ``{"code": ..., <parameters>}``, see :func:`blocker_text`.
    blocker: Optional[dict[str, Any]] = None
    attempts: int = 1
    # What was checked, e.g. the verified SHA-256 of the Ghidra download.
    evidence: Optional[dict[str, Any]] = None

    @property
    def ok(self) -> bool:
        return self.status in ("reused", "installed")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _sleep_before_retry(attempt: int) -> None:
    await asyncio.sleep(_RETRY_DELAY_SECONDS * attempt)


# ── skill ──


async def ensure_skill(*, attempts: int = SKILL_ATTEMPTS) -> ComponentResult:
    if runtime.skill_installed():
        return ComponentResult("skill", "reused", detail=str(runtime.installed_skill_dir()))
    from flocks.skill.installer import SkillInstaller

    error = None
    for attempt in range(1, attempts + 1):
        try:
            result = await SkillInstaller.install_from_source(runtime.REA_SKILL_SOURCE, scope="global")
        except Exception as exc:  # network and parser errors: retry as is
            error = str(exc)
        else:
            if getattr(result, "success", False) and runtime.skill_installed():
                return ComponentResult(
                    "skill", "installed", detail=str(runtime.installed_skill_dir()), attempts=attempt,
                )
            error = getattr(result, "error", None) or "skill installer reported no skill"
        log.warning("rea.component.skill_failed", {"attempt": attempt, "error": error})
        if attempt < attempts:
            await _sleep_before_retry(attempt)
    return ComponentResult("skill", "failed", detail=f"{runtime.REA_SKILL_SOURCE}: {error}", attempts=attempts)


# ── REA CLI in the npx cache ──


_NPM_KEY_LINE = re.compile(r"npm (?:error|ERR!) (?:code \S+|\d{3} .+|[A-Za-z]*Error: .+|network .+)")


def npm_failure_summary(output: Optional[str]) -> str:
    """The lines of npm's error output that say what failed (code, HTTP status, fetch error)."""

    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    keys = list(dict.fromkeys(line for line in lines if _NPM_KEY_LINE.match(line)))
    if keys:
        return " / ".join(keys)[:400]
    return (output or "").strip()[-400:]


def npx_cache_dir() -> Path:
    cache = os.environ.get("npm_config_cache") or os.environ.get("NPM_CONFIG_CACHE")
    return Path(cache) / "_npx" if cache else Path.home() / ".npm" / "_npx"


def rea_cached() -> bool:
    """rea-agents 6.4.0 is already in the npx cache."""

    root = npx_cache_dir()
    if not root.is_dir():
        return False
    for manifest in root.glob(f"*/node_modules/{runtime.REA_PACKAGE}/package.json"):
        try:
            if json.loads(manifest.read_text(encoding="utf-8")).get("version") == runtime.REA_VERSION:
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


async def ensure_rea_cli() -> ComponentResult:
    command = runtime.rea_command()
    if command is None:
        return ComponentResult("rea", "failed", detail="npx not found")
    cached = rea_cached()
    try:
        code, output = await runtime.run_process(
            [*command, "--version"], timeout=_REA_CLI_TIMEOUT_SECONDS, env=runtime.service_env(),
        )
    except asyncio.TimeoutError:
        return ComponentResult("rea", "failed", detail=f"npx {runtime.REA_PACKAGE_SPEC} timed out")
    except OSError as exc:
        return ComponentResult("rea", "failed", detail=str(exc))
    version = (output or "").strip().splitlines()[-1:] or [""]
    if code != 0 or version[0].strip() != runtime.REA_VERSION:
        return ComponentResult("rea", "failed", detail=npm_failure_summary(output) or f"exit code {code}")
    return ComponentResult("rea", "reused" if cached else "installed", detail=runtime.REA_PACKAGE_SPEC)


# ── JDK 21 ──


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def jdk_install_command() -> str:
    return f"dnf install -y {runtime.JDK_PACKAGE}"


def _jdk_blocker() -> Optional[dict[str, Any]]:
    """Why the service cannot install JDK 21 itself, or None when it can."""

    if not shutil.which("dnf"):
        return {"code": "jdk_no_dnf", "javaHome": runtime.JAVA_HOME_DEFAULT, "env": "FLOCKS_REA_JAVA_HOME"}
    if not _is_root():
        return {"code": "jdk_needs_root", "command": jdk_install_command()}
    return None


def _occupied(target: Path) -> bool:
    """Something other than an empty folder is at ``target`` (a complete Ghidra would be reused)."""

    if not target.exists():
        return False
    try:
        return not target.is_dir() or any(target.iterdir())
    except OSError:
        return True


def _ghidra_blocker(target: Path) -> Optional[dict[str, Any]]:
    if _occupied(target):
        # Never unpack over or move what is already there.
        return {"code": "ghidra_dir_occupied", "path": str(target), "env": "FLOCKS_REA_GHIDRA_DIR"}
    if not _writable_dir(target.parent):
        return {"code": "ghidra_dir_not_writable", "path": str(target.parent), "env": "FLOCKS_REA_GHIDRA_DIR"}
    return None


def _decompiler_blocker(directory: str) -> Optional[dict[str, Any]]:
    """Ghidra is in place but has no native decompiler for this host (e.g. macOS arm64)."""

    if runtime.ghidra_decompiler_ok(directory):
        return None
    return {
        "code": "ghidra_decompiler_missing",
        "platform": runtime.ghidra_platform() or "unknown",
        "command": _build_natives_command(Path(directory)),
    }


def _build_natives_command(directory: Path) -> str:
    gradle = directory / "support" / "gradle"
    if runtime._is_windows():
        return f'cd /d "{gradle}" && gradlew.bat buildNatives'
    return f"cd {shlex.quote(str(gradle))} && ./gradlew buildNatives"


def public_blocker(blocker: dict[str, Any]) -> dict[str, Any]:
    """The blocker as a conversation keeps it: its code and fixed values; this host's paths are <path>."""

    code = blocker.get("code")
    public: dict[str, Any] = {"code": code}
    placeholder = runtime.PATH_PLACEHOLDER
    if code == "jdk_no_dnf":
        java_home = blocker.get("javaHome")
        public["javaHome"] = java_home if java_home == runtime.JAVA_HOME_DEFAULT else placeholder
        public["env"] = blocker.get("env")
    elif code == "jdk_needs_root":
        public["command"] = jdk_install_command()
    elif code in ("ghidra_dir_not_writable", "ghidra_dir_occupied"):
        path = blocker.get("path")
        public["path"] = path if path in runtime.GUIDE_PATHS else placeholder
        public["env"] = blocker.get("env")
    elif code == "ghidra_decompiler_missing":
        public["platform"] = blocker.get("platform")
        public["command"] = (
            f'cd /d "{placeholder}\\support\\gradle" && gradlew.bat buildNatives' if runtime._is_windows()
            else f"cd {placeholder}/support/gradle && ./gradlew buildNatives"
        )
    return public


def blocker_text(blocker: dict[str, Any]) -> str:
    """The blocker as an instruction for the model (the WebUI localizes the code)."""

    code = blocker.get("code")
    if code == "jdk_no_dnf":
        return (
            f"This host has no dnf: install JDK {runtime.JDK_MAJOR} and make it available at "
            f"{blocker.get('javaHome')} (or set {blocker.get('env')} for the Flocks service), then retry."
        )
    if code == "jdk_needs_root":
        return f"Root is required: run `{blocker.get('command')}` as root, then retry."
    if code == "ghidra_dir_not_writable":
        return (
            f"The Flocks service cannot write {blocker.get('path')}: grant write access there (root) or set "
            f"{blocker.get('env')} to a writable directory for the Flocks service, then retry."
        )
    if code == "ghidra_dir_occupied":
        return (
            f"{blocker.get('path')} already exists and is not a complete Ghidra {runtime.GHIDRA_VERSION}: move it "
            f"away or set {blocker.get('env')} to another directory for the Flocks service, then retry."
        )
    if code == "ghidra_decompiler_missing":
        return (
            f"The Ghidra release has no native decompiler for this host ({blocker.get('platform')}): build it with "
            f"`{blocker.get('command')}` (needs a C/C++ compiler and network for Gradle), then retry."
        )
    return str(code or "blocked")


async def ensure_jdk() -> ComponentResult:
    found = await runtime.find_java_home()
    if found:
        return ComponentResult("jdk", "reused", detail=found)
    blocker = _jdk_blocker()
    if blocker is not None:
        return ComponentResult("jdk", "blocked", blocker=blocker)
    command = jdk_install_command()
    try:
        code, output = await runtime.run_process(command.split(), timeout=_DNF_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return ComponentResult("jdk", "failed", detail=f"{command} timed out")
    except OSError as exc:
        return ComponentResult("jdk", "failed", detail=str(exc))
    found = await runtime.find_java_home()
    if code != 0 or not found:
        return ComponentResult("jdk", "failed", detail=(output or "").strip()[-400:] or f"exit code {code}")
    return ComponentResult("jdk", "installed", detail=found)


# ── Ghidra 12.1.4 ──


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _writable_dir(path: Path) -> bool:
    anchor = path
    while not anchor.exists() and anchor.parent != anchor:
        anchor = anchor.parent
    return anchor.is_dir() and os.access(anchor, os.W_OK | os.X_OK)


def _unpack_zip(archive: Path, destination: Path) -> None:
    """Python fallback for ``unzip``: keeps the executable bits Ghidra's scripts need."""

    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"unsafe path in archive: {member.filename}")
            bundle.extract(member, destination)
            mode = (member.external_attr >> 16) & 0o777
            if mode and not member.is_dir():
                os.chmod(target, mode | stat.S_IRUSR)


async def _download(url: str, target: Path) -> tuple[bool, str]:
    curl = shutil.which("curl")
    if not curl:
        return False, "curl not found"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        code, output = await runtime.run_process(
            [curl, "-fL", "--retry", "2", "--connect-timeout", "30", "-o", str(target), url],
            timeout=_GHIDRA_DOWNLOAD_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return False, f"download timed out: {url}"
    except OSError as exc:
        return False, str(exc)
    return code == 0, (output or "").strip()[-300:]


async def ensure_ghidra(*, attempts: int = GHIDRA_ATTEMPTS) -> ComponentResult:
    found = runtime.find_ghidra_dir()
    if found:
        blocker = _decompiler_blocker(found)
        if blocker is not None:
            return ComponentResult("ghidra", "blocked", detail=found, blocker=blocker)
        return ComponentResult("ghidra", "reused", detail=found)
    target = Path(runtime.ghidra_target_dir())
    blocker = _ghidra_blocker(target)
    if blocker is not None:
        return ComponentResult("ghidra", "blocked", blocker=blocker)
    archive = runtime.state_dir() / "downloads" / runtime.GHIDRA_ARCHIVE
    sources = [runtime.GHIDRA_MIRROR_PREFIX + runtime.GHIDRA_URL, runtime.GHIDRA_URL]
    error = None
    attempt = 0
    rejected: list[str] = []
    for attempt in range(1, attempts + 1):
        url = sources[0] if attempt < attempts else sources[-1]
        if archive.exists():
            archive.unlink()
        ok, detail = await _download(url, archive)
        if not ok:
            error = detail
        else:
            digest = await runtime.run_in_thread(_sha256, archive)
            if digest == runtime.GHIDRA_SHA256:
                break
            rejected.append(digest)
            # A bad download is discarded and fetched again, never unpacked.
            error = f"SHA-256 mismatch: got {digest}, expected {runtime.GHIDRA_SHA256}"
            archive.unlink(missing_ok=True)
        log.warning("rea.component.ghidra_download_failed", {"attempt": attempt, "url": url, "error": error})
        if attempt < attempts:
            await _sleep_before_retry(attempt)
    else:
        return ComponentResult("ghidra", "failed", detail=error, attempts=attempts)

    # Unpack into a fresh folder of our own beside the target, then move only the release in:
    # nothing that already sits next to the target is overwritten or moved.
    staging = target.parent / f".rea-ghidra-{uuid.uuid4().hex[:12]}"
    try:
        staging.mkdir(parents=True)
        unzip = shutil.which("unzip")
        if unzip:
            code, output = await runtime.run_process(
                [unzip, "-q", str(archive), "-d", str(staging)], timeout=_UNPACK_TIMEOUT_SECONDS,
            )
            if code != 0:
                raise OSError((output or "").strip()[-300:] or f"unzip exit code {code}")
        else:
            await runtime.run_in_thread(_unpack_zip, archive, staging)
        unpacked = staging / runtime.GHIDRA_FOLDER
        if not unpacked.is_dir():
            raise OSError(f"the archive has no {runtime.GHIDRA_FOLDER} folder")
        if _occupied(target):
            raise OSError(f"{target} was filled meanwhile")
        if target.exists():
            target.rmdir()  # the empty folder the user prepared
        os.replace(unpacked, target)
    except (OSError, ValueError, zipfile.BadZipFile, asyncio.TimeoutError) as exc:
        return ComponentResult("ghidra", "failed", detail=f"unpack failed: {exc}", attempts=attempt)
    finally:
        archive.unlink(missing_ok=True)
        await runtime.run_in_thread(shutil.rmtree, staging, True)
    if not runtime.ghidra_install_ok(str(target)):
        return ComponentResult("ghidra", "failed", detail=f"{target} is incomplete after unpacking", attempts=attempt)
    evidence = {
        "sha256": runtime.GHIDRA_SHA256,
        "verified": True,
        "source": "mirror" if attempt < attempts else "official",
        "rejected": rejected,
    }
    blocker = _decompiler_blocker(str(target))
    if blocker is not None:
        # Downloaded and verified, but this host needs its decompiler built first.
        return ComponentResult(
            "ghidra", "blocked", detail=str(target), attempts=attempt, blocker=blocker, evidence=evidence,
        )
    return ComponentResult("ghidra", "installed", detail=str(target), attempts=attempt, evidence=evidence)


async def ensure_components(tier: str, on_done=None) -> dict[str, ComponentResult]:
    """Run the tier's components concurrently; ``on_done(result)`` follows each finish."""

    jobs = [ensure_skill(), ensure_rea_cli()]
    if tier == "full":
        jobs += [ensure_jdk(), ensure_ghidra()]

    async def watched(job):
        result = await job
        if on_done is not None:
            await on_done(result)
        return result

    # Every component finishes (or is cancelled and stops) before this returns or raises, so the
    # install job never ends while one still writes files.
    outcomes = await asyncio.gather(*(watched(job) for job in jobs), return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    return {result.key: result for result in outcomes}


async def plan_components(tier: str) -> dict[str, dict[str, Any]]:
    """What an install of ``tier`` would do per component, without doing it.

    ``action`` is ``reuse``, ``install`` (skill into Flocks, JDK as a system
    package), ``download`` (REA into the npx cache, Ghidra into its directory)
    or ``blocked`` with the ``blocker`` the user has to clear.
    """

    plan: dict[str, dict[str, Any]] = {
        "skill": {"action": "reuse" if runtime.skill_installed() else "install"},
        "rea": {"action": "reuse" if rea_cached() else "download"},
    }
    if tier == "full":
        if await runtime.find_java_home():
            plan["jdk"] = {"action": "reuse"}
        else:
            blocker = _jdk_blocker()
            plan["jdk"] = (
                {"action": "blocked", "blocker": blocker} if blocker
                else {"action": "install", "command": jdk_install_command()}
            )
        found = runtime.find_ghidra_dir()
        if found:
            blocker = _decompiler_blocker(found)
            plan["ghidra"] = {"action": "blocked", "blocker": blocker} if blocker else {"action": "reuse"}
        else:
            target = Path(runtime.ghidra_target_dir())
            blocker = _ghidra_blocker(target)
            plan["ghidra"] = (
                {"action": "blocked", "blocker": blocker} if blocker
                else {"action": "download", "path": str(target)}
            )
    return plan
