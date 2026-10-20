"""
Holding a task execution's lease while it runs.

Claiming once is not enough: a claim that never expires is indistinguishable
from an abandoned one, and a node that dies mid-execution would keep its work
locked forever.  So the owner renews on a timer, and the absence of renewals is
what tells the other node it may take over.

The renewal loop also watches for losing the claim.  If a renewal is refused --
another node took over during a long stall -- there are now two nodes that think
they own this execution.  Learning that promptly is the difference between
noticing the split and silently writing conflicting results.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from flocks.task.node import process_owner_id, heartbeat_seconds, lease_seconds
from flocks.utils.log import Log

log = Log.create(service="task.lease")
_current_lease: contextvars.ContextVar[Optional["ExecutionLease"]] = contextvars.ContextVar(
    "task_execution_lease", default=None,
)


def current_execution_cancelled() -> bool:
    """Copied into workflow threads by asyncio.to_thread; checked before nodes."""
    lease = _current_lease.get()
    return lease is not None and lease.cancelled()


def require_current_execution() -> None:
    """Fail synchronously at an execution boundary, even before timers run."""
    lease = _current_lease.get()
    if lease is not None:
        lease.require_owned()


@contextmanager
def outside_task_execution() -> Iterator[None]:
    """Start work that outlives the task execution it was started from.

    The task counterpart of ``flocks.session.ownership.outside_execution``:
    a task created inside keeps running after that execution's lease ends.
    """
    previous = _current_lease.set(None)
    try:
        yield
    finally:
        _current_lease.reset(previous)


class ExecutionOwnershipError(RuntimeError):
    """This dispatcher has no authority to run or finish the execution."""


class ExecutionLease:
    """Async context manager that holds one execution's lease while it runs.

    Loss stops subsequent work. Cancellation cannot undo a completed external
    effect, so the final database write must still check the ownership token.
    """

    def __init__(
        self,
        execution_id: str,
        *,
        node_id: Optional[str] = None,
        lease_s: Optional[int] = None,
        heartbeat_s: Optional[int] = None,
    ) -> None:
        self.execution_id = execution_id
        self.node_id = node_id or process_owner_id()
        self._lease_s = max(1, lease_s if lease_s is not None else lease_seconds())
        self._heartbeat_s = heartbeat_s if heartbeat_s is not None else heartbeat_seconds()
        self.generation: Optional[int] = None
        self.acquired = False
        self.lost = False
        self._task: Optional[asyncio.Task] = None
        self._deadline_task: Optional[asyncio.Task] = None
        self._owner_task: Optional[asyncio.Task] = None
        self._deadline = 0.0
        self._context_token = None
        self._stopped = False
        self._finished = False
        self._renew_lock = asyncio.Lock()

    def cancelled(self) -> bool:
        return self._stopped or self.lost or (self.acquired and time.monotonic() >= self._deadline)

    def require_owned(self) -> None:
        if not self.acquired or self.cancelled():
            self.lost = True
            raise ExecutionOwnershipError(f"execution ownership expired or superseded: {self.execution_id}")

    def _lose(self) -> None:
        if self.lost:
            return
        self.lost = True
        if self._owner_task is not None and not self._owner_task.done():
            self._owner_task.cancel("task execution lease lost")

    async def _watch_deadline(self) -> None:
        while not self._finished:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                self._lose()
                return
            await asyncio.sleep(remaining)

    async def finish_execution(self, execution):
        """Serialize the final CAS with renewals of this lease."""
        from flocks.task.store import TaskStore

        async with self._renew_lock:
            self.require_owned()
            written = await TaskStore.finish_execution(
                execution, node_id=self.node_id,
                expected_generation=self.generation or 0,
            )
            if written is not None:
                self._finished = True
            return written

    async def __aenter__(self) -> "ExecutionLease":
        from flocks.task.store import TaskStore

        started = time.monotonic()
        claimed = await TaskStore.claim_execution(
            self.execution_id,
            node_id=self.node_id,
            lease_seconds=self._lease_s,
        )
        if claimed is None:
            # The caller must check acquired before starting any work.
            log.warn(
                "task.lease.claim_refused",
                {"execution_id": self.execution_id, "node_id": self.node_id},
            )
            return self

        self.acquired = True
        self.generation = claimed.owner_generation
        # Do not add request/commit latency to the database lease duration.
        self._deadline = started + self._lease_s
        self.require_owned()
        self._owner_task = asyncio.current_task()
        self._context_token = _current_lease.set(self)
        self._task = asyncio.create_task(self._renew_loop())
        self._deadline_task = asyncio.create_task(self._watch_deadline())
        log.debug(
            "task.lease.acquired",
            {
                "execution_id": self.execution_id,
                "node_id": self.node_id,
                "generation": self.generation,
            },
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        from flocks.task.store import TaskStore

        # A cancelled to_thread await does not kill its worker. The copied
        # context keeps this object, so leaving the lease also stops its nodes.
        self._stopped = True
        tasks = [task for task in (self._task, self._deadline_task) if task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = self._deadline_task = None
        if self._context_token is not None:
            _current_lease.reset(self._context_token)
            self._context_token = None

        if self.acquired and not self.lost:
            try:
                await TaskStore.release_execution_lease(
                    self.execution_id,
                    node_id=self.node_id,
                    expected_generation=self.generation,
                )
            except Exception as release_exc:
                # Releasing is cleanup; failing here only means the lease waits
                # out its expiry instead of ending immediately.
                log.warn(
                    "task.lease.release_failed",
                    {"execution_id": self.execution_id, "error": str(release_exc)},
                )
        if self.lost and (exc_type is None or issubclass(exc_type, asyncio.CancelledError)):
            raise ExecutionOwnershipError(f"execution ownership expired or superseded: {self.execution_id}")
        return False

    async def _renew_loop(self) -> None:
        from flocks.task.store import TaskStore

        while True:
            await asyncio.sleep(min(self._heartbeat_s, self._lease_s / 3))
            try:
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    self._lose()
                    return
                started = time.monotonic()
                async with self._renew_lock:
                    if self._finished:
                        return
                    renewed = await asyncio.wait_for(
                        TaskStore.renew_execution_lease(
                            self.execution_id,
                            node_id=self.node_id,
                            generation=self.generation or 0,
                            lease_seconds=self._lease_s,
                        ),
                        timeout=remaining,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Retry a transient error only inside the locally known lease.
                log.warn(
                    "task.lease.renew_error",
                    {"execution_id": self.execution_id, "error": str(exc)},
                )
                if time.monotonic() >= self._deadline:
                    self._lose()
                    return
                continue

            if not renewed:
                self._lose()
                log.error(
                    "task.lease.lost",
                    {
                        "execution_id": self.execution_id,
                        "node_id": self.node_id,
                        "generation": self.generation,
                    },
                )
                return
            self._deadline = started + self._lease_s


__all__ = [
    "ExecutionLease", "ExecutionOwnershipError", "current_execution_cancelled",
    "outside_task_execution", "require_current_execution",
]
