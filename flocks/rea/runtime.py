"""Pinned REA release, install tiers, on-disk locations and host checks.

REA runs as Flocks' MCP tool service ``rea`` started with
``npx -y rea-agents@6.4.0 --mcp`` (the package lives in the npx cache). The
light tier needs only Node.js; the full tier adds JDK 21 and Ghidra 12.1.4
for native decompilation and passes their locations to the service.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Literal, Optional, TypeVar

from flocks.config.config import Config, _get_flocks_root
from flocks.utils.log import Log


log = Log.create(service="rea.runtime")
_T = TypeVar("_T")
_record_lock = threading.Lock()

REA_PACKAGE = "rea-agents"
REA_VERSION = "6.4.0"
REA_PACKAGE_SPEC = f"{REA_PACKAGE}@{REA_VERSION}"
REA_SKILL_NAME = "reverse-engineer-anything"
# Skill source named by the REA install guide; intermittently unreachable, so retried as is.
REA_SKILL_SOURCE = "github:zouineoualid/rea-reverse-engineering/reverse-engineer-anything"
# Name of the REA tool service in Flocks' MCP configuration; its tools appear as ``rea_<tool>``.
MCP_SERVER_NAME = "rea"
_DOCTOR_TIMEOUT_SECONDS = 300.0
_MCP_TIMEOUT_SECONDS = 60

Tier = Literal["light", "full"]
TIERS: tuple[str, ...] = ("light", "full")

# Full tier: JDK 21 from the distribution packages and Ghidra 12.1.4 under /opt.
JDK_MAJOR = 21
JDK_PACKAGE = "java-21-openjdk-devel"
JAVA_HOME_DEFAULT = "/usr/lib/jvm/java-21-openjdk"
GHIDRA_VERSION = "12.1.4"
GHIDRA_DIR_DEFAULT = "/opt/ghidra_12.1.4_PUBLIC"
GHIDRA_ARCHIVE = "ghidra_12.1.4_PUBLIC_20260921.zip"
GHIDRA_FOLDER = "ghidra_12.1.4_PUBLIC"  # top-level folder inside the archive
GHIDRA_SHA256 = "ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db"
GHIDRA_URL = (
    "https://github.com/NationalSecurityAgency/ghidra/releases/download/"
    f"Ghidra_{GHIDRA_VERSION}_build/{GHIDRA_ARCHIVE}"
)
# The official host is slow from some networks; the guide downloads through this mirror.
GHIDRA_MIRROR_PREFIX = "https://ghfast.top/"
# Locations the install guide itself names: they say nothing about this host's layout.
_GUIDE_PATHS = frozenset({JAVA_HOME_DEFAULT, GHIDRA_DIR_DEFAULT, "/opt", "/opt/ghidra_*", "/root/.npm/_npx"})
GUIDE_PATHS = _GUIDE_PATHS
# ``engines.node`` of rea-agents 6.4.0; Node 23, 25 and prereleases are rejected.
NODE_REQUIREMENT = "^22.19.0 || ^24.11.0 || >=26.0.0"
_NODE_SUPPORTED_RANGES: tuple[tuple[tuple[int, int, int], Optional[tuple[int, int, int]]], ...] = (
    ((22, 19, 0), (23, 0, 0)),
    ((24, 11, 0), (25, 0, 0)),
    ((26, 0, 0), None),
)
_NODE_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_JAVA_VERSION_RE = re.compile(r'version "(\d+)(?:\.\d+)*')
_ENVIRONMENT_CACHE_TTL_SECONDS = 60.0
_VERSION_PROBE_TIMEOUT_SECONDS = 15.0
_KILL_WAIT_SECONDS = 10.0
_THREAD_POLL_SECONDS = 0.02

_environment_cache: Optional[tuple[float, list["EnvironmentCheck"]]] = None


@dataclass(frozen=True)
class EnvironmentCheck:
    """One prerequisite of REA on the Flocks service host."""

    key: str
    ok: bool
    code: Optional[str] = None
    detail: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def flocks_version() -> str:
    from flocks import __version__

    return str(__version__)


def state_dir() -> Path:
    return Config.get_data_path() / "rea"


def install_record_path() -> Path:
    return state_dir() / "install.json"


def validation_root() -> Path:
    """Samples and evidence of the validation runs (kept for review)."""

    return state_dir() / "validation"


def legacy_install_root() -> Path:
    """npm prefix used by earlier builds; removed with the plugin on uninstall."""

    return _get_flocks_root() / "plugins" / "rea"


def installed_skill_dir() -> Path:
    from flocks.skill.installer import _resolve_install_root

    return _resolve_install_root("global") / REA_SKILL_NAME


def skill_installed() -> bool:
    return (installed_skill_dir() / "SKILL.md").is_file()


def node_executable() -> Optional[str]:
    return shutil.which("node")


def _is_windows() -> bool:
    return os.name == "nt"


def npx_executable() -> Optional[str]:
    return shutil.which("npx")


def npx_command() -> Optional[list[str]]:
    """How to start npx, or None when no runnable npx is found.

    On Windows ``npx`` is usually a ``.cmd`` shim, and ``cmd.exe /c`` mangles
    paths with spaces in the shim and its arguments. The shim only runs npm's
    ``npx-cli.js`` with Node, so run that script directly: the copy next to
    the shim (shipped with this Node.js), else the one under the default
    global prefix (``%APPDATA%\\npm``). A native ``npx.exe`` runs as is.
    Without either, npx is reported missing instead of trying a shell.
    """

    npx = npx_executable()
    if not npx:
        return None
    if not _is_windows() or Path(npx).suffix.lower() == ".exe":
        return [npx]
    node = node_executable()
    if not node:
        return None
    candidates = [Path(npx).resolve().parent / "node_modules" / "npm" / "bin" / "npx-cli.js"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "npm" / "node_modules" / "npm" / "bin" / "npx-cli.js")
    for cli in candidates:
        if cli.is_file():
            return [node, str(cli)]
    return None


def rea_command() -> Optional[list[str]]:
    """``npx -y rea-agents@6.4.0``: the pinned CLI from the npx cache (downloaded once)."""

    npx = npx_command()
    if not npx:
        return None
    return [*npx, "-y", REA_PACKAGE_SPEC]


def parse_node_version(text: str) -> Optional[tuple[int, int, int]]:
    match = _NODE_VERSION_RE.match((text or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def node_version_supported(version: tuple[int, int, int]) -> bool:
    for lower, upper in _NODE_SUPPORTED_RANGES:
        if version >= lower and (upper is None or version < upper):
            return True
    return False


def _process_group_kwargs() -> dict[str, Any]:
    if _is_windows():
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _start_tree_kill(proc: asyncio.subprocess.Process) -> Optional[subprocess.Popen]:
    """Send the kill right away, without awaiting anything a cancel could skip.

    POSIX: SIGKILL the whole process group, also when the leader already
    exited (a descendant that kept the output pipe open still belongs to the
    group, and the group id is not reused while it exists). Windows: start
    ``taskkill /T`` for the tree; the caller waits for it.
    """

    if _is_windows():
        if proc.returncode is None:
            try:
                return subprocess.Popen(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass
        return None
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    return None


async def _finish_tree_kill(proc: asyncio.subprocess.Process, killer: Optional[subprocess.Popen]) -> None:
    """Wait (bounded) until the tree is gone; repeated cancels do not cut it short."""

    deadline = time.monotonic() + _KILL_WAIT_SECONDS
    while time.monotonic() < deadline:
        killer_done = killer is None or killer.poll() is not None
        if killer_done and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        if killer_done and proc.returncode is not None:
            return
        try:
            await asyncio.sleep(_THREAD_POLL_SECONDS)
        except asyncio.CancelledError:
            continue
    if killer is not None and killer.poll() is None:
        # taskkill itself hung: stop it rather than leave it running.
        try:
            killer.kill()
        except OSError:
            pass
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def run_process(
    command: list[str],
    *,
    timeout: float,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> tuple[int, str]:
    """Run a command in its own process group and return (exit code, stdout+stderr).

    On timeout or cancellation the whole tree is killed and reaped (bounded
    wait) before the exception propagates, so a retry never races a
    leftover npm or node process. The cleanup runs inline rather than as a
    separate task, so a shutdown that cancels every task cannot skip it.
    """

    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd) if cwd else None,
        env=env,
        **_process_group_kwargs(),
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except BaseException:
        killer = _start_tree_kill(proc)
        await _finish_tree_kill(proc, killer)
        raise
    return (proc.returncode if proc.returncode is not None else 0), output.decode(errors="replace")


def _settle_future(future: "asyncio.Future[Any]", result: Any = None, error: Optional[BaseException] = None) -> None:
    if future.done():
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)


async def run_in_thread(func: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Run blocking file work in its own thread and wait for it even when cancelled.

    A thread cannot be interrupted. Waiting for it keeps a lock held by the
    caller (or a rollback that follows) from racing work still in progress.
    The thread is started here, so the work always runs and the wait always
    ends; a pooled executor could drop a queued call when shutdown cancels
    everything, leaving nothing to wait for.
    """

    loop = asyncio.get_running_loop()
    future: asyncio.Future[_T] = loop.create_future()
    # A caller that was cancelled no longer reads the outcome; mark it read.
    future.add_done_callback(lambda done: done.cancelled() or done.exception())
    finished = threading.Event()
    context = contextvars.copy_context()

    def runner() -> None:
        try:
            result = context.run(func, *args, **kwargs)
        except BaseException as exc:  # handed to the awaiting coroutine
            outcome: tuple[Any, Optional[BaseException]] = (None, exc)
        else:
            outcome = (result, None)
        finally:
            finished.set()
        try:
            loop.call_soon_threadsafe(_settle_future, future, *outcome)
        except RuntimeError:
            pass  # the loop is closed; nobody is waiting any more

    threading.Thread(target=runner, name="rea-file-step").start()
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        while not finished.is_set():
            try:
                await asyncio.sleep(_THREAD_POLL_SECONDS)
            except asyncio.CancelledError:
                continue
        raise


