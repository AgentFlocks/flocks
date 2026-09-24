"""The configured PostgreSQL document store, scoped to process and event loop."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from flocks.db import registry
from flocks.db.registry import DatabaseConfigurationError
from flocks.db.store import DocumentStore

_store: Optional[DocumentStore] = None
_lock = asyncio.Lock()
_store_key = None
_lock_key = None


def _build(settings: registry.DatabaseSettings) -> DocumentStore:
    if settings.backend == "postgres":
        from flocks.db.backends.postgres import PostgresDocumentStore

        return PostgresDocumentStore(settings.dsn or "")
    raise DatabaseConfigurationError(
        f"backend {settings.backend!r} has a dialect but no document store yet"
    )


async def document_store() -> DocumentStore:
    """The PostgreSQL document store for the configured process."""
    settings = registry.current()

    global _store, _store_key, _lock, _lock_key
    key = (os.getpid(), asyncio.get_running_loop(), settings.dsn)
    if _lock_key != key:
        _lock, _lock_key = asyncio.Lock(), key
    if _store_key != key:
        _store = None
    if _store is None:
        async with _lock:
            if _store is None:
                store = _build(settings)
                await store.initialize()
                _store = store
                _store_key = key
    return _store


async def reset() -> None:
    """Drop the resolved store, closing it. For tests and for reconfiguration."""
    global _store, _store_key
    async with _lock:
        if _store is not None:
            await _store.close()
            _store = None
            _store_key = None


__all__ = ["document_store", "reset"]
