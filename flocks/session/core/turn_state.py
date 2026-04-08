"""
Lightweight runtime turn/context state tracking.

This keeps a small in-memory view of the latest turn-level state so the loop,
routes, and SSE layer can share consistent semantics without introducing a
heavier runtime state machine.
"""

import threading
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from flocks.utils.log import Log


log = Log.create(service="session.turn_state")

_lock = threading.Lock()
_turn_state: Dict[str, "TurnStateInfo"] = {}
_context_state: Dict[str, "ContextStateInfo"] = {}


class TurnStateInfo(BaseModel):
    session_id: str = Field(alias="sessionID")
    step: int = 0
    status: str = "idle"
    stop_reason: Optional[str] = None
    continue_reason: Optional[str] = None
    queued_message_detected: bool = False


class ContextStateInfo(BaseModel):
    session_id: str = Field(alias="sessionID")
    compaction_performed: bool = False
    tool_results_compacted: bool = False
    last_compaction_step: Optional[int] = None
    last_compaction_reason: Optional[str] = None


def set_turn_state(
    session_id: str,
    *,
    step: int,
    status: str,
    stop_reason: Optional[str] = None,
    continue_reason: Optional[str] = None,
    queued_message_detected: Optional[bool] = None,
) -> TurnStateInfo:
    with _lock:
        previous = _turn_state.get(session_id)
        turn_state = TurnStateInfo(
            sessionID=session_id,
            step=step,
            status=status,
            stop_reason=stop_reason,
            continue_reason=continue_reason,
            queued_message_detected=(
                previous.queued_message_detected if previous and queued_message_detected is None else bool(queued_message_detected)
            ),
        )
        _turn_state[session_id] = turn_state
    log.debug("turn_state.updated", turn_state.model_dump(by_alias=True))
    return turn_state


def get_turn_state(session_id: str) -> TurnStateInfo:
    with _lock:
        return _turn_state.get(session_id) or TurnStateInfo(sessionID=session_id)


def clear_turn_state(session_id: str) -> None:
    with _lock:
        _turn_state.pop(session_id, None)
        _context_state.pop(session_id, None)


def set_context_state(
    session_id: str,
    *,
    compaction_performed: Optional[bool] = None,
    tool_results_compacted: Optional[bool] = None,
    last_compaction_step: Optional[int] = None,
    last_compaction_reason: Optional[str] = None,
) -> ContextStateInfo:
    with _lock:
        previous = _context_state.get(session_id) or ContextStateInfo(sessionID=session_id)
        context_state = ContextStateInfo(
            sessionID=session_id,
            compaction_performed=(
                previous.compaction_performed if compaction_performed is None else compaction_performed
            ),
            tool_results_compacted=(
                previous.tool_results_compacted if tool_results_compacted is None else tool_results_compacted
            ),
            last_compaction_step=(
                previous.last_compaction_step if last_compaction_step is None else last_compaction_step
            ),
            last_compaction_reason=(
                previous.last_compaction_reason if last_compaction_reason is None else last_compaction_reason
            ),
        )
        _context_state[session_id] = context_state
    log.debug("context_state.updated", context_state.model_dump(by_alias=True))
    return context_state


def get_context_state(session_id: str) -> ContextStateInfo:
    with _lock:
        return _context_state.get(session_id) or ContextStateInfo(sessionID=session_id)
