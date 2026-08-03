"""
Session Memory Integration

Bridges Session and MemoryManager for seamless memory access within sessions.
"""

from typing import Optional, List, Dict, Any, Set
from pathlib import Path
import asyncio

from flocks.memory import MemoryManager, MemoryConfig, MemorySearchResult, MemorySource
from flocks.config import Config
from flocks.utils.log import Log

log = Log.create(service="session.memory")


class SessionMemory:
    """
    Session-level memory management
    
    Provides convenient access to memory system within a session context.
    """
    
    _active_sessions: Set[str] = set()
    
    def __init__(
        self,
        session_id: str,
        project_id: str,
        workspace_dir: str,
        enabled: bool = False,
    ):
        """
        Initialize session memory
        
        Args:
            session_id: Session ID
            project_id: Project ID
            workspace_dir: Workspace directory
            enabled: Whether memory is enabled
        """
        self.session_id = session_id
        self.project_id = project_id
        self.workspace_dir = Path(workspace_dir)
        self.enabled = enabled
        self._manager: Optional[MemoryManager] = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
    
    async def initialize(self) -> bool:
        """Initialize memory system for session (concurrency-safe)."""
        if not self.enabled:
            log.debug("session.memory.disabled", {"session_id": self.session_id})
            return False
        
        if self._initialized:
            return True
        
        async with self._init_lock:
            if self._initialized:
                return True
            
            try:
                config = await Config.get()
                memory_config_dict = config.memory if hasattr(config, 'memory') and config.memory else None
                
                if not memory_config_dict:
                    log.info("session.memory.no_config", {"session_id": self.session_id})
                    memory_config = MemoryConfig(enabled=True)
                else:
                    if isinstance(memory_config_dict, dict):
                        memory_config = MemoryConfig(**memory_config_dict)
                    else:
                        memory_config = memory_config_dict
                
                self._manager = MemoryManager.get_instance(
                    project_id=self.project_id,
                    workspace_dir=str(self.workspace_dir),
                    config=memory_config,
                )
                self._active_sessions.add(self.session_id)
                
                await self._manager.initialize()
                
                self._initialized = True
                log.info("session.memory.initialized", {
                    "session_id": self.session_id,
                    "project_id": self.project_id,
                })
                
                return True
            
            except Exception as e:
                log.error("session.memory.init_failed", {
                    "session_id": self.session_id,
                    "error": str(e),
                })
                return False
    
    async def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        min_score: Optional[float] = None,
        sources: Optional[List[MemorySource]] = None,
    ) -> List[MemorySearchResult]:
        """
        Search memory within session context
        
        Args:
            query: Search query
            max_results: Maximum results
            min_score: Minimum score
            sources: Sources to search (default from config)
            
        Returns:
            Search results
        """
        if not self.enabled:
            return []
        
        if not self._initialized:
            if not await self.initialize():
                return []
        
        try:
            results = await self._manager.search(
                query=query,
                max_results=max_results,
                min_score=min_score,
                sources=sources,
            )
            
            log.debug("session.memory.search", {
                "session_id": self.session_id,
                "query": query[:50],
                "results": len(results),
            })
            
            return results
        
        except Exception as e:
            log.error("session.memory.search_failed", {
                "session_id": self.session_id,
                "error": str(e),
            })
            return []
    
    async def write(
        self,
        content: str,
        path: Optional[str] = None,
        append: bool = True,
    ) -> Optional[str]:
        """
        Write to memory within session context
        
        Args:
            content: Content to write
            path: Target path
            append: Append mode
            
        Returns:
            Path written to, or None if failed
        """
        if not self.enabled:
            return None
        
        if not self._initialized:
            if not await self.initialize():
                return None
        
        try:
            written_path = await self._manager.write_memory(
                content=content,
                path=path,
                append=append,
            )
            
            log.info("session.memory.write", {
                "session_id": self.session_id,
                "path": written_path,
                "length": len(content),
            })
            
            return written_path
        
        except Exception as e:
            log.error("session.memory.write_failed", {
                "session_id": self.session_id,
                "error": str(e),
            })
            return None
    
    async def sync(self, force: bool = False) -> Dict[str, Any]:
        """
        Sync memory index
        
        Args:
            force: Force full re-index
            
        Returns:
            Sync statistics
        """
        if not self.enabled:
            return {"error": "Memory not enabled"}
        
        if not self._initialized:
            if not await self.initialize():
                return {"error": "Memory initialization failed"}
        
        try:
            stats = await self._manager.sync(
                reason=f"session:{self.session_id}",
                force=force,
            )
            
            log.info("session.memory.sync", {
                "session_id": self.session_id,
                "stats": stats,
            })
            
            return stats
        
        except Exception as e:
            log.error("session.memory.sync_failed", {
                "session_id": self.session_id,
                "error": str(e),
            })
            return {"error": str(e)}
    
    def get_manager(self) -> Optional[MemoryManager]:
        """
        Get underlying memory manager
        
        Returns:
            MemoryManager instance or None
        """
        return self._manager
    
    async def close(self) -> None:
        """Close and cleanup session-level references without shutting down the shared manager."""
        async with self._init_lock:
            self._manager = None
            self._active_sessions.discard(self.session_id)
            self._initialized = False
        log.debug("session.memory.closed", {"session_id": self.session_id})
    
    @classmethod
    async def shutdown_all(cls) -> None:
        """Shut down all MemoryManager singletons (call only at process exit)."""
        managers = list(MemoryManager._instances.values())
        for manager in managers:
            await manager.close()
        cls._active_sessions.clear()
        log.info("session.memory.shutdown_all", {"count": len(managers)})
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear session tracking set (does not close managers)."""
        count = len(cls._active_sessions)
        cls._active_sessions.clear()
        log.info("session.memory.cache_cleared", {"count": count})
