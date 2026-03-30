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
    
    def __init__(self):
        self._clients: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()
    
    @classmethod
    def get(cls) -> "EventBroadcaster":
        """Get singleton instance"""
        if cls._instance is None:
            cls._instance = EventBroadcaster()
        return cls._instance
    
    async def subscribe(self) -> asyncio.Queue:
        """Subscribe a new client"""
        queue: asyncio.Queue = asyncio.Queue()
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
            for queue in self._clients:
                try:
                    await queue.put(event)
                except Exception:
                    pass  # Ignore errors for disconnected clients
    
    @property
    def client_count(self) -> int:
        """Number of connected clients"""
        return len(self._clients)

    async def shutdown(self):
        """Notify all clients that the server is shutting down, then clear."""
        shutdown_event = create_event("server.shutting_down", {})
        async with self._lock:
            for queue in self._clients:
                try:
                    await queue.put(shutdown_event)
                except Exception:
                    pass
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
    "EventBroadcaster",
    "create_event",
    "wrap_global_event",
    "set_event_directory",
    "get_event_directory",
    "sse_generator",
]
