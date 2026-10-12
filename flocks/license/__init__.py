"""
License checker facade (OSS default: always active).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from flocks.extensions import ensure_callable_methods


@runtime_checkable
class LicenseChecker(Protocol):
    @classmethod
    async def is_active(cls, feature: str | None = None) -> bool: ...

    @classmethod
    async def assert_active(cls, feature: str | None = None) -> None: ...

    @classmethod
    async def status(cls) -> dict[str, Any]: ...


class AlwaysOkLicenseChecker:
    @classmethod
    async def is_active(cls, feature: str | None = None) -> bool:
        return True

    @classmethod
    async def assert_active(cls, feature: str | None = None) -> None:
        return None

    @classmethod
    async def status(cls) -> dict[str, Any]:
        return {"activated": True, "active": True, "status": "oss"}

    @classmethod
    async def feature_status(cls, feature: str | None = None) -> dict[str, Any]:
        return {"active": True, "status": "oss", "details": {}}


class _LicenseService:
    _checker: type[LicenseChecker] = AlwaysOkLicenseChecker

    @classmethod
    def register_checker(cls, checker: type[LicenseChecker]) -> None:
        if checker is None:
            raise ValueError("checker 不能为空")
        ensure_callable_methods(checker, ("is_active", "assert_active", "status"), label="license checker")
        cls._checker = checker

    @classmethod
    def get_checker(cls) -> type[LicenseChecker]:
        return cls._checker

    @classmethod
    async def is_active(cls, feature: str | None = None) -> bool:
        return await cls._checker.is_active(feature=feature)

    @classmethod
    async def assert_active(cls, feature: str | None = None) -> None:
        await cls._checker.assert_active(feature=feature)

    @classmethod
    async def status(cls) -> dict[str, Any]:
        return await cls._checker.status()

    @classmethod
    async def feature_status(cls, feature: str | None = None) -> dict[str, Any]:
        """One feature's verdict with whatever the checker says about it.

        ``feature_status`` is optional for a checker: one without it is asked
        ``is_active`` and the answer carries no details.
        """
        method = getattr(cls._checker, "feature_status", None)
        if callable(method):
            return dict(await method(feature=feature) or {})
        active = await cls._checker.is_active(feature=feature)
        return {"active": bool(active), "status": "active" if active else "inactive", "details": {}}


def is_pro_edition_status(status: dict[str, Any]) -> bool:
    """Whether a checker status grants the Pro edition (the OSS default never does)."""
    edition = str(status.get("status") or "").lower()
    return edition != "oss" and bool(status.get("active"))


register_checker = _LicenseService.register_checker
get_checker = _LicenseService.get_checker
is_license_active = _LicenseService.is_active
assert_license_active = _LicenseService.assert_active
license_status = _LicenseService.status
license_feature_status = _LicenseService.feature_status

__all__ = [
    "LicenseChecker",
    "AlwaysOkLicenseChecker",
    "register_checker",
    "get_checker",
    "is_license_active",
    "assert_license_active",
    "license_status",
    "is_pro_edition_status",
    "license_feature_status",
]

