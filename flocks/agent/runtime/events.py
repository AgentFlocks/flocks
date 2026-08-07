"""Runtime event contract emitted by the agent core."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class RuntimeEvent:
    """Host-neutral event produced during an agent run."""

    type: str
    session_id: str
    model_turn_index: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    trace_step: Optional[int] = None
