"""
Hook management routes

Provides API endpoints for hook system management and monitoring.
"""

from typing import Dict, Any
from fastapi import APIRouter
from pydantic import BaseModel, Field

from flocks.hooks import get_hook_stats
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="hooks-routes")


class HookStatsResponse(BaseModel):
    """Hook statistics response"""
    total_event_keys: int = Field(..., description="Total number of event keys")
    total_handlers: int = Field(..., description="Total number of handlers")
    event_keys: Dict[str, Any] = Field(..., description="Event keys and their handlers")


@router.get(
    "/stats",
    response_model=HookStatsResponse,
    summary="Get hook statistics",
    description="Get statistics about registered hooks",
)
async def get_hooks_stats() -> HookStatsResponse:
    """Get hook system statistics"""
    stats = get_hook_stats()
    
    log.debug("hooks.stats.requested", {
        "total_handlers": stats["total_handlers"],
    })
    
    return HookStatsResponse(**stats)


@router.get(
    "/status",
    summary="Get hook system status",
    description="Get hook system status and configuration",
)
async def get_hooks_status() -> Dict[str, Any]:
    """Get hook system status"""
    from flocks.config import Config
    
    try:
        config = await Config.get()
        memory_config = config.memory
        
        # Get stats
        stats = get_hook_stats()
        
        return {
            "enabled": getattr(memory_config, "enabled", False),
            "stats": stats,
        }
        
    except Exception as e:
        log.error("hooks.status.error", {"error": str(e)})
        return {
            "enabled": False,
            "error": str(e),
        }
