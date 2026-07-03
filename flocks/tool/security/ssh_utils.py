"""
Shared SSH utilities for host security tools.

Provides common SSH connection logic, credential resolution,
audit logging, and session-level connection pooling used by
both ssh_host_cmd and ssh_run_script tools.
"""

import asyncio
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import asyncssh

from flocks.utils.log import Log

log = Log.create(service="tool.ssh_utils")

# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

AUDIT_LOG_PATH = Path.home() / ".flocks" / "audit" / "ssh_commands.log"


def audit_log(
    session_id: str,
    host: str,
    username: str,
    port: int,
    command: str,
    decision: str,
    source: str,
    exit_code: Optional[int] = None,
    output_bytes: int = 0,
    elapsed_ms: int = 0,
) -> None:
    """Append an audit record for an SSH command decision/execution."""
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = (
        f"[{ts}] session={session_id} host={host} user={username} port={port}\n"
        f"  cmd: {command}\n"
        f"  decision: {decision} | source: {source}\n"
        f"  exit_code: {exit_code if exit_code is not None else '-'}"
        f"  output_bytes: {output_bytes}  elapsed_ms: {elapsed_ms}\n\n"
    )
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Session-level SSH connection pool
# ---------------------------------------------------------------------------


DEFAULT_POOL_MAX_CONNECTIONS = 128
DEFAULT_POOL_IDLE_TTL_S = 120.0


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        log.warn("ssh_pool.invalid_int_env", {"name": name, "value": raw})
        return default


