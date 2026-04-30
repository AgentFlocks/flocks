"""CDP websocket holder and IPC relay daemon."""

import asyncio
import json
import os
import socket
import sys
import time
import urllib.request
from collections import deque
from pathlib import Path

from cdp_use.client import CDPClient

from . import DEFAULT_AGENT_WORKSPACE
from . import _ipc as ipc


AGENT_WORKSPACE = Path(os.environ.get("BH_AGENT_WORKSPACE", DEFAULT_AGENT_WORKSPACE)).expanduser()
NAME = os.environ.get("BU_NAME", "default")
SOCK = ipc.sock_addr(NAME)
LOG = str(ipc.log_path(NAME))
PID = str(ipc.pid_path(NAME))
BUF = 500
PROFILES = [
    Path.home() / "Library/Application Support/Google/Chrome",
    Path.home() / "Library/Application Support/Comet",
    Path.home() / "Library/Application Support/Arc/User Data",
    Path.home() / "Library/Application Support/Microsoft Edge",
    Path.home() / "Library/Application Support/Microsoft Edge Beta",
    Path.home() / "Library/Application Support/Microsoft Edge Dev",
    Path.home() / "Library/Application Support/Microsoft Edge Canary",
    Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser",
    Path.home() / ".config/google-chrome",
    Path.home() / ".config/chromium",
    Path.home() / ".config/chromium-browser",
    Path.home() / ".config/microsoft-edge",
    Path.home() / ".config/microsoft-edge-beta",
    Path.home() / ".config/microsoft-edge-dev",
    Path.home() / ".var/app/org.chromium.Chromium/config/chromium",
    Path.home() / ".var/app/com.google.Chrome/config/google-chrome",
    Path.home() / ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",
    Path.home() / ".var/app/com.microsoft.Edge/config/microsoft-edge",
    Path.home() / "AppData/Local/Google/Chrome/User Data",
    Path.home() / "AppData/Local/Chromium/User Data",
    Path.home() / "AppData/Local/Microsoft/Edge/User Data",
    Path.home() / "AppData/Local/Microsoft/Edge Beta/User Data",
    Path.home() / "AppData/Local/Microsoft/Edge Dev/User Data",
    Path.home() / "AppData/Local/Microsoft/Edge SxS/User Data",
]
INTERNAL = ("chrome://", "chrome-untrusted://", "devtools://", "chrome-extension://", "about:")
BU_API = "https://api.browser-use.com/api/v3"
REMOTE_ID = os.environ.get("BU_BROWSER_ID")
API_KEY = os.environ.get("BROWSER_USE_API_KEY")
MARKER = "🟢"


def _load_env() -> None:
    for path in (Path(__file__).resolve().parents[2] / ".env", AGENT_WORKSPACE / ".env"):
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


def log(msg: str) -> None:
    Path(LOG).open("a", encoding="utf-8").write(f"{msg}\n")


async def _silent(coro) -> None:
    try:
        await coro
    except Exception:
        pass


def get_ws_url() -> str:
    if url := os.environ.get("BU_CDP_WS"):
        return url
    if url := os.environ.get("BU_CDP_URL"):
        deadline = time.time() + 30
        last_err = None
        while time.time() < deadline:
            try:
                return json.loads(urllib.request.urlopen(f"{url}/json/version", timeout=5).read())["webSocketDebuggerUrl"]
            except Exception as error:
                last_err = error
                time.sleep(1)
        raise RuntimeError(
            f"BU_CDP_URL={url} unreachable after 30s: {last_err} -- is the dedicated automation Chrome running?"
        )
    for base in PROFILES:
        try:
            port, path = (base / "DevToolsActivePort").read_text().strip().split("\n", 1)
        except (FileNotFoundError, NotADirectoryError):
            continue
        deadline = time.time() + 30
        while True:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.settimeout(1)
            try:
                probe.connect(("127.0.0.1", int(port.strip())))
                break
            except OSError:
                if time.time() >= deadline:
                    raise RuntimeError(
                        "Chrome's remote-debugging page is open, but DevTools is not live yet on "
                        f"127.0.0.1:{port.strip()} — if Chrome opened a profile picker, choose your normal "
                        "profile first, then tick the checkbox and click Allow if shown"
                    )
                time.sleep(1)
            finally:
                probe.close()
        return f"ws://127.0.0.1:{port.strip()}{path.strip()}"
    for probe_port in (9222, 9223):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{probe_port}/json/version", timeout=1) as response:
                return json.loads(response.read())["webSocketDebuggerUrl"]
        except (OSError, KeyError, ValueError):
            continue
    raise RuntimeError(
        "DevToolsActivePort not found in "
        f"{[str(path) for path in PROFILES]} — enable chrome://inspect/#remote-debugging, "
        "or set BU_CDP_WS for a remote browser"
    )


