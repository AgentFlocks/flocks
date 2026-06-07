"""In-memory prompt queue for non-blocking session interaction."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from flocks.utils.id import Identifier


MAX_QUEUE_SIZE = 50


class QueueFullError(Exception):
    """Raised when a session prompt queue reaches its configured limit."""


class QueueItemNotFoundError(Exception):
    """Raised when a queued prompt cannot be found."""


class QueuedPrompt(BaseModel):
    id: str = Field(default_factory=lambda: Identifier.create("part"))
    sessionID: str
    parts: List[Dict[str, Any]] = Field(default_factory=list)
    agent: Optional[str] = None
    model: Optional[Dict[str, Any]] = None
    variant: Optional[str] = None
    messageID: Optional[str] = None
    noReply: Optional[bool] = None
    mockReply: Optional[str] = None
    tools: Optional[Dict[str, bool]] = None
    system: Optional[str] = None
    loop_engine: Optional[str] = None
    status: str = "pending"
    createdAt: int = Field(default_factory=lambda: int(time.time() * 1000))
    updatedAt: int = Field(default_factory=lambda: int(time.time() * 1000))


class InteractionQueue:
    """Process-local per-session FIFO prompt queues."""

    _queues: Dict[str, List[QueuedPrompt]] = {}
    _locks: Dict[str, asyncio.Lock] = {}

    @classmethod
    def _lock_for(cls, session_id: str) -> asyncio.Lock:
        lock = cls._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            cls._locks[session_id] = lock
        return lock

    @classmethod
    async def enqueue(
        cls,
        session_id: str,
        *,
        parts: List[Dict[str, Any]],
        agent: Optional[str] = None,
        model: Optional[Dict[str, Any]] = None,
        variant: Optional[str] = None,
        message_id: Optional[str] = None,
        no_reply: Optional[bool] = None,
        mock_reply: Optional[str] = None,
        tools: Optional[Dict[str, bool]] = None,
        system: Optional[str] = None,
        loop_engine: Optional[str] = None,
    ) -> QueuedPrompt:
        async with cls._lock_for(session_id):
            queue = cls._queues.setdefault(session_id, [])
            if len(queue) >= MAX_QUEUE_SIZE:
                raise QueueFullError(f"Session {session_id} prompt queue is full")

            item = QueuedPrompt(
                sessionID=session_id,
                parts=[dict(part) for part in parts],
                agent=agent,
                model=dict(model) if isinstance(model, dict) else model,
                variant=variant,
                messageID=message_id,
                noReply=no_reply,
                mockReply=mock_reply,
                tools=dict(tools) if tools else None,
                system=system,
                loop_engine=loop_engine,
            )
            queue.append(item)
            return item

    @classmethod
    async def list(cls, session_id: str) -> List[QueuedPrompt]:
        async with cls._lock_for(session_id):
            return [item.model_copy(deep=True) for item in cls._queues.get(session_id, [])]

    @classmethod
    async def update_text(cls, session_id: str, item_id: str, text: str) -> QueuedPrompt:
        async with cls._lock_for(session_id):
            item = cls._find_locked(session_id, item_id)
            if item.status == "executing":
                raise QueueItemNotFoundError(f"Queued prompt {item_id} is already executing")

            parts: List[Dict[str, Any]] = []
            replaced = False
            for part in item.parts:
                if part.get("type") == "text" and not replaced:
                    next_part = dict(part)
                    next_part["text"] = text
                    parts.append(next_part)
                    replaced = True
                elif part.get("type") != "text":
                    parts.append(dict(part))
            if not replaced:
                parts.insert(0, {"type": "text", "text": text})

            item.parts = parts
            item.updatedAt = int(time.time() * 1000)
            return item.model_copy(deep=True)

    @classmethod
    async def remove(cls, session_id: str, item_id: str) -> QueuedPrompt:
        async with cls._lock_for(session_id):
            queue = cls._queues.get(session_id, [])
            for idx, item in enumerate(queue):
                if item.id == item_id:
                    return queue.pop(idx)
            raise QueueItemNotFoundError(f"Queued prompt {item_id} not found")

    @classmethod
    async def pop_next(cls, session_id: str) -> Optional[QueuedPrompt]:
        async with cls._lock_for(session_id):
            queue = cls._queues.get(session_id, [])
            if not queue:
                return None
            item = queue.pop(0)
            item.status = "executing"
            item.updatedAt = int(time.time() * 1000)
            return item

    @classmethod
    async def promote(cls, session_id: str, item_id: str) -> QueuedPrompt:
        async with cls._lock_for(session_id):
            queue = cls._queues.get(session_id, [])
            for idx, item in enumerate(queue):
                if item.id == item_id:
                    if item.status == "executing":
                        raise QueueItemNotFoundError(f"Queued prompt {item_id} is already executing")
                    promoted = queue.pop(idx)
                    promoted.updatedAt = int(time.time() * 1000)
                    queue.insert(0, promoted)
                    return promoted.model_copy(deep=True)
            raise QueueItemNotFoundError(f"Queued prompt {item_id} not found")

    @classmethod
    async def clear(cls, session_id: str) -> None:
        async with cls._lock_for(session_id):
            cls._queues.pop(session_id, None)

    @classmethod
    def _find_locked(cls, session_id: str, item_id: str) -> QueuedPrompt:
        for item in cls._queues.get(session_id, []):
            if item.id == item_id:
                return item
        raise QueueItemNotFoundError(f"Queued prompt {item_id} not found")
