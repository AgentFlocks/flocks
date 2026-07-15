"""
Global routes for Flocks TUI compatibility

Provides /global/* endpoints that Flocks SDK expects.

Flocks expects health response:
{
    "healthy": true,
    "version": "x.x.x"
}
"""

import asyncio
import json
from typing import AsyncGenerator, Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from flocks.utils.log import Log
from flocks.server.routes.event import EventBroadcaster, sse_generator


router = APIRouter()
log = Log.create(service="global-routes")


class HealthResponse(BaseModel):
    healthy: Literal[True] = True
    version: str = "unknown"


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Get health",
    description="Get health information about the Flocks server"
)
async def get_health() -> HealthResponse:
    """Health check endpoint for Flocks TUI"""
    from flocks.updater import get_current_version
    return HealthResponse(version=get_current_version())


@router.get(
    "/event",
    summary="Get global events",
    description="Subscribe to global events using server-sent events"
)
async def get_global_events(request: Request):
    """
    Subscribe to global SSE event stream
    
    This is the main event endpoint that Flocks TUI uses.
    """
    queue = await EventBroadcaster.get().subscribe()
    
    log.info("global.event.subscribe", {
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


@router.post(
    "/dispose",
    summary="Dispose instance",
    description="Clean up and dispose all Flocks instances"
)
async def dispose_global():
    """Dispose all instances"""
    log.info("global.dispose")
    return {"success": True}
