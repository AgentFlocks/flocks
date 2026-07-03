"""
Storage module for persistent data management

Provides SQLite-based storage similar to Flocks's Storage namespace
"""

import asyncio
import os

from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple, Type, TypeVar
import json
import aiosqlite
from datetime import datetime
from pydantic import BaseModel

from flocks.utils.log import Log
from flocks.config.config import Config


T = TypeVar("T", bound=BaseModel)
DDLScript = str | Callable[[aiosqlite.Connection], Awaitable[None]]


class NotFoundError(Exception):
    """Raised when a resource is not found"""

    pass


class StorageError(Exception):
    """Base storage error"""

    pass


class CheckpointBusyError(StorageError):
    """``PRAGMA wal_checkpoint`` reported ``busy=1`` (no exception raised).

    SQLite signals contention via the **return row** ``(busy, log_pages,
    checkpointed_pages)`` rather than a SQL error, so callers must
    actively inspect the result.  We surface that as a typed exception
    so callers can distinguish "checkpoint silently no-op'd" from a real
    success and retry / abort accordingly.
    """

    def __init__(self, mode: str, log_pages: int, checkpointed_pages: int) -> None:
        super().__init__(f"wal_checkpoint({mode}) busy: log_pages={log_pages}, checkpointed_pages={checkpointed_pages}")
        self.mode = mode
        self.log_pages = log_pages
        self.checkpointed_pages = checkpointed_pages


