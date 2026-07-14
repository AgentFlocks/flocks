"""CDP websocket holder and IPC relay daemon."""

import asyncio
import json
import os
import platform
import signal
import socket
import sys
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from collections import deque
from pathlib import Path

from cdp_use.client import CDPClient

from . import DEFAULT_AGENT_WORKSPACE, INTERNAL_URL_PREFIXES
from . import _ipc as ipc
from .utils import load_env_file


AGENT_WORKSPACE = Path(os.environ.get("BH_AGENT_WORKSPACE", DEFAULT_AGENT_WORKSPACE)).expanduser()
BUF = 500
INTERNAL = INTERNAL_URL_PREFIXES
MARKER = "🟢"


def _load_env() -> None:
    for path in (Path(__file__).resolve().parents[2] / ".env", AGENT_WORKSPACE / ".env"):
        if not path.exists():
            continue
        load_env_file(path)


_load_env()

NAME = ipc.runtime_paths().name
SOCK = ipc.sock_addr(NAME)
LOG = str(ipc.log_path(NAME))
PID = str(ipc.pid_path(NAME))


def profile_dirs(
    system: str | None = None,
    home: Path | None = None,
    environ: dict[str, str] | None = None,
) -> list[Path]:
    """Return only the browser profile directories for the active OS."""
    system = system or platform.system()
    home = home or Path.home()
    environ = os.environ if environ is None else environ
    if system == "Darwin":
        support = home / "Library/Application Support"
        return [
            support / "Google/Chrome",
            support / "Google/Chrome Canary",
            support / "Comet",
            support / "Arc/User Data",
            support / "Microsoft Edge",
            support / "Microsoft Edge Beta",
            support / "Microsoft Edge Dev",
            support / "Microsoft Edge Canary",
            support / "BraveSoftware/Brave-Browser",
            support / "Chromium",
        ]
    if system == "Windows":
        local = Path(environ.get("LOCALAPPDATA", str(home / "AppData/Local"))).expanduser()
        return [
            local / "Google/Chrome/User Data",
            local / "Google/Chrome Beta/User Data",
            local / "Google/Chrome Dev/User Data",
            local / "Google/Chrome SxS/User Data",
            local / "Chromium/User Data",
            local / "Microsoft/Edge/User Data",
            local / "Microsoft/Edge Beta/User Data",
            local / "Microsoft/Edge Dev/User Data",
            local / "Microsoft/Edge SxS/User Data",
            local / "BraveSoftware/Brave-Browser/User Data",
            local / "BraveSoftware/Brave-Browser-Beta/User Data",
            local / "BraveSoftware/Brave-Browser-Nightly/User Data",
        ]
    return [
        home / ".config/google-chrome",
        home / ".config/google-chrome-beta",
        home / ".config/google-chrome-unstable",
        home / ".config/chromium",
        home / ".config/chromium-browser",
        home / ".config/microsoft-edge",
        home / ".config/microsoft-edge-beta",
        home / ".config/microsoft-edge-dev",
        home / ".config/BraveSoftware/Brave-Browser",
        home / ".var/app/org.chromium.Chromium/config/chromium",
        home / ".var/app/com.google.Chrome/config/google-chrome",
        home / ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",
        home / ".var/app/com.microsoft.Edge/config/microsoft-edge",
    ]


def log(msg: str) -> None:
    paths = ipc.runtime_paths(NAME)
    try:
        paths.ensure_root()
        with paths.log.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"{msg}\n")
    except OSError:
        print(msg, file=sys.stderr)


async def _silent(coro) -> None:
    try:
        await coro
    except Exception:
        pass


