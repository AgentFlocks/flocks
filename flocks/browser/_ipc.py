"""Authenticated daemon IPC over AF_UNIX on POSIX and loopback TCP on Windows."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .paths import BrowserRuntimePaths, browser_dir, resolve_name, validate_name


IS_WINDOWS = sys.platform == "win32"
PROTOCOL_VERSION = 1
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
LOCK_BUSY_EXIT_CODE = 75

# Compatibility aliases for callers that only need the default location. Runtime
# operations resolve paths lazily so FLOCKS_ROOT changes remain testable.
BU_TMP_DIR = str(browser_dir())


class IPCProtocolError(RuntimeError):
    """Raised when an endpoint does not speak the current daemon protocol."""


class LegacyProtocolError(IPCProtocolError):
    """Raised when a responsive daemon explicitly lacks the ping protocol."""


class EndpointRecordError(RuntimeError):
    """Raised when a local Windows endpoint record is malformed."""


class DaemonLock:
    """Cross-platform advisory lock held for a daemon's entire lifetime."""

    def __init__(self, name: str | None = None) -> None:
        self.paths = BrowserRuntimePaths.resolve(name)
        self._file = None
        self.acquired = False

    def acquire(self, blocking: bool = False) -> bool:
        if self.acquired:
            return True
        self.paths.ensure_root()
        handle = self.paths.lock.open("a+b")
        try:
            os.chmod(self.paths.lock, 0o600)
        except OSError:
            pass
        try:
            if IS_WINDOWS:
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(handle.fileno(), mode, 1)
            else:
                import fcntl

                flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                fcntl.flock(handle.fileno(), flags)
        except (OSError, BlockingIOError):
            handle.close()
            return False
        self._file = handle
        self.acquired = True
        return True

    def release(self) -> None:
        if not self.acquired or self._file is None:
            return
        try:
            if IS_WINDOWS:
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None
            self.acquired = False

    def __enter__(self) -> DaemonLock:
        if not self.acquire():
            raise BlockingIOError(f"browser daemon lock is held: {self.paths.lock}")
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


def runtime_paths(name: str | None = None) -> BrowserRuntimePaths:
    return BrowserRuntimePaths.resolve(name)


def runtime_dir() -> Path:
    return browser_dir()


def log_path(name: str | None = None) -> Path:
    return runtime_paths(name).log


def pid_path(name: str | None = None) -> Path:
    return runtime_paths(name).pid


def port_path(name: str | None = None) -> Path:
    return runtime_paths(name).port


def screenshot_path(name: str | None = None) -> Path:
    return runtime_paths(name).screenshot


def debug_screenshot_path(sequence: int, name: str | None = None) -> Path:
    return runtime_paths(name).debug_screenshot(sequence)


def sock_addr(name: str | None = None) -> str:
    """Return a human-readable endpoint address for logs."""
    paths = runtime_paths(name)
    if not IS_WINDOWS:
        return str(paths.socket)
    try:
        port, _token = _read_port_record(paths.port)
        return f"127.0.0.1:{port}"
    except (FileNotFoundError, EndpointRecordError):
        return f"tcp:{paths.stem}"