async def run_version_probe(command: list[str], timeout: float = _VERSION_PROBE_TIMEOUT_SECONDS) -> Optional[str]:
    """Run ``<command> --version`` style probes and return trimmed stdout."""

    try:
        code, output = await run_process(command, timeout=timeout)
    except OSError as exc:
        log.warning("rea.version_probe.spawn_failed", {"command": command[0], "error": str(exc)})
        return None
    except asyncio.TimeoutError:
        log.warning("rea.version_probe.timeout", {"command": command[0]})
        return None
    if code != 0:
        return None
    return output.strip()


def _check_storage() -> EnvironmentCheck:
    root = state_dir()
    anchor = _nearest_existing_ancestor(root)
    writable = anchor is not None and anchor.is_dir() and os.access(anchor, os.W_OK | os.X_OK)
    if writable:
        return EnvironmentCheck(key="storage", ok=True, detail=str(root))
    return EnvironmentCheck(key="storage", ok=False, code="not_writable", detail=str(root))


def _nearest_existing_ancestor(path: Path) -> Optional[Path]:
    current = path
    while True:
        if current.exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


async def _check_node() -> EnvironmentCheck:
    node = node_executable()
    if not node:
        return EnvironmentCheck(key="node", ok=False, code="missing")
    raw = await run_version_probe([node, "--version"])
    version = parse_node_version(raw) if raw is not None else None
    if version is None:
        # Only a parsed version is ever shown; other output stays in the logs.
        log.warning("rea.node.unreadable", {"node": node, "output": (raw or "")[:200]})
        return EnvironmentCheck(key="node", ok=False, code="unreadable")
    shown = "v{}.{}.{}".format(*version)
    if not node_version_supported(version):
        return EnvironmentCheck(key="node", ok=False, code="unsupported", detail=shown)
    return EnvironmentCheck(key="node", ok=True, detail=shown)


