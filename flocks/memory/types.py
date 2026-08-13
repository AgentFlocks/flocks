"""
Memory system type definitions

Defines data models for memory search, sync, and management.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class MemorySource(str, Enum):
    """Memory source type"""
    MEMORY = "memory"      # Global and Project Markdown memory files
    SESSION = "session"    # Historical session transcripts


class MemoryScope(str, Enum):
    """Visibility scope for file-backed Memory."""

    GLOBAL = "global"
    PROJECT = "project"


@dataclass(frozen=True)
class MemoryTimeRange:
    """Normalized half-open time range for Session and Daily search."""

    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    daily_start_date: Optional[str] = None
    daily_end_date: Optional[str] = None

    @classmethod
    def from_strings(
        cls,
        start_time: Optional[str],
        end_time: Optional[str],
    ) -> Optional["MemoryTimeRange"]:
        """Parse ISO 8601 bounds, assuming local time when no offset is given."""
        if start_time is None and end_time is None:
            return None

        local_tz = datetime.now().astimezone().tzinfo

        def parse(value: Optional[str], name: str) -> Optional[datetime]:
            if value is None:
                return None
            text = value.strip()
            if not text:
                raise ValueError(f"{name} must be a non-empty ISO 8601 value")
            if text.endswith("Z"):
                text = f"{text[:-1]}+00:00"
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid {name}: {value!r}. Use ISO 8601 format."
                ) from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=local_tz)
            return parsed

        start = parse(start_time, "start_time")
        end = parse(end_time, "end_time")
        start_ms = int(start.timestamp() * 1000) if start is not None else None
        end_ms = int(end.timestamp() * 1000) if end is not None else None
        if start_ms is not None and end_ms is not None and start_ms >= end_ms:
            raise ValueError("start_time must be earlier than end_time")

        daily_end = None
        if end is not None:
            daily_end_date = end.date()
            if end.time() != datetime.min.time():
                daily_end_date += timedelta(days=1)
            daily_end = daily_end_date.isoformat()

        return cls(
            start_ms=start_ms,
            end_ms=end_ms,
            daily_start_date=(start.date().isoformat() if start is not None else None),
            daily_end_date=daily_end,
        )


class MemorySearchResult(BaseModel):
    """Search result from memory system"""
    path: str = Field(..., description="File path relative to workspace")
    start_line: int = Field(..., description="Starting line number")
    end_line: int = Field(..., description="Ending line number")
    score: float = Field(..., description="Similarity score (0-1)")
    snippet: str = Field(..., description="Text snippet")
    source: MemorySource = Field(..., description="Memory source")
    citation: Optional[str] = Field(None, description="Citation format (e.g., MEMORY.md#L10-L15)")


class MemorySyncProgress(BaseModel):
    """Progress update during sync operation"""
    completed: int = Field(..., description="Number of completed items")
    total: int = Field(..., description="Total number of items")
    label: Optional[str] = Field(None, description="Current operation label")


class MemoryProviderStatus(BaseModel):
    """Memory system status information"""
    enabled: bool = Field(..., description="Whether memory system is enabled")
    provider: str = Field(..., description="Current embedding provider")
    model: Optional[str] = Field(None, description="Embedding model name")
    requested_provider: Optional[str] = Field(None, description="Requested provider")
    fallback_from: Optional[str] = Field(None, description="Fallback source provider")
    fallback_reason: Optional[str] = Field(None, description="Reason for fallback")
    
    # Statistics
    files: int = Field(0, description="Number of indexed files")
    chunks: int = Field(0, description="Number of indexed chunks")
    
    # Configuration
    workspace_dir: Optional[str] = Field(None, description="Workspace directory")
    db_path: Optional[str] = Field(None, description="Database path")
    extra_paths: List[str] = Field(default_factory=list, description="Extra paths to index")
    sources: List[MemorySource] = Field(default_factory=list, description="Enabled sources")
    
    # Feature status
    cache: Dict[str, Any] = Field(default_factory=dict, description="Cache status")
    fts: Dict[str, Any] = Field(default_factory=dict, description="FTS5 status")
    vector: Dict[str, Any] = Field(default_factory=dict, description="Vector search status")


class MemoryFileEntry(BaseModel):
    """File entry for indexing"""
    scope: MemoryScope = Field(..., description="Memory visibility scope")
    scope_id: str = Field(..., description="Scope identifier")
    path: str = Field(..., description="Relative path")
    abs_path: str = Field(..., description="Absolute path")
    mtime_ms: float = Field(..., description="Modification time (milliseconds)")
    size: int = Field(..., description="File size (bytes)")
    hash: str = Field(..., description="Content hash (SHA256)")


class MemoryChunk(BaseModel):
    """Text chunk for embedding"""
    start_line: int = Field(..., description="Starting line number")
    end_line: int = Field(..., description="Ending line number")
    text: str = Field(..., description="Chunk text content")
    hash: str = Field(..., description="Chunk content hash")


class EmbeddingResult(BaseModel):
    """Embedding generation result"""
    embedding: List[float] = Field(..., description="Embedding vector")
    model: str = Field(..., description="Model name")
    dims: int = Field(..., description="Vector dimensions")
