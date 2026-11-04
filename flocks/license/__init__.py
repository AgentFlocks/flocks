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


register_checker = _LicenseService.register_checker
get_checker = _LicenseService.get_checker
is_license_active = _LicenseService.is_active
assert_license_active = _LicenseService.assert_active
license_status = _LicenseService.status

__all__ = [
    "LicenseChecker",
    "AlwaysOkLicenseChecker",
    "register_checker",
    "get_checker",
    "is_license_active",
    "assert_license_active",
    "license_status",
]

