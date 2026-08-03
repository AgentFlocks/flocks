"""
Memory system type definitions

Defines data models for memory search, sync, and management.
"""

from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class MemorySource(str, Enum):
    """Memory source type"""
    MEMORY = "memory"      # MEMORY.md and memory/*.md files
    SESSION = "session"    # Historical session transcripts


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
    dirty: bool = Field(False, description="Whether sync is needed")
    
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
