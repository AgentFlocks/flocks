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
        
        # Get hook configuration
        hooks_config = getattr(memory_config, 'hooks', {})
        session_memory_config = getattr(hooks_config, 'session_memory', {})
        
        # Get stats
        stats = get_hook_stats()
        
        return {
            "enabled": memory_config.enabled,
            "session_memory": {
                "enabled": getattr(session_memory_config, 'enabled', False),
                "message_count": getattr(session_memory_config, 'message_count', 15),
                "use_llm_slug": getattr(session_memory_config, 'use_llm_slug', True),
                "slug_timeout": getattr(session_memory_config, 'slug_timeout', 15),
            },
            "stats": stats,
        }
        
    except Exception as e:
        log.error("hooks.status.error", {"error": str(e)})
        return {
            "enabled": False,
            "error": str(e),
        }
