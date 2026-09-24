"""
Node identity for task ownership.

When two instances share one task database, "this execution has been running a
while" is not enough to decide it has been abandoned -- the other instance may
be running it right now.  Ownership has to say *which* node holds an execution,
and that node has to keep saying so.

The logical node id is stable for diagnostics. Each process launch has a
separate owner token: workers on one host must not reclaim each other's live
leases. Recovery always waits for expiry, including after a local restart.
"""

from __future__ import annotations

import os
import socket
import uuid
from typing import Optional

from flocks.utils.log import Log

log = Log.create(service="task.node")

ENV_NODE_ID = "FLOCKS_NODE_ID"

#: How long a claim stays valid without a heartbeat.  Long enough to ride out a
#: pause -- GC, a slow query, a brief network stall -- and short enough that a
#: genuinely dead node's work is picked up promptly.
DEFAULT_LEASE_SECONDS = 90

#: How often an owner renews.  A third of the lease leaves room for two missed
#: renewals before the work is considered abandoned.
DEFAULT_HEARTBEAT_SECONDS = 30

_cached_node_id: Optional[str] = None
_process_owner: tuple[int, str] | None = None


def current_node_id() -> str:
    """This process's node identity, resolved once per process."""

    global _cached_node_id
    if _cached_node_id is not None:
        return _cached_node_id

    configured = (os.environ.get(ENV_NODE_ID) or "").strip()
    if configured:
        _cached_node_id = configured
    else:
        try:
            hostname = socket.gethostname().strip()
        except Exception as exc:
            hostname = ""
            log.warn("task.node.hostname_failed", {"error": str(exc)})
        # A blank fallback would make every node look like the same node and
        # let each one reclaim the others' work, so it has to be distinct.
        _cached_node_id = hostname or f"node-{os.getpid()}"

    log.info("task.node.identity", {"node_id": _cached_node_id})
    return _cached_node_id


def reset_node_id_cache() -> None:
    """Forget the cached identity.  For tests that vary the environment."""

    global _cached_node_id, _process_owner
    _cached_node_id = None
    _process_owner = None


def process_owner_id() -> str:
    """A lease belongs to one process launch, not every worker on its node."""
    global _process_owner
    pid = os.getpid()
    if _process_owner is None or _process_owner[0] != pid:
        _process_owner = (pid, f"{current_node_id()}:{pid}:{uuid.uuid4().hex}")
    return _process_owner[1]


def lease_seconds() -> int:
    raw = (os.environ.get("FLOCKS_TASK_LEASE_SECONDS") or "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            log.warn("task.node.invalid_lease_seconds", {"value": raw})
    return DEFAULT_LEASE_SECONDS


def heartbeat_seconds() -> int:
    raw = (os.environ.get("FLOCKS_TASK_HEARTBEAT_SECONDS") or "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            log.warn("task.node.invalid_heartbeat_seconds", {"value": raw})
    # Keep the renewal comfortably inside the lease even if the lease was
    # configured shorter than the default heartbeat.
    return max(1, min(DEFAULT_HEARTBEAT_SECONDS, lease_seconds() // 3 or 1))


__all__ = [
    "DEFAULT_HEARTBEAT_SECONDS",
    "DEFAULT_LEASE_SECONDS",
    "ENV_NODE_ID",
    "current_node_id",
    "process_owner_id",
    "heartbeat_seconds",
    "lease_seconds",
    "reset_node_id_cache",
]
