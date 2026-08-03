"""
Memory system configuration models

Defines configuration structures for the memory system.
"""

from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class MemoryEmbeddingConfig(BaseModel):
    """Embedding provider configuration"""
    provider: Literal["auto", "openai", "google", "local"] = Field(
        "auto",
        description="Embedding provider (auto=try openai then google)"
    )
    model: str = Field(
        "text-embedding-3-small",
        description="Embedding model name"
    )
    api_key: Optional[str] = Field(
        None,
        description="API key (optional, can use env var)"
    )
    local_model_path: Optional[str] = Field(
        None,
        description="Local model path for local provider"
    )
    timeout_ms: int = Field(
        60000,
        description="Request timeout in milliseconds"
    )


class MemoryChunkingConfig(BaseModel):
    """Text chunking configuration"""
    tokens: int = Field(
        400,
        description="Tokens per chunk"
    )
    overlap: int = Field(
        80,
        description="Overlap tokens between chunks"
    )


class MemorySyncSessionConfig(BaseModel):
    """Session sync configuration"""
    enabled: bool = Field(
        True,
        description="Whether to index session transcripts"
    )
    delta_messages: int = Field(
        50,
        description="Number of new messages to trigger sync"
    )


class MemoryHooksSessionMemoryConfig(BaseModel):
    """Session memory hook configuration"""
    enabled: bool = Field(
        True,
        description="Enable session memory hook"
    )
    message_count: int = Field(
        15,
        description="Number of recent messages to save"
    )
    use_llm_slug: bool = Field(
        True,
        description="Use LLM to generate slug"
    )
    slug_timeout: int = Field(
        15,
        description="Slug generation timeout (seconds)"
    )


class MemoryHooksConfig(BaseModel):
    """Hooks configuration"""
    session_memory: MemoryHooksSessionMemoryConfig = Field(
        default_factory=MemoryHooksSessionMemoryConfig,
        description="Session memory hook configuration"
    )


class MemorySyncConfig(BaseModel):
    """Sync operation configuration"""
    on_session_start: bool = Field(
        True,
        description="Sync when session starts"
    )
    on_search: bool = Field(
        True,
        description="Sync before search if dirty"
    )
    watch: bool = Field(
        True,
        description="Watch files for changes"
    )
    watch_debounce_ms: int = Field(
        1500,
        description="Debounce delay for file watcher (ms)"
    )
    interval_minutes: int = Field(
        0,
        description="Periodic sync interval (0=disabled)"
    )
    sessions: MemorySyncSessionConfig = Field(
        default_factory=MemorySyncSessionConfig,
        description="Session sync configuration"
    )


class MemoryQueryHybridConfig(BaseModel):
    """Hybrid search configuration"""
    enabled: bool = Field(
        True,
        description="Enable hybrid search (vector + keyword)"
    )
    vector_weight: float = Field(
        0.7,
        description="Weight for vector search results"
    )
    text_weight: float = Field(
        0.3,
        description="Weight for keyword search results"
    )
    candidate_multiplier: int = Field(
        4,
        description="Candidate multiplier for hybrid search"
    )


class MemoryQueryConfig(BaseModel):
    """Search query configuration"""
    max_results: int = Field(
        6,
        description="Maximum number of results"
    )
    min_score: float = Field(
        0.35,
        description="Minimum similarity score (0-1)"
    )
    hybrid: MemoryQueryHybridConfig = Field(
        default_factory=MemoryQueryHybridConfig,
        description="Hybrid search configuration"
    )


class MemoryCacheConfig(BaseModel):
    """Embedding cache configuration"""
    enabled: bool = Field(
        True,
        description="Enable embedding cache"
    )
    max_entries: int = Field(
        10000,
        description="Maximum cache entries"
    )
    ttl_days: int = Field(
        90,
        description="Cache entry TTL in days"
    )


class MemoryBatchConfig(BaseModel):
    """Batch processing configuration"""
    enabled: bool = Field(
        True,
        description="Enable batch embedding generation"
    )
    concurrency: int = Field(
        4,
        description="Concurrent batch operations"
    )
    batch_size: int = Field(
        100,
        description="Items per batch"
    )
    max_tokens_per_batch: int = Field(
        8000,
        description="Maximum tokens per batch"
    )


