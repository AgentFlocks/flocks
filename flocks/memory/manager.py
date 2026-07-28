"""
Memory Manager - Core orchestrator for memory system

Coordinates all memory system components: indexing, search, and sync.
"""

from typing import Optional, List, Dict, Any, Callable, Literal
from pathlib import Path
import asyncio
import os
import tempfile

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


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically replace a UTF-8 text file in its current directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


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
        self._requested_provider = config.embedding.provider
        self.provider_id: Optional[str] = config.embedding.provider
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
            old_provider = instance._requested_provider
            old_model = instance.embedding_model

            instance.config = config
            instance.workspace_dir = Path(workspace_dir)

            new_provider = config.embedding.provider
            new_model = config.embedding.model

            if new_provider != old_provider or new_model != old_model:
                instance._requested_provider = new_provider
                instance.provider_id = (
                    "openai" if new_provider == "auto" else new_provider
                )
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
                await Storage._ensure_init()
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
                
                self.indexer = MemoryIndexer(
                    project_id=self.project_id,
                    workspace_dir=self.workspace_dir,
                    provider_id=self.provider_id,
                    embedding_model=self.embedding_model,
                    config=self.config,
                )
                
                self._initialized = True
                if (
                    "session" in self.config.sources
                    and self.config.sync.sessions.enabled
                ):
                    await self._reconcile_sessions()
                log.info("manager.init.complete", {
                    "project_id": self.project_id,
                    "provider": self.provider_id or "fts",
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

        # Trigger Memory file sync if configured and dirty.
        if self.config.sync.on_search and self._dirty:
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
                await self._reconcile_sessions()
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

    async def _reconcile_sessions(self) -> None:
        """Synchronize historical transcript FTS rows for this project."""
        if not self.config.sync.sessions.enabled:
            return
        from flocks.storage.session_search import reconcile_session_index

        await reconcile_session_index(
            project_id=self.project_id,
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

    async def update_curated_memory(
        self,
        *,
        target: Literal["user", "memory"],
        action: Literal["add", "replace", "remove"],
        content: Optional[str] = None,
        old_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mutate USER.md or MEMORY.md using Hermes-style entry operations."""
        from flocks.config import Config

        filenames = {"user": "USER.md", "memory": "MEMORY.md"}
        if target not in filenames:
            raise ValueError(f"Unsupported memory target: {target}")
        if action not in {"add", "replace", "remove"}:
            raise ValueError(f"Unsupported memory action: {action}")

        clean_content = content.strip() if content else ""
        clean_old_text = old_text.strip() if old_text else ""
        if action in {"add", "replace"} and not clean_content:
            raise ValueError(f"content is required for action={action}")
        if action in {"replace", "remove"} and not clean_old_text:
            raise ValueError(f"old_text is required for action={action}")
        if action == "remove" and clean_content:
            raise ValueError("content is not allowed for action=remove")
        if action == "add" and clean_old_text:
            raise ValueError("old_text is not allowed for action=add")

        path = Config.get_data_path() / "memory" / filenames[target]
        async with self._write_lock:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            changed = True

            if action == "add":
                existing_entries = {
                    line.strip()
                    for line in current.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                }
                if clean_content in existing_entries:
                    changed = False
                    updated = current
                else:
                    prefix = current.rstrip()
                    updated = prefix + ("\n\n" if prefix else "") + clean_content + "\n"
            else:
                lines = current.splitlines()
                matches = [
                    index
                    for index, line in enumerate(lines)
                    if clean_old_text in line and not line.lstrip().startswith("#")
                ]
                if not matches:
                    raise ValueError(
                        f"old_text did not uniquely identify an entry in {filenames[target]}"
                    )
                if len(matches) > 1:
                    raise ValueError(
                        f"old_text matched multiple entries in {filenames[target]}"
                    )
                if action == "replace":
                    lines[matches[0]] = clean_content
                else:
                    lines.pop(matches[0])
                updated = "\n".join(lines).rstrip()
                if updated:
                    updated += "\n"
            if changed:
                _atomic_write_text(path, updated)
                self._dirty = True

        log.info(
            "manager.curated_memory.update",
            {
                "target": target,
                "action": action,
                "changed": changed,
            },
        )
        return {
            "target": target,
            "action": action,
            "path": filenames[target],
            "changed": changed,
            "content": updated,
        }
    
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
            provider=self.provider_id or "fts",
            model=self.embedding_model,
            requested_provider=self.config.embedding.provider,
            workspace_dir=str(self.workspace_dir),
            sources=[MemorySource(s) for s in self.config.sources],
            dirty=self._dirty,
            cache={"enabled": self.config.cache.enabled},
            fts={"enabled": True},  # Always available
            vector={"enabled": self.provider_id is not None},
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
