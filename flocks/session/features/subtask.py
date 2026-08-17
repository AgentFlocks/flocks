"""
Session Subtask data models.

Note: The SessionSubtask business logic has been removed as it was dead code.
The live subtask execution path is session_loop.py::_execute_subtask(), which
handles the full lifecycle inline without using this module.

These data classes are kept because they are exported from session/__init__.py
and may be referenced by external consumers.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class SubtaskInfo:
    """Information about a subtask"""
    id: str
    parent_session_id: str
    child_session_id: Optional[str] = None
    task_description: str = ""
    agent: Optional[str] = None
    model: Optional[str] = None
    status: str = "pending"  # pending, running, completed, error
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: int = field(default_factory=lambda: int(datetime.now().timestamp() * 1000))
    completed_at: Optional[int] = None


@dataclass
class SubtaskResult:
    """Result of subtask execution"""
    subtask_id: str
    success: bool
    output: str
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# Minimal stub so imports of SessionSubtask don't break existing code.
class SessionSubtask:
    """Subtask manager stub — business logic removed (was dead code).

    The active execution path is SessionLoop._execute_subtask() in session_loop.py.
    """

    @classmethod
    async def execute_subtask(cls, *args, **kwargs) -> SubtaskResult:
        raise NotImplementedError(
            "SessionSubtask.execute_subtask() is deprecated. "
            "Subtask execution is handled by SessionLoop._execute_subtask()."
        )


__all__ = [
    "SessionSubtask",
    "SubtaskInfo",
    "SubtaskResult",
]
