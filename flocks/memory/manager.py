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


class _MemoryIndexCoordinator:
    """Own the process-wide Memory file indexer for one SQLite database."""

    def __init__(self) -> None:
        self.indexer: Optional[MemoryIndexer] = None
        self.signature: Optional[tuple[Any, ...]] = None
        self.initialized = False
        self.sync_lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()

    def configure(
        self,
        *,
        workspace_dir: Path,
        provider_id: Optional[str],
        embedding_model: str,
        config: MemoryConfig,
    ) -> MemoryIndexer:
        """Create or reuse the one global file indexer."""
        signature = (
            provider_id,
            embedding_model,
            config.chunking.model_dump_json(),
            config.batch.model_dump_json(),
            config.cache.model_dump_json(),
            tuple(config.extra_paths),
        )
        if self.indexer is not None and self.signature == signature:
            return self.indexer

        self.indexer = MemoryIndexer(
            project_id="global",
            workspace_dir=workspace_dir,
            provider_id=provider_id,
            embedding_model=embedding_model,
            config=config,
        )
        self.signature = signature
        self.initialized = False
        return self.indexer

    async def _sync_locked(
        self,
        *,
        force: bool,
        progress_callback: Optional[Callable[[MemorySyncProgress], None]],
    ) -> Dict[str, Any]:
        """Reconcile while the coordinator sync lock is held."""
        if self.indexer is None:
            raise RuntimeError("Memory file indexer is not configured")
        async with self.write_lock:
            stats = await self.indexer.sync(
                force=force,
                progress_callback=progress_callback,
            )
        self.initialized = True
        return stats

    async def sync_on_start(self) -> Optional[Dict[str, Any]]:
        """Run the initial reconciliation once for a shared indexer."""
        async with self.sync_lock:
            if self.initialized:
                return None
            return await self._sync_locked(
                force=False,
                progress_callback=None,
            )

    async def sync(
        self,
        *,
        force: bool = False,
        progress_callback: Optional[
            Callable[[MemorySyncProgress], None]
        ] = None,
    ) -> Dict[str, Any]:
        """Serialize global Memory index reconciliation."""
        async with self.sync_lock:
            return await self._sync_locked(
                force=force,
                progress_callback=progress_callback,
            )


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
    _index_coordinators: Dict[str, _MemoryIndexCoordinator] = {}
    
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
        self._embedding_enabled = config.search.embedding.enabled
        self._requested_provider = config.search.embedding.provider
        self.provider_id: Optional[str] = (
            config.search.embedding.provider
            if self._embedding_enabled
            else None
        )
        if self._embedding_enabled and self.provider_id == "auto":
            self.provider_id = "openai"  # Default fallback
        
        self.embedding_model = config.search.embedding.model
        
        # Components (lazy initialization)
        self.search_engine: Optional[HybridSearch] = None
        self.indexer: Optional[MemoryIndexer] = None
        
        # State
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._index_coordinator: Optional[_MemoryIndexCoordinator] = None

    @classmethod
    def _coordinator_for_active_db(cls) -> _MemoryIndexCoordinator:
        """Return the global Memory index owner for the active database."""
        key = str(Storage.get_db_path().resolve())
        coordinator = cls._index_coordinators.get(key)
        if coordinator is None:
            coordinator = _MemoryIndexCoordinator()
            cls._index_coordinators[key] = coordinator
        return coordinator

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
            old_enabled = instance._embedding_enabled
            old_provider = instance._requested_provider
            old_model = instance.embedding_model

            instance.config = config
            instance.workspace_dir = Path(workspace_dir)

            new_enabled = config.search.embedding.enabled
            new_provider = config.search.embedding.provider
            new_model = config.search.embedding.model

            if (
                new_enabled != old_enabled
                or new_provider != old_provider
                or new_model != old_model
            ):
                instance._embedding_enabled = new_enabled
                instance._requested_provider = new_provider
                instance.provider_id = (
                    ("openai" if new_provider == "auto" else new_provider)
                    if new_enabled
                    else None
                )
                instance.embedding_model = new_model
                instance._initialized = False
                instance.search_engine = None
                instance.indexer = None
                log.info("manager.config_changed", {
                    "project_id": project_id,
                    "old_enabled": old_enabled,
                    "new_enabled": new_enabled,
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
                await Storage._ensure_init()
                
                if self._embedding_enabled:
                    await Provider.init()
                    provider = Provider.get(self.provider_id) if self.provider_id else None
                    if not provider or not provider.supports_embeddings():
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
                            log.info(
                                "manager.embedding.unavailable",
                                {"project_id": self.project_id},
                            )
                            self.provider_id = None
                
                self.search_engine = HybridSearch(
                    project_id=self.project_id,
                    provider_id=self.provider_id,
                    embedding_model=self.embedding_model,
                    config=self.config.query,
                )
                
                coordinator = self._coordinator_for_active_db()
                self._index_coordinator = coordinator
                previous_indexer = coordinator.indexer
                self.indexer = coordinator.configure(
                    workspace_dir=self.workspace_dir,
                    provider_id=self.provider_id,
                    embedding_model=self.embedding_model,
                    config=self.config,
                )
                if self.indexer is not previous_indexer:
                    for manager in self._instances.values():
                        if manager._index_coordinator is coordinator:
                            manager.indexer = self.indexer
                
                self._initialized = True
                if self.config.sync.on_session_start:
                    await coordinator.sync_on_start()
                if (
                    "session" in self.config.sources
                    and self.config.sync.sessions.enabled
                ):
                    if Storage.session_search_available():
                        await self._ensure_session_index_ready()
                    else:
                        log.warn(
                            "manager.session_search.disabled",
                            {
                                "project_id": self.project_id,
                                "reason": (
                                    "SQLite runtime does not support FTS5"
                                ),
                            },
                        )
                log.info("manager.init.complete", {
                    "project_id": self.project_id,
                    "provider": self.provider_id or "fts",
                    "model": self.embedding_model,
                })
            
            except Exception as e:
                self._initialized = False
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
        
        selected_sources = (
            list(sources)
            if sources is not None
            else [MemorySource(source) for source in self.config.sources]
        )
        limit = (
            max_results
            if max_results is not None
            else self.config.query.max_results
        )
        threshold = (
            min_score
            if min_score is not None
            else self.config.query.min_score
        )

        if sources is not None and MemorySource.SESSION in selected_sources:
            await self._persist_session_source()

        # Filesystem tools and external editors can update Memory without going
        # through MemoryManager. Reconcile on every search and let the indexer
        # skip files whose content hash is unchanged.
        if self.config.sync.on_search:
            await self.sync(reason="search")

        results: List[MemorySearchResult] = []
        errors: List[Exception] = []
        successful_sources = 0

        if MemorySource.MEMORY in selected_sources:
            try:
                results.extend(
                    await self.search_engine.search(
                        query=query,
                        max_results=limit,
                        min_score=threshold,
                        sources=[MemorySource.MEMORY],
                    )
                )
                successful_sources += 1
            except Exception as exc:
                errors.append(exc)
                log.warn("manager.search.memory_failed", {"error": str(exc)})

        if MemorySource.SESSION in selected_sources:
            try:
                await self._ensure_session_index_ready()
                from flocks.storage.session_search import session_fts_search

                raw_results = await session_fts_search(
                    db_path=Storage.get_db_path(),
                    project_id=self.project_id,
                    query=query,
                    max_results=limit
                    * self.config.query.hybrid.candidate_multiplier,
                )
                results.extend(
                    MemorySearchResult(
                        path=result["path"],
                        start_line=result["start_line"],
                        end_line=result["end_line"],
                        score=result["score"],
                        snippet=result["text"][:700],
                        source=MemorySource.SESSION,
                        citation=result["citation"],
                    )
                    for result in raw_results
                    if result["score"] >= threshold
                )
                successful_sources += 1
            except Exception as exc:
                errors.append(exc)
                log.warn("manager.search.session_failed", {"error": str(exc)})

        if successful_sources == 0 and errors:
            from flocks.storage.session_search import (
                SessionSearchUnavailableError,
            )

            if len(errors) == 1 and isinstance(
                errors[0],
                SessionSearchUnavailableError,
            ):
                raise errors[0]
            raise RuntimeError(
                "All requested memory sources failed: "
                + "; ".join(str(error) for error in errors)
            )

        deduplicated: Dict[str, MemorySearchResult] = {}
        for result in sorted(results, key=lambda item: item.score, reverse=True):
            key = f"{result.source.value}:{result.path}"
            deduplicated.setdefault(key, result)
        results = list(deduplicated.values())[:limit]
        
        # Decorate citations if enabled
        if self.config.citations != "off":
            results = decorate_citations(results, mode=self.config.citations)
        
        return results

    async def _persist_session_source(self) -> None:
        """Persist explicit Session search opt-in without touching other config."""
        if "session" in self.config.sources:
            return
        from flocks.config.config_writer import ConfigWriter

        await asyncio.to_thread(ConfigWriter.enable_memory_source, "session")
        self.config.sources.append("session")

    async def _ensure_session_index_ready(self) -> None:
        """Run the one-time historical Session backfill when required."""
        if not self.config.sync.sessions.enabled:
            return
        from flocks.storage.session_search import ensure_session_index_ready

        await ensure_session_index_ready(
            batch_size=self.config.sync.sessions.delta_messages,
        )
    
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
        
        coordinator = self._index_coordinator or self._coordinator_for_active_db()
        async with coordinator.write_lock:
            if append:
                needs_separator = file_path.exists() and file_path.stat().st_size > 0
                with open(file_path, "a", encoding="utf-8") as f:
                    if needs_separator:
                        f.write("\n\n")
                    f.write(content)
            else:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
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
        
        coordinator = self._index_coordinator or self._coordinator_for_active_db()
        log.info("manager.sync.start", {
            "project_id": self.project_id,
            "reason": reason,
            "force": force,
        })

        try:
            stats = await coordinator.sync(
                force=force,
                progress_callback=progress_callback,
            )

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
            enabled=True,
            provider=self.provider_id or "fts",
            model=self.embedding_model,
            requested_provider=self.config.search.embedding.provider,
            workspace_dir=str(self.workspace_dir),
            sources=[MemorySource(s) for s in self.config.sources],
            cache={"enabled": self.config.cache.enabled},
            fts={"enabled": True},  # Always available
            vector={"enabled": self.provider_id is not None},
        )
    
    async def close(self) -> None:
        """Close and cleanup manager"""
        coordinator = self._index_coordinator
        self._initialized = False
        self.search_engine = None
        self.indexer = None
        self._index_coordinator = None
        self._instances.pop(self.project_id, None)
        if coordinator is not None and not any(
            manager._index_coordinator is coordinator
            for manager in self._instances.values()
        ):
            for key, candidate in list(self._index_coordinators.items()):
                if candidate is coordinator:
                    self._index_coordinators.pop(key, None)
        log.info("manager.closed", {"project_id": self.project_id})
