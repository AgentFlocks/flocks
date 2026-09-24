"""
Shared runtime state: the interface two instances read the same state through.

On one machine, sessions and conversations can live in local files because
only one process reads them. Two instances behind a load balancer cannot: a
user whose next request lands on the other instance has to see the same
conversation, and a background task must not be picked up twice because each
instance kept its own idea of what was running.

So everything here is addressed the same way regardless of backend -- a
key-value contract for documents (sessions, messages, memory) and explicit
ownership for work (task executions). What differs between a single machine
and a pair is which implementation is registered, not the calling code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional, Protocol, Sequence, Tuple, runtime_checkable


@dataclass(frozen=True)
class StateEntry:
    """One stored document."""

    key: str
    value: Any
    value_type: str = "json"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class ExecutionOwnership:
    """Who currently holds a unit of background work, and until when."""

    execution_id: str
    node_id: Optional[str]
    lease_expires_at: Optional[datetime]
    generation: int = 0

    def lease_is_live(self, now: datetime) -> bool:
        if self.node_id is None or self.lease_expires_at is None:
            return False
        return self.lease_expires_at > now


@runtime_checkable
class RuntimeStateStore(Protocol):
    """Documents and work ownership, shared across instances."""

    async def initialize(self) -> None:
        ...

    async def close(self) -> None:
        ...

    # -- documents ---------------------------------------------------------

    async def set(self, key: str, value: Any, value_type: str = "json") -> None:
        ...

    async def set_many(self, entries: Sequence[Tuple[str, Any, str]]) -> None:
        """Write several documents in one transaction.

        A conversation turn writes a message and its parts together; a partial
        write would leave the other instance reading a message whose content is
        missing.
        """
        ...

    async def get(self, key: str) -> Optional[Any]:
        ...

    async def delete(self, key: str) -> bool:
        ...

    async def list_keys(self, prefix: Optional[str] = None) -> List[str]:
        ...

    async def list_entries(self, prefix: Optional[str] = None) -> List[StateEntry]:
        ...

    # -- work ownership ----------------------------------------------------

    async def claim_execution(
        self, execution_id: str, *, node_id: str, lease_seconds: int
    ) -> Optional[ExecutionOwnership]:
        """Take ownership, or return None if another node holds a live lease."""
        ...

    async def renew_execution_lease(
        self, execution_id: str, *, node_id: str, generation: int, lease_seconds: int
    ) -> bool:
        """Extend our claim; fails once superseded, so a stalled node learns."""
        ...

    async def release_execution_lease(self, execution_id: str, *, node_id: str) -> bool:
        ...

    async def list_reclaimable_executions(
        self, *, node_id: str, stale_before: datetime
    ) -> List[ExecutionOwnership]:
        """Work this node may take over: its own, or a lease nobody renewed."""
        ...


__all__ = ["ExecutionOwnership", "RuntimeStateStore", "StateEntry"]