class MemoryAutoFlushConfig(BaseModel):
    """Auto memory flush configuration"""
    enabled: bool = Field(
        True,
        description="Enable auto memory flush"
    )
    trigger_tokens: int = Field(
        4000,
        description="Trigger threshold in tokens"
    )
    reserve_tokens: int = Field(
        2000,
        description="Reserved tokens"
    )
    system_prompt: str = Field(
        "Session nearing context limit. Store important memories now.",
        description="System prompt for memory flush"
    )
    user_prompt: str = Field(
        "Write any lasting notes to memory/ directory; reply with NO_REPLY if nothing to store.",
        description="User prompt for memory flush"
    )


class CompactionConfig(BaseModel):
    """
    Dynamic compaction configuration.
    
    When ``auto`` is True (default), all thresholds are computed dynamically
    from the model's context_window via CompactionPolicy.  Individual ratios
    or absolute values can be overridden here; any non-None value will be
    forwarded as an override to ``CompactionPolicy.from_model(overrides=...)``.
    """
    auto: bool = Field(
        True,
        description="Enable dynamic compaction thresholds based on model context"
    )
    overflow_ratio: Optional[float] = Field(
        None,
        description="Override overflow detection ratio (0-1, e.g. 0.85 = trigger at 85%% of usable context)"
    )
    prune_protect_ratio: Optional[float] = Field(
        None,
        description="Override ratio of usable context to protect recent tool calls (0-1)"
    )
    prune_minimum_ratio: Optional[float] = Field(
        None,
        description="Override minimum prunable ratio to trigger pruning (0-1)"
    )
    flush_trigger_ratio: Optional[float] = Field(
        None,
        description="Override flush trigger ratio (0-1)"
    )
    flush_reserve_ratio: Optional[float] = Field(
        None,
        description="Override flush reserve ratio (0-1)"
    )
    summary_ratio: Optional[float] = Field(
        None,
        description="Override summary token budget ratio (0-1)"
    )
    # Absolute overrides (take precedence over ratios)
    summary_max_tokens: Optional[int] = Field(
        None,
        description="Override summary max tokens (absolute value)"
    )
    prune_protect: Optional[int] = Field(
        None,
        description="Override prune protect tokens (absolute value)"
    )
    prune_minimum: Optional[int] = Field(
        None,
        description="Override prune minimum tokens (absolute value)"
    )
    preserve_last: Optional[int] = Field(
        None,
        description="Override number of recent messages to preserve during truncation"
    )

    def to_overrides(self) -> dict:
        """
        Convert non-None fields into an overrides dict suitable for
        ``CompactionPolicy.from_model(overrides=...)``.
        """
        overrides: dict = {}
        for field_name in [
            "overflow_ratio",
            "prune_protect_ratio", "prune_minimum_ratio",
            "flush_trigger_ratio", "flush_reserve_ratio",
            "summary_ratio",
            "summary_max_tokens", "prune_protect", "prune_minimum",
            "preserve_last",
        ]:
            value = getattr(self, field_name, None)
            if value is not None:
                overrides[field_name] = value
        return overrides


class MemoryConfig(BaseModel):
    """Complete memory system configuration"""
    enabled: bool = Field(
        True,
        description="Enable memory system"
    )
    sources: List[Literal["memory", "session"]] = Field(
        ["memory"],
        description="Memory sources to index"
    )
    extra_paths: List[str] = Field(
        default_factory=list,
        description="Extra paths to index"
    )
    citations: Literal["auto", "on", "off"] = Field(
        "auto",
        description="Citation mode (auto=show in direct chats)"
    )
    
    # Sub-configurations
    hooks: MemoryHooksConfig = Field(
        default_factory=MemoryHooksConfig,
        description="Hooks configuration"
    )
    embedding: MemoryEmbeddingConfig = Field(
        default_factory=MemoryEmbeddingConfig,
        description="Embedding configuration"
    )
    chunking: MemoryChunkingConfig = Field(
        default_factory=MemoryChunkingConfig,
        description="Chunking configuration"
    )
    sync: MemorySyncConfig = Field(
        default_factory=MemorySyncConfig,
        description="Sync configuration"
    )
    query: MemoryQueryConfig = Field(
        default_factory=MemoryQueryConfig,
        description="Query configuration"
    )
    cache: MemoryCacheConfig = Field(
        default_factory=MemoryCacheConfig,
        description="Cache configuration"
    )
    batch: MemoryBatchConfig = Field(
        default_factory=MemoryBatchConfig,
        description="Batch processing configuration"
    )
    auto_flush: MemoryAutoFlushConfig = Field(
        default_factory=MemoryAutoFlushConfig,
        description="Auto flush configuration"
    )
    compaction: CompactionConfig = Field(
        default_factory=CompactionConfig,
        description="Dynamic compaction configuration (auto-scales to model context)"
    )
