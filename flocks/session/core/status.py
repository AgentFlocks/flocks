"""
Session status tracking

Manages session execution state (idle, queued, busy, retry).
Based on Flocks' ported src/session/status.ts
"""

from typing import Dict, Any, List, Literal, Optional
from pydantic import BaseModel, Field

from flocks.utils.log import Log
from flocks.project.instance import Instance


log = Log.create(service="session.status")


class SessionStatusIdle(BaseModel):
    """Idle status"""
    type: Literal["idle"] = "idle"


class SessionStatusBusy(BaseModel):
    """Busy status"""
    type: Literal["busy"] = "busy"


class SessionStatusQueued(BaseModel):
    """Queued status - accepted work is waiting to enter the session loop."""

    type: Literal["queued"] = "queued"


class SessionStatusRetry(BaseModel):
    """Retry status"""
    type: Literal["retry"] = "retry"
    attempt: int = Field(..., description="Retry attempt number")
    message: str = Field(..., description="Error message")
    next: int = Field(..., description="Next retry timestamp (ms)")


COMPACTING_DEFAULT_MESSAGE = "Compacting context…"


class SessionStatusCompacting(BaseModel):
    """Compacting status - context compression in progress"""
    type: Literal["compacting"] = "compacting"
    message: str = Field(COMPACTING_DEFAULT_MESSAGE, description="Display message")


class SessionStatusDreaming(BaseModel):
    """Dreaming status - manual self-improvement is in progress."""

    type: Literal["dreaming"] = "dreaming"
    message: str = Field(..., description="Display message")


# Union of all status types
SessionStatusInfo = (
    SessionStatusIdle
    | SessionStatusQueued
    | SessionStatusBusy
    | SessionStatusRetry
    | SessionStatusCompacting
    | SessionStatusDreaming
)


class SessionStatus:
    """
    Session status namespace
    
    Tracks session execution state across the application.
    Matches Flocks SessionStatus namespace.
    """
    
    # Instance-scoped state storage
    _state: Dict[str, Dict[str, SessionStatusInfo]] = {}
    
    @classmethod
    def _get_state(cls) -> Dict[str, SessionStatusInfo]:
        """Get instance-scoped state"""
        try:
            instance_id = Instance.directory if hasattr(Instance, 'directory') else "default"
        except Exception as _e:
            log.debug("status.instance_id.fallback", {"error": str(_e)})
            instance_id = "default"
        
        if instance_id not in cls._state:
            cls._state[instance_id] = {}
        
        return cls._state[instance_id]
    
    @classmethod
    def get(cls, session_id: str) -> SessionStatusInfo:
        """
        Get session status
        
        Args:
            session_id: Session ID
            
        Returns:
            Session status (defaults to idle if not found)
        """
        state = cls._get_state()
        return state.get(session_id, SessionStatusIdle())

    @classmethod
    def get_for_session(
        cls,
        session_id: str,
        preferred_instance_id: Optional[str] = None,
    ) -> SessionStatusInfo:
        """Get a session status without relying on the ambient instance context.

        Session IDs are globally unique, but runtime state remains grouped by
        instance directory for compatibility. Prefer the session's persisted
        directory and fall back to the remaining live instance states so a
        canonicalized or historical directory cannot cause a false idle.
        """
        if preferred_instance_id:
            preferred_state = cls._state.get(preferred_instance_id, {})
            status = preferred_state.get(session_id)
            if status is not None:
                return status

        for instance_id, state in list(cls._state.items()):
            if instance_id == preferred_instance_id:
                continue
            status = state.get(session_id)
            if status is not None:
                return status

        return SessionStatusIdle()
    
    @classmethod
    def list(cls) -> Dict[str, SessionStatusInfo]:
        """
        List all session statuses
        
        Returns:
            Dictionary mapping session IDs to their status
        """
        state = cls._get_state()
        return dict(state)
    
    @classmethod
    def set(cls, session_id: str, status: SessionStatusInfo) -> None:
        """
        Set session status
        
        Args:
            session_id: Session ID
            status: New status
        """
        state = cls._get_state()
        
        if status.type == "idle":
            # Remove idle sessions from state
            if session_id in state:
                del state[session_id]
        else:
            state[session_id] = status
        
        log.debug("session.status", {
            "session_id": session_id,
            "status": status.type,
        })
    
    @classmethod
    def clear(cls, session_id: str) -> None:
        """
        Clear session status (set to idle)
        
        Args:
            session_id: Session ID
        """
        cls.set(session_id, SessionStatusIdle())
    
    @classmethod
    def clear_all(cls) -> None:
        """Clear all session statuses"""
        state = cls._get_state()
        state.clear()

    @classmethod
    def get_busy_session_ids(cls) -> List[str]:
        """Return IDs of all sessions that are busy or compacting (across all instances)."""
        result: List[str] = []
        for _inst_id, statuses in list(cls._state.items()):
            for sid, info in list(statuses.items()):
                if info.type in ("busy", "compacting", "dreaming"):
                    result.append(sid)
        return result
