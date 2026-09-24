"""
Types for the knowledge store.

A knowledge base is versioned rather than mutated in place. Ingestion writes
chunks under a new version and publishes it only once every chunk is in, so a
retrieval never sees a half-ingested document -- and a failed ingest leaves the
previous version serving rather than a partial one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence


class VersionState(str, Enum):
    """Where a version is in its lifecycle."""

    BUILDING = "building"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"
    FAILED = "failed"


@dataclass(frozen=True)
class EmbeddingSpec:
    """Which model produced a version's vectors, and their shape.

    Recorded per version because vectors from different models are not
    comparable. A model or dimension change means the version has to be rebuilt
    rather than topped up, and mixing them silently would degrade retrieval in
    a way no error surfaces.
    """

    model: str
    dimensions: int
    provider: str = ""

    def matches(self, other: "EmbeddingSpec") -> bool:
        return self.model == other.model and self.dimensions == other.dimensions


@dataclass(frozen=True)
class AccessScope:
    """Who may see a document.

    Retrieval filters on this in SQL rather than after the fact: a post-filter
    would have already spent the similarity search on rows the caller cannot
    read, and would return fewer results than asked for whenever some were
    filtered away.
    """

    owner_id: str = ""
    project_id: str = ""
    groups: Sequence[str] = field(default_factory=tuple)

    @property
    def is_unrestricted(self) -> bool:
        return not self.owner_id and not self.project_id and not self.groups


@dataclass
class Chunk:
    """One retrievable piece of a document."""

    document_id: str
    ordinal: int
    text: str
    embedding: Optional[Sequence[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    chunk_id: str = ""


@dataclass
class Document:
    """A source document, before chunking."""

    document_id: str
    title: str
    source_uri: str = ""
    media_type: str = "text/markdown"
    owner_id: str = ""
    project_id: str = ""
    groups: Sequence[str] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeVersion:
    """One build of a knowledge base."""

    base_id: str
    version: str
    state: VersionState
    embedding: EmbeddingSpec
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: Optional[datetime] = None
    document_count: int = 0
    chunk_count: int = 0


@dataclass
class SearchHit:
    """One retrieval result."""

    chunk_id: str
    document_id: str
    title: str
    text: str
    score: float
    ordinal: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class KnowledgeError(RuntimeError):
    """The store could not satisfy the request."""


class EmbeddingMismatch(KnowledgeError):
    """A query's vectors do not match the version's.

    Comparing vectors from different models produces a number, not a
    similarity, so this is refused rather than answered with nonsense.
    """


__all__ = [
    "AccessScope",
    "Chunk",
    "Document",
    "EmbeddingMismatch",
    "EmbeddingSpec",
    "KnowledgeError",
    "KnowledgeVersion",
    "SearchHit",
    "VersionState",
]