def spawn_kwargs() -> dict[str, object]:
    """Return subprocess flags that keep the daemon detached from the terminal."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def _read_port_record(path: Path) -> tuple[int, str | None]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    try:
        record = json.loads(raw)
    except json.JSONDecodeError:
        record = raw
    if isinstance(record, int) and not isinstance(record, bool):
        port = record
        token = None
    elif isinstance(record, str):
        try:
            port = int(record)
        except ValueError as error:
            raise EndpointRecordError(f"invalid browser daemon port file: {path}") from error
        token = None
    elif isinstance(record, dict):
        port = record.get("port")
        token = record.get("token")
        if type(port) is not int or not isinstance(token, str) or not token:
            raise EndpointRecordError(f"invalid browser daemon port record: {path}")
    else:
        raise EndpointRecordError(f"invalid browser daemon port record: {path}")
    if not 0 < port < 65536:
        raise EndpointRecordError(f"invalid browser daemon port: {port}")
    return port, token


def _connect_with_token(name: str | None, timeout: float) -> tuple[socket.socket, str | None]:
    paths = runtime_paths(name)
    if not IS_WINDOWS:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(paths.socket))
        except Exception:
            sock.close()
            raise
        return sock, None
    port, token = _read_port_record(paths.port)
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    sock.settimeout(timeout)
    return sock, token


def connect(name: str | None = None, timeout: float = 1.0) -> socket.socket:
    """Connect to a browser daemon endpoint."""
    sock, _token = _connect_with_token(name, timeout)
    return sock


def request(name: str | None, payload: dict[str, Any], timeout: float = 3.0) -> dict[str, Any]:
    """Send one JSON request and return one JSON response."""
    sock, token = _connect_with_token(name, timeout)
    try:
        outgoing = dict(payload)
        if token:
            outgoing["_ipc_token"] = token
        sock.sendall((json.dumps(outgoing, separators=(",", ":")) + "\n").encode("utf-8"))
        data = bytearray()
        while not data.endswith(b"\n"):
            chunk = sock.recv(1 << 16)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_RESPONSE_BYTES:
                raise IPCProtocolError("browser daemon response exceeded size limit")
        if not data:
            raise IPCProtocolError("browser daemon returned an empty response")
        response = json.loads(data.decode("utf-8", errors="replace"))
        if not isinstance(response, dict):
            raise IPCProtocolError("browser daemon response must be a JSON object")
        return response
    except json.JSONDecodeError as error:
        raise IPCProtocolError("browser daemon returned invalid JSON") from error
    finally:
        sock.close()


def identify(name: str | None = None, timeout: float = 1.0) -> dict[str, Any]:
    """Return a strictly validated identity for a current daemon."""
    expected_name = resolve_name(name)
    response = request(expected_name, {"meta": "ping"}, timeout=timeout)
    pid = response.get("pid")
    protocol_version = response.get("protocol_version")
    instance_id = response.get("instance_id")
    browser_kind = response.get("browser_kind")
    if response.get("pong") is not True:
        error = str(response.get("error") or "")
        if error.startswith("unknown meta") or error in {"'method'", "method"}:
            raise LegacyProtocolError("browser daemon predates the ping protocol")
        raise IPCProtocolError("browser daemon does not support ping")
    if response.get("name") != expected_name:
        raise IPCProtocolError("browser daemon session identity mismatch")
    if type(pid) is not int or pid <= 0:
        raise IPCProtocolError("browser daemon returned an invalid PID")
    if type(protocol_version) is not int or protocol_version < 1:
        raise IPCProtocolError("browser daemon returned an invalid protocol version")
    if not isinstance(instance_id, str) or not instance_id:
        raise IPCProtocolError("browser daemon returned an invalid instance ID")
    if browser_kind not in {"local", "cdp"}:
        raise IPCProtocolError("browser daemon returned an invalid browser kind")
    return response


def endpoint_reachable(name: str | None = None, timeout: float = 1.0) -> bool:
    try:
        sock = connect(name, timeout=timeout)
    except (
        EndpointRecordError,
        IPCProtocolError,
        FileNotFoundError,
        ConnectionRefusedError,
        TimeoutError,
        socket.timeout,
        OSError,
    ):
        return False
    sock.close()
    return True


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace a private UTF-8 text file."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def write_pid(name: str | None, pid: int) -> None:
    paths = runtime_paths(name)
    paths.ensure_root()
    atomic_write_text(paths.pid, str(pid))


async def serve(
    name: str | None,
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    lock: DaemonLock,
) -> None:
    """Serve authenticated JSON requests while the caller owns ``lock``."""
    paths = runtime_paths(name)
    if not lock.acquired or lock.paths != paths:
        raise RuntimeError("browser daemon endpoint requires its acquired session lock")
    token = secrets.token_urlsafe(32) if IS_WINDOWS else None

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            req = json.loads(line.decode("utf-8", errors="replace"))
            if not isinstance(req, dict):
                raise IPCProtocolError("request must be a JSON object")
            supplied_token = req.pop("_ipc_token", None)
            if token is not None and not secrets.compare_digest(str(supplied_token or ""), token):
                response = {"error": "unauthorized browser daemon request"}
            else:
                response = await handler(req)
            writer.write((json.dumps(response, default=str) + "\n").encode("utf-8"))
            await writer.drain()
        except Exception as error:
            try:
                writer.write((json.dumps({"error": str(error)}) + "\n").encode("utf-8"))
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    server = None
    try:
        paths.ensure_root()
        if not IS_WINDOWS:
            paths.socket.unlink(missing_ok=True)
            old_umask = os.umask(0o077)
            try:
                server = await asyncio.start_unix_server(handle_client, path=str(paths.socket))
            finally:
                os.umask(old_umask)
            os.chmod(paths.socket, 0o600)
        else:
            server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            atomic_write_text(paths.port, json.dumps({"port": port, "token": token}))
        async with server:
            await server.serve_forever()
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()
        cleanup_endpoint(name, lock)


def cleanup_endpoint(name: str | None, lock: DaemonLock) -> None:
    """Remove an endpoint only while holding its session lock."""
    paths = runtime_paths(name)
    if not lock.acquired or lock.paths != paths:
        raise RuntimeError("browser daemon endpoint cleanup requires its acquired session lock")
    path = paths.port if IS_WINDOWS else paths.socket
    path.unlink(missing_ok=True)


def discover_names() -> list[str]:
    """Return session names with endpoint or PID artifacts."""
    root = runtime_dir()
    if not root.is_dir():
        return []
    names: set[str] = set()
    for suffix in {".port", ".pid"} if IS_WINDOWS else {".sock", ".pid"}:
        if (root / f"bu{suffix}").exists():
            names.add("default")
        for path in root.glob(f"bu-*{suffix}"):
            raw = path.name[3 : -len(suffix)]
            try:
                names.add(validate_name(raw))
            except ValueError:
                continue
    return sorted(names, key=lambda name: (name != "default", name))
