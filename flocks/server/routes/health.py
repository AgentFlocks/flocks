"""
Health check routes
"""

from fastapi import APIRouter, status
from pydantic import BaseModel
from datetime import datetime

from flocks.config.config import Config


router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    version: str
    timestamp: str
    config_dir: str
    data_dir: str
    task_manager_started: bool
    task_scheduler_running: bool
    task_scheduler_available: bool
    task_manager_error: str | None = None


@router.get(
    "/health",
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
    config = Config.get_global()
    from flocks.task.manager import TaskManager
    task_status = TaskManager.runtime_status()
    
    from flocks.updater import get_current_version
    return HealthResponse(
        status="healthy",
        version=get_current_version(),
        timestamp=datetime.now(UTC).isoformat(),
        config_dir=str(config.config_dir),
        data_dir=str(config.data_dir),
        **task_status,
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
