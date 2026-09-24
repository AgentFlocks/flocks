"""Synchronous PostgreSQL reads on a dedicated worker event loop.

Callers must offload this synchronous API from async request handlers."""

from __future__ import annotations

import json
import atexit
import asyncio
import os
import threading
from pathlib import Path
from typing import Any, Optional

from flocks.db import registry

_reader_lock = threading.Lock()
_reader_loop = None
_reader_thread = None
_reader_pid = None


def _background_loop():
    """One loop per process, so synchronous reads reuse their asyncpg pool."""
    global _reader_loop, _reader_thread, _reader_pid
    with _reader_lock:
        if _reader_loop is None or _reader_pid != os.getpid():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, name="flocks-db-reader", daemon=True)
            thread.start()
            _reader_loop, _reader_thread, _reader_pid = loop, thread, os.getpid()
        return _reader_loop


def close_sync_reader() -> None:
    global _reader_loop, _reader_thread, _reader_pid
    with _reader_lock:
        loop, thread = _reader_loop, _reader_thread
        if loop is None or _reader_pid != os.getpid():
            return
        from flocks.db.connect import close_pools
        try:
            asyncio.run_coroutine_threadsafe(close_pools(), loop).result(timeout=35)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            if not thread.is_alive():
                loop.close()
            _reader_loop = _reader_thread = _reader_pid = None


atexit.register(close_sync_reader)


def _decode(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw if isinstance(raw, str) else str(raw))
    except (json.JSONDecodeError, TypeError):
        return None




class PostgresSyncReader:
    """The same reads against ``runtime_state``, on a borrowed event loop."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def available(self) -> bool:
        return bool(self._dsn)

    def _run(self, coroutine_factory) -> Any:
        async def main():
            from flocks.db.connect import pool_for
            pool = await pool_for(self._dsn)
            async with pool.acquire(timeout=30) as connection:
                return await coroutine_factory(connection)

        # The synchronous API necessarily waits; only its I/O runs on the
        # persistent loop. Repeated reads no longer reconnect or create loops.
        future = asyncio.run_coroutine_threadsafe(main(), _background_loop())
        try:
            return future.result(timeout=95)
        except BaseException:
            future.cancel()
            raise

    def get(self, key: str) -> Any:
        async def query(connection):
            return await connection.fetchval(
                "SELECT value FROM runtime_state WHERE key = $1", key
            )

        return _decode(self._run(query))

    def get_prefix(self, prefix: str) -> dict[str, Any]:
        async def query(connection):
            return await connection.fetch(
                "SELECT key, value FROM runtime_state WHERE key LIKE $1", f"{prefix}%"
            )

        return {str(row["key"]): _decode(row["value"]) for row in self._run(query)}


def reader(sqlite_path: Optional[Path] = None):
    """A synchronous reader for the configured backend, or None if it has none."""
    settings = registry.current()
    if settings.backend == "postgres":
        return PostgresSyncReader(settings.dsn or "")
    return None


__all__ = ["PostgresSyncReader", "close_sync_reader", "reader"]
