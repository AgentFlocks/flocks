"""Which process owns a workflow run.

One process runs an installation's workflows, so a run is never taken over
by another one.  This module keeps that process's own book of the runs it
started (``create_execution_record``) and has not yet recorded as ended
(``record_execution_result``), each with a cancel event that the code running
it checks between nodes through ``execution_cancelled``.  A cancel request
(``request_cancel``), an owner that stops waiting (``abandon_execution``) and
shutdown set that event.

A database backend whose processes share the workflow store replaces this
module (``flocks.db_backend``) with leases that the owner renews while the run
goes on.  Note the difference in ``execution_cancelled``: there, a run this
process holds no lease for counts as cancelled; here a run this process never
started is not cancelled by it.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

from flocks.utils.log import Log

log = Log.create(service="workflow.ownership")

#: Abandoned runs whose worker thread may still be going are kept so the
#: thread keeps seeing the cancellation at its next node: for a day (a node
#: runs far shorter), and at most this many, the oldest going first.
_ABANDONED_KEEP_SECONDS = 24 * 3600
_ABANDONED_KEPT = 65536

_lock = threading.Lock()
_live: dict[str, threading.Event] = {}
_abandoned: OrderedDict[str, float] = OrderedDict()  # id -> when it was let go (monotonic)


class WorkflowOwnershipError(RuntimeError):
    """The caller cannot start or persist this workflow execution."""


class WorkflowAlreadyRunning(WorkflowOwnershipError):
    """Another process owns the requested no-overlap scope."""


def start_renewal(exec_data: dict[str, Any], *, duration: int | None = None, claimed_at: float | None = None) -> None:
    """Note a run this process started: it is live here until it is recorded as ended."""
    execution_id = str(exec_data["id"])
    with _lock:
        _abandoned.pop(execution_id, None)
        _live[execution_id] = threading.Event()


def running_here(execution_id: str) -> bool:
    """Whether this process started the run and has not seen it end or let it go."""
    with _lock:
        return execution_id in _live


def execution_cancelled(execution_id: str) -> bool:
    """Whether the run was asked to stop (thread-safe, checked between nodes)."""
    with _lock:
        event = _live.get(execution_id)
        return (event is not None and event.is_set()) or execution_id in _abandoned


def request_cancel(execution_id: str) -> bool:
    """Ask a live run of this process to stop at its next node."""
    with _lock:
        event = _live.get(execution_id)
    if event is None:
        return False
    event.set()
    return True


def owned_summary(exec_data: dict[str, Any]) -> dict[str, Any]:
    return dict(exec_data)


async def stop_renewal(execution_id: str) -> None:
    """The run's end is recorded: it is no longer live here."""
    with _lock:
        _live.pop(execution_id, None)


def _let_go(execution_id: str) -> bool:
    with _lock:
        event = _live.pop(execution_id, None)
        if event is None:
            return False
        event.set()
        now = time.monotonic()
        _abandoned[execution_id] = now
        _abandoned.move_to_end(execution_id)
        while _abandoned and (
            len(_abandoned) > _ABANDONED_KEPT
            or now - next(iter(_abandoned.values())) > _ABANDONED_KEEP_SECONDS
        ):
            _abandoned.popitem(last=False)
        return True


async def abandon_execution(execution_id: str) -> None:
    """The owner stopped waiting for the run: stop it and record it as cancelled.

    Nothing else would end the record: the run's thread may still finish its
    current node, but it stops there and nobody writes its result.  A run whose
    end is already recorded is left as it is.
    """
    if not _let_go(execution_id):
        return
    from flocks.workflow.store import WorkflowStore

    try:
        await WorkflowStore.end_abandoned_execution(execution_id)
    except Exception as exc:
        # Startup recovery finalizes the record if this write is lost.
        log.warning("workflow.abandon.record_failed", {"exec_id": execution_id, "error": str(exc)})


async def close_execution_leases() -> None:
    """Shutdown: ask every live run to stop; startup recovery ends their records."""
    with _lock:
        execution_ids = list(_live)
    for execution_id in execution_ids:
        _let_go(execution_id)
