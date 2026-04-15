"""Priority execution queue."""

import asyncio
from typing import Optional

from flocks.utils.log import Log

from .models import TaskExecution
from .store import TaskStore

log = Log.create(service="task.queue")


class TaskQueue:
    def __init__(self, max_concurrent: int = 4):
        self.max_concurrent = max_concurrent
        self._paused = False
        self._running_ids: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def paused(self) -> bool:
        return self._paused

    async def dequeue(self) -> Optional[TaskExecution]:
        async with self._lock:
            if self._paused:
                return None
            # Treat claimed-but-not-yet-started executions as occupying slots too,
            # otherwise a slow session bootstrap can temporarily exceed concurrency.
            if len(self._running_ids) >= self.max_concurrent:
                return None
            claimed = await TaskStore.claim_next_queue_execution(
                exclude_ids=list(self._running_ids)
            )
            if not claimed:
                return None
            execution, _ = claimed
            self._running_ids.add(execution.id)
            return execution

    def mark_started(self, execution_id: str) -> None:
        self._running_ids.add(execution_id)

    def mark_finished(self, execution_id: str) -> None:
        self._running_ids.discard(execution_id)

    async def pending_count(self) -> int:
        return await TaskStore.count_queued_refs()

    def pause(self) -> None:
        self._paused = True
        log.info("queue.paused")

    def resume(self) -> None:
        self._paused = False
        log.info("queue.resumed")

    async def status(self) -> dict:
        running = await TaskStore.count_running()
        return {
            "paused": self._paused,
            "max_concurrent": self.max_concurrent,
            "running": max(running, len(self._running_ids)),
            "queued": await self.pending_count(),
        }
