"""Administrative helpers for ``flocks browser``."""

import json
import os
import socket
import tempfile
import time
import urllib.request
from pathlib import Path

from . import BROWSER_LABEL, PROJECT_ROOT, get_browser_version
from . import _ipc as ipc


NAME = os.environ.get("BU_NAME", "default")
BU_API = "https://api.browser-use.com/api/v3"
VERSION_CACHE = Path(tempfile.gettempdir()) / "flocks-browser-version-cache.json"
VERSION_CACHE_TTL = 24 * 3600
DOCTOR_TEXT_LIMIT = 140


def _load_env() -> None:
    workspace = Path(os.environ.get("BH_AGENT_WORKSPACE", "")).expanduser()
    env_paths = [PROJECT_ROOT / ".env"]
    if str(workspace):
        env_paths.append(workspace / ".env")
    for path in env_paths:
        if not path.exists():
            continue
        _load_env_file(path)


def _load_env_file(path: Path) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()


def _log_tail(name: str | None):
    try:
        return ipc.log_path(name or NAME).read_text().strip().splitlines()[-1]
    except (FileNotFoundError, IndexError):
        return None


def _needs_chrome_remote_debugging_prompt(msg: str | None) -> bool:
    """Return True when Chrome needs the inspect-page permission flow."""
    lower = (msg or "").lower()
    return (
        "devtoolsactiveport not found" in lower
        or "enable chrome://inspect" in lower
        or "not live yet" in lower
        or (
            "ws handshake failed" in lower
            and ("403" in lower or "opening handshake" in lower or "timed out" in lower or "timeout" in lower)
        )
    )


def _is_local_chrome_mode(env: dict | None = None) -> bool:
    return not (env or {}).get("BU_CDP_WS") and not os.environ.get("BU_CDP_WS")


def daemon_alive(name: str | None = None) -> bool:
    try:
        conn = ipc.connect(name or NAME, timeout=1.0)
        conn.close()
        return True
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError, socket.timeout, OSError):
        return False


def _daemon_endpoint_names() -> list[str]:
    suffix = ".port" if ipc.IS_WINDOWS else ".sock"
    if ipc.BH_TMP_DIR:
        return [NAME] if (ipc._TMP / f"bu{suffix}").exists() else []
    names: list[str] = []
    for path in sorted(ipc._TMP.glob(f"bu-*{suffix}")):
        raw = path.name[3 : -len(suffix)]
        try:
            ipc._check(raw)
        except ValueError:
            continue
        names.append(raw)
    return names