def _check_npx() -> EnvironmentCheck:
    command = npx_command()
    if not command:
        return EnvironmentCheck(key="npx", ok=False, code="missing")
    return EnvironmentCheck(key="npx", ok=True, detail=command[-1])


async def check_environment(*, refresh: bool = False) -> list[EnvironmentCheck]:
    """Return host prerequisite checks, cached briefly for status polling."""

    global _environment_cache
    now = time.monotonic()
    if (
        not refresh
        and _environment_cache is not None
        and now - _environment_cache[0] < _ENVIRONMENT_CACHE_TTL_SECONDS
    ):
        return list(_environment_cache[1])
    checks = [await _check_node(), _check_npx(), _check_storage()]
    _environment_cache = (now, checks)
    return list(checks)


def reset_environment_cache() -> None:
    global _environment_cache
    _environment_cache = None


# ── full tier components ──


def _java_bin(java_home: str) -> Path:
    return Path(java_home) / "bin" / ("java.exe" if _is_windows() else "java")


def _javac_bin(java_home: str) -> Path:
    return Path(java_home) / "bin" / ("javac.exe" if _is_windows() else "javac")


def is_jdk(java_home: str) -> bool:
    """A JDK, not a JRE: Ghidra needs the development kit the guide installs (java-21-openjdk-devel)."""

    return _java_bin(java_home).is_file() and _javac_bin(java_home).is_file()


