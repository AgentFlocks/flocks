"""Stable storage interfaces for the PostgreSQL runtime.

Document writes and their derived search/message indexes share a scoped
Transaction. Extension hooks receive this interface instead of a driver
connection, so connection ownership stays with the pool and transaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import (
    Any,
    AsyncContextManager,
    Awaitable,
    Callable,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    runtime_checkable,
)

from flocks.db.dialect import Dialect
from flocks.runtime_state.store import ExecutionOwnership, StateEntry

# One document to write: key, value, and how the value should be stored.
Entry = Tuple[str, Any, str]


@runtime_checkable
class Transaction(Protocol):
    """A unit of work, and the handle a transaction hook is given.

    Deliberately small: enough to write a derived index, not enough to let a
    caller reach the driver underneath and reintroduce the coupling this
    abstraction exists to remove.
    """

    @property
    def dialect(self) -> Dialect:
        """The active backend's dialect, for building statements."""
        ...

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        ...

    async def execute_many(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        ...

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        ...

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[tuple]:
        ...

    async def fetch_all_mappings(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        """Rows keyed by column name.

        Callers can read ``row["session_id"]`` without depending on column
        order. The PostgreSQL implementation converts driver rows to dicts.
        """
        ...

    async def fetch_one_mapping(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        ...

    async def rollback(self) -> None:
        """Discard the work in progress.

        The counterpart to :meth:`commit`, for the same stores: code that
        opened its own transaction and has to undo it. On a backend where
        the surrounding block owns the transaction this is a no-op, so the
        call is safe either way.
        """
        ...

    async def commit(self) -> None:
        """Commit, for stores that hold a long-lived handle instead of a block.

        Inside ``Storage.transaction`` this is unnecessary -- the block commits
        on exit -- but several stores keep one connection open and commit as
        they go. On a backend in autocommit this is a no-op, so the call is
        safe either way.
        """
        ...


# A hook runs inside the same transaction as the documents it derives from.
TransactionHook = Callable[[Transaction], Awaitable[None]]


@runtime_checkable
class DocumentStore(Protocol):
    """Key-value documents, which is all the business code actually uses."""

    async def initialize(self) -> None:
        """Create whatever the backend needs. Safe to call repeatedly."""
        ...

    async def close(self) -> None:
        ...

    async def get(self, key: str) -> Optional[Any]:
        ...

    async def set(self, key: str, value: Any, value_type: str = "json") -> None:
        ...

    async def set_many(self, entries: Sequence[Entry]) -> None:
        """Write several documents atomically.

        A conversation turn writes a message and its parts together; a partial
        write leaves a reader with a message whose content is missing.
        """
        ...

    async def delete(self, key: str) -> bool:
        ...

    async def exists(self, key: str) -> bool:
        ...

    async def list_keys(self, prefix: Optional[str] = None) -> list[str]:
        ...

    async def list_entries(self, prefix: Optional[str] = None) -> list[StateEntry]:
        ...

    def transaction(self) -> "AsyncContextManager[Transaction]":
        """Open a transaction; everything inside commits or rolls back together.

        This is the door for modules that keep their own relational tables
        (workflows, tasks, devices, auth, usage) rather than key-value
        documents. They get a portable handle instead of a driver connection.
        """
        ...

    async def mutate_many(
        self,
        *,
        set_entries: Sequence[Entry] = (),
        delete_keys: Sequence[str] = (),
        delete_prefixes: Sequence[str] = (),
        pre_transaction_hook: Optional[TransactionHook] = None,
        transaction_hook: Optional[TransactionHook] = None,
    ) -> int:
        """Apply related writes, deletes and a derived-index hook in one commit.

        Returns the number of documents deleted, matching the existing
        ``Storage.mutate_many`` contract so call sites do not have to change.
        The pre-hook acquires related locks before any document mutations;
        both hooks share the document transaction and its rollback boundary.
        """
        ...


@runtime_checkable
class SearchIndex(Protocol):
    """Full-text search over session transcripts.

    Kept separate from :class:`DocumentStore` because the implementations have
    nothing in common: SQLite uses an FTS5 virtual table, PostgreSQL a
    ``tsvector`` column with a GIN index, and MySQL would need an ngram parser
    before it could tokenise Chinese at all. A deployment whose backend cannot
    do this must degrade to "no search", never to "search that finds nothing".
    """

    @property
    def available(self) -> bool:
        """False when this backend cannot provide full-text search."""
        ...

    async def initialize(self) -> None:
        ...

    async def upsert_document(
        self, transaction: Transaction, *, document_id: str, text: str, **fields: Any
    ) -> bool:
        """Index one document inside *transaction*, atomically with its source."""
        ...

    async def delete_documents(self, transaction: Transaction, document_ids: Sequence[str]) -> int:
        ...

    async def search(self, query: str, *, limit: int = 20, offset: int = 0) -> list[tuple]:
        ...


@runtime_checkable
class ExecutionLeases(Protocol):
    """Ownership of background work, for deployments running more than one node."""

    async def claim_execution(
        self, execution_id: str, *, node_id: str, lease_seconds: int
    ) -> Optional[ExecutionOwnership]:
        ...

    async def renew_execution_lease(
        self, execution_id: str, *, node_id: str, generation: int, lease_seconds: int
    ) -> bool:
        ...

    async def release_execution_lease(self, execution_id: str, *, node_id: str) -> bool:
        ...

    async def list_reclaimable_executions(
        self, *, node_id: str, stale_before: datetime
    ) -> list[ExecutionOwnership]:
        ...


__all__ = [
    "DocumentStore",
    "Entry",
    "ExecutionLeases",
    "ExecutionOwnership",
    "SearchIndex",
    "StateEntry",
    "Transaction",
    "TransactionHook",
]
