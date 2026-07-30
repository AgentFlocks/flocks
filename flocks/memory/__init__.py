"""
Flocks Memory System

Provides persistent memory and semantic search capabilities for agents.

Based on OpenClaw's memory system, adapted for Flocks architecture.
"""

# Core manager
from flocks.memory.manager import MemoryManager

# OpenClaw-style components
from flocks.memory.bootstrap import MemoryBootstrap
from flocks.memory.daily import DailyMemory
from flocks.memory.flush import MemoryFlush, extract_and_save

from flocks.memory.types import (
    MemorySource,
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
    MemoryChunkingConfig,
    MemorySyncConfig,
    MemoryQueryConfig,
    MemoryCacheConfig,
    MemoryBatchConfig,
    MemoryAutoFlushConfig,
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
    
    # OpenClaw-style components
    "MemoryBootstrap",
    "DailyMemory",
    "MemoryFlush",
    "extract_and_save",
    
    # Types
    "MemorySource",
    "MemorySearchResult",
    "MemorySyncProgress",
    "MemoryProviderStatus",
    "MemoryFileEntry",
    "MemoryChunk",
    "EmbeddingResult",
    
    # Config
    "MemoryConfig",
    "MemoryEmbeddingConfig",
    "MemoryChunkingConfig",
    "MemorySyncConfig",
    "MemoryQueryConfig",
    "MemoryCacheConfig",
    "MemoryBatchConfig",
    "MemoryAutoFlushConfig",
    
    # Utils
    "compute_hash",
    "compute_text_hash",
    "truncate_text",
    "extract_snippet",
    "normalize_path",
]

__version__ = "0.2.0"