def _path_java_home() -> Optional[str]:
    """The JDK behind ``java`` on PATH (its real location; a launcher stub without a JDK is skipped)."""

    java = shutil.which("java")
    if not java:
        return None
    home = Path(os.path.realpath(java)).parent.parent
    return str(home) if (home / "release").is_file() and is_jdk(str(home)) else None


def java_home_candidates() -> list[str]:
    """Where JDK 21 may live: the override, JAVA_HOME, the guide's path, platform defaults, ``java`` on PATH."""

    candidates = [os.environ.get("FLOCKS_REA_JAVA_HOME") or "", os.environ.get("JAVA_HOME") or "", JAVA_HOME_DEFAULT]
    candidates += [
        "/usr/lib/jvm/java-21-openjdk-amd64",
        "/usr/lib/jvm/java-21-openjdk-arm64",
        "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home",
        "/usr/local/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home",
    ]
    candidates.append(_path_java_home() or "")
    seen: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.append(candidate)
    return seen


async def java_major(java_home: str) -> Optional[int]:
    java = _java_bin(java_home)
    if not java.is_file():
        return None
    try:
        code, output = await run_process([str(java), "-version"], timeout=_VERSION_PROBE_TIMEOUT_SECONDS)
    except (OSError, asyncio.TimeoutError):
        return None
    match = _JAVA_VERSION_RE.search(output or "")
    return int(match.group(1)) if code == 0 and match else None


async def find_java_home() -> Optional[str]:
    """A JDK 21 home (``java`` and ``javac``), or None."""

    for candidate in java_home_candidates():
        if is_jdk(candidate) and await java_major(candidate) == JDK_MAJOR:
            return candidate
    return None


def ghidra_dir_candidates() -> list[str]:
    candidates = [os.environ.get("FLOCKS_REA_GHIDRA_DIR") or "", GHIDRA_DIR_DEFAULT]
    return [candidate for index, candidate in enumerate(candidates) if candidate and candidate not in candidates[:index]]


def _ghidra_version(root: Path) -> Optional[str]:
    """``application.version`` from Ghidra/application.properties (comment lines skipped)."""

    try:
        text = (root / "Ghidra" / "application.properties").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        key, separator, value = line.partition("=")
        if separator and key.strip() == "application.version":
            return value.strip()
    return None


def ghidra_install_ok(directory: str) -> bool:
    """A complete Ghidra of the version REA 6.4.0 is validated with (another version is not reused)."""

    root = Path(directory)
    headless = root / "support" / ("analyzeHeadless.bat" if _is_windows() else "analyzeHeadless")
    return headless.is_file() and _ghidra_version(root) == GHIDRA_VERSION


def find_ghidra_dir() -> Optional[str]:
    for candidate in ghidra_dir_candidates():
        if ghidra_install_ok(candidate):
            return candidate
    return None