def _env_float(name: str, default: float, minimum: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        log.warn("ssh_pool.invalid_float_env", {"name": name, "value": raw})
        return default


@dataclass
class _PoolEntry:
    conn: asyncssh.SSHClientConnection
    last_used: float
    in_use: int = 0


class SSHConnectionPool:
    """Per-session SSH connection cache.

    Keeps at most one connection per (session_id, host, port, username) tuple.
    Idle connections are released by LRU/TTL pressure, explicit session close,
    or process shutdown.
    """

    def __init__(
        self,
        *,
        max_connections: Optional[int] = None,
        idle_ttl_s: Optional[float] = None,
    ) -> None:
        self.max_connections = (
            max_connections
            if max_connections is not None
            else _env_int(
                "FLOCKS_SSH_POOL_MAX_CONNECTIONS",
                DEFAULT_POOL_MAX_CONNECTIONS,
                1,
            )
        )
        self.idle_ttl_s = (
            idle_ttl_s
            if idle_ttl_s is not None
            else _env_float(
                "FLOCKS_SSH_POOL_IDLE_TTL_SECONDS",
                DEFAULT_POOL_IDLE_TTL_S,
                1.0,
            )
        )
        self._connections: OrderedDict[
            tuple[str, str, int, str],
            _PoolEntry,
        ] = OrderedDict()
        self._locks: dict[tuple[str, str, int, str], asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def _key(self, session_id: str, host: str, port: int, username: str) -> tuple[str, str, int, str]:
        return (session_id, host, port, username)

    def _is_connection_closed(self, conn: asyncssh.SSHClientConnection) -> bool:
        """Return whether an asyncssh connection is already closed."""
        is_closed = getattr(conn, "is_closed", None)
        if not callable(is_closed):
            return False
        try:
            return bool(is_closed())
        except Exception:
            return False

    async def get_connection(
        self,
        session_id: str,
        host: str,
        port: int,
        username: str,
        key_path: Optional[str],
        password: Optional[str],
    ) -> asyncssh.SSHClientConnection:
        """Return an existing connection or create a new one and mark it in use.

        Closed connections are evicted before reuse. Other stale connection
        failures are handled by the caller, which should invalidate and retry.
        """
        key = self._key(session_id, host, port, username)

        async with self._global_lock:
            self._prune_idle_locked(time.monotonic())
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            lock = self._locks[key]

        async with lock:
            now = time.monotonic()
            stale_conn: Optional[asyncssh.SSHClientConnection] = None
            async with self._global_lock:
                entry = self._connections.get(key)
                if entry is not None:
                    if self._is_connection_closed(entry.conn):
                        self._connections.pop(key, None)
                        stale_conn = entry.conn
                    else:
                        entry.in_use += 1
                        entry.last_used = now
                        self._connections.move_to_end(key)
                        return entry.conn
            if stale_conn is not None:
                self._close_connection(stale_conn)

            connect_kwargs: dict = dict(
                host=host,
                port=port,
                username=username,
                connect_timeout=15,
                known_hosts=None,
                keepalive_interval=30,
            )
            if key_path:
                connect_kwargs["client_keys"] = [key_path]
            elif password:
                connect_kwargs["password"] = password

            conn = await asyncssh.connect(**connect_kwargs)
            async with self._global_lock:
                self._connections[key] = _PoolEntry(
                    conn=conn,
                    last_used=time.monotonic(),
                    in_use=1,
                )
                self._connections.move_to_end(key)
                self._enforce_limits_locked()
            return conn

    async def release_connection(
        self, session_id: str, host: str, port: int, username: str
    ) -> None:
        """Mark a pooled connection as no longer actively executing a command."""
        key = self._key(session_id, host, port, username)
        async with self._global_lock:
            entry = self._connections.get(key)
            if entry is None:
                return
            entry.in_use = max(0, entry.in_use - 1)
            entry.last_used = time.monotonic()
            self._connections.move_to_end(key)
            self._prune_idle_locked(entry.last_used)
            self._enforce_limits_locked()

    async def invalidate_connection(
        self,
        session_id: str,
        host: str,
        port: int,
        username: str,
        *,
        close_active: bool = True,
    ) -> bool:
        """Close and evict a stale connection so the next call reconnects.

        When ``close_active`` is false, a connection still used by other
        commands is left open so one channel failure doesn't interrupt
        unrelated in-flight commands sharing the same SSH transport.
        """
        key = self._key(session_id, host, port, username)
        async with self._global_lock:
            entry = self._connections.get(key)
            if entry is not None and not close_active and entry.in_use > 1:
                return False
            entry = self._connections.pop(key, None)
            self._locks.pop(key, None)
        if entry is not None:
            self._close_connection(entry.conn)
        return True

    async def close_session(self, session_id: str) -> None:
        """Close all connections belonging to *session_id*."""
        async with self._global_lock:
            to_close: list[tuple] = [
                k for k in self._connections if k[0] == session_id
            ]
            entries = [self._connections.pop(key) for key in to_close]
            for key in to_close:
                self._locks.pop(key, None)
        for entry in entries:
            self._close_connection(entry.conn)

    async def close_all(self) -> None:
        """Close every cached connection."""
        async with self._global_lock:
            entries = list(self._connections.values())
            self._connections.clear()
            self._locks.clear()
        for entry in entries:
            self._close_connection(entry.conn)

    def _prune_idle_locked(self, now: float) -> None:
        """Close idle expired connections. Caller must hold ``_global_lock``."""
        if self.idle_ttl_s <= 0:
            return
        expired = [
            key
            for key, entry in self._connections.items()
            if entry.in_use <= 0 and now - entry.last_used >= self.idle_ttl_s
        ]
        for key in expired:
            entry = self._connections.pop(key, None)
            self._locks.pop(key, None)
            if entry is not None:
                self._close_connection(entry.conn)

    def _enforce_limits_locked(self) -> None:
        """Apply LRU cap to idle connections. Caller must hold ``_global_lock``."""
        if self.max_connections <= 0:
            return
        while len(self._connections) > self.max_connections:
            evicted_key = None
            for key, entry in self._connections.items():
                if entry.in_use <= 0:
                    evicted_key = key
                    break
            if evicted_key is None:
                # Every connection is active; allow temporary overflow rather
                # than closing a command that is still running.
                return
            entry = self._connections.pop(evicted_key, None)
            self._locks.pop(evicted_key, None)
            if entry is not None:
                self._close_connection(entry.conn)

    def _close_connection(self, conn: asyncssh.SSHClientConnection) -> None:
        """Best-effort close for asyncssh connections."""
        try:
            conn.close()
        except Exception:
            pass

    def stats(self) -> dict[str, int | float]:
        """Return lightweight pool stats for tests and diagnostics."""
        return {
            "connections": len(self._connections),
            "locks": len(self._locks),
            "active_connections": sum(
                1 for entry in self._connections.values() if entry.in_use > 0
            ),
            "max_connections": self.max_connections,
            "idle_ttl_s": self.idle_ttl_s,
        }


_pool = SSHConnectionPool()


def get_ssh_pool() -> SSHConnectionPool:
    """Return the module-level connection pool singleton."""
    return _pool


# ---------------------------------------------------------------------------
# Session lifecycle integration
# ---------------------------------------------------------------------------

def _on_session_deleted(event: dict) -> None:
    """Release SSH connections when a session is deleted.

    Subscribed to the ``session.deleted`` bus event at module import time.
    Schedules an async close on the running event loop so it doesn't block
    the synchronous bus callback.
    """
    try:
        props = event.get("properties", {})
        session_id = props.get("sessionID") or props.get("session_id")
        if not session_id:
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_pool.close_session(session_id))
        except RuntimeError:
            pass
    except Exception:
        pass


try:
    from flocks.bus.bus import Bus
    from flocks.bus.events import SessionDeleted
    Bus.subscribe(SessionDeleted, _on_session_deleted)
except Exception:
    pass


# ---------------------------------------------------------------------------
# SSH command execution
# ---------------------------------------------------------------------------

async def execute_ssh_command(
    host: str,
    command: str,
    username: str,
    port: int,
    key_path: Optional[str],
    password: Optional[str],
    timeout_s: int,
    session_id: Optional[str] = None,
) -> tuple[int, str, str]:
    """Execute a command on a remote host via SSH.

    When *session_id* is provided the connection is fetched from (and
    cached in) the session-level connection pool, dramatically reducing
    latency for multi-command investigations.  Without a session_id a
    fresh one-shot connection is created each time.

    Returns:
        (exit_code, stdout, stderr)
    """
    if session_id:
        invalidated = False
        conn = await _pool.get_connection(
            session_id=session_id,
            host=host, port=port, username=username,
            key_path=key_path, password=password,
        )
        try:
            result = await asyncio.wait_for(
                conn.run(command, check=False),
                timeout=timeout_s,
            )
            return (
                result.exit_status or 0,
                result.stdout or "",
                result.stderr or "",
            )
        except (
            asyncssh.ConnectionLost,
            asyncssh.DisconnectError,
            asyncssh.ChannelOpenError,
            BrokenPipeError,
            OSError,
        ):
            # Stale connection — evict from pool and retry with a fresh one.
            invalidated = await _pool.invalidate_connection(
                session_id,
                host,
                port,
                username,
                close_active=False,
            )
            if invalidated:
                conn = await _pool.get_connection(
                    session_id=session_id,
                    host=host, port=port, username=username,
                    key_path=key_path, password=password,
                )
            else:
                connect_kwargs: dict = dict(
                    host=host,
                    port=port,
                    username=username,
                    connect_timeout=15,
                    known_hosts=None,
                )
                if key_path:
                    connect_kwargs["client_keys"] = [key_path]
                elif password:
                    connect_kwargs["password"] = password
                conn = await asyncssh.connect(**connect_kwargs)
            try:
                result = await asyncio.wait_for(
                    conn.run(command, check=False),
                    timeout=timeout_s,
                )
                return (
                    result.exit_status or 0,
                    result.stdout or "",
                    result.stderr or "",
                )
            finally:
                if invalidated:
                    await _pool.release_connection(session_id, host, port, username)
                else:
                    try:
                        conn.close()
                    except Exception:
                        pass
        finally:
            if not invalidated:
                await _pool.release_connection(session_id, host, port, username)

    connect_kwargs: dict = dict(
        host=host,
        port=port,
        username=username,
        connect_timeout=15,
        known_hosts=None,
    )
    if key_path:
        connect_kwargs["client_keys"] = [key_path]
    elif password:
        connect_kwargs["password"] = password

    async with asyncssh.connect(**connect_kwargs) as conn:
        result = await asyncio.wait_for(
            conn.run(command, check=False),
            timeout=timeout_s,
        )
        return (
            result.exit_status or 0,
            result.stdout or "",
            result.stderr or "",
        )


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def resolve_ssh_credentials(
    username: Optional[str],
    key_path: Optional[str],
    password: Optional[str],
) -> tuple[str, Optional[str], Optional[str]]:
    """Resolve SSH credentials from provided values or SecretManager.

    Falls back to SecretManager defaults for any unset credential.

    Returns:
        (username, key_path, password) with defaults applied
    """
    try:
        from flocks.security import get_secret_manager
        sm = get_secret_manager()
        if not username:
            username = sm.get("ssh_default_user") or "root"
        if not key_path:
            key_path = sm.get("ssh_default_key_path")
        if not password and not key_path:
            password = sm.get("ssh_default_password")
    except Exception as e:
        log.warn("ssh_utils.credential_resolve_failed", {"error": str(e)})
        if not username:
            username = "root"

    return username, key_path, password
