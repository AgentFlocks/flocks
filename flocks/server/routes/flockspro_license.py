"""OSS fallback routes for Flocks Pro license status.

When the Pro package is installed it owns the actual license runtime. These
routes keep the WebUI status calls deterministic before that runtime is
available and delegate to the Pro checker whenever it can be imported.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from flocks.server.auth import require_user
from flocks.server.routes.console_upgrade import _get_pro_capability_status, _is_pro_component_installed

router = APIRouter()


def _inactive_status(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "activated": False,
        "active": False,
        "pro_enabled": False,
        "license_status": "uninstalled" if reason == "flockspro_not_installed" else "unknown",
        "inactive_reason": reason,
        **extra,
    }


@router.get("/status")
async def get_flockspro_license_status(request: Request) -> dict[str, Any]:
    require_user(request)
    if not _is_pro_component_installed():
        return _inactive_status("flockspro_not_installed")

    status = _get_pro_capability_status()
    if not status:
        return _inactive_status("capability_check_failed")
    status.setdefault("activated", bool(status.get("active") or status.get("pro_enabled")))
    status.setdefault("active", bool(status.get("pro_enabled")))
    status.setdefault("pro_enabled", bool(status.get("active")))
    return status


@router.post("/refresh")
async def refresh_flockspro_license_status(request: Request) -> dict[str, Any]:
    require_user(request)
    if not _is_pro_component_installed():
        return _inactive_status("flockspro_not_installed")

    try:
        from flockspro.license.runtime import get_license_checker  # type: ignore[import-not-found]

        checker = get_license_checker()
        refresh_fn = getattr(checker, "refresh", None)
        if callable(refresh_fn):
            result = refresh_fn()
            if hasattr(result, "__await__"):
                await result
    except Exception as exc:
        return _inactive_status("capability_check_failed", error=str(exc))

    return await get_flockspro_license_status(request)