def ghidra_platform() -> Optional[str]:
    """Ghidra's name for this host's native platform (``linux_x86_64``, ``mac_arm_64`` …)."""

    import platform

    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm_64", "aarch64": "arm_64"}.get(machine)
    prefix = {"linux": "linux", "darwin": "mac", "windows": "win", "freebsd": "freebsd"}.get(system)
    return f"{prefix}_{arch}" if prefix and arch else None


def ghidra_decompiler_ok(directory: str) -> bool:
    """The release ships the native decompiler only for some hosts (Linux / Windows x86_64);
    elsewhere it has to be built (``support/gradle/gradlew buildNatives``)."""

    name = "decompile.exe" if _is_windows() else "decompile"
    platform_dir = ghidra_platform()
    if platform_dir is None:
        return False
    module = Path(directory) / "Ghidra" / "Features" / "Decompiler"
    return any(
        (module / folder / platform_dir / name).is_file() for folder in ("os", Path("build") / "os")
    )


def ghidra_target_dir() -> str:
    """Where the full tier unpacks Ghidra (override first, else /opt)."""

    return ghidra_dir_candidates()[0]


def c_compiler() -> Optional[str]:
    for name in ("cc", "gcc", "clang"):
        found = shutil.which(name)
        if found:
            return found
    return None


def dotnet_executable() -> Optional[str]:
    return shutil.which("dotnet")


# ── Flocks' MCP registration ──


