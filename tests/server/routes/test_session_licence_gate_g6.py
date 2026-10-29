"""POST /api/session under a licence checker that withholds session_create (G6, E2E-0024 follow-up).

A registered checker (a deployment's own, once its licence lapsed) used to surface as an
unhandled PermissionError -> 500. The route now answers 403 with a structured
``license_inactive`` detail, so the UI can show "licence inactive" instead of "internal error".
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from flocks import license as license_facade


class _WithholdingChecker:
    """Withholds every feature; reports why, the way the core checker does."""

    @classmethod
    async def is_active(cls, feature=None) -> bool:
        return False

    @classmethod
    async def assert_active(cls, feature=None) -> None:
        raise PermissionError(f"licence does not cover {feature!r}" if feature else "licence is not active")

    @classmethod
    async def status(cls) -> dict:
        return {"status": "revoked", "active": False, "source": "core", "reapply_allowed": True}


class _BrokenStatusChecker(_WithholdingChecker):
    @classmethod
    async def status(cls) -> dict:
        raise RuntimeError("core unreachable")


@pytest.fixture
def withholding_checker():
    previous = license_facade.get_checker()
    license_facade.register_checker(_WithholdingChecker)
    yield
    license_facade.register_checker(previous)


@pytest.mark.asyncio
async def test_lapsed_licence_is_a_403_in_the_pro_gate_shape(client: AsyncClient, withholding_checker):
    resp = await client.post("/api/session", json={"title": "gated"})
    assert resp.status_code == 403, resp.text
    detail = resp.json()["message"]
    assert detail["code"] == "license_inactive"
    assert detail["feature"] == "session_create"
    assert detail["license_status"] == "revoked" and detail["reapply_allowed"] is True
    assert "session_create" in detail["message"]


@pytest.mark.asyncio
async def test_checker_that_cannot_report_status_still_answers_403(client: AsyncClient):
    previous = license_facade.get_checker()
    license_facade.register_checker(_BrokenStatusChecker)
    try:
        resp = await client.post("/api/session", json={"title": "gated"})
    finally:
        license_facade.register_checker(previous)
    assert resp.status_code == 403, resp.text
    detail = resp.json()["message"]
    assert detail["code"] == "license_inactive" and detail["license_status"] is None


@pytest.mark.asyncio
async def test_stock_checker_keeps_creating_sessions(client: AsyncClient):
    assert license_facade.get_checker() is license_facade.AlwaysOkLicenseChecker
    resp = await client.post("/api/session", json={"title": "open"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "open"