class Storage:
    """
    Storage namespace for persistent data operations

    Similar to Flocks's Storage namespace.
    Provides both TypeScript-compatible API (key arrays) and Python API (key strings).
    """

    NotFoundError = NotFoundError
    StorageError = StorageError
    CheckpointBusyError = CheckpointBusyError

    _log = Log.create(service="storage")
    _db_path: Optional[Path] = None
    _initialized = False
    # PID of the process that called ``init()``.  Used by ``_ensure_init`` to
    # detect ``fork()`` (uvicorn ``--reload`` / multiprocessing workers) and
    # re-initialise per-process state so the parent's open SQLite file
    # descriptors and ``_initialized=True`` flag are never silently inherited
    # — a known SQLite corruption vector.
    _init_pid: Optional[int] = None
    _extension_ddls: List[DDLScript] = []
    _sqlite_timeout_s = 5.0
    _sqlite_busy_timeout_ms = 5000
    _sqlite_journal_mode = "WAL"
    # Auto-checkpoint after this many WAL pages (default SQLite is 1000 ≈
    # 4 MB).  We shrink it so the WAL never accumulates more than a few
    # hundred KB of un-checkpointed writes — shortening the window in
    # which a process kill / power loss leaves SQLite needing to rewrite
    # main-DB page 1 (the header) during the next start-up recovery.
    _sqlite_wal_autocheckpoint_pages = 200
    # ``NORMAL`` is the documented safe sync level for WAL mode (see
    # https://www.sqlite.org/pragma.html#pragma_synchronous): writes still
    # survive process crashes, and only the very last few transactions may be
    # lost on hard power-loss.  We set it explicitly so the value cannot drift
    # to ``OFF`` via some future PRAGMA or accidental ``synchronous=0`` and
    # silently weaken durability.
    _sqlite_synchronous = "NORMAL"
    _sqlite_write_retry_attempts = 6
    _sqlite_write_retry_base_delay_s = 0.05
    _multi_db_migration_marker_key = "storage.migration.multi_db.v1"
    _multi_db_migration_batch_size = 500

    # Substrings that mark an SQLite file as unrecoverably damaged at open
    # time.  We deliberately keep this list short and English-only because
    # SQLite always raises these errors in English regardless of locale.
    _DB_CORRUPTION_TOKENS = (
        "file is not a database",
        "file is encrypted or is not a database",
        "database disk image is malformed",
    )

    @classmethod
    def _prefix_matches_runtime_cache(cls, prefix: Optional[str], roots: tuple[str, ...]) -> bool:
        if prefix is None:
            return True
        normalized = str(prefix)
        return any(normalized == root.rstrip(":/") or normalized.startswith(root) for root in roots)

    @classmethod
    def _invalidate_runtime_caches(cls, prefix: Optional[str] = None) -> None:
        """Clear higher-level caches that depend on the active storage DB."""
        if cls._prefix_matches_runtime_cache(prefix, ("session:",)):
            try:
                from flocks.session.session import Session

                Session.invalidate_cache()
            except Exception:
                pass

        if cls._prefix_matches_runtime_cache(prefix, ("message:", "message_parts:")):
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
    def get_workflow_db_path(cls) -> Path:
        """Return the SQLite database path for workflow-domain KV data."""
        return cls.get_db_path().with_name("workflow.db")

    @classmethod
    def route_db_path_for_key(cls, key: str) -> Path:
        """Return the storage DB path that owns *key*."""
        return cls.get_db_path()

    @classmethod
    def route_db_path_for_prefix(cls, prefix: Optional[str]) -> Path:
        """Return the storage DB path for a prefix-scoped KV operation."""
        return cls.get_db_path()

    @classmethod
    async def configure_connection(cls, conn: aiosqlite.Connection) -> aiosqlite.Connection:
        """Apply the runtime SQLite contract to an async connection."""
        await conn.execute(f"PRAGMA journal_mode={cls._sqlite_journal_mode}")
        await conn.execute(f"PRAGMA synchronous={cls._sqlite_synchronous}")
        await conn.execute(f"PRAGMA busy_timeout={cls._sqlite_busy_timeout_ms}")
        await conn.execute(f"PRAGMA wal_autocheckpoint={cls._sqlite_wal_autocheckpoint_pages}")
        await conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @classmethod
    def configure_sync_connection(cls, conn: sqlite3.Connection) -> sqlite3.Connection:
        """Apply the runtime SQLite contract to a sync connection."""
        conn.execute(f"PRAGMA journal_mode={cls._sqlite_journal_mode}")
        conn.execute(f"PRAGMA synchronous={cls._sqlite_synchronous}")
        conn.execute(f"PRAGMA busy_timeout={cls._sqlite_busy_timeout_ms}")
        conn.execute(f"PRAGMA wal_autocheckpoint={cls._sqlite_wal_autocheckpoint_pages}")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # SQLite database files begin with this 16-byte magic string (see
    # https://www.sqlite.org/fileformat.html#magic_header_string).  A
    # non-empty file that does not start with this sequence is guaranteed
    # to trip ``DatabaseError: file is not a database`` on the first I/O.
    _SQLITE_MAGIC = b"SQLite format 3\x00"

    @classmethod
    def _file_has_invalid_sqlite_header(cls, db_path: Path) -> bool:
        """Return ``True`` when *db_path* exists, is non-empty, and is not SQLite.

        We use this as a fast pre-flight check before opening the database
        so that ``aiosqlite``/SQLite never gets a chance to delete the WAL
        and SHM sidecars next to a corrupted main DB — preserving them on
        disk for later offline recovery.
        """
        try:
            if not db_path.is_file():
                return False
            if db_path.stat().st_size == 0:
                return False
            with db_path.open("rb") as fh:
                header = fh.read(len(cls._SQLITE_MAGIC))
        except OSError:
            return False
        return header != cls._SQLITE_MAGIC

    @classmethod
    def _is_db_corruption_error(cls, exc: BaseException) -> bool:
        """Return whether *exc* indicates an unrecoverably damaged DB file.

        SQLite reports these as ``DatabaseError`` (or wrapped variants via
        aiosqlite).  We match by both ``sqlite_errorcode`` and a small set of
        well-known message substrings — sufficient because SQLite emits
        these errors in English regardless of the process locale.
        """
        notadb = getattr(sqlite3, "SQLITE_NOTADB", None)
        corrupt = getattr(sqlite3, "SQLITE_CORRUPT", None)
        corruption_codes = {code for code in (notadb, corrupt) if code is not None}

        seen: set[int] = set()
        queue: List[BaseException] = [exc]
        while queue:
            current = queue.pop(0)
            if current is None:
                continue
            ident = id(current)
            if ident in seen:
                continue
            seen.add(ident)

            error_code = getattr(current, "sqlite_errorcode", None)
            if error_code is not None and error_code in corruption_codes:
                return True

            module_name = getattr(type(current), "__module__", "")
            is_sqlite_error = (
                isinstance(current, sqlite3.Error)
                or module_name.startswith("sqlite3")
                or module_name.startswith("aiosqlite")
            )
            if is_sqlite_error:
                text = str(current).lower()
                if any(token in text for token in cls._DB_CORRUPTION_TOKENS):
                    return True

            cause = getattr(current, "__cause__", None)
            context = getattr(current, "__context__", None)
            if cause is not None:
                queue.append(cause)
            if context is not None:
                queue.append(context)

        return False

    @classmethod
    def _quarantine_corrupt_db(cls, db_path: Path) -> Optional[Path]:
        """Move a damaged DB (and its WAL/SHM sidecars) to a timestamped name.

        Returns the new location of the main file so callers can surface it
        in logs or recovery instructions.  Returns ``None`` when there was
        nothing to quarantine (no main file present), or when the rename
        failed — in which case the caller must propagate the original error
        because we cannot safely recreate the file in place.
        """
        db_path = Path(db_path)
        if not db_path.exists():
            return None

        from datetime import UTC

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        suffix = f".corrupt.{timestamp}"

        new_main = db_path.with_name(db_path.name + suffix)
        # Avoid collision if multiple corruptions happen within the same
        # second — fall back to a counter suffix.
        counter = 1
        while new_main.exists():
            new_main = db_path.with_name(f"{db_path.name}{suffix}.{counter}")
            counter += 1

        try:
            db_path.rename(new_main)
        except OSError as exc:
            cls._log.error(
                "storage.quarantine.rename_failed",
                {
                    "path": str(db_path),
                    "error": str(exc),
                },
            )
            return None

        for sidecar_name in (f"{db_path.name}-wal", f"{db_path.name}-shm"):
            side_path = db_path.with_name(sidecar_name)
            if not side_path.exists():
                continue
            try:
                side_path.rename(side_path.with_name(sidecar_name + suffix))
            except OSError as exc:
                cls._log.warn(
                    "storage.quarantine.sidecar_rename_failed",
                    {
                        "path": str(side_path),
                        "error": str(exc),
                    },
                )

        cls._log.error(
            "storage.corruption.quarantined",
            {
                "original_path": str(db_path),
                "quarantined_path": str(new_main),
                "hint": (
                    "Server is starting with a fresh empty database. "
                    "Run scripts/recover_raw_flocks_db.py against the "
                    "quarantined file to attempt data recovery."
                ),
            },
        )
        return new_main

    @classmethod
    def _is_sqlite_busy_error(cls, exc: Exception) -> bool:
        """Return whether *exc* is a retryable SQLite busy/locked write error."""
        busy_codes = {
            code
            for code in (
                getattr(sqlite3, "SQLITE_BUSY", None),
                getattr(sqlite3, "SQLITE_LOCKED", None),
            )
            if code is not None
        }
        seen: set[int] = set()
        queue: List[BaseException] = [exc]

        while queue:
            current = queue.pop(0)
            if current is None:
                continue
            ident = id(current)
            if ident in seen:
                continue
            seen.add(ident)

            error_code = getattr(current, "sqlite_errorcode", None)
            if error_code in busy_codes:
                return True

            module_name = getattr(type(current), "__module__", "")
            is_sqlite_error = isinstance(current, sqlite3.Error) or module_name.startswith("sqlite3")
            is_aiosqlite_error = module_name.startswith("aiosqlite")
            if is_sqlite_error or is_aiosqlite_error:
                text = str(current).lower()
                if any(
                    token in text
                    for token in (
                        "database is locked",
                        "database table is locked",
                        "database schema is locked",
                        "database is busy",
                        "database table is busy",
                    )
                ):
                    return True

            cause = getattr(current, "__cause__", None)
            context = getattr(current, "__context__", None)
            if cause is not None:
                queue.append(cause)
            if context is not None:
                queue.append(context)

        return False

    @classmethod
    async def _run_write_with_retry(
        cls,
        operation: Callable[[], Awaitable[Any]],
        *,
        action: str,
        target: Optional[str] = None,
    ) -> Any:
        """Run a write operation with bounded retries for SQLite lock contention."""
        attempts = max(int(cls._sqlite_write_retry_attempts), 1)
        delay_s = max(float(cls._sqlite_write_retry_base_delay_s), 0.0)
        last_exc: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                return await operation()
            except Exception as exc:
                if not cls._is_sqlite_busy_error(exc) or attempt >= attempts:
                    raise
                last_exc = exc
                sleep_s = delay_s * (2 ** (attempt - 1))
                cls._log.warn(
                    "storage.sqlite_write_retry",
                    {
                        "action": action,
                        "target": target,
                        "attempt": attempt,
                        "max_attempts": attempts,
                        "sleep_s": round(sleep_s, 3),
                        "error": str(exc),
                    },
                )
                await asyncio.sleep(sleep_s)

        assert last_exc is not None
        raise last_exc

    @classmethod
    @asynccontextmanager
    async def connect(cls, db_path: Optional[Path] = None) -> AsyncIterator[aiosqlite.Connection]:
        """Open a configured async SQLite connection for the active storage DB."""
        target = Path(db_path) if db_path is not None else cls.get_db_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(target, timeout=cls._sqlite_timeout_s)
        try:
            await cls.configure_connection(conn)
            yield conn
        finally:
            await conn.close()

    @classmethod
    def connect_sync(cls, db_path: Optional[Path] = None) -> sqlite3.Connection:
        """Open a configured sync SQLite connection for the active storage DB."""
        target = Path(db_path) if db_path is not None else cls.get_db_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target, timeout=cls._sqlite_timeout_s)
        conn.row_factory = sqlite3.Row
        return cls.configure_sync_connection(conn)

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
        """Return a SQLite LIKE pattern that treats prefix chars literally."""
        return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    @classmethod
    def _like_prefix_clause(cls, column: str = "key") -> str:
        return f"{column} LIKE ? ESCAPE '\\'"

    @staticmethod
    def _marker_row_count(marker: Dict[str, Any], key: str) -> int:
        try:
            return int(marker.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _read_multi_db_migration_marker_sync(cls) -> Dict[str, Any]:
        if cls._db_path is None or not cls._db_path.exists():
            return {}
        try:
            conn = cls.connect_sync(cls._db_path)
            try:
                row = conn.execute(
                    "SELECT value FROM storage WHERE key = ?",
                    (cls._multi_db_migration_marker_key,),
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                return {}
            value = json.loads(row["value"])
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    @classmethod
    def _write_multi_db_migration_marker_sync(cls, marker: Dict[str, Any]) -> None:
        if cls._db_path is None:
            raise StorageError("Storage DB path is not initialized")
        from datetime import UTC

        now = datetime.now(UTC).isoformat()
        serialized = json.dumps(marker)
        conn = cls.connect_sync(cls._db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO storage (key, value, type, created_at, updated_at)
                VALUES (?, ?, ?,
                    COALESCE((SELECT created_at FROM storage WHERE key = ?), ?),
                    ?)
                """,
                (
                    cls._multi_db_migration_marker_key,
                    serialized,
                    "json",
                    cls._multi_db_migration_marker_key,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    @classmethod
    async def _ensure_storage_table(cls, db_path: Path) -> None:
        """Ensure the generic KV storage table exists in *db_path*."""
        db_path.parent.mkdir(parents=True, exist_ok=True)

        async def _create_storage_table() -> None:
            async with cls.connect(db_path) as db:
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

        await cls._run_write_with_retry(
            _create_storage_table,
            action="init.create_storage_table",
            target=str(db_path),
        )

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
        # temporary database that later disappears.  We also force a
        # reinit after ``fork()`` (detected via PID mismatch) to avoid the
        # child silently reusing the parent's open SQLite handle.
        current_pid = os.getpid()
        same_path = cls._db_path == db_path and db_path.exists()
        same_process = cls._init_pid is None or cls._init_pid == current_pid
        if cls._initialized and same_path and same_process:
            return

        cls._db_path = db_path
        cls._db_path.parent.mkdir(parents=True, exist_ok=True)
        cls._invalidate_runtime_caches()

        # Fast-path: if the file on disk is non-empty but does not carry
        # the SQLite magic header, quarantine it *before* opening so that
        # SQLite never gets a chance to delete adjacent WAL/SHM sidecars
        # — those sidecars are what the offline recovery script reads.
        if cls._file_has_invalid_sqlite_header(cls._db_path):
            cls._log.error(
                "storage.corruption.invalid_header",
                {
                    "db_path": str(cls._db_path),
                    "size": cls._db_path.stat().st_size,
                },
            )
            quarantined = cls._quarantine_corrupt_db(cls._db_path)
            if quarantined is None:
                # The pre-flight check confirmed the file is not SQLite,
                # but the rename failed.  Continuing would let SQLite
                # open the bad file, raise ``DatabaseError`` *and* delete
                # the adjacent WAL/SHM sidecars we wanted to preserve
                # for offline recovery.  Fail loudly instead so the
                # operator can move the file aside manually.
                raise StorageError(
                    f"Storage path {cls._db_path} contains non-SQLite "
                    f"content and could not be quarantined.  Move the "
                    f"file aside manually before restarting (see logs)."
                )

        # The schema bootstrap is the very first time we open the DB file
        # in this process, so it is also where any remaining on-disk
        # corruption surfaces as ``DatabaseError: file is not a database``
        # (or "disk image is malformed") — for example when only an
        # internal SQLite page is damaged while the header still looks
        # valid.  When the failure is unrecoverable we quarantine the
        # damaged file, leave a recovery hint in the logs, and retry once
        # against a fresh empty database — keeping the server bootable.
        try:
            await cls._bootstrap_schema()
        except Exception as exc:
            if not cls._is_db_corruption_error(exc):
                raise
            cls._log.error(
                "storage.corruption.detected_on_init",
                {
                    "db_path": str(cls._db_path),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            quarantined = cls._quarantine_corrupt_db(cls._db_path)
            if quarantined is None:
                raise
            await cls._bootstrap_schema()

        # Drain any residual WAL frames left by the previous process so the
        # next ``SIGKILL`` does not have to truncate a 4 MB-class WAL during
        # recovery (which is exactly when main-DB page 1 / the header can
        # get a half-written update).  This is a no-op on a freshly created
        # DB.  Best-effort — we never want this to break startup.
        try:
            await cls._checkpoint(mode="TRUNCATE")
        except Exception as exc:
            cls._log.warn("storage.startup_checkpoint.failed", {"error": str(exc)})

        cls._init_pid = os.getpid()
        cls._initialized = True
        cls._log.info(
            "storage.initialized",
            {
                "db_path": str(db_path),
                "pid": cls._init_pid,
            },
        )

    @classmethod
    async def _checkpoint(cls, *, mode: str = "TRUNCATE") -> tuple[int, int, int]:
        """Run ``PRAGMA wal_checkpoint(<mode>)`` against the active DB.

        ``TRUNCATE`` flushes the WAL back into the main DB and resets the
        ``-wal`` file to zero length, which means the next process start
        does **not** need a WAL recovery step and therefore cannot crash
        midway through rewriting the main-DB header page.

        SQLite reports contention by returning a row of integers rather
        than raising — specifically ``(busy, log_pages, checkpointed_pages)``
        where ``busy=1`` means at least one reader/writer was holding a
        lock that prevented the requested mode from completing.  We
        fetch that row, surface ``busy=1`` as :class:`CheckpointBusyError`,
        and otherwise return the tuple so callers can record metrics.

        Quietly returns ``(0, 0, 0)`` when no DB has been initialised
        yet — callers can invoke this unconditionally during shutdown.
        """
        if cls._db_path is None:
            return (0, 0, 0)
        if not cls._db_path.exists():
            return (0, 0, 0)
        valid_modes = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
        mode_normalised = mode.upper()
        if mode_normalised not in valid_modes:
            raise ValueError(f"invalid checkpoint mode: {mode!r}")
        async with cls.connect(cls._db_path) as db:
            cursor = await db.execute(f"PRAGMA wal_checkpoint({mode_normalised})")
            row = await cursor.fetchone()
            await db.commit()

        # Some aiosqlite paths / wrapped connections may return no row
        # for a PRAGMA statement.  We treat that as success because we
        # have no signal to act on — the caller would have observed an
        # exception if the PRAGMA had actually failed.
        if row is None or len(row) < 3:
            return (0, 0, 0)

        busy = int(row[0])
        log_pages = int(row[1])
        checkpointed_pages = int(row[2])
        if busy != 0:
            raise CheckpointBusyError(mode_normalised, log_pages, checkpointed_pages)
        return (busy, log_pages, checkpointed_pages)

    # Tunables for the shutdown WAL drain.  Kept as class attributes so
    # tests can override them without touching globals.
    _shutdown_checkpoint_attempts = 3
    _shutdown_checkpoint_backoff_s = 0.1

    @classmethod
    async def shutdown(cls) -> None:
        """Flush the WAL and release state — call once during process exit.

        This is the missing counterpart to ``init()``.  It performs a
        ``wal_checkpoint(TRUNCATE)`` so the on-disk DB is left fully
        consistent (no pending WAL frames waiting to be replayed), then
        clears the in-memory ``_initialized`` / ``_init_pid`` flags.

        Failure handling:

        * If the checkpoint cannot acquire the locks it needs (SQLite
          returns ``busy=1`` without raising) we retry a few times with
          a short backoff.  This handles the common case where a
          background task is still draining when shutdown starts.
        * If every attempt is still busy we record a structured
          ``checkpoint.unfinished`` warning rather than a misleading
          ``done`` so operators can spot the residual-WAL risk.
        * Any other exception (e.g. disk full) is logged at ``warn`` so
          shutdown cannot itself crash the lifespan.

        The in-memory state is always cleared at the end because the
        process is exiting regardless.
        """
        if not cls._initialized:
            return

        attempts = max(int(cls._shutdown_checkpoint_attempts), 1)
        backoff = max(float(cls._shutdown_checkpoint_backoff_s), 0.0)
        last_busy: Optional[CheckpointBusyError] = None
        last_failure: Optional[Exception] = None

        try:
            for attempt in range(1, attempts + 1):
                try:
                    _, log_pages, checkpointed = await cls._checkpoint(mode="TRUNCATE")
                    cls._log.info(
                        "storage.shutdown.checkpoint.done",
                        {
                            "db_path": str(cls._db_path) if cls._db_path else None,
                            "log_pages": log_pages,
                            "checkpointed_pages": checkpointed,
                            "attempts": attempt,
                        },
                    )
                    return
                except CheckpointBusyError as exc:
                    last_busy = exc
                    cls._log.warn(
                        "storage.shutdown.checkpoint.busy",
                        {
                            "attempt": attempt,
                            "max_attempts": attempts,
                            "mode": exc.mode,
                            "log_pages": exc.log_pages,
                            "checkpointed_pages": exc.checkpointed_pages,
                        },
                    )
                    if attempt < attempts:
                        await asyncio.sleep(backoff * attempt)
                except Exception as exc:
                    last_failure = exc
                    cls._log.warn(
                        "storage.shutdown.checkpoint.failed",
                        {
                            "db_path": str(cls._db_path) if cls._db_path else None,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        },
                    )
                    break

            # Reached only on persistent busy or fatal failure.  Do NOT
            # log "done" — that would mask the residual-WAL risk this
            # whole method exists to prevent.
            cls._log.warn(
                "storage.shutdown.checkpoint.unfinished",
                {
                    "db_path": str(cls._db_path) if cls._db_path else None,
                    "busy": last_busy is not None,
                    "log_pages": getattr(last_busy, "log_pages", None),
                    "checkpointed_pages": getattr(last_busy, "checkpointed_pages", None),
                    "fatal_error": str(last_failure) if last_failure else None,
                    "hint": (
                        "WAL was not truncated; next startup will run WAL "
                        "recovery and remains at risk of header corruption if "
                        "killed mid-recovery."
                    ),
                },
            )
        finally:
            cls._initialized = False
            cls._init_pid = None

    @classmethod
    async def _bootstrap_schema(cls) -> None:
        """Create the storage tables and run registered extension DDLs.

        Split out from ``init()`` so we can call it twice in the rare case
        where the existing DB file is corrupted and gets quarantined: the
        first call surfaces the corruption, and the second call (after the
        rename) bootstraps a fresh database in its place.
        """
        await cls._ensure_storage_table(cls._db_path)

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

                async def _run_extension_ddl() -> None:
                    async with cls.connect(cls._db_path) as db:
                        if isinstance(ddl, str):
                            await db.executescript(ddl)
                        else:
                            await ddl(db)
                        await db.commit()

                await cls._run_write_with_retry(
                    _run_extension_ddl,
                    action="init.extension_ddl",
                    target=str(cls._db_path),
                )
            except Exception as e:
                cls._log.warn("storage.extension_ddl.failed", {"error": str(e)})

    @classmethod
    async def _create_model_management_tables(cls) -> None:
        """Create dynamic data tables (idempotent).

        Only usage_records lives in SQLite. All static configuration
        (credentials, model settings, default models, custom providers)
        is stored in flocks.json / .secret.json.
        """

        async def _create_tables() -> None:
            async with cls.connect(cls._db_path) as db:
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
                    (
                        "cache_write_tokens",
                        "ALTER TABLE usage_records ADD COLUMN cache_write_tokens INTEGER NOT NULL DEFAULT 0",
                    ),
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

        await cls._run_write_with_retry(
            _create_tables,
            action="init.model_management_tables",
            target=str(cls._db_path),
        )
        cls._log.info("storage.model_management_tables_ready")

    @classmethod
    async def _ensure_init(cls) -> None:
        """Ensure storage is initialized for the *current* process.

        SQLite explicitly warns against using a connection opened in a
        parent across ``fork()``.  uvicorn ``--reload`` and any future
        multi-worker launch will fork *after* this module has been
        imported, which copies the parent's ``_initialized=True`` flag
        into the child.  Without the PID check below the child would
        reuse the parent's open file descriptor — a classic SQLite
        corruption vector ("locking problems will result").

        We detect that case by remembering the PID that called
        :meth:`init` and forcing a fresh per-process initialisation
        whenever the current PID differs.
        """
        current_pid = os.getpid()
        forked = cls._initialized and cls._init_pid is not None and cls._init_pid != current_pid
        if forked:
            cls._log.warn(
                "storage.fork_detected",
                {
                    "parent_pid": cls._init_pid,
                    "child_pid": current_pid,
                    "hint": "Reinitialising Storage to avoid sharing SQLite file descriptors across processes.",
                },
            )
            cls._initialized = False
            cls._init_pid = None

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
        db_path = cls.route_db_path_for_key(key)

        if isinstance(value, BaseModel):
            serialized = value.model_dump_json()
        else:
            serialized = json.dumps(value)

        from datetime import UTC

        now = datetime.now(UTC).isoformat()

        async def _write() -> None:
            async with cls.connect(db_path) as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO storage (key, value, type, created_at, updated_at)
                    VALUES (?, ?, ?, 
                        COALESCE((SELECT created_at FROM storage WHERE key = ?), ?),
                        ?)
                """,
                    (key, serialized, value_type, key, now, now),
                )
                await db.commit()

        await cls._run_write_with_retry(_write, action="set", target=key)

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
        db_path = cls.route_db_path_for_key(key)

        async with cls.connect(db_path) as db:
            async with db.execute("SELECT value, type FROM storage WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None

        value_str, value_type = row

        if model is not None and hasattr(model, "model_validate_json"):
            return model.model_validate_json(value_str)
        # Fall back to a plain JSON decode when no Pydantic model is supplied
        # (or when callers accidentally pass a builtin container type such as
        # ``dict``/``list``, which is not a Pydantic model).
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
        db_path = cls.route_db_path_for_key(key)

        async def _delete() -> bool:
            async with cls.connect(db_path) as db:
                cursor = await db.execute("DELETE FROM storage WHERE key = ?", (key,))
                await db.commit()
                return cursor.rowcount > 0

        deleted = await cls._run_write_with_retry(_delete, action="delete", target=key)

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
        if prefix is None:
            db_paths = (cls.get_db_path(),)
        else:
            db_paths = (cls.route_db_path_for_prefix(prefix),)

        keys: set[str] = set()
        for db_path in db_paths:
            async with cls.connect(db_path) as db:
                if prefix:
                    query = f"SELECT key FROM storage WHERE {cls._like_prefix_clause()}"
                    params = (cls._like_prefix_pattern(prefix),)
                else:
                    query = "SELECT key FROM storage"
                    params = ()

                async with db.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
            keys.update(row[0] for row in rows)

        return sorted(keys)

    @classmethod
    async def _list_entry_rows(
        cls,
        *,
        prefix: Optional[str],
    ) -> List[Tuple[str, str]]:
        if prefix is None:
            db_paths = (cls.get_db_path(),)
        else:
            db_paths = (cls.route_db_path_for_prefix(prefix),)

        rows_by_key: dict[str, str] = {}
        for db_path in db_paths:
            async with cls.connect(db_path) as db:
                if prefix:
                    query = f"SELECT key, value FROM storage WHERE {cls._like_prefix_clause()}"
                    params = (cls._like_prefix_pattern(prefix),)
                else:
                    query = "SELECT key, value FROM storage"
                    params = ()

                async with db.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
            for key, value in rows:
                rows_by_key[key] = value

        return sorted(rows_by_key.items(), key=lambda item: item[0])

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
    async def list_entries_page(
        cls,
        prefix: str,
        *,
        offset: int = 0,
        limit: int = 100,
        model: Optional[Type[T]] = None,
    ) -> tuple[List[Tuple[str, T | Any]], int]:
        """List one page of entries for a prefix, plus total matching rows."""
        await cls._ensure_init()
        db_path = cls.route_db_path_for_prefix(prefix)

        safe_offset = max(int(offset), 0)
        safe_limit = max(int(limit), 0)
        params = (cls._like_prefix_pattern(prefix),)

        async with cls.connect(db_path) as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM storage WHERE {cls._like_prefix_clause()}",
                params,
            ) as cursor:
                row = await cursor.fetchone()
                total = int(row[0]) if row else 0

            if safe_limit == 0:
                return [], total

            async with db.execute(
                f"""
                SELECT key, value FROM storage
                WHERE {cls._like_prefix_clause()}
                ORDER BY key
                LIMIT ? OFFSET ?
                """,
                (cls._like_prefix_pattern(prefix), safe_limit, safe_offset),
            ) as cursor:
                rows = await cursor.fetchall()

        entries: List[Tuple[str, T | Any]] = []
        for key, value_str in rows:
            if model is not None and hasattr(model, "model_validate_json"):
                value = model.model_validate_json(value_str)
            else:
                value = json.loads(value_str)
            entries.append((key, value))
        return entries, total

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
        Compatible with all SQLite versions (no ``json_extract`` required).
        """
        await cls._ensure_init()
        return await cls._list_entry_rows(prefix=prefix)

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
        db_path = cls.route_db_path_for_key(key)

        async with cls.connect(db_path) as db:
            async with db.execute("SELECT 1 FROM storage WHERE key = ?", (key,)) as cursor:
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
        if prefix is None:
            db_paths = (cls.get_db_path(),)
        else:
            db_paths = (cls.route_db_path_for_prefix(prefix),)

        async def _clear_db(db_path: Path) -> int:
            async with cls.connect(db_path) as db:
                if prefix:
                    query = f"DELETE FROM storage WHERE {cls._like_prefix_clause()}"
                    params = (cls._like_prefix_pattern(prefix),)
                    cursor = await db.execute(query, params)
                else:
                    query = "DELETE FROM storage WHERE key != ?"
                    params = (cls._multi_db_migration_marker_key,)
                    cursor = await db.execute(query, params)
                    await db.execute(
                        "DELETE FROM storage WHERE key = ?",
                        (cls._multi_db_migration_marker_key,),
                    )

                await db.commit()
                return cursor.rowcount

        deleted = 0
        for db_path in db_paths:
            deleted += await cls._run_write_with_retry(
                lambda db_path=db_path: _clear_db(db_path),
                action="clear",
                target=f"{prefix or '<all>'}@{db_path}",
            )

        cls._log.info("storage.clear", {"prefix": prefix, "deleted": deleted})
        cls._invalidate_runtime_caches(prefix)
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
