"""Persist model activity at stream boundaries, never on token/timer ticks."""

from contextlib import asynccontextmanager
import time
from typing import Any, Awaitable, Callable, Optional

from flocks.session.message import Message, PartTime, StepStartPart
from flocks.utils.log import Log

log = Log.create(service="session.model_activity")


class ModelActivity:
    def __init__(
        self,
        session_id: str,
        message_id: str,
        publish: Optional[Callable[[str, dict[str, Any]], Awaitable[None]]],
    ):
        self.session_id = session_id
        self.message_id = message_id
        self.publish = publish
        self.current: Optional[StepStartPart] = None

    async def _save(self, part: StepStartPart) -> None:
        # Timing is observational: failures must not fail/replay a model/tool call.
        try:
            await Message.store_part(self.session_id, self.message_id, part)
        except Exception as exc:
            log.warn("persist_failed", {"error": str(exc)})
        try:
            if self.publish:
                await self.publish("message.part.updated", {
                    "part": part.model_dump(mode="json", exclude_none=True),
                })
        except Exception as exc:
            log.warn("publish_failed", {"error": str(exc)})

    async def start(self) -> None:
        if self.current is not None:
            return
        self.current = StepStartPart(
            sessionID=self.session_id,
            messageID=self.message_id,
            time=PartTime(start=int(time.time() * 1000)),
        )
        await self._save(self.current)

    async def stop(self) -> None:
        part = self.current
        if part is None:
            return
        # Do not mutate the cached open part before Message.store_part sees it.
        closed = part.model_copy(update={
            "time": PartTime(start=part.time.start, end=max(part.time.start, int(time.time() * 1000))),
        })
        await self._save(closed)
        self.current = None

    @asynccontextmanager
    async def suspend(self):
        """Synchronous tool execution/approval is not model-stream activity."""
        resume = self.current is not None
        await self.stop()
        yield
        # Exceptions/cancellation unwind to the runner; never reopen on failure.
        if resume:
            await self.start()
