"""
Session management

Handles session lifecycle, metadata, and state.
Based on Flocks' ported src/session/index.ts
"""

import asyncio
import contextvars
import re
import weakref
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, List, Dict, Any, Optional, TypeVar
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

from flocks.auth.context import (
    API_TOKEN_SERVICE_USER_ID,
    get_current_auth_user,
    reset_current_auth_user,
    set_current_auth_user,
)
from flocks.storage.storage import Storage
from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.session.message import Message, MessageInfo, MessageRole, AssistantMessageInfo

# Sentinel for explicitly setting a field to None via Session.update()
_UNSET = object()

log = Log.create(service="session")


# Title prefix patterns for default title detection
PARENT_TITLE_PREFIX = "New session - "
CHILD_TITLE_PREFIX = "Child session - "
MODEL_AUTO_SESSION_CATEGORIES = frozenset({"user", "entity-config", "workflow"})
_WriteResult = TypeVar("_WriteResult")


class SessionNotFoundError(ValueError):
    """Raised when a lifecycle-aware write cannot find its session."""


class SessionInactiveError(RuntimeError):
    """Raised when a lifecycle-aware write targets a non-active session."""


def is_model_auto_session_category(category: Optional[str]) -> bool:
    """Return whether a session category may use WebUI Auto mode."""
    return category in MODEL_AUTO_SESSION_CATEGORIES


class SessionChangeStats(BaseModel):
    """Session file change statistics (additions, deletions, files)"""
    additions: int = Field(0, description="Total lines added")
    deletions: int = Field(0, description="Total lines deleted")
    files: int = Field(0, description="Number of files changed")


# Backwards-compatible alias
SessionSummary = SessionChangeStats


class SessionRevert(BaseModel):
    """Session revert state"""
    model_config = ConfigDict(populate_by_name=True)
    
    message_id: str = Field(..., alias="messageID", description="Message ID to revert to")
    part_id: Optional[str] = Field(None, alias="partID", description="Part ID for partial revert")
    snapshot: Optional[str] = Field(None, description="Snapshot ID")
    diff: Optional[str] = Field(None, description="Diff content")


class SessionTime(BaseModel):
    """Session timestamps"""
    created: int = Field(default_factory=lambda: int(datetime.now().timestamp() * 1000))
    updated: int = Field(default_factory=lambda: int(datetime.now().timestamp() * 1000))
    compacting: Optional[int] = Field(None, description="Compaction start time")
    archived: Optional[int] = Field(None, description="Archive time")


class PermissionRule(BaseModel):
    """Permission rule for session"""
    permission: str = Field(..., description="Permission name (tool name)")
    action: str = Field("allow", description="Action: allow or deny")
    pattern: str = Field("*", description="Pattern to match")


class SessionInfo(BaseModel):
    """
    Session information
    
    Matches TypeScript Session.Info structure from index.ts
    """
    model_config = ConfigDict(populate_by_name=True)
    
    id: str = Field(default_factory=lambda: Identifier.descending("session"))
    slug: str = Field(default_factory=lambda: Identifier.ascending("slug")[:8])
    project_id: str = Field(..., alias="projectID")
    directory: str
    title: str = Field(default_factory=lambda: f"{PARENT_TITLE_PREFIX}{datetime.now().isoformat()}")
    version: str = Field("1.0.0", description="Session version")
    
    # Agent and model
    agent: Optional[str] = Field("hephaestus", description="Agent type: hephaestus, build, plan, rex, …")
    model: Optional[str] = Field(None, description="Model ID")
    provider: Optional[str] = Field(None, description="Provider ID")
    model_pinned: bool = Field(
        False,
        description=(
            "Whether provider/model were explicitly locked for this session. "
            "Unpinned sessions follow the normal default-model resolution chain."
        ),
    )
    model_auto: bool = Field(
        False,
        description=(
            "Whether WebUI Auto runtime failover was explicitly selected for "
            "this session. Other session entry points ignore this flag."
        ),
    )
    
    # Session hierarchy
    parent_id: Optional[str] = Field(None, alias="parentID", description="Parent session for branching")

    # Local account ownership (single-admin mode: session is private to its owner;
    # admins can see all sessions; there is no cross-user sharing).
    owner_user_id: Optional[str] = Field(None, alias="ownerUserID", description="Owner local user id")
    owner_username: Optional[str] = Field(None, alias="ownerUsername", description="Owner local username")

    # File change summary
    summary: Optional[SessionChangeStats] = Field(None, description="File change summary")

    # Revert state
    revert: Optional[SessionRevert] = Field(None, description="Revert state")
    
    # Permissions
    permission: Optional[List[PermissionRule]] = Field(None, description="Permission rules")
    
    # Timestamps
    time: SessionTime = Field(default_factory=SessionTime)
    
    # Memory system
    memory_enabled: bool = Field(True, description="Enable memory system for this session")

    # Session category: "user" for human-initiated conversations, "task" for task-triggered sessions
    category: str = Field("user", description="Session category: user or task")

    # Legacy fields for backwards compatibility
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: str = Field("active", description="Session status: active, archived, deleted")