def stop_remote() -> None:
    if not REMOTE_ID or not API_KEY:
        return
    try:
        req = urllib.request.Request(
            f"{BU_API}/browsers/{REMOTE_ID}",
            data=json.dumps({"action": "stop"}).encode(),
            method="PATCH",
            headers={"X-Browser-Use-API-Key": API_KEY, "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15).read()
        log(f"stopped remote browser {REMOTE_ID}")
    except Exception as error:
        log(f"stop_remote failed ({REMOTE_ID}): {error}")


def is_real_page(target: dict) -> bool:
    return target["type"] == "page" and not target.get("url", "").startswith(INTERNAL)


class Daemon:
    """Long-lived CDP client that serves simple JSON IPC requests."""

    def __init__(self) -> None:
        self.cdp = None
        self.session = None
        self.target_id = None
        self.events = deque(maxlen=BUF)
        self.dialog = None
        self.stop = None

    async def attach_first_page(self):
        targets = (await self.cdp.send_raw("Target.getTargets"))["targetInfos"]
        pages = [target for target in targets if is_real_page(target)]
        if not pages:
            target_id = (await self.cdp.send_raw("Target.createTarget", {"url": "about:blank"}))["targetId"]
            log(f"no real pages found, created about:blank ({target_id})")
            pages = [{"targetId": target_id, "url": "about:blank", "type": "page"}]
        self.session = (
            await self.cdp.send_raw("Target.attachToTarget", {"targetId": pages[0]["targetId"], "flatten": True})
        )["sessionId"]
        self.target_id = pages[0]["targetId"]
        log(f"attached {pages[0]['targetId']} ({pages[0].get('url', '')[:80]}) session={self.session}")
        for domain in ("Page", "DOM", "Runtime", "Network"):
            try:
                await asyncio.wait_for(self.cdp.send_raw(f"{domain}.enable", session_id=self.session), timeout=5)
            except Exception as error:
                log(f"enable {domain}: {error}")
        return pages[0]

    async def start(self) -> None:
        self.stop = asyncio.Event()
        url = get_ws_url()
        log(f"connecting to {url}")
        self.cdp = CDPClient(url)
        try:
            await self.cdp.start()
        except Exception as error:
            if os.environ.get("BU_CDP_WS"):
                raise RuntimeError(
                    f"CDP WS handshake failed: {error} -- remote browser WebSocket connection failed. "
                    "This can happen when network policy blocks the connection, the WS URL is wrong or expired, "
                    "or the remote endpoint is down. If you use Browser Use cloud, verify "
                    "BROWSER_USE_API_KEY and get a fresh URL via start_remote_daemon()."
                ) from error
            raise RuntimeError(f"CDP WS handshake failed: {error} -- click Allow in Chrome if prompted, then retry")
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
            try:
                await asyncio.wait_for(self.cdp.send_raw("Page.enable", session_id=self.session), timeout=3)
                await asyncio.wait_for(
                    self.cdp.send_raw(
                        "Runtime.evaluate",
                        {"expression": f"if(!document.title.startsWith('{MARKER}'))document.title='{MARKER} '+document.title"},
                        session_id=self.session,
                    ),
                    timeout=2,
                )
            except Exception:
                pass
            return {"session_id": self.session}
        if meta == "pending_dialog":
            return {"dialog": self.dialog}
        if meta == "shutdown":
            self.stop.set()
            return {"ok": True}

        method = req["method"]
        params = req.get("params") or {}
        session_id = None if method.startswith("Target.") else (req.get("session_id") or self.session)
        try:
            return {"result": await self.cdp.send_raw(method, params, session_id=session_id)}
        except Exception as error:
            msg = str(error)
            if "Session with given id not found" in msg and session_id == self.session and session_id:
                log(f"stale session {session_id}, re-attaching")
                if await self.attach_first_page():
                    return {"result": await self.cdp.send_raw(method, params, session_id=self.session)}
            return {"error": msg}


async def serve(daemon: Daemon) -> None:
    async def handler(reader, writer):
        try:
            line = await reader.readline()
            if not line:
                return
            resp = await daemon.handle(json.loads(line))
            writer.write((json.dumps(resp, default=str) + "\n").encode())
            await writer.drain()
        except Exception as error:
            log(f"conn: {error}")
            try:
                writer.write((json.dumps({"error": str(error)}) + "\n").encode())
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()

    serve_task = asyncio.create_task(ipc.serve(NAME, handler))
    stop_task = asyncio.create_task(daemon.stop.wait())
    await asyncio.sleep(0.05)
    log(f"listening on {ipc.sock_addr(NAME)} (name={NAME}, remote={REMOTE_ID or 'local'})")
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
        ipc.cleanup_endpoint(NAME)


async def main() -> None:
    daemon = Daemon()
    await daemon.start()
    await serve(daemon)


def already_running() -> bool:
    try:
        sock = ipc.connect(NAME, timeout=1.0)
        sock.close()
        return True
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError, socket.timeout, OSError):
        return False


if __name__ == "__main__":
    if already_running():
        print(f"daemon already running on {SOCK}", file=sys.stderr)
        sys.exit(0)
    Path(LOG).write_text("", encoding="utf-8")
    Path(PID).write_text(str(os.getpid()), encoding="utf-8")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as error:
        log(f"fatal: {error}")
        sys.exit(1)
    finally:
        stop_remote()
        try:
            os.unlink(PID)
        except FileNotFoundError:
            pass
