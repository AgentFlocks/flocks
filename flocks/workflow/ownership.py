"""Process ownership of workflow runs, backed by PostgreSQL leases."""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from flocks.task.node import current_node_id, heartbeat_seconds, lease_seconds
from flocks.utils.log import Log

log = Log.create(service="workflow.ownership")


class WorkflowOwnershipError(RuntimeError):
    """The caller cannot start or persist this workflow execution."""


class WorkflowAlreadyRunning(WorkflowOwnershipError):
    """Another process owns the requested no-overlap scope."""


_process_identity: tuple[int, str] | None = None


def process_owner_id() -> str:
    global _process_identity
    pid = os.getpid()
    if _process_identity is None or _process_identity[0] != pid:
        _process_identity = (pid, f"{current_node_id()}:{pid}:{uuid.uuid4().hex}")
    return _process_identity[1]


@dataclass
class WorkflowLease:
    execution_id: str
    token: dict[str, Any]
    duration: int
    lost: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task | None = None
    deadline: float = 0.0

    async def renew(self) -> None:
        from flocks.workflow.store import WorkflowStore

        interval = min(heartbeat_seconds(), max(0.1, self.duration / 3))
        while True:
            await asyncio.sleep(interval)
            try:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    self.lost.set()
                    return
                renewal_started = time.monotonic()
                renewed = await asyncio.wait_for(
                    WorkflowStore.renew_execution_lease(self.execution_id, self.token, self.duration),
                    timeout=remaining,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("workflow.lease.renew_failed", {"exec_id": self.execution_id, "error": str(exc)})
                if time.monotonic() >= self.deadline:
                    self.lost.set()
                    return
                continue
            if not renewed:
                self.lost.set()
                return
            if renewed.get("cancel_requested"):
                self.cancelled.set()
            # Start the local deadline before the request. Network/commit time
            # must never let this process run beyond the database lease.
            self.deadline = renewal_started + self.duration


_leases: dict[str, WorkflowLease] = {}


def start_renewal(exec_data: dict[str, Any], *, duration: int | None = None, claimed_at: float | None = None) -> None:
    execution_id = str(exec_data["id"])
    token = {key: exec_data[key] for key in ("leaseKey", "ownerNodeId", "ownerGeneration")}
    lease = WorkflowLease(execution_id, token, duration or lease_seconds())
    lease.deadline = (claimed_at if claimed_at is not None else time.monotonic()) + lease.duration
    lease.task = asyncio.create_task(lease.renew(), name=f"workflow-lease-{execution_id}")
    _leases[execution_id] = lease


def execution_cancelled(execution_id: str) -> bool:
    """Thread-safe cancellation check used between workflow nodes/tool calls."""
    lease = _leases.get(execution_id)
    return lease is None or lease.lost.is_set() or lease.cancelled.is_set() or time.monotonic() >= lease.deadline


def owned_summary(exec_data: dict[str, Any]) -> dict[str, Any]:
    result = dict(exec_data)
    lease = _leases.get(str(exec_data["id"]))
    if lease is not None:
        if lease.lost.is_set() or time.monotonic() >= lease.deadline:
            raise WorkflowOwnershipError(f"Workflow execution lease expired: {lease.execution_id}")
        result.update(lease.token)
    return result


async def stop_renewal(execution_id: str) -> None:
    lease = _leases.pop(execution_id, None)
    if lease is not None and lease.task is not None:
        lease.task.cancel()
        try:
            await lease.task
        except asyncio.CancelledError:
            pass


async def abandon_execution(execution_id: str) -> None:
    """Stop renewing after cancellation; recovery fences and finalizes the run."""
    lease = _leases.get(execution_id)
    if lease is not None:
        from flocks.workflow.store import WorkflowStore

        lease.lost.set()
        try:
            await WorkflowStore.release_execution_lease(execution_id, lease.token)
        except Exception as exc:
            # Expiry still fences the run if the database is unavailable.
            log.warning("workflow.lease.release_failed", {"exec_id": execution_id, "error": str(exc)})
        finally:
            await stop_renewal(execution_id)


async def close_execution_leases() -> None:
    """Shutdown cancels renewal; other nodes may recover after the lease expires."""
    for execution_id in list(_leases):
        await stop_renewal(execution_id)
