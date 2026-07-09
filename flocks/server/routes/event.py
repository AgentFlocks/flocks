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
from typing import AsyncGenerator, Optional
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from flocks.utils.log import Log
from flocks.utils.id import Identifier


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
    
    def __init__(self, queue_maxsize: int = EVENT_QUEUE_MAXSIZE, queue_drop_to: Optional[int] = None):
        self._clients: list[asyncio.Queue] = []
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
    
    async def subscribe(self) -> asyncio.Queue:
        """Subscribe a new client"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        async with self._lock:
            self._clients.append(queue)
        return queue
    
    async def unsubscribe(self, queue: asyncio.Queue):
        """Unsubscribe a client"""
        async with self._lock:
            if queue in self._clients:
                self._clients.remove(queue)
    
    async def publish(self, event: dict):
        """Publish event to all clients"""
        async with self._lock:
            clients = list(self._clients)
        for queue in clients:
            self._publish_to_queue(queue, event)

    def _publish_to_queue(self, queue: asyncio.Queue, event: dict):
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass

        dropped = 0
        while queue.qsize() > self._queue_drop_to:
            try:
                queue.get_nowait()
                dropped += 1
            except asyncio.QueueEmpty:
                break

        try:
            queue.put_nowait(create_event("server.events_dropped", {
                "dropped": dropped,
                "reason": "client_backpressure",
            }))
        except asyncio.QueueFull:
            pass

        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            dropped += 1

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

    async def shutdown(self):
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
    queue: asyncio.Queue, 
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
    queue = await EventBroadcaster.get().subscribe()
    
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
