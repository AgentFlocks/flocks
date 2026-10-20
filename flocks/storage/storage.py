"""PostgreSQL storage for sessions, messages and application state.

Local paths accepted by older extensions are logical compatibility arguments;
runtime operations never open a SQLite file. Legacy files are handled only by
the explicit offline migration command.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple, Type, TypeVar

from pydantic import BaseModel
from flocks.config.config import Config
from flocks.db.store import Transaction
from flocks.utils.log import Log

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")
DDLScript = str | Callable[[Transaction], Awaitable[None]]
TransactionHook = Callable[[Transaction], Awaitable[None]]

class NotFoundError(Exception):
    """The requested key does not exist."""

class StorageError(Exception):
    """Persistent storage could not complete an operation."""

async def _delegate():
    from flocks.db.active import document_store
    return await document_store()

class Storage:
    NotFoundError = NotFoundError
    StorageError = StorageError
    _log = Log.create(service="storage")
    _db_path: Optional[Path] = None
    _db_identity = None
    _initialized = False
    _init_pid: Optional[int] = None
    _init_key = None
    _init_lock = asyncio.Lock()
    _init_lock_key = None
    _session_search_available = False
    _extension_ddls: List[DDLScript] = []

    @classmethod
    def _runtime_cache_scope(
        cls,
        target: str,
        *,
        exact: bool,
    ) -> tuple[Optional[str], Optional[str]]:
        """Map a deleted key (``exact``) or prefix onto ``(cache, session_id)``.

        ``cache`` is ``"session"`` or ``"message"`` (``None`` when the target
        backs no runtime cache).  ``session_id`` is set only when the target
        unambiguously belongs to one session; ``None`` means every session.
        Key shapes: ``session:{project_id}:{session_id}``, ``message:{sid}``,
        ``message_parts:{sid}``, ``message_parts:{sid}:{message_id}``.
        """
        segments = str(target).split(":")
        root = segments[0]
        if root == "session":
            # Only a complete key names one session; ``session:`` and
            # ``session:{project_id}:`` prefixes span many.
            if exact and len(segments) >= 3 and segments[-1]:
                return "session", segments[-1]
            return "session", None
        if root in ("message", "message_parts"):
            # ``message_parts:{sid}:`` is the per-session prefix; without the
            # trailing colon a prefix could match other session ids.
            if len(segments) >= 2 and segments[1] and (exact or len(segments) >= 3):
                return "message", segments[1]
            return "message", None
        return None, None


    @classmethod
    def _invalidate_runtime_caches(
        cls,
        target: Optional[str] = None,
        *,
        exact: bool = False,
    ) -> None:
        """Drop higher-level caches that depend on the affected storage rows.

        ``target`` is the deleted key (``exact=True``) or prefix.  A target
        that names a single session evicts only that session; ``None``
        (database swap / full clear) and root-level prefixes reset everything.
        """
        if not target:
            cls._invalidate_session_cache(None)
            cls._invalidate_message_cache(None)
            return
        cache, session_id = cls._runtime_cache_scope(target, exact=exact)
        if cache == "session":
            cls._invalidate_session_cache(session_id)
        elif cache == "message":
            cls._invalidate_message_cache(session_id)


    @classmethod
    def _invalidate_session_cache(cls, session_id: Optional[str]) -> None:
        try:
            from flocks.session.session import Session

            Session.invalidate_cache(session_id)
        except Exception:
            pass


    @classmethod
    def _invalidate_message_cache(cls, session_id: Optional[str]) -> None:
        try:
            from flocks.session.message import Message

            if session_id is None:
                Message.invalidate_cache()
            else:
                # Keep the per-session lock: the caller may be holding it.
                Message.invalidate_cache(session_id, discard_lock=False)
        except Exception:
            pass


    @classmethod
    def register_ddl(cls, ddl: DDLScript) -> None:
        """Register an extension DDL script to be executed during ``init()``.

        If init() has already completed the DDL is executed immediately
        on the next call to ``_ensure_init()``.
        """
        cls._extension_ddls.append(ddl)


    @staticmethod
    def _resolve_key(key: List[str] | str) -> str:
        """
        Convert key to string format

        Matches TypeScript's resolve() function:
        - Array keys: ["session", "proj1", "ses1"] -> "session/proj1/ses1"
        - String keys: passed through unchanged

        Args:
            key: Key as list or string

        Returns:
            Key as string
        """
        if isinstance(key, list):
            return "/".join(key)
        return key


    @classmethod
    def _like_prefix_pattern(cls, prefix: str) -> str:
        """Return a LIKE pattern that treats prefix characters literally."""
        return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


    @classmethod
    def _like_prefix_clause(cls, column: str = "key") -> str:
        return f"{column} LIKE ? ESCAPE '\\'"


    @classmethod
    async def _create_model_management_tables(cls) -> None:
        """Create dynamic data tables (idempotent).

        Usage records live in PostgreSQL. All static configuration
        (credentials, model settings, default models, custom providers)
        is stored in flocks.json / .secret.json.
        """

        async def _create_tables() -> None:
            # Keep extension DDL on the same PostgreSQL dialect contract.
            async with cls.transaction(cls._db_path, ensure_init=False) as handle:
                d = handle.dialect
                text, num, money = d.text_type(), d.integer_type(), d.double_type()
                await handle.execute(
                    "CREATE TABLE IF NOT EXISTS usage_records ("
                    f"id {text} PRIMARY KEY, "
                    f"provider_id {text} NOT NULL, "
                    f"model_id {text} NOT NULL, "
                    f"credential_id {text}, "
                    f"session_id {text}, "
                    f"message_id {text}, "
                    f"input_tokens {num} NOT NULL DEFAULT 0, "
                    f"output_tokens {num} NOT NULL DEFAULT 0, "
                    f"cached_tokens {num} NOT NULL DEFAULT 0, "
                    f"cache_write_tokens {num} NOT NULL DEFAULT 0, "
                    f"cache_reported {num}, "
                    f"reasoning_tokens {num} NOT NULL DEFAULT 0, "
                    f"total_tokens {num} NOT NULL DEFAULT 0, "
                    f"input_cost {money} NOT NULL DEFAULT 0, "
                    f"output_cost {money} NOT NULL DEFAULT 0, "
                    f"total_cost {money} NOT NULL DEFAULT 0, "
                    f"currency {text} NOT NULL DEFAULT 'USD', "
                    f"latency_ms {num}, "
                    f"source {text} NOT NULL DEFAULT 'live', "
                    f"response_id {text}, "
                    f"created_at {text} NOT NULL, "
                    f"backfilled_at {text})"
                )

                # Inspect only the active PostgreSQL schema.
                column_rows = await handle.fetch_all(
                    d.columns_query().format(table="usage_records"),
                    d.columns_query_params("usage_records"),
                )
                name_at = d.column_name_index()
                existing_columns = {row[name_at] for row in column_rows}

                # Columns added after the table first shipped. Typed by the
                # dialect for the same reason the CREATE TABLE above is.
                schema_additions = [
                    ("message_id", f"ADD COLUMN message_id {text}"),
                    ("cache_write_tokens", f"ADD COLUMN cache_write_tokens {num} NOT NULL DEFAULT 0"),
                    ("source", f"ADD COLUMN source {text} NOT NULL DEFAULT 'live'"),
                    ("backfilled_at", f"ADD COLUMN backfilled_at {text}"),
                    # 1 / 0: the provider did / did not report cache usage;
                    # NULL on rows written before this was tracked. IF NOT EXISTS:
                    # another process upgrading the same database may add it after
                    # the column list above was read.
                    ("cache_reported", f"ADD COLUMN IF NOT EXISTS cache_reported {num}"),
                    ("response_id", f"ADD COLUMN IF NOT EXISTS response_id {text}"),
                ]
                for column_name, clause in schema_additions:
                    if column_name in existing_columns:
                        continue
                    await handle.execute(f"ALTER TABLE usage_records {clause}")

                index_statements = [
                    "CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_records(provider_id, model_id)",
                    "CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_records(session_id)",
                    "CREATE INDEX IF NOT EXISTS idx_usage_time ON usage_records(created_at)",
                    "CREATE INDEX IF NOT EXISTS idx_usage_message ON usage_records(session_id, message_id)",
                    # Prevent a message from being billed twice.
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_unique_message "
                    "ON usage_records(session_id, message_id) WHERE message_id IS NOT NULL",
                ]
                for statement in index_statements:
                    try:
                        await handle.execute(statement)
                    except Exception:
                        pass  # Index already exists

        await cls._run_write_with_retry(
            _create_tables,
            action="init.model_management_tables",
            target=str(cls._db_path),
        )
        cls._log.info("storage.model_management_tables_ready")


    @classmethod
    async def list_entries(
        cls,
        prefix: Optional[str] = None,
        model: Optional[Type[T]] = None,
    ) -> List[Tuple[str, T | Any]]:
        """
        List storage entries, optionally filtered by prefix.

        This is more efficient than calling ``list_keys()`` followed by
        repeated ``get()`` calls because it loads matching rows in one query.

        Args:
            prefix: Optional key prefix to filter by
            model: Optional Pydantic model class to deserialize into

        Returns:
            List of ``(key, value)`` tuples
        """
        await cls._ensure_init()
        rows = await cls._list_entry_rows(prefix=prefix)

        entries: List[Tuple[str, T | Any]] = []
        for key, value_str in rows:
            if model is not None and hasattr(model, "model_validate_json"):
                value = model.model_validate_json(value_str)
            else:
                value = json.loads(value_str)
            entries.append((key, value))
        return entries


    @classmethod
    async def list_raw(
        cls,
        prefix: Optional[str] = None,
    ) -> List[Tuple[str, str]]:
        """List ``(key, raw_value_str)`` pairs without Python-side JSON parsing.

        Unlike :meth:`list_entries`, the value is returned as a plain string
        so callers can apply lightweight extraction (e.g. regex) instead of
        full ``json.loads``.  This is critical for hot trim paths that only
        need a couple of scalar fields from otherwise large JSON blobs.
        PostgreSQL serializes JSONB once for callers that need raw text.
        """
        await cls._ensure_init()
        return await cls._list_entry_rows(prefix=prefix)


    @classmethod
    async def read(cls, key: List[str] | str, model: Optional[Type[T]] = None) -> Optional[T | Any]:
        """
        Read a value (TypeScript-compatible API)

        Matches TypeScript: Storage.read<T>(key: string[])

        Args:
            key: Storage key as list or string
            model: Optional Pydantic model class

        Returns:
            Stored value or None if not found

        Raises:
            NotFoundError: If key not found (when strict mode needed)
        """
        resolved_key = cls._resolve_key(key)
        return await cls.get(resolved_key, model)


    @classmethod
    async def write(cls, key: List[str] | str, content: Any) -> None:
        """
        Write a value (TypeScript-compatible API)

        Matches TypeScript: Storage.write<T>(key: string[], content: T)

        Args:
            key: Storage key as list or string
            content: Content to store
        """
        resolved_key = cls._resolve_key(key)
        await cls.set(resolved_key, content)


    @classmethod
    async def update(cls, key: List[str] | str, fn: callable, model: Optional[Type[T]] = None) -> Optional[T | Any]:
        """
        Update a value in place (TypeScript-compatible API)

        Matches TypeScript: Storage.update<T>(key: string[], fn: (draft: T) => void)

        Args:
            key: Storage key as list or string
            fn: Function that modifies the content in place
            model: Optional Pydantic model class

        Returns:
            Updated value

        Raises:
            NotFoundError: If key not found
        """
        resolved_key = cls._resolve_key(key)

        # Read current value
        content = await cls.get(resolved_key, model)

        if content is None:
            raise NotFoundError(f"Key not found: {resolved_key}")

        # If it's a dict, apply function
        if isinstance(content, dict):
            fn(content)
        else:
            # If it's a Pydantic model, convert to dict, apply, convert back
            if isinstance(content, BaseModel):
                content_dict = content.model_dump()
                fn(content_dict)
                content = model.model_validate(content_dict) if model else content_dict
            else:
                # For other types, try to call fn on it
                fn(content)

        # Write back
        await cls.set(resolved_key, content)

        return content


    @classmethod
    async def remove(cls, key: List[str] | str) -> bool:
        """
        Remove a value (TypeScript-compatible API)

        Matches TypeScript: Storage.remove(key: string[])

        Args:
            key: Storage key as list or string

        Returns:
            True if deleted, False if not found
        """
        resolved_key = cls._resolve_key(key)
        return await cls.delete(resolved_key)


    @classmethod
    async def list(cls, prefix: List[str] | str | None = None) -> List[List[str]]:
        """
        List keys (TypeScript-compatible API)

        Matches TypeScript: Storage.list(prefix: string[])

        Args:
            prefix: Optional key prefix as list or string

        Returns:
            List of keys as lists (e.g., [["session", "proj1", "ses1"], ...])
        """
        prefix_str = cls._resolve_key(prefix) if prefix else None
        keys = await cls.list_keys(prefix_str)

        # Convert string keys back to list format
        return [key.split("/") for key in keys]


    @classmethod
    def get_db_path(cls) -> Path:
        """Legacy logical path for extension arguments; never opened at runtime."""
        return cls._db_path or Config.get_data_path() / "flocks.db"

    @classmethod
    def get_workflow_db_path(cls) -> Path:
        return cls.get_db_path().with_name("workflow.db")

    @classmethod
    def route_db_path_for_key(cls, key: str) -> Path:
        return cls.get_db_path()

    @classmethod
    def route_db_path_for_prefix(cls, prefix: Optional[str]) -> Path:
        return cls.get_db_path()

    @classmethod
    def session_search_available(cls) -> bool:
        return cls._session_search_available

    @classmethod
    async def configure_connection(cls, connection: Transaction) -> Transaction:
        """Compatibility hook: PostgreSQL needs no per-file setup."""
        return connection

    @classmethod
    @asynccontextmanager
    async def connect(cls, db_path: Optional[Path] = None) -> AsyncIterator[Transaction]:
        async with cls.transaction(db_path) as transaction:
            yield transaction

    @classmethod
    @asynccontextmanager
    async def transaction(cls, db_path: Optional[Path] = None, *, ensure_init: bool = True) -> AsyncIterator[Transaction]:
        if ensure_init:
            await cls._ensure_init()
        from flocks.db.connect import open_transaction
        handle = await open_transaction()
        try:
            async with handle.transaction() as transaction:
                yield transaction
        finally:
            await handle.close()

    @classmethod
    async def init(cls, db_path: Optional[Path] = None) -> None:
        from flocks.db import registry
        key = (os.getpid(), asyncio.get_running_loop(), registry.current().dsn)
        if cls._init_lock_key != key:
            cls._init_lock, cls._init_lock_key = asyncio.Lock(), key
        async with cls._init_lock:
            if cls._initialized and cls._init_key == key:
                return
            cls._initialized = False
            cls._db_path = Path(db_path) if db_path is not None else Config.get_data_path() / "flocks.db"
            identity = (os.getpid(), registry.current().dsn)
            if cls._db_identity != identity:
                cls._invalidate_runtime_caches()
            await _delegate()
            await cls._bootstrap_schema()
            cls._initialized = True
            cls._init_pid = os.getpid()
            cls._init_key = key
            cls._db_identity = identity
            cls._log.info("storage.initialized", {"backend": "postgres", "pid": cls._init_pid})

    @classmethod
    async def _ensure_init(cls) -> None:
        from flocks.db import registry
        key = (os.getpid(), asyncio.get_running_loop(), registry.current().dsn)
        if not cls._initialized or cls._init_key != key:
            await cls.init(cls._db_path)

    @classmethod
    async def _bootstrap_schema(cls) -> None:
        from flocks.db import registry
        from flocks.db.connect import pool_for
        from flocks.storage.vector import ensure_vector_tables
        from flocks.storage.session_search import ensure_session_search_tables
        pool = await pool_for(registry.current().dsn)
        async with pool.acquire(timeout=30) as connection:
            await connection.execute("SELECT pg_advisory_lock(hashtext(current_schema()), hashtext($1))", "flocks.storage.schema")
            try:
                await ensure_vector_tables(cls.get_db_path())
                cls._session_search_available = await ensure_session_search_tables(cls.get_db_path())
                await cls._create_model_management_tables()
                from flocks.session.message_pages import ensure_schema as ensure_message_pages
                from flocks.session.ownership import SCHEMA as session_ownership_schema
                async with cls.transaction(ensure_init=False) as transaction:
                    await ensure_message_pages(transaction)
                    await transaction.executescript(session_ownership_schema)
                for ddl in cls._extension_ddls:
                    async with cls.transaction(ensure_init=False) as transaction:
                        if isinstance(ddl, str):
                            await transaction.executescript(ddl)
                        else:
                            await ddl(transaction)
            finally:
                await connection.execute("SELECT pg_advisory_unlock(hashtext(current_schema()), hashtext($1))", "flocks.storage.schema")

    @classmethod
    async def shutdown(cls) -> None:
        from flocks.channel.inbound.session_binding import close_binding_db
        from flocks.db.active import reset
        from flocks.db.connect import close_pools
        from flocks.db.sync_store import close_sync_reader
        await close_binding_db()
        await asyncio.to_thread(close_sync_reader)
        await reset()
        await close_pools()
        cls._initialized = False
        cls._init_pid = None
        cls._init_key = None
        cls._db_identity = None

    @classmethod
    async def _run_write_with_retry(cls, operation, **_compat):
        """Legacy internal hook; transaction retries require caller idempotency."""
        return await operation()

    @classmethod
    async def set(cls, key: str, value: Any, value_type: str = "json") -> None:
        await cls._ensure_init()
        if isinstance(value, BaseModel):
            value = json.loads(value.model_dump_json())
        await (await _delegate()).set(key, value, value_type)

    @classmethod
    async def set_many(cls, entries: Sequence[Tuple[str, Any, str]]) -> None:
        await cls.mutate_many(set_entries=entries)

    @classmethod
    async def mutate_many(cls, *, set_entries=(), delete_keys=(), delete_prefixes=(), pre_transaction_hook=None, transaction_hook=None, invalidate_caches=True) -> int:
        await cls._ensure_init()
        payload = [(key, json.loads(value.model_dump_json()) if isinstance(value, BaseModel) else value, kind)
                   for key, value, kind in set_entries]
        keys, prefixes = list(delete_keys), list(delete_prefixes)
        if any(not prefix for prefix in prefixes):
            raise ValueError("A non-empty prefix is required for bulk deletion")
        removed = await (await _delegate()).mutate_many(set_entries=payload, delete_keys=keys,
            delete_prefixes=prefixes, pre_transaction_hook=pre_transaction_hook, transaction_hook=transaction_hook)
        if invalidate_caches:
            for key in keys:
                cls._invalidate_runtime_caches(key, exact=True)
            for prefix in prefixes:
                cls._invalidate_runtime_caches(prefix)
        return removed

    @classmethod
    async def get(cls, key: str, model: Optional[Type[T]] = None) -> Optional[T | Any]:
        await cls._ensure_init()
        value = await (await _delegate()).get(key)
        if value is not None and model is not None and hasattr(model, "model_validate"):
            return model.model_validate(value)
        return value

    @classmethod
    async def delete(cls, key: str) -> bool:
        await cls._ensure_init()
        removed = await (await _delegate()).delete(key)
        if removed:
            cls._invalidate_runtime_caches(key, exact=True)
        return removed

    @classmethod
    async def list_keys(cls, prefix: Optional[str] = None) -> List[str]:
        await cls._ensure_init()
        return await (await _delegate()).list_keys(prefix)

    @classmethod
    async def _list_entry_rows(cls, *, prefix: Optional[str]) -> List[Tuple[str, str]]:
        await cls._ensure_init()
        async with cls.transaction() as db:
            if prefix is not None:
                return await db.fetch_all("SELECT key, value::text FROM runtime_state WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
                                          (cls._like_prefix_pattern(prefix),))
            return await db.fetch_all("SELECT key, value::text FROM runtime_state ORDER BY key")

    @classmethod
    async def list_entries_page(cls, prefix: str, *, offset: int = 0, limit: int = 100, model: Optional[Type[T]] = None):
        await cls._ensure_init()
        params = (cls._like_prefix_pattern(prefix),)
        async with cls.transaction() as db:
            row = await db.fetch_one("SELECT COUNT(*) FROM runtime_state WHERE key LIKE ? ESCAPE '\\'", params)
            total = int(row[0])
            rows = await db.fetch_all("SELECT key, value::text FROM runtime_state WHERE key LIKE ? ESCAPE '\\' ORDER BY key LIMIT ? OFFSET ?",
                                      (*params, max(int(limit), 0), max(int(offset), 0))) if limit else []
        entries = [(key, model.model_validate_json(raw) if model is not None and hasattr(model, "model_validate_json") else json.loads(raw))
                   for key, raw in rows]
        return entries, total

    @classmethod
    async def exists(cls, key: str) -> bool:
        await cls._ensure_init()
        return await (await _delegate()).exists(key)

    @classmethod
    async def clear(cls, prefix: Optional[str] = None) -> int:
        if not prefix:
            raise ValueError("Storage.clear requires a non-empty prefix")
        return await cls.mutate_many(delete_prefixes=(prefix,))
