"""
License status for the current installation.

Answers from whatever license checker is registered.  A stock install has the
built-in checker, which reports an active, unrestricted installation; a
deployment that registers its own checker gets that one's answer instead.  The
route shape is the same either way, so the UI has one endpoint to call rather
than one per kind of install.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request

from flocks.server.auth import require_user

router = APIRouter()


@router.get("/status")
async def get_license_status(request: Request) -> Dict[str, Any]:
    """Report whether this installation is licensed, and for what."""

    require_user(request)

    from flocks.license import get_checker

    checker = get_checker()
    try:
        status = await checker.status()
    except Exception as exc:
        # A checker that cannot answer is not an active licence; saying so is
        # better than a 500 that leaves the UI unable to render at all.
        return {
            "activated": False,
            "active": False,
            "status": "unavailable",
            "reason": str(exc),
        }

    payload = dict(status or {})
    payload.setdefault("active", bool(payload.get("activated")))
    payload.setdefault("activated", bool(payload.get("active")))
    return payload


@router.get("/features/{feature}")
async def get_feature_status(feature: str, request: Request) -> Dict[str, Any]:
    """Whether one named feature is available under the current license."""

    require_user(request)

    from flocks.license import is_license_active

    try:
        available = await is_license_active(feature)
    except Exception as exc:
        return {"feature": feature, "available": False, "reason": str(exc)}
    return {"feature": feature, "available": bool(available)}
