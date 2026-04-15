"""
Storage module for persistent data management

Provides SQLite-based storage similar to Flocks's Storage namespace
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar
import json
import aiosqlite
from datetime import datetime
from pydantic import BaseModel

from flocks.utils.log import Log
from flocks.config.config import Config


T = TypeVar("T", bound=BaseModel)


class NotFoundError(Exception):
    """Raised when a resource is not found"""
    pass


class StorageError(Exception):
    """Base storage error"""
    pass


class Storage:
    """
    Storage namespace for persistent data operations
    
    Similar to Flocks's Storage namespace.
    Provides both TypeScript-compatible API (key arrays) and Python API (key strings).
    """
    
    NotFoundError = NotFoundError
    StorageError = StorageError
    
    _log = Log.create(service="storage")
    _db_path: Optional[Path] = None
    _initialized = False
    _extension_ddls: List[str] = []

    @classmethod
    def _invalidate_runtime_caches(cls) -> None:
        """Clear higher-level caches that depend on the active storage DB."""
        try:
            from flocks.session.session import Session
            Session.invalidate_cache()
        except Exception:
            pass

        try:
            from flocks.session.message import Message
            Message.invalidate_cache()
        except Exception:
            pass
    
    @classmethod
    def get_db_path(cls) -> Path:
        """Return the resolved database file path.

        Can be called before ``init()`` — in that case it computes the
        default path without creating the file.
        """
        if cls._db_path is not None:
            return cls._db_path
        data_dir = Config.get_data_path()
        return data_dir / "flocks.db"

    @classmethod
    def register_ddl(cls, ddl: str) -> None:
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
    async def init(cls, db_path: Optional[Path] = None) -> None:
        """
        Initialize storage system
        
        Args:
            db_path: Path to SQLite database file
        """
        if db_path is None:
            data_dir = Config.get_data_path()
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "flocks.db"

        db_path = Path(db_path)
        # Tests and short-lived processes may initialize Storage against a
        # temporary database that later disappears. Allow re-initialization when
        # the target path changes or the old path is no longer usable.
        if cls._initialized and cls._db_path == db_path and db_path.exists():
            return
        
        cls._db_path = db_path
        cls._db_path.parent.mkdir(parents=True, exist_ok=True)
        cls._invalidate_runtime_caches()
        
        # Create tables
        async with aiosqlite.connect(cls._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS storage (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            await db.commit()
        
        # Initialize vector storage tables (for memory system)
        try:
            from flocks.storage.vector import ensure_vector_tables
            vector_status = await ensure_vector_tables(cls._db_path)
            cls._log.info("storage.vector.initialized", vector_status)
        except Exception as e:
            cls._log.warn("storage.vector.init.failed", {"error": str(e)})

        # Create model management tables
        await cls._create_model_management_tables()

        # Run extension DDLs registered before init
        for ddl in cls._extension_ddls:
            try:
                async with aiosqlite.connect(cls._db_path) as db:
                    await db.executescript(ddl)
            except Exception as e:
                cls._log.warn("storage.extension_ddl.failed", {"error": str(e)})

        cls._initialized = True
        cls._log.info("storage.initialized", {"db_path": str(db_path)})

    @classmethod
    async def _create_model_management_tables(cls) -> None:
        """Create dynamic data tables (idempotent).

        Only usage_records lives in SQLite. All static configuration
        (credentials, model settings, default models, custom providers)
        is stored in flocks.json / .secret.json.
        """
        async with aiosqlite.connect(cls._db_path) as db:
            await db.executescript("""
                -- Usage records (dynamic data — the only model-management table in SQLite)
                CREATE TABLE IF NOT EXISTS usage_records (
                    id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    credential_id TEXT,
                    session_id TEXT,
                    message_id TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    input_cost REAL NOT NULL DEFAULT 0,
                    output_cost REAL NOT NULL DEFAULT 0,
                    total_cost REAL NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    latency_ms INTEGER,
                    source TEXT NOT NULL DEFAULT 'live',
                    created_at TEXT NOT NULL,
                    backfilled_at TEXT
                );
            """)

            async with db.execute("PRAGMA table_info(usage_records)") as cursor:
                existing_columns = {row[1] for row in await cursor.fetchall()}

            schema_additions = [
                ("message_id", "ALTER TABLE usage_records ADD COLUMN message_id TEXT"),
                ("cache_write_tokens", "ALTER TABLE usage_records ADD COLUMN cache_write_tokens INTEGER NOT NULL DEFAULT 0"),
                ("source", "ALTER TABLE usage_records ADD COLUMN source TEXT NOT NULL DEFAULT 'live'"),
                ("backfilled_at", "ALTER TABLE usage_records ADD COLUMN backfilled_at TEXT"),
            ]
            for column_name, statement in schema_additions:
                if column_name in existing_columns:
                    continue
                await db.execute(statement)

            index_statements = [
                "CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_records(provider_id, model_id)",
                "CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_records(session_id)",
                "CREATE INDEX IF NOT EXISTS idx_usage_time ON usage_records(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_usage_message ON usage_records(session_id, message_id)",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_unique_message ON usage_records(session_id, message_id) WHERE message_id IS NOT NULL",
            ]
            for stmt in index_statements:
                try:
                    await db.execute(stmt)
                except Exception:
                    pass  # Index already exists

            await db.commit()
            cls._log.info("storage.model_management_tables_ready")
    
    @classmethod
    async def _ensure_init(cls) -> None:
        """Ensure storage is initialized"""
        if not cls._initialized or cls._db_path is None or not cls._db_path.exists():
            await cls.init(cls._db_path)
    
    @classmethod
    async def set(cls, key: str, value: Any, value_type: str = "json") -> None:
        """
        Store a value
        
        Args:
            key: Storage key
            value: Value to store (will be JSON serialized)
            value_type: Type identifier for the value
        """
        await cls._ensure_init()
        
        if isinstance(value, BaseModel):
            serialized = value.model_dump_json()
        else:
            serialized = json.dumps(value)
        
        from datetime import UTC
        now = datetime.now(UTC).isoformat()
        
        async with aiosqlite.connect(cls._db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO storage (key, value, type, created_at, updated_at)
                VALUES (?, ?, ?, 
                    COALESCE((SELECT created_at FROM storage WHERE key = ?), ?),
                    ?)
            """, (key, serialized, value_type, key, now, now))
            await db.commit()
        
        cls._log.debug("storage.set", {"key": key, "type": value_type})
    
    @classmethod
    async def get(cls, key: str, model: Optional[Type[T]] = None) -> Optional[T | Any]:
        """
        Retrieve a value
        
        Args:
            key: Storage key
            model: Optional Pydantic model class to deserialize into
            
        Returns:
            Stored value or None if not found
        """
        await cls._ensure_init()
        
        async with aiosqlite.connect(cls._db_path) as db:
            async with db.execute(
                "SELECT value, type FROM storage WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
        
        if row is None:
            return None
        
        value_str, value_type = row
        
        if model is not None:
            return model.model_validate_json(value_str)
        else:
            return json.loads(value_str)
    
    @classmethod
    async def delete(cls, key: str) -> bool:
        """
        Delete a value
        
        Args:
            key: Storage key
            
        Returns:
            True if deleted, False if not found
        """
        await cls._ensure_init()
        
        async with aiosqlite.connect(cls._db_path) as db:
            cursor = await db.execute("DELETE FROM storage WHERE key = ?", (key,))
            await db.commit()
            deleted = cursor.rowcount > 0
        
        if deleted:
            cls._log.debug("storage.delete", {"key": key})
        
        return deleted
    
    @classmethod
    async def list_keys(cls, prefix: Optional[str] = None) -> List[str]:
        """
        List all keys, optionally filtered by prefix
        
        Args:
            prefix: Optional key prefix to filter by
            
        Returns:
            List of matching keys
        """
        await cls._ensure_init()
        
        async with aiosqlite.connect(cls._db_path) as db:
            if prefix:
                query = "SELECT key FROM storage WHERE key LIKE ?"
                params = (f"{prefix}%",)
            else:
                query = "SELECT key FROM storage"
                params = ()
            
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        
        return [row[0] for row in rows]

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

        async with aiosqlite.connect(cls._db_path) as db:
            if prefix:
                query = "SELECT key, value FROM storage WHERE key LIKE ?"
                params = (f"{prefix}%",)
            else:
                query = "SELECT key, value FROM storage"
                params = ()

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()

        entries: List[Tuple[str, T | Any]] = []
        for key, value_str in rows:
            if model is not None:
                value = model.model_validate_json(value_str)
            else:
                value = json.loads(value_str)
            entries.append((key, value))
        return entries
    
    @classmethod
    async def exists(cls, key: str) -> bool:
        """
        Check if a key exists
        
        Args:
            key: Storage key
            
        Returns:
            True if exists, False otherwise
        """
        await cls._ensure_init()
        
        async with aiosqlite.connect(cls._db_path) as db:
            async with db.execute(
                "SELECT 1 FROM storage WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
        
        return row is not None
    
    @classmethod
    async def clear(cls, prefix: Optional[str] = None) -> int:
        """
        Clear storage, optionally filtered by prefix
        
        Args:
            prefix: Optional key prefix to filter by
            
        Returns:
            Number of deleted entries
        """
        await cls._ensure_init()
        
        async with aiosqlite.connect(cls._db_path) as db:
            if prefix:
                query = "DELETE FROM storage WHERE key LIKE ?"
                params = (f"{prefix}%",)
            else:
                query = "DELETE FROM storage"
                params = ()
            
            cursor = await db.execute(query, params)
            await db.commit()
            deleted = cursor.rowcount
        
        cls._log.info("storage.clear", {"prefix": prefix, "deleted": deleted})
        cls._invalidate_runtime_caches()
        return deleted
    
    # ==================== TypeScript-compatible API ====================
    
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