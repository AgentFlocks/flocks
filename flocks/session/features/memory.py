"""
Session Memory Integration

Bridges Session and MemoryManager for seamless memory access within sessions.
"""

from typing import Optional, List, Dict, Any, Set, TYPE_CHECKING
from pathlib import Path
import asyncio

from flocks.memory import MemoryManager, MemorySearchResult, MemorySource
from flocks.memory.config import resolve_memory_config
from flocks.config import Config
from flocks.utils.log import Log

if TYPE_CHECKING:
    from flocks.auth.context import AuthUser
    from flocks.session.session import SessionInfo

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
                if getattr(config, "memory", None) is None:
                    log.info(
                        "session.memory.no_config",
                        {"session_id": self.session_id},
                    )
                memory_config = resolve_memory_config(config)
                
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

    async def _resolve_search_caller(
        self,
        session: "SessionInfo",
    ) -> Optional["AuthUser"]:
        """Resolve the authenticated caller, falling back to Session owner."""
        from flocks.auth.context import (
            API_TOKEN_SERVICE_USER_ID,
            AuthUser,
            get_current_auth_user,
        )

        caller = get_current_auth_user()
        if caller is not None:
            return caller

        owner_id = getattr(session, "owner_user_id", None)
        if not owner_id:
            return None
        if owner_id == API_TOKEN_SERVICE_USER_ID:
            return AuthUser(
                id=API_TOKEN_SERVICE_USER_ID,
                username=API_TOKEN_SERVICE_USER_ID,
                role="admin",
            )

        from flocks.auth.service import AuthService

        owner = await AuthService.get_user_by_id(owner_id)
        if owner is None:
            return None
        to_auth_user = getattr(owner, "to_auth_user", None)
        if callable(to_auth_user):
            return to_auth_user()
        return AuthUser(
            id=str(owner.id),
            username=str(owner.username),
            role=str(owner.role),
            status=str(getattr(owner, "status", "active")),
        )

    async def _search_access_context(
        self,
    ) -> tuple["SessionInfo", Optional["AuthUser"], Set[str]]:
        """Validate the current Session and resolve its effective caller."""
        from flocks.project.project import Project
        from flocks.session.policy import SessionPolicy
        from flocks.session.session import Session

        session = await Session.get_by_id_unfiltered(self.session_id)
        if session is None:
            raise PermissionError("Session not found")

        caller = await self._resolve_search_caller(session)
        shared_project_ids = Project.shared_project_ids()
        if caller is not None and not SessionPolicy.can_read(
            session,
            caller,
            shared_project_ids=shared_project_ids,
        ):
            raise PermissionError("Session access denied")
        return session, caller, shared_project_ids

    async def _readable_session_ids(
        self,
        current_session: "SessionInfo",
        caller: Optional["AuthUser"],
        shared_project_ids: Set[str],
    ) -> Set[str]:
        """Return readable, non-deleted Session IDs in the current project."""
        if caller is None:
            return {current_session.id}

        from flocks.session.policy import SessionPolicy
        from flocks.session.session import Session

        return {
            session.id
            for session in await Session.list_all_unfiltered()
            if session.project_id == self.project_id
            and session.status != "deleted"
            and SessionPolicy.can_read(
                session,
                caller,
                shared_project_ids=shared_project_ids,
            )
        }
    
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
            manager = self._manager
            if manager is None:
                raise RuntimeError("Memory manager is not initialized")
            current_session, caller, shared_project_ids = (
                await self._search_access_context()
            )
            selected_sources = (
                list(sources)
                if sources is not None
                else [
                    MemorySource(source)
                    for source in manager.config.sources
                ]
            )
            readable_session_ids = None
            if MemorySource.SESSION in selected_sources:
                readable_session_ids = await self._readable_session_ids(
                    current_session,
                    caller,
                    shared_project_ids,
                )
            results = await manager.search(
                query=query,
                max_results=max_results,
                min_score=min_score,
                sources=sources,
                readable_session_ids=readable_session_ids,
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
            raise
    
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