def _daemon_browser_connection(name: str):
    conn = None
    try:
        conn = ipc.connect(name, timeout=1.0)
        conn.sendall(b'{"meta":"connection_status"}\n')
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(1 << 16)
            if not chunk:
                break
            data += chunk
        response = json.loads(data)
        if "error" in response:
            return None
        page = response.get("page")
        if page:
            page = {"title": page.get("title") or "(untitled)", "url": page.get("url") or ""}
        return {"name": name, "page": page}
    except (
        FileNotFoundError,
        ConnectionRefusedError,
        TimeoutError,
        socket.timeout,
        OSError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None
    finally:
        if conn:
            conn.close()


def browser_connections() -> list[dict]:
    """Return live daemons with healthy browser connections."""
    output = []
    for name in _daemon_endpoint_names():
        conn = _daemon_browser_connection(name)
        if conn:
            output.append(conn)
    return output


def active_browser_connections() -> int:
    return len(browser_connections())


def _doctor_short_text(value, limit: int | None = None) -> str:
    limit = limit or DOCTOR_TEXT_LIMIT
    value = str(value)
    return value if len(value) <= limit else value[: limit - 3] + "..."


def ensure_daemon(wait: float = 60.0, name: str | None = None, env: dict | None = None, _open_inspect: bool = True) -> None:
    """Ensure a healthy daemon is running, restarting stale sessions when needed."""
    if daemon_alive(name):
        try:
            sock = ipc.connect(name or NAME, timeout=3.0)
            sock.sendall(b'{"method":"Target.getTargets","params":{}}\n')
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(1 << 16)
                if not chunk:
                    break
                data += chunk
            if b'"result"' in data:
                return
        except Exception:
            pass
        restart_daemon(name)

    import subprocess
    import sys

    local = _is_local_chrome_mode(env)
    for attempt in (0, 1):
        merged_env = {**os.environ, **({"BU_NAME": name} if name else {}), **(env or {})}
        proc = subprocess.Popen(
            [sys.executable, "-m", "flocks.browser.daemon"],
            env=merged_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **ipc.spawn_kwargs(),
        )
        deadline = time.time() + wait
        while time.time() < deadline:
            if daemon_alive(name):
                return
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        msg = _log_tail(name) or ""
        if local and attempt == 0 and _needs_chrome_remote_debugging_prompt(msg):
            if _open_inspect:
                _open_chrome_inspect()
            print(f"{BROWSER_LABEL}: click Allow on chrome://inspect (and tick the checkbox if shown)", file=sys.stderr)
            restart_daemon(name)
            continue
        raise RuntimeError(msg or f"daemon {name or NAME} didn't come up -- check {ipc.log_path(name or NAME)}")


def stop_remote_daemon(name: str = "remote") -> None:
    """Stop a remote daemon and its backing Browser Use cloud browser."""
    restart_daemon(name)


def restart_daemon(name: str | None = None) -> None:
    """Best-effort daemon shutdown and endpoint cleanup."""
    import signal

    pid_file = str(ipc.pid_path(name or NAME))
    try:
        conn = ipc.connect(name or NAME, timeout=5.0)
        conn.sendall(b'{"meta":"shutdown"}\n')
        conn.recv(1024)
        conn.close()
    except Exception:
        pass
    try:
        pid = int(Path(pid_file).read_text())
    except (FileNotFoundError, ValueError):
        pid = None
    if pid:
        for _ in range(75):
            try:
                os.kill(pid, 0)
                time.sleep(0.2)
            except (ProcessLookupError, OSError, SystemError):
                break
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, OSError, SystemError):
                pass
    ipc.cleanup_endpoint(name or NAME)
    try:
        os.unlink(pid_file)
    except FileNotFoundError:
        pass


