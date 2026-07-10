"""
Event routes for Server-Sent Events (SSE)

Compatible with Flocks TypeScript API.
Provides real-time event streaming to TUI clients.

Flocks expects GlobalEvent format:
{
    "directory": string,  // Project directory
    "payload": Event      // The actual event
}
"""

import asyncio
import json
import os
from collections import deque
from typing import AsyncGenerator, Optional, TYPE_CHECKING, cast
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from flocks.server.auth import require_user
from flocks.utils.log import Log
from flocks.utils.id import Identifier

if TYPE_CHECKING:
    from flocks.auth.context import AuthUser
    from flocks.session.session import SessionInfo


router = APIRouter()
log = Log.create(service="event-routes")

RUNTIME_EVENT_PREFIXES = (
    "runtime.",
    "turn.",
    "context.",
    "permission.",
)
EVENT_QUEUE_MAXSIZE = max(100, int(os.getenv("FLOCKS_EVENT_QUEUE_MAXSIZE", "1000")))
EVENT_QUEUE_DROP_TO = max(0, min(
    EVENT_QUEUE_MAXSIZE - 1,
    int(os.getenv("FLOCKS_EVENT_QUEUE_DROP_TO", str(EVENT_QUEUE_MAXSIZE // 2))),
))


# Current directory context for SSE events
_current_directory: str = os.getcwd()


def _event_session_id(event: dict) -> Optional[str]:
    """Extract the session id from the event shapes emitted by the runtime."""
    properties = event.get("properties")
    if not isinstance(properties, dict):
        return None

    for key in ("sessionID", "session_id"):
        value = properties.get(key)
        if isinstance(value, str) and value:
            return value

    for container_name in ("part", "info"):
        container = properties.get(container_name)
        if not isinstance(container, dict):
            continue
        for key in ("sessionID", "session_id"):
            value = container.get(key)
            if isinstance(value, str) and value:
                return value

    event_type = event.get("type")
    runtime_type = properties.get("runtimeType")
    semantic_type = runtime_type if event_type == "runtime.event" else event_type
    if isinstance(semantic_type, str) and semantic_type.startswith("session."):
        value = properties.get("id")
        if isinstance(value, str) and value:
            return value
        info = properties.get("info")
        if isinstance(info, dict):
            value = info.get("id")
            if isinstance(value, str) and value:
                return value
    return None


async def _get_event_session(session_id: str) -> Optional["SessionInfo"]:
    """Load a session without inheriting the publisher's auth context."""
    from flocks.auth.context import reset_current_auth_user, set_current_auth_user
    from flocks.session.session import Session, SessionInfo
    from flocks.storage.storage import Storage

    token = set_current_auth_user(None)
    try:
        session = await Session.get_by_id(session_id)
        if session is not None:
            return session

        cached_sessions = getattr(Session, "_all_sessions_cache", None) or []
        cached = next((item for item in cached_sessions if item.id == session_id), None)
        if cached is not None:
            return cached

        for key in await Storage.list_keys(prefix="session:"):
            if key.endswith(f":{session_id}"):
                return await Storage.get(key, SessionInfo)
        return None
    finally:
        reset_current_auth_user(token)


def _snapshot_key(event: dict) -> Optional[tuple[str, str, str]]:
    """Return the identity of a coalescible accumulated-text snapshot."""
    if event.get("type") != "message.part.updated":
        return None
    properties = event.get("properties")
    part = properties.get("part") if isinstance(properties, dict) else None
    if not isinstance(part, dict) or part.get("type") not in {"text", "reasoning", "thinking"}:
        return None
    session_id = part.get("sessionID")
    message_id = part.get("messageID")
    part_id = part.get("id")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(message_id, str) or not message_id:
        return None
    if not isinstance(part_id, str) or not part_id:
        return None
    return session_id, message_id, part_id


def _merge_snapshots(previous: dict, current: dict) -> dict:
    """Keep the latest full part while preserving all unconsumed deltas."""
    previous_properties = previous.get("properties")
    current_properties = current.get("properties")
    if not isinstance(previous_properties, dict) or not isinstance(current_properties, dict):
        return current

    previous_delta = previous_properties.get("delta")
    current_delta = current_properties.get("delta")
    if not isinstance(previous_delta, str):
        return current

    merged_properties = dict(current_properties)
    merged_properties["delta"] = previous_delta + (current_delta if isinstance(current_delta, str) else "")
    return {**current, "properties": merged_properties}


class EventQueue(asyncio.Queue[dict]):
    """Queue with slow-client compaction for accumulated part snapshots."""

    def __init__(self, maxsize: int = 0) -> None:
        super().__init__(maxsize=maxsize)
        self._pending_drop_count = 0

    def _pending_events(self) -> deque[dict]:
        return cast(deque[dict], self.__dict__["_queue"])

    def coalesce_snapshot(self, event: dict) -> bool:
        key = _snapshot_key(event)
        pending = self._pending_events()
        if key is None or not pending:
            return False
        for index in range(len(pending) - 1, -1, -1):
            queued = pending[index]
            if _snapshot_key(queued) != key:
                continue
            del pending[index]
            pending.append(_merge_snapshots(queued, event))
            return True
        return False

    def drop_snapshots_until(self, target_size: int) -> int:
        pending = self._pending_events()
        dropped = 0
        while len(pending) > target_size:
            snapshot_index = next(
                (index for index, queued in enumerate(pending) if _snapshot_key(queued) is not None),
                None,
            )
            if snapshot_index is None:
                break
            del pending[snapshot_index]
            dropped += 1
        return dropped

    def report_dropped(self, count: int) -> None:
        """Emit a recovery marker now or defer it until capacity is freed."""
        if count <= 0:
            return
        self._pending_drop_count += count
        self._flush_drop_marker()

    def _flush_drop_marker(self) -> None:
        if self._pending_drop_count <= 0 or self.full():
            return
        dropped = self._pending_drop_count
        self._pending_drop_count = 0
        super().put_nowait(create_event("server.events_dropped", {
            "dropped": dropped,
            "reason": "client_backpressure",
        }))

    async def get(self) -> dict:
        event = await super().get()
        # A queue containing only control events has no safe item to evict for
        # a marker. Once the client consumes one, append the deferred marker
        # without sacrificing those control events.
        self._flush_drop_marker()
        return event


def set_event_directory(directory: str):
    """Set the current directory for SSE events"""
    global _current_directory
    _current_directory = directory


def get_event_directory() -> str:
    """Get the current directory for SSE events"""
    return _current_directory


# Global event queue for broadcasting
class EventBroadcaster:
    """Broadcast events to all connected SSE clients"""
    
    _instance: Optional["EventBroadcaster"] = None
    
    def __init__(self, queue_maxsize: int = EVENT_QUEUE_MAXSIZE, queue_drop_to: Optional[int] = None) -> None:
        self._clients: dict[EventQueue, Optional["AuthUser"]] = {}
        self._lock = asyncio.Lock()
        self._queue_maxsize = max(1, queue_maxsize)
        self._queue_drop_to = max(
            0,
            min(
                self._queue_maxsize - 1,
                EVENT_QUEUE_DROP_TO if queue_drop_to is None else queue_drop_to,
            ),
        )
    
    @classmethod
    def get(cls) -> "EventBroadcaster":
        """Get singleton instance"""
        if cls._instance is None:
            cls._instance = EventBroadcaster()
        return cls._instance
    
    async def subscribe(self, user: Optional["AuthUser"] = None) -> EventQueue:
        """Subscribe a new client"""
        queue = EventQueue(maxsize=self._queue_maxsize)
        async with self._lock:
            self._clients[queue] = user
        return queue
    
    async def unsubscribe(self, queue: EventQueue) -> None:
        """Unsubscribe a client"""
        async with self._lock:
            self._clients.pop(queue, None)
    
    async def publish(self, event: dict) -> None:
        """Publish event to all clients"""
        async with self._lock:
            clients = list(self._clients.items())

        session = None
        session_id = _event_session_id(event)
        if session_id:
            try:
                session = await _get_event_session(session_id)
            except Exception as exc:
                log.warning("event.session_access_lookup_failed", {
                    "session_id": session_id,
                    "event_type": event.get("type"),
                    "error": str(exc),
                })
                return
            if session is None:
                log.debug("event.session_access_missing", {
                    "session_id": session_id,
                    "event_type": event.get("type"),
                })
                return

        if session is not None:
            from flocks.session.policy import SessionPolicy

        for queue, user in clients:
            if session is not None and user is not None and not SessionPolicy.can_read(session, user):
                continue
            self._publish_to_queue(queue, event)

    def _publish_to_queue(self, queue: EventQueue, event: dict) -> None:
        if queue.coalesce_snapshot(event):
            return

        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass

        is_snapshot = _snapshot_key(event) is not None
        marker_target = min(self._queue_drop_to, max(0, self._queue_maxsize - 2))
        dropped = queue.drop_snapshots_until(marker_target)

        if is_snapshot and queue.qsize() > marker_target:
            # A text snapshot must never evict permission/question/session
            # control events. If removing older snapshots only leaves room for
            # the recovery marker, keep that marker and discard this ordinary
            # snapshot so the client knows it must reconcile over REST.
            # Include the incoming snapshot itself. If the queue consists only
            # of control events, defer the marker until the client frees one
            # slot rather than evicting a permission/question/session event.
            dropped += 1
            queue.report_dropped(dropped)
            log.debug("event.queue.snapshot_dropped", {
                "queue_size": queue.qsize(),
                "queue_maxsize": self._queue_maxsize,
                "event_type": event.get("type"),
            })
            return
        else:
            while queue.qsize() > marker_target:
                try:
                    queue.get_nowait()
                    dropped += 1
                except asyncio.QueueEmpty:
                    break

        reported_drops = 0
        if dropped > 0 and queue.qsize() <= self._queue_maxsize - 2:
            queue.report_dropped(dropped)
            reported_drops = dropped

        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            dropped += 1

        if dropped > reported_drops:
            queue.report_dropped(dropped - reported_drops)

        if dropped > 0:
            log.debug("event.queue.overflow", {
                "dropped": dropped,
                "queue_size": queue.qsize(),
                "queue_maxsize": self._queue_maxsize,
                "event_type": event.get("type"),
            })
    
    @property
    def client_count(self) -> int:
        """Number of connected clients"""
        return len(self._clients)

    async def shutdown(self) -> None:
        """Notify all clients that the server is shutting down, then clear."""
        shutdown_event = create_event("server.shutting_down", {})
        async with self._lock:
            for queue in self._clients:
                self._publish_to_queue(queue, shutdown_event)
            self._clients.clear()
        log.info("event.broadcaster.shutdown", {"clients_notified": True})


def create_event(event_type: str, properties: dict = None) -> dict:
    """
    Create an event object in direct Event format.
    
    TUI SDK expects direct Event format for /event endpoint:
    {
        "type": string,
        "properties": object
    }
    """
    return {
        "type": event_type,
        "properties": properties or {},
    }


def wrap_global_event(event: dict, directory: str = None) -> dict:
    """
    Wrap an event in GlobalEvent format for /global/event endpoint.
    
    GlobalEvent format:
    {
        "directory": string,
        "payload": Event
    }
    """
    return {
        "directory": directory or get_event_directory(),
        "payload": event,
    }


def is_runtime_event(event_type: str) -> bool:
    return event_type == "runtime.event" or any(
        event_type.startswith(prefix) for prefix in RUNTIME_EVENT_PREFIXES
    )


def create_runtime_event(runtime_type: str, properties: dict = None) -> dict:
    return create_event("runtime.event", {
        "runtimeType": runtime_type,
        **(properties or {}),
    })


# Helper to publish events
async def publish_event(event_type: str, properties: dict = None, directory: str = None):
    """
    Publish an event to all SSE clients.
    
    Events are sent in direct Event format (type + properties) for TUI compatibility.
    The /event endpoint expects direct events, not wrapped in GlobalEvent.
    """
    event = create_event(event_type, properties)
    broadcaster = EventBroadcaster.get()
    
    # Debug: 记录事件发布
    if event_type == "message.part.updated":
        text_len = properties.get("part", {}).get("text", "") if properties else ""
        delta = properties.get("delta", "") if properties else ""
        log.debug("event.publish.part_updated", {
            "clients": broadcaster.client_count,
            "text_length": len(text_len) if text_len else 0,
            "delta_length": len(delta) if delta else 0,
        })
    
    # Send direct event format for /event endpoint compatibility
    await broadcaster.publish(event)
    if is_runtime_event(event_type) and event_type != "runtime.event":
        await broadcaster.publish(create_runtime_event(event_type, properties))


async def publish_runtime_event(runtime_type: str, properties: dict = None, directory: str = None):
    """Publish runtime semantic event on the normalized runtime channel."""
    broadcaster = EventBroadcaster.get()
    await broadcaster.publish(create_runtime_event(runtime_type, properties))


async def sse_generator(
    queue: EventQueue,
    request: Request,
    directory: str = None,
) -> AsyncGenerator[str, None]:
    """
    Generate SSE events in direct Event format.
    
    TUI SDK expects direct Event format:
    {
        "type": string,
        "properties": object
    }
    """
    try:
        # Send initial connection event in direct Event format
        init_event = create_event("server.connected", {})
        yield f"data: {json.dumps(init_event)}\n\n"
        
        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break
            
            try:
                # Wait for event with timeout
                # Events from publish_event are already in direct Event format
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "server.shutting_down":
                    break
            except asyncio.TimeoutError:
                # Send heartbeat in direct Event format (matches Flocks)
                heartbeat = create_event("server.heartbeat", {})
                yield f"data: {json.dumps(heartbeat)}\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        await EventBroadcaster.get().unsubscribe(queue)


@router.get(
    "",
    summary="Subscribe to events",
    description="Subscribe to server-sent events (SSE) stream"
)
async def subscribe_events(request: Request):
    """
    Subscribe to SSE event stream
    
    Returns:
        StreamingResponse with SSE events
    """
    user = require_user(request)
    queue = await EventBroadcaster.get().subscribe(user)
    
    log.info("event.subscribe", {
        "clients": EventBroadcaster.get().client_count,
    })
    
    return StreamingResponse(
        sse_generator(queue, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# Export for use in other modules
__all__ = [
    "router", 
    "publish_event", 
    "publish_runtime_event",
    "EventBroadcaster",
    "create_event",
    "create_runtime_event",
    "wrap_global_event",
    "is_runtime_event",
    "set_event_directory",
    "get_event_directory",
    "sse_generator",
]
