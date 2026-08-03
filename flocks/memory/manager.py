"""
Memory Manager - Core orchestrator for memory system

Coordinates all memory system components: indexing, search, and sync.
"""

from typing import Optional, List, Dict, Any, Callable
from pathlib import Path
import asyncio
import os

from flocks.provider import Provider
from flocks.storage import Storage
from flocks.utils.file import File
from flocks.memory.types import (
    MemorySource,
    MemorySearchResult,
    MemoryProviderStatus,
    MemorySyncProgress,
)
from flocks.memory.config import MemoryConfig
from flocks.memory.search.hybrid import HybridSearch, decorate_citations
from flocks.memory.sync.indexer import MemoryIndexer
from flocks.utils.log import Log

log = Log.create(service="memory.manager")


def _safe_resolve_memory_path(memory_root: Path, rel_path: str) -> Path:
    """Resolve *rel_path* under *memory_root* and reject path-traversal attempts."""
    resolved = (memory_root / rel_path).resolve()
    root_resolved = memory_root.resolve()
    if not (resolved == root_resolved or os.path.commonpath([resolved, root_resolved]) == str(root_resolved)):
        raise ValueError(f"Path traversal detected: {rel_path}")
    return resolved


class MemoryManager:
    """
    Memory manager - orchestrates memory system
    
    Singleton per project, manages:
    - File indexing and sync
    - Hybrid search (vector + keyword)
    - Memory file operations
    """
    
    # Singleton cache by project_id
    _instances: Dict[str, "MemoryManager"] = {}
    
    def __init__(
        self,
        project_id: str,
        workspace_dir: str,
        config: MemoryConfig,
    ):
        """
        Initialize memory manager
        
        Args:
            project_id: Project ID
            workspace_dir: Workspace directory path
            config: Memory configuration
        """
        self.project_id = project_id
        self.workspace_dir = Path(workspace_dir)
        self.config = config
        
        # Provider configuration
        self.provider_id = config.embedding.provider
        if self.provider_id == "auto":
            self.provider_id = "openai"  # Default fallback
        
        self.embedding_model = config.embedding.model
        
        # Components (lazy initialization)
        self.search_engine: Optional[HybridSearch] = None
        self.indexer: Optional[MemoryIndexer] = None
        
        # State
        self._initialized = False
        self._dirty = False
        self._sync_lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
    
    @classmethod
    def get_instance(
        cls,
        project_id: str,
        workspace_dir: str,
        config: "MemoryConfig | dict",
    ) -> "MemoryManager":
        """
        Get or create singleton instance for project.

        If *config* is a plain dict it will be coerced to ``MemoryConfig``.
        When an instance already exists, its config and workspace_dir are
        updated in-place so callers always work against the latest values.
        
        Args:
            project_id: Project ID
            workspace_dir: Workspace directory
            config: Memory configuration (MemoryConfig or dict)
            
        Returns:
            MemoryManager instance
        """
        if isinstance(config, dict):
            config = MemoryConfig(**config)

        if project_id in cls._instances:
            instance = cls._instances[project_id]
            old_provider = instance.provider_id
            old_model = instance.embedding_model

            instance.config = config
            instance.workspace_dir = Path(workspace_dir)

            new_provider = config.embedding.provider
            if new_provider == "auto":
                new_provider = "openai"
            new_model = config.embedding.model

            if new_provider != old_provider or new_model != old_model:
                instance.provider_id = new_provider
                instance.embedding_model = new_model
                instance._initialized = False
                instance.search_engine = None
                instance.indexer = None
                log.info("manager.config_changed", {
                    "project_id": project_id,
                    "old_provider": old_provider,
                    "new_provider": new_provider,
                    "old_model": old_model,
                    "new_model": new_model,
                })

            return instance

        cls._instances[project_id] = cls(
            project_id=project_id,
            workspace_dir=workspace_dir,
            config=config,
        )
        return cls._instances[project_id]
    
    async def initialize(self) -> None:
        """Initialize memory system (concurrency-safe)."""
        if self._initialized:
            return
        
        async with self._init_lock:
            if self._initialized:
                return
            
            log.info("manager.init.start", {"project_id": self.project_id})
            
            try:
                await Storage.init()
                await Provider.init()
                
                provider = Provider.get(self.provider_id)
                if not provider:
                    raise ValueError(f"Provider {self.provider_id} not found")
                
                if not provider.supports_embeddings():
                    for fallback_id in ["openai", "google"]:
                        fallback = Provider.get(fallback_id)
                        if fallback and fallback.supports_embeddings():
                            log.warn("manager.provider.fallback", {
                                "from": self.provider_id,
                                "to": fallback_id,
                            })
                            self.provider_id = fallback_id
                            break
                    else:
                        raise ValueError("No provider with embeddings support available")
                
                self.search_engine = HybridSearch(
                    project_id=self.project_id,
                    provider_id=self.provider_id,
                    embedding_model=self.embedding_model,
                    config=self.config.query,
                )
                
                self.indexer = MemoryIndexer(
                    project_id=self.project_id,
                    workspace_dir=self.workspace_dir,
                    provider_id=self.provider_id,
                    embedding_model=self.embedding_model,
                    config=self.config,
                )
                
                self._initialized = True
                log.info("manager.init.complete", {
                    "project_id": self.project_id,
                    "provider": self.provider_id,
                    "model": self.embedding_model,
                })
            
            except Exception as e:
                log.error("manager.init.failed", {"error": str(e)})
                raise
    
    async def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        min_score: Optional[float] = None,
        sources: Optional[List[MemorySource]] = None,
    ) -> List[MemorySearchResult]:
        """
        Search memory
        
        Args:
            query: Search query (natural language)
            max_results: Maximum results (default from config)
            min_score: Minimum similarity score (default from config)
            sources: Sources to search (default from config)
            
        Returns:
            List of search results
        """
        if not self._initialized:
            await self.initialize()
        
        # Trigger sync if configured and dirty
        if self.config.sync.on_search and self._dirty:
            await self.sync(reason="search")
        
        # Execute search
        results = await self.search_engine.search(
            query=query,
            max_results=max_results or self.config.query.max_results,
            min_score=min_score or self.config.query.min_score,
            sources=sources or [MemorySource(s) for s in self.config.sources],
        )
        
        # Decorate citations if enabled
        if self.config.citations != "off":
            results = decorate_citations(results, mode=self.config.citations)
        
        return results
    
    async def read_file(
        self,
        rel_path: str,
        from_line: Optional[int] = None,
        lines: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Read memory file
        
        Uses Flocks' File.read() for consistency.
        
        Args:
            rel_path: Relative path from memory root
            from_line: Starting line number (optional)
            lines: Number of lines to read (optional)
            
        Returns:
            Dict with path and text
        """
        from flocks.config import Config
        
        data_dir = Config.get_data_path()
        memory_root = data_dir / "memory"
        file_path = _safe_resolve_memory_path(memory_root, rel_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {rel_path}")
        
        # Read file content
        content = file_path.read_text(encoding="utf-8")
        lines_list = content.splitlines()
        
        # Extract specified range
        if from_line is not None:
            start = max(0, from_line - 1)
            end = start + lines if lines else len(lines_list)
            lines_list = lines_list[start:end]
        
        return {
            "path": rel_path,
            "text": "\n".join(lines_list),
        }
    
    async def write_memory(
        self,
        content: str,
        path: Optional[str] = None,
        append: bool = True,
    ) -> str:
        """
        Write content to memory file
        
        Args:
            content: Content to write
            path: Target path (default: memory/YYYY-MM-DD.md)
            append: Whether to append (default True)
            
        Returns:
            Path where content was written
        """
        from datetime import datetime
        from flocks.config import Config
        
        if path is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
            path = f"{date_str}.md"
        
        data_dir = Config.get_data_path()
        memory_root = data_dir / "memory"
        file_path = _safe_resolve_memory_path(memory_root, path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with self._write_lock:
            if append:
                needs_separator = file_path.exists() and file_path.stat().st_size > 0
                with open(file_path, "a", encoding="utf-8") as f:
                    if needs_separator:
                        f.write("\n\n")
                    f.write(content)
            else:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
        
        # Mark as dirty for next sync
        self._dirty = True
        
        log.info("manager.write", {"path": path, "append": append, "length": len(content)})
        
        return path
    
    async def sync(
        self,
        reason: Optional[str] = None,
        force: bool = False,
        progress_callback: Optional[Callable[[MemorySyncProgress], None]] = None,
    ) -> Dict[str, Any]:
        """
        Sync memory files to index
        
        Args:
            reason: Reason for sync (for logging)
            force: Force re-index all files
            progress_callback: Optional progress callback
            
        Returns:
            Sync statistics
        """
        if not self._initialized:
            await self.initialize()
        
        async with self._sync_lock:
            log.info("manager.sync.start", {
                "project_id": self.project_id,
                "reason": reason,
                "force": force,
            })
            
            try:
                stats = await self.indexer.sync(
                    force=force,
                    progress_callback=progress_callback,
                )
                
                self._dirty = False
                
                log.info("manager.sync.complete", stats)
                return stats
            
            except Exception as e:
                log.error("manager.sync.failed", {"error": str(e)})
                raise
    
    def status(self) -> MemoryProviderStatus:
        """
        Get memory system status
        
        Returns:
            Status information
        """
        # TODO: Implement comprehensive status collection
        return MemoryProviderStatus(
            enabled=self.config.enabled,
            provider=self.provider_id,
            model=self.embedding_model,
            requested_provider=self.config.embedding.provider,
            workspace_dir=str(self.workspace_dir),
            sources=[MemorySource(s) for s in self.config.sources],
            dirty=self._dirty,
            cache={"enabled": self.config.cache.enabled},
            fts={"enabled": True},  # Always available
            vector={"enabled": True},  # Always available
        )
    
    async def close(self) -> None:
        """Close and cleanup manager"""
        self._initialized = False
        self.search_engine = None
        self.indexer = None
        self._instances.pop(self.project_id, None)
        log.info("manager.closed", {"project_id": self.project_id})
    
    def mark_dirty(self) -> None:
        """Mark as needing sync"""
        self._dirty = True
