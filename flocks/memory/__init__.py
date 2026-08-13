"""
Flocks Memory System

Provides persistent memory and semantic search capabilities for agents.

Uses filesystem-managed curated Memory plus lifecycle-owned Daily evidence.
"""

# Core manager
from flocks.memory.manager import MemoryManager

# Filesystem-managed components
from flocks.memory.bootstrap import MemoryBootstrap
from flocks.memory.daily import DailyMemory
from flocks.memory.flush import MemoryFlush, extract_and_save

from flocks.memory.types import (
    MemoryScope,
    MemorySource,
    MemoryTimeRange,
    MemorySearchResult,
    MemorySyncProgress,
    MemoryProviderStatus,
    MemoryFileEntry,
    MemoryChunk,
    EmbeddingResult,
)

from flocks.memory.config import (
    MemoryConfig,
    MemoryEmbeddingConfig,
    MemorySearchConfig,
    MemoryChunkingConfig,
    MemorySyncConfig,
    MemoryQueryConfig,
    MemoryCacheConfig,
    MemoryBatchConfig,
    MemoryAutoFlushConfig,
    MemoryDreamConfig,
    resolve_memory_config,
)

from flocks.memory.utils import (
    compute_hash,
    compute_text_hash,
    truncate_text,
    extract_snippet,
    normalize_path,
)

__all__ = [
    # Core
    "MemoryManager",
    
    # Filesystem-managed components
    "MemoryBootstrap",
    "DailyMemory",
    "MemoryFlush",
    "extract_and_save",
    
    # Types
    "MemoryScope",
    "MemorySource",
    "MemoryTimeRange",
    "MemorySearchResult",
    "MemorySyncProgress",
    "MemoryProviderStatus",
    "MemoryFileEntry",
    "MemoryChunk",
    "EmbeddingResult",
    
    # Config
    "MemoryConfig",
    "MemoryEmbeddingConfig",
    "MemorySearchConfig",
    "MemoryChunkingConfig",
    "MemorySyncConfig",
    "MemoryQueryConfig",
    "MemoryCacheConfig",
    "MemoryBatchConfig",
    "MemoryAutoFlushConfig",
    "MemoryDreamConfig",
    "resolve_memory_config",
    
    # Utils
    "compute_hash",
    "compute_text_hash",
    "truncate_text",
    "extract_snippet",
    "normalize_path",
]

__version__ = "0.2.0"