def _local_cdp_ws_url(port: int) -> str:
    """Return a validated local browser WebSocket URL discovered over HTTP."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise ValueError("invalid CDP version response")
    websocket_url = payload.get("webSocketDebuggerUrl")
    if not isinstance(websocket_url, str):
        raise ValueError("invalid webSocketDebuggerUrl")
    parsed = urllib.parse.urlparse(websocket_url)
    if (
        parsed.scheme not in {"ws", "wss"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port != port
    ):
        raise ValueError("invalid webSocketDebuggerUrl")
    return websocket_url


def get_ws_url() -> str:
    if url := os.environ.get("BU_CDP_WS"):
        return url
    if url := os.environ.get("BU_CDP_URL"):
        deadline = time.time() + 30
        last_err = None
        while time.time() < deadline:
            try:
                return json.loads(urllib.request.urlopen(f"{url}/json/version", timeout=5).read())[
                    "webSocketDebuggerUrl"
                ]
            except Exception as error:
                last_err = error
                time.sleep(1)
        raise RuntimeError(
            f"BU_CDP_URL={url} unreachable after 30s: {last_err} -- is the dedicated automation browser running?"
        )
    profiles = profile_dirs()
    profile_candidates = []
    profile_errors = []
    for base in profiles:
        try:
            port, path = (
                (base / "DevToolsActivePort").read_text(encoding="utf-8-sig", errors="replace").strip().split("\n", 1)
            )
        except (FileNotFoundError, NotADirectoryError):
            continue
        except ValueError:
            profile_errors.append(f"{base}: invalid DevToolsActivePort")
            continue
        try:
            port_number = int(port.strip())
            if not 1 <= port_number <= 65535 or not path.strip():
                raise ValueError
        except ValueError:
            profile_errors.append(f"{base}: invalid DevToolsActivePort")
            continue
        profile_candidates.append((base, port_number))

    deadline = time.time() + 30
    while profile_candidates:
        for base, port in profile_candidates:
            try:
                return _local_cdp_ws_url(port)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                profile_errors.append(f"{base}: 127.0.0.1:{port} ({error})")
        if time.time() >= deadline:
            break
        time.sleep(1)
    for probe_port in (9222, 9223):
        try:
            return _local_cdp_ws_url(probe_port)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    if profile_errors:
        details = "; ".join(dict.fromkeys(profile_errors))
        raise RuntimeError(
            "The browser's remote-debugging page is open, but DevTools is not live yet for any detected profile: "
            f"{details} — if the browser opened a profile picker, choose your normal profile first, then tick the "
            "checkbox and click Allow if shown"
        )
    raise RuntimeError(
        "DevToolsActivePort not found in "
        f"{[str(path) for path in profiles]} — enable your browser's remote-debugging page "
        "(for example chrome://inspect/#remote-debugging or edge://inspect/#remote-debugging), "
        "or set BU_CDP_WS for a remote browser"
    )


def is_real_page(target: dict) -> bool:
    return target["type"] == "page" and not target.get("url", "").startswith(INTERNAL)


class Daemon:
    """Long-lived CDP client that serves simple JSON IPC requests."""

    def __init__(self, name: str = NAME) -> None:
        self.name = ipc.runtime_paths(name).name
        self.instance_id = uuid.uuid4().hex
        self.browser_kind = "cdp" if os.environ.get("BU_CDP_WS") or os.environ.get("BU_CDP_URL") else "local"
        self.cdp = None
        self.session = None
        self.target_id = None
        self.managed_tabs = {}
        self.events = deque(maxlen=BUF)
        self.dialog = None
        self.stop = asyncio.Event()

    async def _enable_session_domains(self) -> None:
        for domain in ("Page", "DOM", "Runtime", "Network"):
            try:
                await asyncio.wait_for(self.cdp.send_raw(f"{domain}.enable", session_id=self.session), timeout=5)
            except Exception as error:
                log(f"enable {domain}: {error}")

    async def _attach_target(self, target_id: str) -> dict:
        self.session = (await self.cdp.send_raw("Target.attachToTarget", {"targetId": target_id, "flatten": True}))[
            "sessionId"
        ]
        self.target_id = target_id
        try:
            info = (await self.cdp.send_raw("Target.getTargetInfo", {"targetId": target_id}))["targetInfo"]
        except Exception:
            info = {"targetId": target_id, "url": "", "title": "(unknown)", "type": "page"}
        log(f"attached {target_id} ({info.get('url', '')[:80]}) session={self.session}")
        await self._enable_session_domains()
        return info

    async def attach_first_page(self):
        targets = (await self.cdp.send_raw("Target.getTargets"))["targetInfos"]
        pages = [target for target in targets if is_real_page(target)]
        if not pages:
            target_id = (await self.cdp.send_raw("Target.createTarget", {"url": "about:blank"}))["targetId"]
            log(f"no real pages found, created about:blank ({target_id})")
            pages = [{"targetId": target_id, "url": "about:blank", "type": "page"}]
        return await self._attach_target(pages[0]["targetId"])

    async def start(self) -> None:
        url = get_ws_url()
        log(f"connecting to {url}")
        self.cdp = CDPClient(url)
        try:
            await self.cdp.start()
        except Exception as error:
            if os.environ.get("BU_CDP_WS") or os.environ.get("BU_CDP_URL"):
                msg = str(error)
                hint = (
                    " If the endpoint comes from a dedicated headless Chrome/Chromium instance and the server returns "
                    "HTTP 403, restart it with '--remote-allow-origins=*'."
                    if "403" in msg
                    else ""
                )
                raise RuntimeError(
                    f"CDP WS handshake failed: {error} -- remote browser WebSocket connection failed. "
                    "This can happen when network policy blocks the connection, the WS URL is wrong or expired, "
                    "or the remote endpoint is down. Verify BU_CDP_WS and refresh the remote session if needed."
                    f"{hint}"
                ) from error
            raise RuntimeError(
                f"CDP WS handshake failed: {error} -- click Allow in your browser if prompted, then retry"
            )
        await self.attach_first_page()
        orig = self.cdp._event_registry.handle_event
        mark_js = f"if(!document.title.startsWith('{MARKER}'))document.title='{MARKER} '+document.title"

        async def tap(method, params, session_id=None):
            self.events.append({"method": method, "params": params, "session_id": session_id})
            if method == "Page.javascriptDialogOpening":
                self.dialog = params
            elif method == "Page.javascriptDialogClosed":
                self.dialog = None
            elif method in ("Page.loadEventFired", "Page.domContentEventFired"):
                asyncio.create_task(
                    _silent(
                        asyncio.wait_for(
                            self.cdp.send_raw("Runtime.evaluate", {"expression": mark_js}, session_id=self.session),
                            timeout=2,
                        )
                    )
                )
            return await orig(method, params, session_id)

        self.cdp._event_registry.handle_event = tap

    async def handle(self, req: dict) -> dict:
        meta = req.get("meta")
        if meta == "ping":
            return {
                "pong": True,
                "protocol_version": ipc.PROTOCOL_VERSION,
                "name": self.name,
                "pid": os.getpid(),
                "instance_id": self.instance_id,
                "browser_kind": self.browser_kind,
            }
        if meta == "drain_events":
            output = list(self.events)
            self.events.clear()
            return {"events": output}
        if meta == "session":
            return {"session_id": self.session}
        if meta == "connection_status":
            if not self.target_id:
                return {"error": "not_attached"}
            try:
                info = (await self.cdp.send_raw("Target.getTargetInfo", {"targetId": self.target_id}))["targetInfo"]
            except Exception:
                return {"error": "cdp_disconnected"}
            page = None
            if is_real_page(info):
                page = {
                    "targetId": info.get("targetId"),
                    "title": info.get("title") or "(untitled)",
                    "url": info.get("url") or "",
                }
            return {"target_id": self.target_id, "session_id": self.session, "page": page}
        if meta == "set_session":
            self.session = req.get("session_id")
            self.target_id = req.get("target_id") or self.target_id
            if self.target_id in self.managed_tabs:
                self.managed_tabs[self.target_id]["last_accessed"] = time.time()
            try:
                await asyncio.wait_for(self.cdp.send_raw("Page.enable", session_id=self.session), timeout=3)
                await asyncio.wait_for(
                    self.cdp.send_raw(
                        "Runtime.evaluate",
                        {
                            "expression": f"if(!document.title.startsWith('{MARKER}'))document.title='{MARKER} '+document.title"
                        },
                        session_id=self.session,
                    ),
                    timeout=2,
                )
            except Exception:
                pass
            return {"session_id": self.session}
        if meta == "managed_tabs":
            return {
                "tabs": [{"targetId": target_id, **entry} for target_id, entry in sorted(self.managed_tabs.items())]
            }
        if meta == "register_managed_tab":
            target_id = req.get("target_id")
            if not target_id:
                return {"error": "target_id is required"}
            now = time.time()
            existing = self.managed_tabs.get(target_id, {})
            url = req.get("url") or existing.get("url") or ""
            self.managed_tabs[target_id] = {
                "url": url,
                "current_url": existing.get("current_url", url),
                "created_at": existing.get("created_at", now),
                "last_accessed": now,
            }
            return {"tab": {"targetId": target_id, **self.managed_tabs[target_id]}}
        if meta == "touch_managed_tab":
            target_id = req.get("target_id")
            entry = self.managed_tabs.get(target_id)
            if not entry:
                return {"tab": None}
            entry["last_accessed"] = time.time()
            if "url" in req and req.get("url") is not None:
                entry["current_url"] = req.get("url") or ""
            return {"tab": {"targetId": target_id, **entry}}
        if meta == "remove_managed_tab":
            target_id = req.get("target_id")
            removed = self.managed_tabs.pop(target_id, None)
            return {"removed": bool(removed)}
        if meta == "pending_dialog":
            return {"dialog": self.dialog}
        if meta == "shutdown":
            self.stop.set()
            return {"ok": True}
        if meta is not None:
            return {"error": f"unknown meta request: {meta}"}

        method = req["method"]
        params = req.get("params") or {}
        session_id = None if method.startswith("Target.") else (req.get("session_id") or self.session)
        try:
            return {"result": await self.cdp.send_raw(method, params, session_id=session_id)}
        except Exception as error:
            msg = str(error)
            if "Session with given id not found" in msg and session_id == self.session and session_id:
                log(f"stale session {session_id}, re-attaching")
                if self.target_id:
                    try:
                        await self._attach_target(self.target_id)
                    except Exception as reattach_error:
                        log(f"reattach {self.target_id}: {reattach_error}")
                    else:
                        return {"result": await self.cdp.send_raw(method, params, session_id=self.session)}
                if await self.attach_first_page():
                    return {"result": await self.cdp.send_raw(method, params, session_id=self.session)}
            return {"error": msg}

    async def close(self) -> None:
        """Close the CDP client and its background tasks."""
        if self.cdp is None:
            return
        try:
            await self.cdp.stop()
        except Exception as error:
            log(f"CDP client stop failed: {error}")
        finally:
            self.cdp = None


async def serve(daemon: Daemon, lock: ipc.DaemonLock) -> None:
    serve_task = asyncio.create_task(ipc.serve(NAME, daemon.handle, lock))
    stop_task = asyncio.create_task(daemon.stop.wait())
    await asyncio.sleep(0.05)
    log(f"listening on {ipc.sock_addr(NAME)} (name={NAME})")
    try:
        await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if serve_task.done():
            await serve_task
    finally:
        for task in (serve_task, stop_task):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


async def main(lock: ipc.DaemonLock) -> None:
    daemon = Daemon()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, daemon.stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                signal.signal(signal_number, lambda _signum, _frame: loop.call_soon_threadsafe(daemon.stop.set))
            except (OSError, RuntimeError, ValueError):
                pass
    try:
        await daemon.start()
        await serve(daemon, lock)
    finally:
        await daemon.close()


def already_running() -> bool:
    try:
        sock = ipc.connect(NAME, timeout=1.0)
        sock.close()
        return True
    except (
        ipc.EndpointRecordError,
        FileNotFoundError,
        ConnectionRefusedError,
        TimeoutError,
        socket.timeout,
        OSError,
    ):
        return False


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def _remove_own_pid() -> None:
    path = ipc.pid_path(NAME)
    try:
        recorded_pid = int(path.read_text(encoding="utf-8", errors="replace").strip())
    except (FileNotFoundError, ValueError):
        return
    if recorded_pid == os.getpid():
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    _configure_stdio()
    daemon_lock = ipc.DaemonLock(NAME)
    owns_runtime = False
    if not daemon_lock.acquire():
        print(f"daemon startup already in progress for {NAME!r}", file=sys.stderr)
        sys.exit(ipc.LOCK_BUSY_EXIT_CODE)
    try:
        if already_running():
            print(f"daemon already running on {SOCK}", file=sys.stderr)
            sys.exit(0)
        log(f"--- starting browser daemon name={NAME} pid={os.getpid()} ---")
        ipc.write_pid(NAME, os.getpid())
        owns_runtime = True
        asyncio.run(main(daemon_lock))
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
        log(f"fatal:\n{traceback.format_exc()}")
        sys.exit(1)
    finally:
        if owns_runtime:
            _remove_own_pid()
            ipc.cleanup_endpoint(NAME, daemon_lock)
        daemon_lock.release()
