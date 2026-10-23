"""
Health check routes
"""

from fastapi import APIRouter, status
from pydantic import BaseModel
from datetime import datetime


router = APIRouter()
# ``/health`` without the ``/api`` prefix: listed in ``server/auth.py``
# PUBLIC_PATHS (and reserved by the static WebUI) for load balancers and
# container probes configured without the prefix.
public_router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    version: str
    timestamp: str


@router.api_route(
    "/health",
    methods=["GET", "HEAD"],  # HEAD for probes that only look at the status line
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health check",
    description="Check if the server is running and healthy"
)
async def health_check() -> HealthResponse:
    """
    Health check endpoint
    
    Returns server status and basic information
    """
    from datetime import UTC
    
    from flocks.updater import get_current_version
    return HealthResponse(
        status="healthy",
        version=get_current_version(),
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.get(
    "/ping",
    status_code=status.HTTP_200_OK,
    summary="Ping",
    description="Simple ping endpoint"
)
async def ping() -> dict:
    """Simple ping endpoint"""
    return {"message": "pong"}


@public_router.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse, summary="Health check (unprefixed alias)")
async def health_check_unprefixed() -> HealthResponse:
    return await health_check()