def _browser_use(path: str, method: str, body=None):
    key = os.environ.get("BROWSER_USE_API_KEY")
    if not key:
        raise RuntimeError("BROWSER_USE_API_KEY missing -- see .env.example")
    req = urllib.request.Request(
        f"{BU_API}{path}",
        method=method,
        data=(json.dumps(body).encode() if body is not None else None),
        headers={"X-Browser-Use-API-Key": key, "Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=60).read() or b"{}")


def _stop_cloud_browser(browser_id: str | None) -> None:
    if not browser_id:
        return
    try:
        _browser_use(f"/browsers/{browser_id}", "PATCH", {"action": "stop"})
    except BaseException:
        pass


def _cdp_ws_from_url(cdp_url: str) -> str:
    return json.loads(urllib.request.urlopen(f"{cdp_url}/json/version", timeout=15).read())["webSocketDebuggerUrl"]


def _has_local_gui() -> bool:
    import platform

    system = platform.system()
    if system in ("Darwin", "Windows"):
        return True
    if system == "Linux":
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return False


def _show_live_url(url: str | None) -> None:
    import sys
    import webbrowser

    if not url:
        return
    print(url)
    if not _has_local_gui():
        print("(no local GUI — share the liveUrl with the user)", file=sys.stderr)
        return
    try:
        webbrowser.open(url, new=2)
        print("(opened liveUrl in your default browser)", file=sys.stderr)
    except Exception as error:
        print(f"(couldn't auto-open: {error} — share the liveUrl with the user)", file=sys.stderr)


def list_cloud_profiles() -> list[dict]:
    """List cloud profiles under the current API key."""
    output: list[dict] = []
    page = 1
    while True:
        listing = _browser_use(f"/profiles?pageSize=100&pageNumber={page}", "GET")
        items = listing.get("items") if isinstance(listing, dict) else listing
        if not items:
            break
        for item in items:
            detail = _browser_use(f"/profiles/{item['id']}", "GET")
            output.append(
                {
                    "id": detail["id"],
                    "name": detail.get("name"),
                    "userId": detail.get("userId"),
                    "cookieDomains": detail.get("cookieDomains") or [],
                    "lastUsedAt": detail.get("lastUsedAt"),
                }
            )
        if isinstance(listing, dict) and len(output) >= listing.get("totalItems", len(output)):
            break
        page += 1
    return output


def _resolve_profile_name(profile_name: str) -> str:
    matches = [profile for profile in list_cloud_profiles() if profile.get("name") == profile_name]
    if not matches:
        raise RuntimeError(
            f"no cloud profile named {profile_name!r} -- call list_cloud_profiles() or sync_local_profile() first"
        )
    if len(matches) > 1:
        raise RuntimeError(f"{len(matches)} cloud profiles named {profile_name!r} -- pass profileId=<uuid> instead")
    return matches[0]["id"]


def start_remote_daemon(name: str = "remote", profileName: str | None = None, **create_kwargs):
    """Provision a Browser Use cloud browser and attach a daemon to it."""
    if daemon_alive(name):
        raise RuntimeError(f"daemon {name!r} already alive -- restart_daemon({name!r}) first")
    if profileName:
        if "profileId" in create_kwargs:
            raise RuntimeError("pass profileName OR profileId, not both")
        create_kwargs["profileId"] = _resolve_profile_name(profileName)
    browser = _browser_use("/browsers", "POST", create_kwargs)
    try:
        ensure_daemon(
            name=name,
            env={"BU_CDP_WS": _cdp_ws_from_url(browser["cdpUrl"]), "BU_BROWSER_ID": browser["id"]},
        )
    except BaseException:
        _stop_cloud_browser(browser.get("id"))
        raise
    _show_live_url(browser.get("liveUrl"))
    return browser


def list_local_profiles():
    """List local browser profiles on this machine."""
    import shutil
    import subprocess

    if not shutil.which("profile-use"):
        raise RuntimeError("profile-use not installed -- curl -fsSL https://browser-use.com/profile.sh | sh")
    return json.loads(subprocess.check_output(["profile-use", "list", "--json"], text=True))


def sync_local_profile(
    profile_name: str,
    browser: str | None = None,
    cloud_profile_id: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> str:
    """Sync a local profile's cookies to a Browser Use cloud profile."""
    import re
    import shutil
    import subprocess
    import sys

    if not shutil.which("profile-use"):
        raise RuntimeError("profile-use not installed -- curl -fsSL https://browser-use.com/profile.sh | sh")
    if not os.environ.get("BROWSER_USE_API_KEY"):
        raise RuntimeError("BROWSER_USE_API_KEY missing")
    command = ["profile-use", "sync", "--profile", profile_name]
    if browser:
        command += ["--browser", browser]
    if cloud_profile_id:
        command += ["--cloud-profile-id", cloud_profile_id]
    for domain in include_domains or []:
        command += ["--domain", domain]
    for domain in exclude_domains or []:
        command += ["--exclude-domain", domain]
    result = subprocess.run(command, text=True, capture_output=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"profile-use sync failed (exit {result.returncode})")
    if cloud_profile_id:
        return cloud_profile_id
    match = re.search(r"Profile created:\s+([0-9a-f-]{36})", result.stdout)
    if not match:
        raise RuntimeError(f"profile-use did not report a profile UUID (exit {result.returncode})")
    return match.group(1)


def _version() -> str:
    return get_browser_version()


def _repo_dir() -> Path | None:
    for path in Path(__file__).resolve().parents:
        if (path / ".git").is_dir():
            return path
    return None


def _install_mode() -> str:
    if _repo_dir():
        return "git"
    return "pypi" if _version() != "unknown" else "unknown"


def _cache_read() -> dict:
    try:
        return json.loads(VERSION_CACHE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _cache_write(data: dict) -> None:
    try:
        VERSION_CACHE.write_text(json.dumps(data))
    except OSError:
        pass


def _latest_release_tag(force: bool = False) -> str | None:
    del force
    cache = _cache_read()
    return cache.get("tag")


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for section in (value or "").split("."):
        prefix = ""
        for char in section:
            if char.isdigit():
                prefix += char
            else:
                break
        parts.append(int(prefix) if prefix else 0)
    return tuple(parts)


def check_for_update() -> tuple[str, str | None, bool]:
    current = _version()
    latest = _latest_release_tag()
    newer = bool(current and latest and _version_tuple(latest) > _version_tuple(current))
    return current, latest, newer


def print_update_banner(out=None) -> None:
    import sys

    out = out or sys.stderr
    cache = _cache_read()
    today = time.strftime("%Y-%m-%d")
    if cache.get("banner_shown_on") == today:
        return
    current, latest, newer = check_for_update()
    if not newer:
        return
    print(f"[{BROWSER_LABEL}] update available: {current} -> {latest}", file=out)
    print(f"[{BROWSER_LABEL}] run `flocks browser --update -y` to upgrade and restart the daemon", file=out)
    _cache_write({**cache, "banner_shown_on": today})


def _chrome_running() -> bool:
    import platform
    import subprocess

    system = platform.system()
    try:
        if system == "Windows":
            output = subprocess.check_output(["tasklist"], text=True, timeout=5)
            names = ("chrome.exe", "msedge.exe")
        else:
            output = subprocess.check_output(["ps", "-A", "-o", "comm="], text=True, timeout=5)
            names = ("Google Chrome", "chrome", "chromium", "Microsoft Edge", "msedge")
        return any(name.lower() in output.lower() for name in names)
    except Exception:
        return False


def _open_chrome_inspect() -> None:
    import platform
    import subprocess
    import webbrowser

    url = "chrome://inspect/#remote-debugging"
    if platform.system() == "Darwin":
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "Google Chrome" to activate',
                    "-e",
                    f'tell application "Google Chrome" to open location "{url}"',
                ],
                timeout=5,
                check=False,
            )
            return
        except Exception:
            pass
    try:
        webbrowser.open(url, new=2)
    except Exception:
        pass


def run_setup() -> int:
    """Interactively attach to the running browser."""
    import sys

    print(f"{BROWSER_LABEL} setup: attaching to your browser...")
    if daemon_alive():
        print("daemon already running; nothing to do.")
        return 0
    if not _chrome_running():
        print(f"no Chrome/Edge process detected. please start your browser and rerun `flocks browser --setup`.")
        return 1
    try:
        ensure_daemon(wait=20.0)
        print("daemon is up.")
        return 0
    except RuntimeError as error:
        first_err = str(error)

    needs_inspect = _is_local_chrome_mode() and _needs_chrome_remote_debugging_prompt(first_err)
    if needs_inspect:
        print("chrome remote-debugging is not enabled on the current profile.")
        print("opening chrome://inspect/#remote-debugging -- in the tab that opens:")
        print("  1. if chrome shows the profile picker, pick your normal profile;")
        print("  2. tick 'Discover network targets' and click Allow if prompted.")
        _open_chrome_inspect()
    else:
        print(f"attach failed: {first_err}")
        print("retrying for up to 60s (chrome may still be starting up)...")

    deadline = time.time() + 60
    last = first_err
    while time.time() < deadline:
        try:
            ensure_daemon(wait=5.0, _open_inspect=False)
            print("daemon is up.")
            return 0
        except RuntimeError as error:
            last = str(error)
            time.sleep(2)

    print(f"setup failed: {last}", file=sys.stderr)
    print("run `flocks browser --doctor` for diagnostics.", file=sys.stderr)
    return 1


def run_doctor() -> int:
    """Read-only diagnostics. Exit 0 iff everything looks healthy."""
    import platform
    import shutil
    import sys

    current = _version()
    mode = _install_mode()
    chrome = _chrome_running()
    daemon = daemon_alive()
    connections = browser_connections()
    profile_use = shutil.which("profile-use") is not None
    api_key = bool(os.environ.get("BROWSER_USE_API_KEY"))
    latest = _latest_release_tag()
    newer = bool(current and latest and _version_tuple(latest) > _version_tuple(current))
    current_display = current or "(unknown)"

    def row(label: str, ok: bool, detail: str = "") -> None:
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")

    print(f"{BROWSER_LABEL} doctor")
    print(f"  platform          {platform.system()} {platform.release()}")
    print(f"  python            {sys.version.split()[0]}")
    print(f"  version           {current_display} ({mode})")
    if latest:
        print(f"  latest release    {latest}" + (" (update available)" if newer else ""))
    else:
        print("  latest release    (not configured)")
    row("chrome running", chrome, "" if chrome else "start chrome/edge and rerun `flocks browser --setup`")
    row("daemon alive", daemon, "" if daemon else "run `flocks browser --setup` to attach")
    row("active browser connections", bool(connections), str(len(connections)))
    for conn in connections:
        page = conn.get("page")
        if page:
            title = _doctor_short_text(page["title"])
            url = _doctor_short_text(page["url"])
            print(f"        {conn['name']} — active page: {title} — {url}")
        else:
            print(f"        {conn['name']} — active page: (no real page)")
    row(
        "profile-use installed",
        profile_use,
        "" if profile_use else "optional: curl -fsSL https://browser-use.com/profile.sh | sh",
    )
    row("BROWSER_USE_API_KEY set", api_key, "" if api_key else "optional: needed only for cloud browsers / profile sync")
    return 0 if (chrome and daemon) else 1


def _prompt_yes(question: str, default_yes: bool = True, yes: bool = False) -> bool:
    if yes:
        return True
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        answer = input(f"{question} {suffix} ").strip().lower()
    except EOFError:
        return default_yes
    if not answer:
        return default_yes
    return answer.startswith("y")


def run_update(yes: bool = False) -> int:
    """Best-effort self-update for the current Flocks install."""
    import subprocess
    import sys

    current, latest, newer = check_for_update()
    if current and latest and not newer:
        print(f"{BROWSER_LABEL} is up to date ({current}).")
        return 0
    if current and latest:
        print(f"updating {BROWSER_LABEL}: {current} -> {latest}")
    else:
        print(f"could not determine a latest published {BROWSER_LABEL} version; will try to update anyway.")

    mode = _install_mode()
    if mode == "git":
        repo = _repo_dir()
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True)
        if status.returncode != 0:
            print(f"git status failed: {status.stderr.strip()}", file=sys.stderr)
            return 1
        if status.stdout.strip():
            print(f"refusing to update: uncommitted changes in {repo}", file=sys.stderr)
            print(f"commit or stash them first, or run `git -C {repo} pull` yourself.", file=sys.stderr)
            return 1
        result = subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"])
        if result.returncode != 0:
            return result.returncode
    elif mode == "pypi":
        tool_upgrade = subprocess.run(["uv", "tool", "upgrade", "flocks"])
        if tool_upgrade.returncode != 0:
            pip = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "flocks"])
            if pip.returncode != 0:
                return pip.returncode
    else:
        print("unknown install mode; can't auto-update.", file=sys.stderr)
        return 1

    cache = _cache_read()
    cache.pop("banner_shown_on", None)
    _cache_write(cache)

    if daemon_alive():
        if _prompt_yes("restart the running daemon so it picks up the new code?", default_yes=True, yes=yes):
            restart_daemon()
            print("daemon stopped; it will auto-restart on next `flocks browser` call.")
        else:
            print("daemon left running on old code. run `flocks browser` and it will use the new code after recycle.")
    print("update complete.")
    return 0
