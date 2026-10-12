"""A licence checker that licenses scene suites, for tests."""

from __future__ import annotations

from contextlib import contextmanager


class FakeProChecker:
    """A licence checker that licenses each scene suite on its own.

    ``licensed`` holds the suites whose own licence is in force; the platform
    licence is active unless ``platform_active`` is turned off, and no suite
    is active without it -- the same rule the commercial core applies.
    """

    licensed: set[str] = set()
    platform_active: bool = True
    asked: list[str] = []

    @classmethod
    def _suite_active(cls, feature):
        suite = (feature or "").removeprefix("scene_suite:")
        return cls.platform_active and suite in cls.licensed

    @classmethod
    async def is_active(cls, feature=None):
        cls.asked.append(feature or "")
        if feature and feature.startswith("scene_suite:"):
            return cls._suite_active(feature)
        return cls.platform_active

    @classmethod
    async def assert_active(cls, feature=None):
        if not await cls.is_active(feature):
            raise PermissionError(feature)

    @classmethod
    async def status(cls):
        return {
            "activated": cls.platform_active,
            "active": cls.platform_active,
            "status": "commercial" if cls.platform_active else "expired",
        }

    @classmethod
    async def feature_status(cls, feature=None):
        cls.asked.append(feature or "")
        active = cls._suite_active(feature)
        if active:
            suite = feature.removeprefix("scene_suite:")
            return {"active": True, "status": "commercial", "expires_unix_ms": 1893456000000,
                    "details": {"license_id": f"lic-{suite}"}}
        return {"active": False, "status": "unlicensed" if cls.platform_active else "platform_inactive",
                "expires_unix_ms": 0, "details": {}}


@contextmanager
def registered_pro_checker():
    """Register FakeProChecker as the licence checker, restoring the previous one."""
    from flocks.license import get_checker, register_checker

    previous = get_checker()
    FakeProChecker.licensed = set()
    FakeProChecker.platform_active = True
    FakeProChecker.asked = []
    register_checker(FakeProChecker)
    try:
        yield FakeProChecker
    finally:
        register_checker(previous)
