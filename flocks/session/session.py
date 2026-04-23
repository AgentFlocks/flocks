"""
Session management

Handles session lifecycle, metadata, and state.
Based on Flocks' ported src/session/index.ts
"""

import contextvars
import re
import secrets
from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

# Sentinel for explicitly setting a field to None via Session.update()
_UNSET = object()

from flocks.storage.storage import Storage
from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.session.message import Message, MessageInfo, AssistantMessageInfo

log = Log.create(service="session")


# Title prefix patterns for default title detection
PARENT_TITLE_PREFIX = "New session - "
CHILD_TITLE_PREFIX = "Child session - "


class SessionChangeStats(BaseModel):
    """Session file change statistics (additions, deletions, files)"""
    additions: int = Field(0, description="Total lines added")
    deletions: int = Field(0, description="Total lines deleted")
    files: int = Field(0, description="Number of files changed")


# Backwards-compatible alias
SessionSummary = SessionChangeStats


class SessionShare(BaseModel):
    """Session share information"""
    url: str = Field(..., description="Share URL")
    secret: Optional[str] = Field(None, description="Share secret for management")


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
    
    # Session hierarchy
    parent_id: Optional[str] = Field(None, alias="parentID", description="Parent session for branching")

    # Local account ownership and visibility
    owner_user_id: Optional[str] = Field(None, alias="ownerUserID", description="Owner local user id")
    owner_username: Optional[str] = Field(None, alias="ownerUsername", description="Owner local username")
    visibility: str = Field("private", description="Session visibility: private or team_shared")
    shared_by: Optional[str] = Field(None, alias="sharedBy", description="User id who enabled sharing")
    shared_at: Optional[int] = Field(None, alias="sharedAt", description="Share timestamp (ms)")
    
    # Summary and share
    summary: Optional[SessionChangeStats] = Field(None, description="File change summary")
    share: Optional[SessionShare] = Field(None, description="Share information")
    
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

    @staticmethod
    def _sort_sessions(sessions: List[SessionInfo]) -> List[SessionInfo]:
        """Return sessions sorted by most recently updated."""
        return sorted(sessions, key=lambda s: s.time.updated, reverse=True)

    @staticmethod
    def _is_accessible_to_current_user(session: SessionInfo) -> bool:
        """
        Check session visibility against request auth context.

        If no auth context exists (CLI/internal runtime), keep backward-compatible behavior.
        """
        try:
            from flocks.auth.context import get_current_auth_user

            auth_user = get_current_auth_user()
        except Exception:
            auth_user = None

        if auth_user is None:
            return True
        if auth_user.role == "admin":
            return True

        if session.visibility == "team_shared":
            return True

        # private session: owner-only
        if Session._is_owned_by_auth_user(session, auth_user):
            return True

        # Legacy sessions without owner should not be exposed to members.
        return False

    @staticmethod
    def _is_owned_by_auth_user(session: SessionInfo, auth_user) -> bool:
        """Match session ownership by stable user id or retained username."""
        if auth_user is None:
            return False
        if session.owner_user_id and session.owner_user_id == auth_user.id:
            return True
        if session.owner_username and session.owner_username == auth_user.username:
            return True
        return False

    @classmethod
    def _sync_list_cache(cls, session: SessionInfo) -> None:
        """Keep the in-memory list cache aligned with session mutations."""
        if cls._all_sessions_cache is None:
            return

        remaining = [cached for cached in cls._all_sessions_cache if cached.id != session.id]
        if session.status != "deleted":
            remaining.append(session)
        cls._all_sessions_cache = cls._sort_sessions(remaining)

    @classmethod
    def invalidate_cache(cls) -> None:
        """Clear in-memory indexes when the underlying storage changes."""
        cls._id_index.clear()
        cls._all_sessions_cache = None

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
        }

    @classmethod
    def inherited_model_kwargs(cls, session: Optional[SessionInfo]) -> Dict[str, Any]:
        """Return pinned model kwargs that should propagate to a child session."""
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
            try:
                from flocks.config import Config
                cfg = await Config.get()
                memory_cfg = getattr(cfg, "memory", None)
                if isinstance(memory_cfg, dict):
                    kwargs["memory_enabled"] = bool(memory_cfg.get("enabled", False))
                elif memory_cfg is not None and hasattr(memory_cfg, "enabled"):
                    kwargs["memory_enabled"] = bool(getattr(memory_cfg, "enabled"))
            except Exception as e:
                log.warn("session.memory.default.error", {"error": str(e)})

        # Bind ownership from current auth context unless explicitly provided.
        if "owner_user_id" not in kwargs or "owner_username" not in kwargs:
            try:
                from flocks.auth.context import get_current_auth_user

                current_user = get_current_auth_user()
                if current_user:
                    kwargs.setdefault("owner_user_id", current_user.id)
                    kwargs.setdefault("owner_username", current_user.username)
            except Exception:
                pass
        
        session = SessionInfo(
            project_id=project_id,
            directory=directory,
            title=title or cls._create_default_title(is_child),
            parent_id=parent_id,
            permission=permission,
            **kwargs
        )
        
        # Save to storage
        storage_key = f"session:{project_id}:{session.id}"
        await Storage.set(storage_key, session, "session")
        cls._id_index[session.id] = storage_key
        cls._sync_list_cache(session)

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
    async def update(
        cls,
        project_id: str,
        session_id: str,
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
        session = await cls.get(project_id, session_id)
        if not session:
            return None
        
        # Field alias mapping (snake_case -> camelCase)
        alias_map = {
            "project_id": "projectID",
            "parent_id": "parentID",
            "owner_user_id": "ownerUserID",
            "owner_username": "ownerUsername",
            "shared_by": "sharedBy",
            "shared_at": "sharedAt",
        }
        
        # Update fields.
        # Use ``_UNSET`` sentinel to explicitly set a field to None
        # (plain ``None`` is skipped to preserve existing values).
        update_data = session.model_dump(by_alias=True)
        for key, value in updates.items():
            # Sentinel means "explicitly clear this field"
            if value is _UNSET:
                alias_key = alias_map.get(key, key)
                if alias_key in update_data:
                    update_data[alias_key] = None
                elif key in update_data:
                    update_data[key] = None
                continue

            if value is not None:
                # Handle nested updates for summary, share, revert, time
                if key == "summary" and isinstance(value, dict):
                    if update_data.get("summary"):
                        update_data["summary"].update(value)
                    else:
                        update_data["summary"] = value
                elif key == "share" and isinstance(value, dict):
                    if update_data.get("share"):
                        update_data["share"].update(value)
                    else:
                        update_data["share"] = value
                elif key == "revert" and isinstance(value, dict):
                    update_data["revert"] = value
                else:
                    # Check both original key and aliased key
                    alias_key = alias_map.get(key, key)
                    if alias_key in update_data:
                        update_data[alias_key] = value
                    elif key in update_data:
                        update_data[key] = value
        
        # Update timestamp
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
    async def delete(cls, project_id: str, session_id: str) -> bool:
        """
        Delete a session (soft delete)
        
        Also deletes child sessions.
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            True if deleted
        """
        session = await cls.get(project_id, session_id)
        if not session:
            return False
        
        # Delete child sessions first
        children = await cls.children(project_id, session_id)
        for child in children:
            await cls.delete(project_id, child.id)
        
        # Unshare if shared
        if session.share:
            await cls.unshare(project_id, session_id)
        
        # Soft delete
        await cls.update(project_id, session_id, status="deleted")
        cls._id_index.pop(session_id, None)
        
        # Clear messages
        await Message.clear(session_id)
        try:
            from flocks.session.callable_state import clear_session_callable_tools

            await clear_session_callable_tools(session_id)
        except Exception as e:
            log.warn("session.callable_tools.clear_error", {"id": session_id, "error": str(e)})
        
        log.info("session.deleted", {
            "id": session_id,
            "project_id": project_id,
        })

        # Flocks compatibility: clear state and publish event
        try:
            from flocks.session.core.session_state import get_main_session_id, set_main_session, remove_subagent_session
            if get_main_session_id() == session_id:
                set_main_session(None)
            remove_subagent_session(session_id)
        except Exception as e:
            log.warn("session.state.error", {"error": str(e)})

        try:
            from flocks.bus.bus import Bus
            from flocks.bus.events import SessionDeleted
            await Bus.publish(SessionDeleted, {
                "sessionID": session_id,
            })
        except Exception as e:
            log.warn("session.deleted.event_error", {"error": str(e)})
        
        return True

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
                owner_user_id=_UNSET,
                owner_username=username,
            )
            migrated += 1

        return migrated
    
    @classmethod
    async def archive(cls, project_id: str, session_id: str) -> bool:
        """
        Archive a session
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            True if archived
        """
        session = await cls.get(project_id, session_id)
        if not session:
            return False

        archived_ts = int(datetime.now().timestamp() * 1000)
        time_data = session.time.model_dump()
        time_data["archived"] = archived_ts
        await cls.update(project_id, session_id, status="archived", time=time_data)

        log.info("session.archived", {
            "id": session_id,
            "project_id": project_id,
        })
        return True
    
    @classmethod
    async def unarchive(cls, project_id: str, session_id: str) -> bool:
        """
        Restore an archived session
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            True if restored
        """
        session = await cls.get(project_id, session_id)
        if not session:
            # get() filters deleted; manually check archived
            raw = await Storage.get(f"session:{project_id}:{session_id}", SessionInfo)
            if not raw or raw.status != "archived":
                return False
            session = raw

        if session.status != "archived":
            return False

        time_data = session.time.model_dump()
        time_data["archived"] = None
        await cls.update(project_id, session_id, status="active", time=time_data)
        
        log.info("session.unarchived", {
            "id": session_id,
            "project_id": project_id,
        })
        
        return True
    
    @classmethod
    async def share(cls, project_id: str, session_id: str) -> Optional[SessionShare]:
        """
        Create a share link for session
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            ShareInfo or None if failed
        """
        # In production, this would create an actual share URL
        # For now, create a placeholder
        share_info = SessionShare(
            url=f"https://share.example.com/s/{session_id[:8]}",
            secret=secrets.token_urlsafe(12),
        )
        
        await cls.update(project_id, session_id, share=share_info.model_dump())
        shared_by = None
        try:
            from flocks.auth.context import get_current_auth_user

            current_user = get_current_auth_user()
            shared_by = current_user.id if current_user else None
        except Exception:
            shared_by = None

        await cls.update(
            project_id,
            session_id,
            visibility="team_shared",
            shared_by=shared_by,
            shared_at=int(datetime.now().timestamp() * 1000),
        )
        
        # Store share secret separately
        await Storage.set(f"share:{session_id}", share_info.model_dump(), "share")
        
        log.info("session.shared", {
            "id": session_id,
            "url": share_info.url,
        })
        
        return share_info
    
    @classmethod
    async def unshare(cls, project_id: str, session_id: str) -> bool:
        """
        Remove share link for session
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            True if unshared
        """
        try:
            await Storage.delete(f"share:{session_id}")
            await cls.update(
                project_id,
                session_id,
                share=_UNSET,
                visibility="private",
                shared_by=_UNSET,
                shared_at=_UNSET,
            )
            
            log.info("session.unshared", {"id": session_id})
            return True
        except Exception as e:
            log.warn("session.unshare.error", {"error": str(e)})
            return False
    
    @classmethod
    async def get_share(cls, project_id: str, session_id: str) -> Optional[SessionShare]:
        """
        Get share info for session
        
        Args:
            project_id: Project ID
            session_id: Session ID
            
        Returns:
            ShareInfo or None
        """
        try:
            data = await Storage.get(f"share:{session_id}")
            return SessionShare(**data) if data else None
        except Exception as _e:
            log.debug("session.share.get_failed", {"session_id": session_id, "error": str(_e)})
            return None
    
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
        # Get original session
        original = await cls.get(project_id, session_id)
        if not original:
            raise ValueError(f"Session {session_id} not found")
        
        # Create new session with parent_id set
        new_session = await cls.create(
            project_id=project_id,
            directory=original.directory,
        )
        
        # Update parent_id after creation
        new_session = await cls.update(
            project_id=project_id,
            session_id=new_session.id,
            parent_id=session_id,
        )
        
        # Copy messages with all parts (include archived so fork preserves full history)
        messages = await Message.list(session_id, include_archived=True)
        id_map: Dict[str, str] = {}
        
        for msg in messages:
            if message_id and msg.id >= message_id:
                break
            
            new_id = Identifier.ascending("message")
            id_map[msg.id] = new_id
            
            # Get text content for the initial message creation
            content = await Message.get_text_content(msg)
            parent_ref = None
            if isinstance(msg, AssistantMessageInfo):
                parent_ref = id_map.get(msg.parentID)
            
            # Create the message (this also creates an initial TextPart)
            await Message.create(
                session_id=new_session.id,
                role=msg.role,
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
