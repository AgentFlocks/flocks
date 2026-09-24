"""
Health check routes
"""

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from datetime import datetime


router = APIRouter()
# ``/health`` and ``/ready`` without the ``/api`` prefix: both are listed in
# ``server/auth.py`` PUBLIC_PATHS (and reserved by the static WebUI) for
# load balancers / container probes that are configured without the prefix.
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


@router.get(
    "/ready",
    summary="Readiness check",
    description=(
        "Whether this instance is ready to take traffic. Unlike /health, which "
        "answers whether the process is up, this answers whether the extensions "
        "the deployment declared mandatory are actually loaded."
    ),
)
async def readiness_check(request: Request, response: Response) -> dict:
    """Report readiness for a load balancer to route on.

    Returns 503 when a declared requirement is unmet, so an instance that came
    up without an extension it depends on is taken out of rotation rather than
    served with the wrong behaviour.

    The body is deliberately just the flag. This endpoint is unauthenticated so
    a proxy can reach it, and the readiness detail is an inventory of what is
    installed, which extensions a deployment declared mandatory, and the text of
    any load error -- none of which an anonymous caller has a reason to read.
    A load balancer routes on the status code. The detail lives at
    ``/api/ready/details`` behind a login.
    """
    report = await _readiness_report(request)
    if not report.satisfied:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": report.satisfied}


@router.get(
    "/ready/details",
    summary="Readiness detail",
    description=(
        "What the readiness check found: declared requirements, what is "
        "installed, load failures and dependency probe results. Requires a "
        "signed-in user."
    ),
)
async def readiness_details(request: Request, response: Response) -> dict:
    """The full readiness report, for an operator diagnosing a 503."""
    from flocks.server.auth import require_user

    require_user(request)
    report = await _readiness_report(request)
    if not report.satisfied:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "ready": report.satisfied,
        "detail": report.describe(),
        "extensions": report.to_dict(),
    }


async def _readiness_report(request: Request):
    from flocks.plugin.readiness import evaluate_readiness

    # Plugins that loaded and failed are recorded at startup, not rediscoverable
    # here, so the recorded list has to be passed in. Without it an instance
    # whose critical plugin failed would answer "ready" and keep taking traffic.
    failed = tuple(
        getattr(request.app.state, "critical_plugin_entrypoint_failures", ()) or ()
    )
    return await evaluate_readiness(failed_entrypoints=failed)


@public_router.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse, summary="Health check (unprefixed alias)")
async def health_check_unprefixed() -> HealthResponse:
    return await health_check()


@public_router.get("/ready", summary="Readiness check (unprefixed alias)")
async def readiness_check_unprefixed(request: Request, response: Response) -> dict:
    return await readiness_check(request, response)