class Session:
    """
    Session management namespace
    
    Mirrors original Flocks Session namespace from index.ts
    """
    
    # Per-task current session (concurrent-safe via contextvars)
    _current_var: contextvars.ContextVar[Optional[SessionInfo]] = contextvars.ContextVar(
        "session_current", default=None,
    )
    # Secondary index: session_id → storage key for O(1) lookup
    _id_index: Dict[str, str] = {}
    # Hot-path cache for repeatedly listing sessions in the UI.
    _all_sessions_cache: Optional[List[SessionInfo]] = None
    # Serializes tree topology changes only. Ordinary writes use a keyed lock,
    # so unrelated sessions no longer block each other.
    _tree_lock = asyncio.Lock()
    _lifecycle_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
        weakref.WeakValueDictionary()
    )
    _lifecycle_transition_ids: set[str] = set()
    _lifecycle_generations: Dict[str, int] = {}
    _active_operation_counts: Dict[str, int] = {}

    @staticmethod
    def _sort_sessions(sessions: List[SessionInfo]) -> List[SessionInfo]:
        """Return sessions sorted by most recently updated."""
        return sorted(sessions, key=lambda s: s.time.updated, reverse=True)

    @staticmethod
    def _is_accessible_to_current_user(session: SessionInfo) -> bool:
        """Delegate to the unified SessionPolicy (kept for backward compatibility)."""
        from flocks.session.policy import SessionPolicy

        return SessionPolicy.can_read(session)

    @staticmethod
    def _is_owned_by_auth_user(session: SessionInfo, auth_user) -> bool:
        """Delegate to the unified SessionPolicy (kept for backward compatibility)."""
        from flocks.session.policy import SessionPolicy

        return SessionPolicy.is_owner(session, auth_user)

    @classmethod
    def _sync_list_cache(cls, session: SessionInfo) -> None:
        """Keep the in-memory list cache aligned with session mutations."""
        from flocks.project.project import Project

        Project.invalidate_session_stats()
        if cls._all_sessions_cache is None:
            return

        remaining = [cached for cached in cls._all_sessions_cache if cached.id != session.id]
        if session.status != "deleted":
            remaining.append(session)
        cls._all_sessions_cache = cls._sort_sessions(remaining)

    @classmethod
    def _remove_from_list_cache(cls, session_id: str) -> None:
        """Remove a permanently deleted session from derived caches."""
        if cls._all_sessions_cache is not None:
            cls._all_sessions_cache = [
                session
                for session in cls._all_sessions_cache
                if session.id != session_id
            ]

    @classmethod
    def invalidate_cache(cls) -> None:
        """Clear in-memory indexes when the underlying storage changes."""
        cls._id_index.clear()
        cls._all_sessions_cache = None

    @classmethod
    def is_lifecycle_transitioning(cls, session_id: str) -> bool:
        """Return whether archive/delete currently owns this session lifecycle."""
        return session_id in cls._lifecycle_transition_ids

    @classmethod
    def lifecycle_generation(cls, session_id: str) -> int:
        """Return a token that changes whenever a lifecycle transition begins."""
        return cls._lifecycle_generations.get(session_id, 0)

    @classmethod
    def lifecycle_lock(cls, session_id: str) -> asyncio.Lock:
        """Return the lock that linearizes durable writes for one session."""

        lock = cls._lifecycle_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            cls._lifecycle_locks[session_id] = lock
        return lock

    @classmethod
    def has_active_operations(cls, session_id: str) -> bool:
        """Return whether a synchronous operation currently owns this session."""

        return cls._active_operation_counts.get(session_id, 0) > 0

    @classmethod
    @asynccontextmanager
    async def active_operation(cls, session_id: str) -> AsyncIterator[SessionInfo]:
        """Prevent lifecycle transitions while a synchronous operation is running."""

        async with cls.lifecycle_lock(session_id):
            session = await cls.get_by_id_unfiltered(session_id)
            if session is None:
                raise SessionNotFoundError(f"Session {session_id} not found")
            if session.status != "active" or cls.is_lifecycle_transitioning(session_id):
                raise SessionInactiveError(f"Session {session_id} is not active")
            cls._active_operation_counts[session_id] = (
                cls._active_operation_counts.get(session_id, 0) + 1
            )

        try:
            yield session
        finally:
            remaining = cls._active_operation_counts.get(session_id, 0) - 1
            if remaining > 0:
                cls._active_operation_counts[session_id] = remaining
            else:
                cls._active_operation_counts.pop(session_id, None)

    @classmethod
    async def get_by_id_unfiltered(cls, session_id: str) -> Optional[SessionInfo]:
        """Get a session by ID without applying the ambient auth policy."""

        token = set_current_auth_user(None)
        try:
            return await cls.get_by_id(session_id)
        finally:
            reset_current_auth_user(token)

    @classmethod
    async def run_active_write(
        cls,
        session_id: str,
        operation: Callable[[], Awaitable[_WriteResult]],
        *,
        expected_generation: Optional[int] = None,
    ) -> _WriteResult:
        """Run one durable write atomically with lifecycle transitions."""

        async with cls.lifecycle_lock(session_id):
            session = await cls.get_by_id_unfiltered(session_id)
            if session is None:
                raise SessionNotFoundError(f"Session {session_id} not found")
            generation_changed = (
                expected_generation is not None
                and cls.lifecycle_generation(session_id) != expected_generation
            )
            if (
                session.status != "active"
                or cls.is_lifecycle_transitioning(session_id)
                or generation_changed
            ):
                raise SessionInactiveError(f"Session {session_id} is not active")
            return await operation()

    @classmethod
    async def _clear_project_move_metadata_locked(cls, session_id: str) -> bool:
        """Remove a stale replay boundary while the session write lock is held."""

        session = await cls.get_by_id_unfiltered(session_id)
        if session is None:
            raise SessionNotFoundError(f"Session {session_id} not found")
        metadata = dict(getattr(session, "metadata", {}) or {})
        if "projectMove" not in metadata:
            return False

        metadata.pop("projectMove")
        updated_session = session.model_copy(update={"metadata": metadata})
        storage_key = f"session:{session.project_id}:{session.id}"
        await Storage.set(storage_key, updated_session, "session")
        cls._id_index[session.id] = storage_key
        cls._sync_list_cache(updated_session)
        return True

    @staticmethod
    def has_pinned_model(session: Optional[SessionInfo]) -> bool:
        """Return whether a session has an explicit model lock."""
        return bool(
            session
            and getattr(session, "model_pinned", False)
            and getattr(session, "provider", None)
            and getattr(session, "model", None)
        )

    @staticmethod
    def explicit_model_updates(provider_id: str, model_id: str) -> Dict[str, Any]:
        """Build update kwargs for explicitly pinning a session model."""
        return {
            "provider": provider_id,
            "model": model_id,
            "model_pinned": True,
            "model_auto": False,
        }

    @classmethod
    def inherited_model_kwargs(cls, session: Optional[SessionInfo]) -> Dict[str, Any]:
        """Return model preference kwargs that should propagate to a new session."""
        if (
            session
            and is_model_auto_session_category(getattr(session, "category", "user"))
            and getattr(session, "model_auto", False)
        ):
            return {
                "model_auto": True,
                "model_pinned": False,
            }
        if not cls.has_pinned_model(session):
            return {}
        return {
            "provider": session.provider,
            "model": session.model,
            "model_pinned": True,
        }
    
    @classmethod
    def is_default_title(cls, title: str) -> bool:
        """
        Check if title is a default auto-generated title
        
        Args:
            title: Title to check
            
        Returns:
            True if default title
        """
        pattern = rf"^({re.escape(PARENT_TITLE_PREFIX)}|{re.escape(CHILD_TITLE_PREFIX)})\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}}:\d{{2}}"
        return bool(re.match(pattern, title))
    
    @classmethod
    def _create_default_title(cls, is_child: bool = False) -> str:
        """Create default title with timestamp"""
        prefix = CHILD_TITLE_PREFIX if is_child else PARENT_TITLE_PREFIX
        return f"{prefix}{datetime.now().isoformat()}"
    
    @classmethod
    async def create(
        cls,
        project_id: str,
        directory: str,
        title: Optional[str] = None,
        parent_id: Optional[str] = None,
        permission: Optional[List[PermissionRule]] = None,
        **kwargs
    ) -> SessionInfo:
        """
        Create a new session
        
        Args:
            project_id: Project ID
            directory: Working directory
            title: Session title (auto-generated if not provided)
            parent_id: Parent session ID for child sessions
            permission: Permission rules
            **kwargs: Additional fields
            
        Returns:
            Session info
        """
        is_child = parent_id is not None

        # Ensure sessions default to the configured primary agent (e.g., rex)
        if not kwargs.get("agent"):
            try:
                from flocks.agent.registry import Agent
                kwargs["agent"] = await Agent.default_agent()
            except Exception as e:
                log.warn("session.default_agent.error", {"error": str(e)})
        
        # Default memory_enabled from config if not explicitly set
        if "memory_enabled" not in kwargs:
            kwargs["memory_enabled"] = True

        # Bind root ownership here; children inherit ownership from their parent below.
        if parent_id is None and (
            "owner_user_id" not in kwargs or "owner_username" not in kwargs
        ):
            current_user = get_current_auth_user()
            if current_user:
                kwargs.setdefault("owner_user_id", current_user.id)
                kwargs.setdefault("owner_username", current_user.username)
            else:
                kwargs.setdefault("owner_user_id", API_TOKEN_SERVICE_USER_ID)
                kwargs.setdefault("owner_username", API_TOKEN_SERVICE_USER_ID)
        
        async def persist(parent: Optional[SessionInfo] = None) -> SessionInfo:
            if parent_id is not None:
                if parent is None:
                    raise ValueError(f"Parent session {parent_id} not found")
                child_owner_id = kwargs.get("owner_user_id")
                child_owner_username = kwargs.get("owner_username")
                if parent.owner_user_id and child_owner_id and parent.owner_user_id != child_owner_id:
                    raise ValueError("Child session owner must match its parent")
                if parent.owner_username and child_owner_username and parent.owner_username != child_owner_username:
                    raise ValueError("Child session owner must match its parent")
                if child_owner_id is None:
                    kwargs["owner_user_id"] = parent.owner_user_id
                if child_owner_username is None:
                    kwargs["owner_username"] = parent.owner_username

            created = SessionInfo(
                project_id=project_id,
                directory=directory,
                title=title or cls._create_default_title(is_child),
                parent_id=parent_id,
                permission=permission,
                **kwargs
            )

            # The parent status check and child row creation share the archive
            # lock, so an archive cannot commit between them in this process.
            storage_key = f"session:{project_id}:{created.id}"
            await Storage.set(storage_key, created, "session")
            cls._id_index[created.id] = storage_key
            cls._sync_list_cache(created)
            return created

        from flocks.project.project import Project, ProjectDeletionError

        # Every creation path participates in the project lifecycle claim. If
        # deletion wins the race, a waiting creator observes the removed marker
        # and cannot recreate an orphan task under the hidden project.
        async with Project.lifecycle_guard(project_id):
            if project_id.startswith("prj_") and Project.is_removed(project_id):
                raise ProjectDeletionError(f"Project {project_id} is no longer available")

            if parent_id is None:
                session = await persist()
            else:
                async with cls._tree_lock:
                    async with cls.lifecycle_lock(parent_id):
                        parent = await Storage.get(
                            f"session:{project_id}:{parent_id}",
                            SessionInfo,
                        )
                        if parent is None or parent.status == "deleted":
                            raise ValueError(f"Parent session {parent_id} not found")
                        if cls.is_lifecycle_transitioning(parent_id):
                            raise ValueError(
                                f"Parent session {parent_id} is changing lifecycle state"
                            )
                        if parent.status != "active":
                            raise ValueError(f"Parent session {parent_id} is not active")
                        session = await persist(parent)

        try:
            from flocks.agent.registry import Agent
            from flocks.tool.catalog import get_always_load_tool_names
            from flocks.session.callable_state import initialize_session_callable_tools

            agent_info = await Agent.get(session.agent or "")
            declared_tools = getattr(agent_info, "tools", None) if agent_info is not None else None
            base_tools = list(declared_tools) if isinstance(declared_tools, (list, tuple, set)) else []
            await initialize_session_callable_tools(
                session.id,
                base_tools,
                always_load_tool_names=get_always_load_tool_names(),
            )
        except Exception as e:
            log.warn("session.callable_tools.init_error", {"id": session.id, "error": str(e)})
        
        log.info("session.created", {
            "id": session.id,
            "project_id": project_id,
            "title": session.title,
            "parent_id": parent_id,
        })

        # Flocks compatibility: track main/sub sessions and publish event
        try:
            from flocks.session.core.session_state import set_main_session, add_subagent_session
            if parent_id:
                add_subagent_session(session.id)
            else:
                set_main_session(session.id)
        except Exception as e:
            log.warn("session.state.error", {"error": str(e)})

        try:
            from flocks.bus.bus import Bus
            from flocks.bus.events import SessionCreated
            await Bus.publish(SessionCreated, {
                "info": {
                    "id": session.id,
                    "title": session.title,
                    "parentID": parent_id,
                    "projectID": project_id,
                }
            })
        except Exception as e:
            log.warn("session.created.event_error", {"error": str(e)})
        
        return session
    
    @classmethod
    async def get(cls, project_id: str, session_id: str) -> Optional[SessionInfo]:
        """
        Get a session
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            Session info or None (returns None if deleted)
        """
        try:
            session = await Storage.get(f"session:{project_id}:{session_id}", SessionInfo)
            # Don't return deleted sessions
            if session and session.status == "deleted":
                return None
            if session and not cls._is_accessible_to_current_user(session):
                return None
            return session
        except Exception as e:
            log.warn("session.get.error", {"error": str(e), "id": session_id})
            return None
    
    @classmethod
    async def get_by_id(cls, session_id: str) -> Optional[SessionInfo]:
        """
        Get a session by ID only (searches across all projects)
        
        TypeScript compatible - doesn't require project_id.
        Uses an in-memory index for O(1) lookup when available,
        falling back to a full key scan on cache miss.
        
        Args:
            session_id: Session ID
            
        Returns:
            Session info or None
        """
        try:
            if cls._all_sessions_cache is not None:
                cached = next((s for s in cls._all_sessions_cache if s.id == session_id), None)
                if cached:
                    if not cls._is_accessible_to_current_user(cached):
                        return None
                    return cached

            # Fast path: check in-memory index
            cached_key = cls._id_index.get(session_id)
            if cached_key:
                session = await Storage.get(cached_key, SessionInfo)
                if session and session.status != "deleted":
                    if not cls._is_accessible_to_current_user(session):
                        return None
                    return session
                # Index is stale — remove and fall through
                cls._id_index.pop(session_id, None)
            
            # Slow path: scan all session keys
            keys = await Storage.list_keys(prefix="session:")
            
            for key in keys:
                if key.endswith(f":{session_id}"):
                    try:
                        session = await Storage.get(key, SessionInfo)
                        if session and session.status != "deleted":
                            if not cls._is_accessible_to_current_user(session):
                                return None
                            cls._id_index[session_id] = key
                            return session
                    except Exception as _e:
                        log.debug("session.get_by_id.parse_failed", {"key": key, "error": str(_e)})
                        continue
            
            return None
        except Exception as e:
            log.warn("session.get_by_id.error", {"error": str(e), "id": session_id})
            return None
    
    @classmethod
    async def list(cls, project_id: str) -> List[SessionInfo]:
        """
        List sessions for a project
        
        Args:
            project_id: Project ID
            
        Returns:
            List of sessions
        """
        try:
            if cls._all_sessions_cache is not None:
                return [s for s in cls._all_sessions_cache if s.project_id == project_id and cls._is_accessible_to_current_user(s)]

            entries = await Storage.list_entries(prefix=f"session:{project_id}:", model=SessionInfo)
            sessions = []

            for key, session in entries:
                try:
                    if session.status != "deleted":
                        if cls._is_accessible_to_current_user(session):
                            sessions.append(session)
                        cls._id_index[session.id] = key
                except Exception as e:
                    log.warn("session.parse.error", {"key": key, "error": str(e)})

            return cls._sort_sessions(sessions)
        except Exception as e:
            log.error("session.list.error", {"error": str(e)})
            return []
    
    @classmethod
    async def list_all(cls) -> List[SessionInfo]:
        """
        List all sessions across all projects
        
        TypeScript compatible - doesn't require project_id
        
        Returns:
            List of all sessions
        """
        try:
            if cls._all_sessions_cache is not None:
                return [s for s in cls._all_sessions_cache if cls._is_accessible_to_current_user(s)]

            entries = await Storage.list_entries(prefix="session:", model=SessionInfo)
            sessions = []

            for key, session in entries:
                try:
                    if session.status != "deleted":
                        sessions.append(session)
                        cls._id_index[session.id] = key
                except Exception as e:
                    log.warn("session.parse.error", {"key": key, "error": str(e)})

            cls._all_sessions_cache = cls._sort_sessions(sessions)
            return [s for s in cls._all_sessions_cache if cls._is_accessible_to_current_user(s)]
        except Exception as e:
            log.error("session.list_all.error", {"error": str(e)})
            return []

    @classmethod
    async def list_all_unfiltered(cls) -> List[SessionInfo]:
        """List all sessions for callers that apply an explicit access policy."""

        token = set_current_auth_user(None)
        try:
            return await cls.list_all()
        finally:
            reset_current_auth_user(token)
    
    @classmethod
    async def update(
        cls,
        project_id: str,
        session_id: str,
        *,
        allow_inactive: bool = False,
        **updates
    ) -> Optional[SessionInfo]:
        """
        Update a session
        
        Args:
            project_id: Project ID
            session_id: Session ID
            **updates: Fields to update
            
        Returns:
            Updated session info or None
        """
        async with cls.lifecycle_lock(session_id):
            if cls.is_lifecycle_transitioning(session_id):
                return None
            session = await cls.get(project_id, session_id)
            if not session or (session.status != "active" and not allow_inactive):
                return None

            alias_map = {
                "project_id": "projectID",
                "parent_id": "parentID",
                "owner_user_id": "ownerUserID",
                "owner_username": "ownerUsername",
            }

            # Use ``_UNSET`` to explicitly clear a field; ordinary ``None``
            # remains a no-op for backward compatibility.
            update_data = session.model_dump(by_alias=True)
            for key, value in updates.items():
                if value is _UNSET:
                    alias_key = alias_map.get(key, key)
                    if alias_key in update_data:
                        update_data[alias_key] = None
                    elif key in update_data:
                        update_data[key] = None
                    continue

                if value is not None:
                    if key == "summary" and isinstance(value, dict):
                        if update_data.get("summary"):
                            update_data["summary"].update(value)
                        else:
                            update_data["summary"] = value
                    elif key == "revert" and isinstance(value, dict):
                        update_data["revert"] = value
                    else:
                        alias_key = alias_map.get(key, key)
                        if alias_key in update_data:
                            update_data[alias_key] = value
                        elif key in update_data:
                            update_data[key] = value

            if "time" not in update_data:
                update_data["time"] = {}
            update_data["time"]["updated"] = int(datetime.now().timestamp() * 1000)

            updated_session = SessionInfo(**update_data)
            await Storage.set(f"session:{project_id}:{session_id}", updated_session, "session")
            cls._id_index[session_id] = f"session:{project_id}:{session_id}"
            cls._sync_list_cache(updated_session)
        
        log.info("session.updated", {
            "id": session_id,
            "project_id": project_id,
        })
        
        return updated_session

    @classmethod
    async def move_to_project(
        cls,
        source_project_id: str,
        session_id: str,
        *,
        target_project_id: str,
        target_directory: str,
        target_owner_id: Optional[str] = None,
        additional_busy_check: Optional[Callable[[str], bool]] = None,
    ) -> Optional[SessionInfo]:
        """Move a complete active root session tree to another project."""

        if source_project_id == target_project_id:
            return await cls.get(source_project_id, session_id)

        from flocks.project.project import Project

        async with AsyncExitStack() as project_guards:
            for project_id in sorted({source_project_id, target_project_id}):
                await project_guards.enter_async_context(Project.lifecycle_guard(project_id))

            if (
                target_owner_id is not None
                and Project.registry_state(target_project_id, owner_id=target_owner_id) != "active"
            ):
                return None

            sessions = await cls._begin_lifecycle_transition(
                source_project_id,
                session_id,
                require_root=True,
            )
            if not sessions:
                return None

            try:
                if any(session.status != "active" for session in sessions):
                    return None
                if any(session.revert is not None for session in sessions):
                    return None

                from flocks.session.session_loop import SessionLoop

                if any(
                    SessionLoop.is_running(session.id)
                    or (
                        additional_busy_check is not None
                        and additional_busy_check(session.id)
                    )
                    for session in sessions
                ):
                    return None

                updated_at = int(datetime.now().timestamp() * 1000)
                move_boundaries: Dict[str, Optional[str]] = {}
                for session in sessions:
                    messages = await Message.list(session.id, include_archived=True)
                    move_boundaries[session.id] = messages[-1].id if messages else None

                moved_sessions = [
                    session.model_copy(update={
                        "project_id": target_project_id,
                        "directory": target_directory,
                        "metadata": {
                            **(
                                session.metadata
                                if isinstance(session.metadata, dict)
                                else {}
                            ),
                            "projectMove": {
                                "sourceProjectID": source_project_id,
                                "targetProjectID": target_project_id,
                                "boundaryMessageID": move_boundaries[session.id],
                                "movedAt": updated_at,
                            },
                        },
                        "time": session.time.model_copy(update={"updated": updated_at}),
                    })
                    for session in sessions
                ]
                await Storage.mutate_many(
                    set_entries=[
                        (f"session:{target_project_id}:{session.id}", session, "session")
                        for session in moved_sessions
                    ],
                    delete_keys=[
                        f"session:{source_project_id}:{session.id}"
                        for session in sessions
                    ],
                )
                for session in moved_sessions:
                    cls._id_index[session.id] = f"session:{target_project_id}:{session.id}"
                    cls._sync_list_cache(session)

                log.info("session.project_moved", {
                    "id": session_id,
                    "source_project_id": source_project_id,
                    "target_project_id": target_project_id,
                    "affected_sessions": len(moved_sessions),
                })
                return moved_sessions[0]
            finally:
                await cls._end_lifecycle_transition(sessions)
    
    @classmethod
    async def delete(cls, project_id: str, session_id: str) -> bool:
        """Permanently delete a session tree and all application-owned data."""

        from flocks.project.project import Project

        async with Project.lifecycle_guard(project_id):
            return await cls._delete_locked(project_id, session_id)

    @classmethod
    async def _delete_locked(cls, project_id: str, session_id: str) -> bool:
        """
        Permanently delete a session tree and all application-owned data.
        
        Also deletes child sessions.
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            True if deleted
        """
        sessions = await cls._begin_lifecycle_transition(project_id, session_id)
        if not sessions:
            return False
        deleted = False
        try:
            if not await cls._stop_session_tree_for_archive(sessions):
                return False
            for session in sessions:
                await Message.quiesce_parts(session.id, persist=False)

            refreshed = await cls.collect_tree(project_id, session_id)
            if not refreshed:
                return False
            sessions = refreshed
            session_ids = [session.id for session in sessions]

            from flocks.permission.next import PermissionNext
            from flocks.storage.session_search import delete_session_documents

            permission_keys = await PermissionNext.deletion_storage_keys(session_ids)

            async def _delete_search_index(db) -> None:
                if not Storage.session_search_available():
                    return
                await delete_session_documents(db, session_ids)

            await Storage.mutate_many(
                delete_keys=[
                    key
                    for session in sessions
                    for key in (
                        f"session:{session.project_id}:{session.id}",
                        f"message:{session.id}",
                        f"message_parts:{session.id}",
                        f"todo:{session.id}",
                        f"goal:{session.id}",
                        f"session_diff:{session.id}",
                        f"session_callable_tools:{session.id}",
                    )
                ] + permission_keys,
                delete_prefixes=[
                    prefix
                    for session in sessions
                    for prefix in (
                        f"message_parts:{session.id}:",
                        f"message_diff:{session.id}:",
                        f"system_prompts:{session.id}:",
                    )
                ],
                transaction_hook=_delete_search_index,
            )
            PermissionNext.clear_session_runtime(session_ids)

            from flocks.session.session_loop import SessionLoop
            from flocks.session.callable_state import invalidate_session_callable_tools_cache
            from flocks.project.project import Project

            Project.invalidate_session_stats()

            for session in sessions:
                cls._id_index.pop(session.id, None)
                cls._remove_from_list_cache(session.id)
                SessionLoop.clear_auto_failover_state(session.id)
                Message.invalidate_cache(session.id)
                invalidate_session_callable_tools_cache(session.id)

                try:
                    from flocks.session.files import remove_session_uploads

                    if await asyncio.to_thread(remove_session_uploads, session.id):
                        log.info("session.uploads.cleaned", {"session_id": session.id})
                except Exception as exc:
                    log.warn("session.uploads.cleanup_failed", {
                        "session_id": session.id,
                        "error": str(exc),
                    })

            for session in reversed(sessions):
                try:
                    from flocks.session.core.session_state import (
                        get_main_session_id,
                        remove_subagent_session,
                        set_main_session,
                    )

                    if get_main_session_id() == session.id:
                        set_main_session(None)
                    remove_subagent_session(session.id)
                except Exception as e:
                    log.warn("session.state.error", {"id": session.id, "error": str(e)})

                try:
                    from flocks.bus.bus import Bus
                    from flocks.bus.events import SessionDeleted

                    await Bus.publish(SessionDeleted, {"sessionID": session.id})
                except Exception as e:
                    log.warn("session.deleted.event_error", {"id": session.id, "error": str(e)})

            log.info("session.deleted", {
                "id": session_id,
                "project_id": project_id,
                "count": len(sessions),
            })
            deleted = True
            return True
        finally:
            await cls._end_lifecycle_transition(sessions)
            if deleted:
                for session in sessions:
                    cls._lifecycle_generations.pop(session.id, None)

    @classmethod
    async def retain_deleted_user_sessions(cls, user_id: str, username: str) -> int:
        """
        Detach session ownership from a deleted user id while preserving username ownership.

        This allows a newly created account with the same username to regain access
        to historical private sessions.
        """
        entries = await Storage.list_entries(prefix="session:", model=SessionInfo)
        migrated = 0

        for _key, session in entries:
            if session.status == "deleted":
                continue
            if session.owner_user_id != user_id:
                continue
            await cls.update(
                project_id=session.project_id,
                session_id=session.id,
                allow_inactive=True,
                owner_user_id=_UNSET,
                owner_username=username,
            )
            migrated += 1

        return migrated

    @classmethod
    async def _stop_session_tree_for_archive(
        cls,
        sessions: List[SessionInfo],
        *,
        timeout_s: float = 5.0,
        clear_prompt_queue: bool = True,
    ) -> bool:
        """Stop persisted and in-memory work before committing archive state."""
        from flocks.session.interaction_queue import InteractionQueue
        from flocks.session.runner import SessionRunner
        from flocks.session.session_loop import SessionLoop

        session_ids = [session.id for session in sessions]
        for session_id in session_ids:
            SessionLoop.abort(session_id)
            SessionRunner.cancel(session_id)
            if clear_prompt_queue:
                await InteractionQueue.clear(session_id)
            try:
                from flocks.server.routes.question import reject_session_questions

                await reject_session_questions(session_id)
            except Exception as exc:
                log.warn("session.archive.question_cleanup_error", {
                    "id": session_id,
                    "error": str(exc),
                })
            try:
                from flocks.session.background_tasks import cancel_session_background_tasks

                await cancel_session_background_tasks(session_id)
            except Exception as exc:
                log.warn("session.archive.background_task_cleanup_error", {
                    "id": session_id,
                    "error": str(exc),
                })
            try:
                from flocks.task.background import get_background_manager

                get_background_manager().cancel_by_parent_session_id(session_id)
            except Exception as exc:
                log.warn("session.archive.background_cleanup_error", {
                    "id": session_id,
                    "error": str(exc),
                })

        deadline = asyncio.get_running_loop().time() + timeout_s
        pending = set(session_ids)
        while pending:
            running = {session_id for session_id in pending if SessionLoop.is_running(session_id)}
            if not running:
                return True
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                log.warn("session.archive.wait_idle_timeout", {
                    "ids": sorted(running),
                    "timeout_s": timeout_s,
                })
                return False
            pending = running
            await asyncio.sleep(min(0.05, deadline - now))
        return True
    
    @classmethod
    async def archive(cls, project_id: str, session_id: str) -> bool:
        """Archive a root session and its descendants."""

        from flocks.project.project import Project

        async with Project.lifecycle_guard(project_id):
            return await cls._archive_locked(project_id, session_id)

    @classmethod
    async def _archive_locked(cls, project_id: str, session_id: str) -> bool:
        """
        Archive a session
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            True if archived
        """
        sessions = await cls._begin_lifecycle_transition(
            project_id,
            session_id,
            require_root=True,
        )
        if not sessions:
            return False
        prompt_queue_paused = False
        archive_committed = False
        try:
            if any(session.status != "archived" for session in sessions):
                from flocks.session.interaction_queue import InteractionQueue

                prompt_queue_paused = True
                for session in sessions:
                    await InteractionQueue.pause(session.id)
                if not await cls._stop_session_tree_for_archive(
                    sessions,
                    clear_prompt_queue=False,
                ):
                    return False
            for session in sessions:
                await Message.quiesce_parts(session.id, persist=True)

            # Lifecycle claims prevent new in-process children and writes. Read
            # once more so every child committed before the claim is included.
            refreshed = await cls.collect_tree(project_id, session_id)
            if not refreshed:
                return False
            sessions = refreshed

            archived_ts = int(datetime.now().timestamp() * 1000)
            updated_sessions: List[SessionInfo] = []
            for session in sessions:
                if session.status == "archived":
                    updated_sessions.append(session)
                    continue
                updated_sessions.append(session.model_copy(update={
                    "status": "archived",
                    "time": session.time.model_copy(update={
                        "archived": session.time.archived or archived_ts,
                    }),
                }))

            original_status = {session.id: session.status for session in sessions}
            changed = [
                session
                for session in updated_sessions
                if original_status[session.id] != session.status
            ]
            if changed:
                await Storage.set_many([
                    (f"session:{session.project_id}:{session.id}", session, "session")
                    for session in changed
                ])
                for session in changed:
                    cls._id_index[session.id] = f"session:{session.project_id}:{session.id}"
                    cls._sync_list_cache(session)
            archive_committed = True

            try:
                from flocks.session.session_loop import SessionLoop
                from flocks.session.core.session_state import (
                    get_main_session_id,
                    remove_subagent_session,
                    set_main_session,
                )

                for session in sessions:
                    SessionLoop.clear_auto_failover_state(session.id)
                    Message.invalidate_cache(session.id)
                    if get_main_session_id() == session.id:
                        set_main_session(None)
                    remove_subagent_session(session.id)
            except Exception as exc:
                log.warn("session.archive.runtime_cleanup_error", {
                    "id": session_id,
                    "error": str(exc),
                })

            log.info("session.archived", {
                "id": session_id,
                "project_id": project_id,
                "count": len(sessions),
            })
            return True
        finally:
            if prompt_queue_paused and not archive_committed:
                from flocks.session.interaction_queue import InteractionQueue

                for session in sessions:
                    await InteractionQueue.resume(session.id)
            await cls._end_lifecycle_transition(sessions)
    
    @classmethod
    async def unarchive(cls, project_id: str, session_id: str) -> bool:
        """Restore an archived root session and its descendants."""

        from flocks.project.project import Project

        async with Project.lifecycle_guard(project_id):
            return await cls._unarchive_locked(project_id, session_id)

    @classmethod
    async def _unarchive_locked(cls, project_id: str, session_id: str) -> bool:
        """
        Restore an archived session
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            True if restored
        """
        sessions = await cls._begin_lifecycle_transition(
            project_id,
            session_id,
            require_root=True,
        )
        if not sessions:
            return False
        try:
            changed = [
                session.model_copy(update={
                    "status": "active",
                    "time": session.time.model_copy(update={
                        "archived": None,
                    }),
                })
                for session in sessions
                if session.status == "archived"
            ]
            if changed:
                await Storage.set_many([
                    (f"session:{session.project_id}:{session.id}", session, "session")
                    for session in changed
                ])
                for session in changed:
                    cls._id_index[session.id] = f"session:{session.project_id}:{session.id}"
                    cls._sync_list_cache(session)

            from flocks.session.interaction_queue import InteractionQueue

            for session in sessions:
                await InteractionQueue.resume(session.id)

            log.info("session.unarchived", {
                "id": session_id,
                "project_id": project_id,
                "count": len(sessions),
            })
            return True
        finally:
            await cls._end_lifecycle_transition(sessions)

    @classmethod
    async def restore(
        cls,
        project_id: str,
        session_id: str,
        *,
        project_owner_id: Optional[str],
    ) -> bool:
        """Restore a session tree and its removed project as one operation."""

        from flocks.project.project import (
            DEFAULT_PROJECT_ID,
            Project,
            ProjectDeletionError,
            TASK_SESSION_GROUP_ID,
        )

        async with Project.lifecycle_guard(project_id):
            project_state = (
                Project.registry_state(project_id, owner_id=project_owner_id)
                if project_owner_id
                else (
                    "virtual"
                    if project_id in {DEFAULT_PROJECT_ID, TASK_SESSION_GROUP_ID}
                    else "missing"
                )
            )
            if project_state == "missing" and project_id.startswith("prj_"):
                raise ProjectDeletionError(
                    f"Project {project_id} restoration metadata is unavailable"
                )

            project_was_removed = project_state == "removed"
            if project_state in {"active", "removed"} and project_owner_id is not None:
                await Project.restore(project_id, owner_id=project_owner_id)

            try:
                restored = await cls.unarchive(project_id, session_id)
            except BaseException:
                if project_was_removed and project_owner_id is not None:
                    try:
                        await asyncio.shield(
                            Project._delete_locked(project_id, owner_id=project_owner_id)
                        )
                    except Exception as rollback_exc:
                        log.error("session.restore.project_rollback_failed", {
                            "project_id": project_id,
                            "error": str(rollback_exc),
                        })
                raise

            if not restored and project_was_removed and project_owner_id is not None:
                await Project.delete(project_id, owner_id=project_owner_id)
            return restored

    @classmethod
    async def collect_tree(cls, project_id: str, session_id: str) -> List[SessionInfo]:
        """Return a root session and all descendants in parent-first order."""
        token = set_current_auth_user(None)
        try:
            root = await cls.get(project_id, session_id)
        finally:
            reset_current_auth_user(token)
        if root is None:
            return []

        children_by_parent: Dict[str, List[SessionInfo]] = {}
        for session in await cls.list_all_unfiltered():
            if session.project_id != project_id:
                continue
            if session.parent_id:
                children_by_parent.setdefault(session.parent_id, []).append(session)

        tree: List[SessionInfo] = []
        seen: set[str] = set()

        def visit(session: SessionInfo) -> None:
            if session.id in seen:
                return
            seen.add(session.id)
            tree.append(session)
            for child in children_by_parent.get(session.id, []):
                visit(child)

        visit(root)
        return tree

    @classmethod
    async def _begin_lifecycle_transition(
        cls,
        project_id: str,
        session_id: str,
        *,
        require_root: bool = False,
    ) -> List[SessionInfo]:
        """Claim a complete tree so ordinary writes cannot overtake its transition."""
        async with cls._tree_lock:
            sessions = await cls.collect_tree(project_id, session_id)
            if not sessions:
                return []
            if require_root and sessions[0].parent_id is not None:
                log.warn("session.lifecycle.non_root_rejected", {"id": session_id})
                return []

            async with AsyncExitStack() as locks:
                for item_id in sorted(session.id for session in sessions):
                    await locks.enter_async_context(cls.lifecycle_lock(item_id))

                # A child may have committed before the topology lock was
                # acquired. Re-read under all known keyed locks before claiming.
                sessions = await cls.collect_tree(project_id, session_id)
                ids = {session.id for session in sessions}
                if (
                    not sessions
                    or ids & cls._lifecycle_transition_ids
                    or any(cls.has_active_operations(item_id) for item_id in ids)
                ):
                    return []
                cls._lifecycle_transition_ids.update(ids)
                for item_id in ids:
                    cls._lifecycle_generations[item_id] = cls.lifecycle_generation(item_id) + 1
                return sessions

    @classmethod
    async def _end_lifecycle_transition(cls, sessions: List[SessionInfo]) -> None:
        async with cls._tree_lock:
            cls._lifecycle_transition_ids.difference_update(session.id for session in sessions)

    @classmethod
    async def children(cls, project_id: str, parent_id: str) -> List[SessionInfo]:
        """
        Get child sessions
        
        Args:
            project_id: Project ID
            parent_id: Parent session ID
            
        Returns:
            List of child sessions
        """
        all_sessions = await cls.list(project_id)
        return [s for s in all_sessions if s.parent_id == parent_id]
    
    @classmethod
    async def fork(
        cls,
        project_id: str,
        session_id: str,
        message_id: Optional[str] = None,
    ) -> SessionInfo:
        """
        Fork a session (create new session with copied messages)
        
        Args:
            project_id: Project ID
            session_id: Session ID to fork
            message_id: Optional message ID to fork up to
            
        Returns:
            New forked session
        """
        from flocks.project.project import Project

        # Keep the parent, new child, and copied history in one project-lifecycle
        # interval. A concurrent move can only happen before or after the fork.
        async with Project.lifecycle_guard(project_id):
            original = await cls.get(project_id, session_id)
            if not original:
                raise ValueError(f"Session {session_id} not found")

            messages = await Message.list(session_id, include_archived=True)
            messages_to_copy: List[MessageInfo] = []
            for msg in messages:
                if message_id and msg.id >= message_id:
                    break
                messages_to_copy.append(msg)
            id_map = {
                msg.id: Identifier.ascending("message")
                for msg in messages_to_copy
            }

            fork_move_metadata: Optional[Dict[str, Any]] = None
            move_metadata = original.metadata.get("projectMove")
            if isinstance(move_metadata, dict) and move_metadata.get("boundaryMessageID"):
                original_boundary = move_metadata["boundaryMessageID"]
                mapped_boundary = id_map.get(original_boundary)
                if mapped_boundary is None and messages_to_copy:
                    # A fork ending before the move boundary contains only unsafe
                    # imported history, so protect the complete copied prefix.
                    mapped_boundary = id_map[messages_to_copy[-1].id]
                if mapped_boundary is not None:
                    fork_move_metadata = {
                        **move_metadata,
                        "boundaryMessageID": mapped_boundary,
                    }

            metadata = (
                {"projectMove": fork_move_metadata}
                if fork_move_metadata is not None
                else None
            )
            new_session = await cls.create(
                project_id=project_id,
                directory=original.directory,
                parent_id=session_id,
                **({"metadata": metadata} if metadata is not None else {}),
            )

            # Copy messages with all parts (include archived so fork preserves full history)
            for msg in messages_to_copy:
                new_id = id_map[msg.id]

                # Get text content for the initial message creation
                content = await Message.get_text_content(msg)
                parent_ref = None
                if isinstance(msg, AssistantMessageInfo):
                    parent_ref = id_map.get(msg.parentID)

                # Create the message (this also creates an initial TextPart)
                await Message.create(
                    session_id=new_session.id,
                    role=MessageRole(msg.role),
                    content=content,
                    id=new_id,
                    parentID=parent_ref or "",
                )

                # Copy non-text parts (tool calls, files, patches, etc.)
                original_parts = await Message.parts(msg.id, session_id)
                for part in original_parts:
                    if part.type == "text":
                        continue  # Already created by Message.create above
                    # Clone part with updated session/message IDs
                    part_data = part.model_dump()
                    part_data["id"] = Identifier.ascending("part")
                    part_data["sessionID"] = new_session.id
                    part_data["messageID"] = new_id
                    cloned_part = Message.deserialize_part(part_data)
                    await Message.store_part(new_session.id, new_id, cloned_part)

            log.info("session.forked", {
                "from": session_id,
                "to": new_session.id,
                "messages": len(id_map),
            })

            return new_session
    
    @classmethod
    async def set_revert(
        cls,
        project_id: str,
        session_id: str,
        message_id: str,
        part_id: Optional[str] = None,
        snapshot: Optional[str] = None,
        diff: Optional[str] = None,
    ) -> bool:
        """
        Set revert state for session
        
        Args:
            project_id: Project ID
            session_id: Session ID
            message_id: Message ID to revert to
            part_id: Part ID for partial revert
            snapshot: Snapshot ID
            diff: Diff content
            
        Returns:
            True if set
        """
        revert = SessionRevert(
            message_id=message_id,
            part_id=part_id,
            snapshot=snapshot,
            diff=diff,
        )
        
        await cls.update(project_id, session_id, revert=revert.model_dump(by_alias=True))
        
        log.info("session.revert.set", {
            "id": session_id,
            "message_id": message_id,
        })
        
        return True
    
    @classmethod
    async def clear_revert(cls, project_id: str, session_id: str) -> bool:
        """
        Clear revert state for session
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            True if cleared
        """
        result = await cls.update(project_id, session_id, revert=_UNSET)
        if result:
            log.info("session.revert.cleared", {"id": session_id})
            return True
        return False
    
    @classmethod
    def set_current(cls, session: SessionInfo) -> None:
        """
        Set current active session (concurrent-safe via contextvars).
        
        Args:
            session: Session info
        """
        cls._current_var.set(session)
        log.info("session.current.set", {"id": session.id})
    
    @classmethod
    def get_current(cls) -> Optional[SessionInfo]:
        """
        Get current active session (concurrent-safe via contextvars).
        
        Returns:
            Current session or None
        """
        return cls._current_var.get(None)
    
    @classmethod
    async def touch(cls, project_id: str, session_id: str) -> None:
        """
        Update session's last updated time
        
        Args:
            project_id: Project ID
            session_id: Session ID
        """
        await cls.update(project_id, session_id)
    
    @classmethod
    async def get_messages(cls, session_id: str) -> List[MessageInfo]:
        """
        Get all messages for a session
        
        Args:
            session_id: Session ID
            
        Returns:
            List of messages
        """
        return await Message.list(session_id)
    
    @classmethod
    async def get_message_count(cls, session_id: str) -> int:
        """
        Get message count for a session
        
        Args:
            session_id: Session ID
            
        Returns:
            Message count
        """
        return len(await Message.list(session_id))
    
    @classmethod
    async def diff(cls, project_id: str, session_id: str) -> List[Dict[str, Any]]:
        """
        Get session diff (file changes)
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            List of file diffs
        """
        try:
            data = await Storage.get(f"session_diff:{session_id}", list)
            return data or []
        except Exception as _e:
            log.debug("session.diffs.get_failed", {"session_id": session_id, "error": str(_e)})
            return []
    
    @classmethod
    async def get_memory(cls, project_id: str, session_id: str) -> Optional["SessionMemory"]:
        """
        Get memory interface for a session
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            SessionMemory instance or None
        """
        session = await cls.get(project_id, session_id)
        if not session:
            return None
        
        from flocks.session.features.memory import SessionMemory
        
        memory = SessionMemory(
            session_id=session_id,
            project_id=project_id,
            workspace_dir=session.directory,
            enabled=session.memory_enabled,
        )
        
        # Auto-initialize if enabled
        if session.memory_enabled:
            await memory.initialize()
        
        return memory
