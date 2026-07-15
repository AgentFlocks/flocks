"""Minimal, non-destructive browser daemon lifecycle operations."""

from __future__ import annotations

import socket
import time

from . import _ipc as ipc


def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def _cleanup_stale(name: str, lock_timeout: float = 0.0) -> None:
    """Clean unreachable runtime artifacts only while holding the session lock."""
    lock = ipc.DaemonLock(name)
    deadline = time.monotonic() + lock_timeout
    acquired = lock.acquire()
    while not acquired and time.monotonic() < deadline:
        time.sleep(0.1)
        acquired = lock.acquire()
    if not acquired:
        raise RuntimeError(f"browser daemon {name!r} is locked but not responding; endpoint was preserved")
    try:
        if ipc.endpoint_reachable(name, timeout=0.2):
            raise RuntimeError(f"browser daemon {name!r} became reachable while cleaning stale state")
        ipc.cleanup_endpoint(name, lock)
        ipc.pid_path(name).unlink(missing_ok=True)
    finally:
        lock.release()


def _is_legacy_status(response: dict) -> bool:
    if response.get("error") in {"not_attached", "cdp_disconnected"}:
        return True
    return "target_id" in response and "session_id" in response and "page" in response


def _stop_legacy_default(timeout: float) -> None:
    """Gracefully stop the single legacy endpoint used before named sessions."""
    try:
        status = ipc.request("default", {"meta": "connection_status"}, timeout=1.0)
    except (ipc.EndpointRecordError, ipc.IPCProtocolError, OSError) as error:
        raise RuntimeError(f"legacy browser daemon could not be confirmed; endpoint was preserved: {error}") from error
    if not _is_legacy_status(status):
        raise RuntimeError("legacy browser daemon could not be confirmed; endpoint was preserved")
    try:
        ipc.request("default", {"meta": "shutdown"}, timeout=3.0)
    except (ipc.IPCProtocolError, TimeoutError, socket.timeout, OSError):
        pass
    if not _wait_until(lambda: not ipc.endpoint_reachable("default", timeout=0.2), timeout):
        raise RuntimeError("legacy browser daemon 'default' refused graceful shutdown; endpoint was preserved")
    _cleanup_stale("default", lock_timeout=min(timeout, 3.0))


def stop_daemon(name: str | None = None, timeout: float = 15.0) -> None:
    """Gracefully stop one daemon without reading or signalling an on-disk PID."""
    effective_name = ipc.runtime_paths(name).name
    try:
        ipc.identify(effective_name, timeout=1.0)
    except ipc.LegacyProtocolError:
        if effective_name != "default":
            raise RuntimeError(f"legacy named daemon {effective_name!r} is unsupported; endpoint was preserved")
        _stop_legacy_default(timeout)
        return
    except ipc.EndpointRecordError:
        _cleanup_stale(effective_name)
        return
    except ipc.IPCProtocolError as error:
        raise RuntimeError(
            f"browser daemon {effective_name!r} returned an invalid identity; endpoint was preserved: {error}"
        ) from error
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError, socket.timeout, OSError):
        _cleanup_stale(effective_name)
        return

    try:
        ipc.request(effective_name, {"meta": "shutdown"}, timeout=3.0)
    except (ipc.IPCProtocolError, TimeoutError, socket.timeout, OSError):
        pass
    if not _wait_until(lambda: not ipc.endpoint_reachable(effective_name, timeout=0.2), timeout):
        raise RuntimeError(f"browser daemon {effective_name!r} refused graceful shutdown; endpoint was preserved")
    _cleanup_stale(effective_name, lock_timeout=min(timeout, 3.0))


def stop_all_daemons(timeout: float = 15.0) -> tuple[list[str], dict[str, str]]:
    """Attempt every visible session and collect failures without stopping early."""
    names = ipc.discover_names()
    errors: dict[str, str] = {}
    for name in names:
        try:
            stop_daemon(name, timeout=timeout)
        except RuntimeError as error:
            errors[name] = str(error)
    return names, errors
