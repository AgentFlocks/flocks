"""Daemon IPC plumbing. AF_UNIX on POSIX, TCP loopback on Windows."""

import asyncio
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


IS_WINDOWS = sys.platform == "win32"
BH_TMP_DIR = os.environ.get("BH_TMP_DIR")
_TMP = Path(BH_TMP_DIR or (tempfile.gettempdir() if IS_WINDOWS else "/tmp"))
_TMP.mkdir(parents=True, exist_ok=True)
_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")


def _check(name: str) -> str:
    """Validate daemon names used in socket paths and filenames."""
    if not _NAME_RE.match(name or ""):
        raise ValueError(f"invalid BU_NAME {name!r}: must match [A-Za-z0-9_-]{{1,64}}")
    return name


def _stem(name: str) -> str:
    """Return the daemon file stem for the given browser session name."""
    _check(name)
    return "bu" if BH_TMP_DIR else f"bu-{name}"


def log_path(name: str) -> Path:
    return _TMP / f"{_stem(name)}.log"


def pid_path(name: str) -> Path:
    return _TMP / f"{_stem(name)}.pid"


def port_path(name: str) -> Path:
    return _TMP / f"{_stem(name)}.port"


def _sock_path(name: str) -> Path:
    return _TMP / f"{_stem(name)}.sock"


def sock_addr(name: str) -> str:
    """Return a human-readable endpoint address for logs."""
    if not IS_WINDOWS:
        return str(_sock_path(name))
    try:
        return f"127.0.0.1:{port_path(name).read_text().strip()}"
    except FileNotFoundError:
        return f"tcp:{_stem(name)}"


def spawn_kwargs() -> dict[str, object]:
    """Return subprocess flags that keep the daemon detached from the terminal."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def connect(name: str, timeout: float = 1.0) -> socket.socket:
    """Connect to a browser daemon endpoint."""
    if not IS_WINDOWS:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(_sock_path(name)))
        return sock
    try:
        port = int(port_path(name).read_text().strip())
    except (FileNotFoundError, ValueError) as error:
        raise FileNotFoundError(str(port_path(name))) from error
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    sock.settimeout(timeout)
    return sock


async def serve(name: str, handler) -> None:
    """Serve daemon requests until cancelled."""
    if not IS_WINDOWS:
        path = str(_sock_path(name))
        if os.path.exists(path):
            os.unlink(path)
        server = await asyncio.start_unix_server(handler, path=path)
        os.chmod(path, 0o600)
        async with server:
            await asyncio.Event().wait()
        return

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port_file = port_path(name)
    port_file.write_text(str(server.sockets[0].getsockname()[1]))
    try:
        async with server:
            await asyncio.Event().wait()
    finally:
        try:
            port_file.unlink()
        except FileNotFoundError:
            pass


def cleanup_endpoint(name: str) -> None:
    """Best-effort daemon endpoint cleanup."""
    path = _sock_path(name) if not IS_WINDOWS else port_path(name)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
