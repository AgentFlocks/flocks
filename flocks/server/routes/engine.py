"""
Engine Router — GET /api/engine/list

Returns all available agent loop engines.  'native' is always the first entry;
additional engines appear after it in registration order.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/list", summary="List available agent loop engines")
async def list_engines():
    """
    Return all registered agent loop engines.

    Response format:
    [
      {"id": "native", "name": "Flocks Native", "description": "..."},
      {"id": "raptor", "name": "Raptor",         "description": "..."},  // P2+
    ]
    """
    from flocks.engine import LoopEngineRegistry, NATIVE_ENGINE_META
    return [NATIVE_ENGINE_META] + LoopEngineRegistry.list()
