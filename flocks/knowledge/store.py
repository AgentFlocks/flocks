"""
The knowledge store interface every backend implements.

The interface is deliberately narrow and says nothing about SQL, vectors, or
which database is underneath, so the code that ingests and retrieves knowledge
is the same whether a deployment runs on SQLite for a single machine or on
PostgreSQL shared between two.
"""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from flocks.knowledge.models import (
    AccessScope,
    Chunk,
    Document,
    EmbeddingSpec,
    KnowledgeVersion,
    SearchHit,
)


@runtime_checkable
class KnowledgeStore(Protocol):
    """Versioned storage and retrieval of knowledge chunks."""

    async def initialize(self) -> None:
        """Create whatever the backend needs. Must be safe to call repeatedly."""
        ...

    async def begin_version(self, base_id: str, version: str, embedding: EmbeddingSpec) -> KnowledgeVersion:
        """Start building a version. Nothing is retrievable until it is published."""
        ...

    async def add_document(self, base_id: str, version: str, document: Document, chunks: Sequence[Chunk]) -> None:
        """Write one document and its chunks into a building version."""
        ...

    async def publish_version(self, base_id: str, version: str) -> KnowledgeVersion:
        """Make a version live and supersede the previous one, atomically."""
        ...

    async def abandon_version(self, base_id: str, version: str, reason: str = "") -> None:
        """Mark a build failed so its partial contents are never served."""
        ...

    async def current_version(self, base_id: str) -> Optional[KnowledgeVersion]:
        """The published version, if there is one."""
        ...

    async def search(
        self,
        base_id: str,
        query_embedding: Sequence[float],
        embedding: EmbeddingSpec,
        scope: AccessScope,
        *,
        limit: int = 8,
        version: Optional[str] = None,
    ) -> list[SearchHit]:
        """Retrieve the most similar chunks the scope is allowed to see."""
        ...

    async def revoke_document(self, base_id: str, document_id: str) -> int:
        """Stop serving a document immediately.

        Takes effect in the query, not by rebuilding an index: waiting for
        index maintenance would keep returning content whose access was
        withdrawn.
        """
        ...

    async def close(self) -> None:
        ...


__all__ = ["KnowledgeStore"]
