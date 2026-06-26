"""
Session status tracking

Manages session execution state (idle, busy, retry).
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


# Union of all status types
SessionStatusInfo = SessionStatusIdle | SessionStatusBusy | SessionStatusRetry | SessionStatusCompacting


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
                if info.type in ("busy", "compacting"):
                    result.append(sid)
        return result
