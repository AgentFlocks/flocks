"""
Health check routes
"""

import hmac
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel


#: Set by a supervisor that starts this process from a signed release: an
#: opaque identity of that release, and when the supervisor started (ms).
ENV_RELEASE_TAG = "FLOCKS_RELEASE_TAG"
ENV_RELEASE_STARTED_AT = "FLOCKS_RELEASE_STARTED_AT"

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


def _release_matches(release: str, started_after: Optional[int]) -> Optional[bool]:
    """Whether this process runs the release *release* names, started after *started_after*.

    None when no supervisor told this process which release it runs: the
    question has no answer here, and the caller falls back to the flag alone.
    """
    tag = os.environ.get(ENV_RELEASE_TAG, "").strip()
    if not tag:
        return None
    if not hmac.compare_digest(tag.encode(), release.strip().encode()):
        return False
    if started_after is None:
        return True
    try:
        return int(os.environ.get(ENV_RELEASE_STARTED_AT, "0")) >= started_after
    except ValueError:
        return False


@router.get(
    "/ready",
    summary="Readiness check",
    description=(
        "Whether this instance is ready to take traffic. Unlike /health, which "
        "answers whether the process is up, this answers whether the extensions "
        "the deployment declared mandatory are actually loaded."
    ),
)
async def readiness_check(
    request: Request,
    response: Response,
    release: Optional[str] = Query(default=None, max_length=128),
    started_after: Optional[int] = Query(default=None, ge=0),
) -> dict:
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

    A release upgrade has to know the answer comes from the release it just
    started, not from whatever else listens on the port. It asks with
    ``release`` (an identity it computed for that release) and
    ``started_after``; the answer adds only "match" or "other". The identity
    cannot be guessed, so an anonymous caller learns nothing from asking, and
    a process that was not started from a release ignores both.
    """
    report = await _readiness_report(request)
    body: dict = {"ready": report.satisfied}
    if release is not None:
        matches = _release_matches(release, started_after)
        if matches is not None:
            body["release"] = "match" if matches else "other"
            if not matches:
                body["ready"] = False
    if not body["ready"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return body


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
async def readiness_check_unprefixed(
    request: Request,
    response: Response,
    release: Optional[str] = Query(default=None, max_length=128),
    started_after: Optional[int] = Query(default=None, ge=0),
) -> dict:
    return await readiness_check(request, response, release=release, started_after=started_after)