def mcp_server_config(
    tier: str = "light",
    *,
    java_home: Optional[str] = None,
    ghidra_dir: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """The one MCP registration of REA: ``npx -y rea-agents@6.4.0 --mcp``.

    The full tier passes ``GHIDRA_INSTALL_DIR`` and ``JAVA_HOME``; the light
    tier writes no environment.
    """

    command = rea_command()
    if command is None:
        return None
    config: dict[str, Any] = {
        "type": "local",
        "command": [*command, "--mcp"],
        "enabled": True,
        "timeout": _MCP_TIMEOUT_SECONDS,
    }
    if tier == "full":
        config["environment"] = {
            "GHIDRA_INSTALL_DIR": ghidra_dir or GHIDRA_DIR_DEFAULT,
            "JAVA_HOME": java_home or JAVA_HOME_DEFAULT,
        }
    return config


def current_mcp_config() -> Optional[dict[str, Any]]:
    from flocks.config.config_writer import ConfigWriter

    current = ConfigWriter.get_mcp_server(MCP_SERVER_NAME)
    return current if isinstance(current, dict) else None


def mcp_config_file() -> Optional[Path]:
    """The REA service file under plugins/tools/mcp/ (Flocks keeps it beside flocks.json)."""

    from flocks.tool.tool_loader import find_mcp_config

    return find_mcp_config(MCP_SERVER_NAME)


def mcp_config_matches(current: Optional[dict[str, Any]], expected: Optional[dict[str, Any]]) -> bool:
    """Same command and environment, enabled: registering again would only reconnect."""

    if not current or not expected or current.get("enabled", True) is False:
        return False
    if list(current.get("command") or []) != list(expected.get("command") or []):
        return False
    current_env = current.get("environment") or current.get("env") or {}
    return dict(current_env) == dict(expected.get("environment") or {})


def mcp_registered() -> bool:
    """Flocks' configuration starts REA 6.4.0 through npx (any tier)."""

    current = current_mcp_config()
    command = list((current or {}).get("command") or [])
    return bool(current) and current.get("enabled", True) is not False and REA_PACKAGE_SPEC in command


# ── text shown in conversations and the WebUI ──


_URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+(?::[^/\s@]*)?@", re.IGNORECASE)


def public_detail(text: Optional[str], limit: int = 400) -> Optional[str]:
    """Installer output fit for conversation history: one line, home shortened, URL credentials masked."""

    if not text:
        return None
    text = " ".join(str(text).split())
    home = str(Path.home())
    if home and home != "/":
        text = text.replace(home, "~")
    text = _URL_CREDENTIALS.sub(r"\1***@", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# Characters that end a path inside running text (quotes, brackets, separators of lists).
_STOP = r"\s'\"<>()\[\]{},;"
# A further space-separated piece that still contains a folder separator belongs to the same path
# ("/srv/my tools/ghidra", "C:\Program Files\x"); a piece with "=" or ":" starts something else.
_MORE = rf"(?:[ ]+[^{_STOP}/\\=:]+[/\\][^{_STOP}]*)*"
_HOST_PATH = re.compile(
    rf"(?<![A-Za-z0-9_.:/~\\-])~?/[^{_STOP}]+{_MORE}"  # POSIX and home paths
    rf"|\b[A-Za-z]:[/\\][^{_STOP}]*{_MORE}"  # drive paths, either separator
    rf"|(?<![\\\w])\\\\[^{_STOP}\\]+\\[^{_STOP}]*{_MORE}"  # UNC paths
)
# Network addresses keep their path, query and fragment; a local file address is a path.
_URL = re.compile(r"(?!file:)[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+", re.IGNORECASE)
_FILE_URL = re.compile(r"\bfile://[^\s'\"<>]+", re.IGNORECASE)
_WHOLE_PATH = re.compile(r"^\s*(?:~?/|[A-Za-z]:[/\\]|\\\\)")
PATH_PLACEHOLDER = "<path>"


def _redact_text(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        path = match.group(0)
        return path if path.rstrip(".,:;。，；：") in _GUIDE_PATHS else PATH_PLACEHOLDER

    text = _FILE_URL.sub(PATH_PLACEHOLDER, text)
    parts = []
    last = 0
    for url in _URL.finditer(text):
        parts.append(_HOST_PATH.sub(replace, text[last:url.start()]))
        parts.append(url.group(0))
        last = url.end()
    parts.append(_HOST_PATH.sub(replace, text[last:]))
    return "".join(parts)


def redact_paths(value: Any, *, field: bool = False) -> Any:
    """Host paths out of what a conversation keeps (texts, and the strings inside card data).

    A card field that holds a path is replaced as a whole (spaces and all); in running text the
    paths are found and replaced, network addresses stay as they are. The guide's own default
    locations stay readable. Administrators read the full diagnostics in the plugin square.
    """

    if isinstance(value, str):
        if field and _WHOLE_PATH.match(value) and value.strip() not in _GUIDE_PATHS:
            return PATH_PLACEHOLDER
        return _redact_text(value)
    if isinstance(value, dict):
        return {key: redact_paths(item, field=True) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_paths(item, field=field) for item in value]
    return value


# ── install record ──


def read_install_record() -> Optional[dict[str, Any]]:
    try:
        data = json.loads(install_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_install_record(record: dict[str, Any]) -> None:
    path = install_record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # A temp name of its own per write: concurrent writers never share one.
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with _record_lock:
        try:
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def drop_install_record() -> None:
    with _record_lock:
        try:
            install_record_path().unlink()
        except FileNotFoundError:
            pass


def installed_version() -> Optional[str]:
    record = read_install_record() or {}
    version = record.get("version")
    return version if isinstance(version, str) and version else None


def recorded_tier() -> Optional[str]:
    tier = (read_install_record() or {}).get("tier")
    return tier if tier in TIERS else None


def verification_passed(record: Optional[dict[str, Any]] = None) -> bool:
    record = read_install_record() if record is None else record
    verification = (record or {}).get("verification")
    return isinstance(verification, dict) and verification.get("ok") is True


def ever_verified(record: Optional[dict[str, Any]] = None) -> bool:
    """The install passed its tier validation at least once (used for "已安装异常")."""

    record = read_install_record() if record is None else record
    return isinstance((record or {}).get("firstPassedAt"), int)


def is_configured() -> bool:
    """Static parts are in place: skill on disk and the REA MCP registration."""

    return skill_installed() and mcp_registered()


def full_tier_problems() -> list[dict[str, Any]]:
    """What a recorded full tier lacks now, from the paths its MCP registration starts REA with.

    File checks only, cheap enough for status polling.
    """

    config = current_mcp_config()
    if recorded_tier() != "full" or config is None:
        return []
    environment = config.get("environment") or config.get("env") or {}
    problems: list[dict[str, Any]] = []
    java_home = environment.get("JAVA_HOME")
    if not (isinstance(java_home, str) and java_home and is_jdk(java_home)):
        problems.append({"code": "jdk_missing", "path": public_detail(java_home or "-", 200)})
    ghidra = environment.get("GHIDRA_INSTALL_DIR")
    if not (isinstance(ghidra, str) and ghidra and ghidra_install_ok(ghidra)):
        problems.append({"code": "ghidra_missing", "path": public_detail(ghidra or "-", 200)})
    elif not ghidra_decompiler_ok(ghidra):
        problems.append({"code": "ghidra_decompiler_missing", "platform": ghidra_platform() or "unknown"})
    return problems


def is_ready() -> bool:
    """Configured, the recorded tier validation passed and a full tier still has its JDK and Ghidra
    (live connection checked elsewhere). The plugin square and chat uploads read this."""

    return (
        is_configured() and verification_passed() and installed_version() == REA_VERSION
        and not full_tier_problems()
    )


# ── rea mcp doctor ──


@dataclass(frozen=True)
class DoctorReport:
    """``rea mcp doctor``: the service starts, lists its tools and answers a real call."""

    ok: bool
    server_version: Optional[str] = None
    tools_expected: Optional[int] = None
    tools_observed: Optional[int] = None
    request_tool: Optional[str] = None
    request_ok: bool = False
    failed_checks: tuple[str, ...] = ()
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["failed_checks"] = list(self.failed_checks)
        return data


def parse_doctor_report(raw: str) -> DoctorReport:
    text = raw or ""
    start = text.find("{")
    try:
        data = json.loads(text[start:] if start >= 0 else text)
    except ValueError:
        return DoctorReport(ok=False, error=(raw or "no output").strip()[:400] or "no output")
    if not isinstance(data, dict):
        return DoctorReport(ok=False, error="unexpected doctor output")
    tools = ((data.get("inventory") or {}).get("tools") or {}) if isinstance(data.get("inventory"), dict) else {}
    flow = data.get("request_flow") if isinstance(data.get("request_flow"), dict) else {}
    server = data.get("server") if isinstance(data.get("server"), dict) else {}
    failed = tuple(
        str(check.get("name"))
        for check in data.get("checks") or []
        if isinstance(check, dict) and not check.get("ok")
    )

    def as_int(value: Any) -> Optional[int]:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    expected = as_int(tools.get("expected"))
    observed = as_int(tools.get("observed"))
    request_ok = flow.get("ok") is True
    ok = (
        data.get("healthy") is True
        and not failed
        and request_ok
        and observed is not None
        and observed > 0
        and observed == expected
    )
    return DoctorReport(
        ok=ok,
        server_version=server.get("version") if isinstance(server.get("version"), str) else None,
        tools_expected=expected,
        tools_observed=observed,
        request_tool=flow.get("tool") if isinstance(flow.get("tool"), str) else None,
        request_ok=request_ok,
        failed_checks=failed,
    )


def service_env(environment: Optional[dict[str, str]] = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update({"npm_config_update_notifier": "false", "npm_config_fund": "false", "npm_config_audit": "false"})
    if environment:
        env.update(environment)
    return env


async def run_doctor(environment: Optional[dict[str, str]] = None) -> DoctorReport:
    """Start the REA tool service, list its tools and make one real request (``rea mcp doctor``)."""

    command = rea_command()
    if command is None:
        return DoctorReport(ok=False, error="npx not found")
    try:
        code, output = await run_process(
            [*command, "mcp", "doctor", "--json"], timeout=_DOCTOR_TIMEOUT_SECONDS, env=service_env(environment),
        )
    except asyncio.TimeoutError:
        return DoctorReport(ok=False, error=f"rea mcp doctor timed out after {_DOCTOR_TIMEOUT_SECONDS:g}s")
    except OSError as exc:
        return DoctorReport(ok=False, error=str(exc))
    report = parse_doctor_report(output)
    if code != 0 and report.ok:
        return replace(report, ok=False, error=f"exit code {code}")
    return report
