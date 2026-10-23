"""Onboarding credential and preference boundaries through the real auth stack."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient
import pytest

from flocks.auth.service import AuthService
from flocks.security.secrets import SecretManager
from flocks.server.routes import onboarding as onboarding_routes
from flocks.storage.storage import Storage


pytestmark = pytest.mark.asyncio


@pytest.fixture
def risk_runtime(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Use real disk persistence under tmp_path; never the user's secret file."""
    from flocks import security
    from flocks.security import secrets as secret_module
    from flocks.server.routes import provider as provider_routes

    secrets = SecretManager(tmp_path / "risk-secrets.json")
    monkeypatch.setattr(secret_module, "_secret_manager", secrets)
    monkeypatch.setattr(security, "get_secret_manager", lambda: secrets)
    monkeypatch.setattr(provider_routes, "_ensure_provider_initialized", AsyncMock())
    monkeypatch.setattr(onboarding_routes.Provider, "get", lambda _provider_id: None)
    monkeypatch.setattr(onboarding_routes.Config, "resolve_default_llm", AsyncMock(return_value=None))
    monkeypatch.setattr(onboarding_routes, "_build_threatbook_intel_status", AsyncMock(
        return_value=onboarding_routes.ThreatBookIntelStatus(
            configured=False, api_configured=False, mcp_configured=False,
            mcp_connected=False, mcp_status="not_configured", service_matrix={},
        ),
    ))
    return secrets


async def _login_client(username: str, *, role: str) -> tuple[AsyncClient, str]:
    """Exercise session cookies, authentication middleware, and route dependencies."""
    from flocks.server.app import app

    password = "RiskTestPassword123!"
    user = await AuthService._create_user_internal(
        username=username, password=password, role=role, must_reset_password=False,
    )
    client = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"User-Agent": "Mozilla/5.0", "Sec-Fetch-Site": "same-origin"},
    )
    response = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    assert client.cookies.get("flocks_session")
    return client, user.id


@pytest.mark.parametrize("headers", [
    {"User-Agent": "Mozilla/5.0", "Sec-Fetch-Site": "same-origin"},
    {"User-Agent": "curl/8.0"},
    {"User-Agent": "curl/8.0", "Authorization": "Bearer wrong-token"},
])
async def test_unauthenticated_draft_requests_cannot_read_or_write_credentials(headers, risk_runtime):
    from flocks.server.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers,
    ) as client:
        read = await client.get("/api/onboarding/draft")
        write = await client.post("/api/onboarding/draft", json={
            "model": {"region": "cn", "threatbook_api_key": "must-not-be-saved"},
            "dismissed": True,
        })
        status = await client.get("/api/onboarding/status")

    assert [read.status_code, write.status_code, status.status_code] == [401, 401, 401]
    assert risk_runtime._load() == {}


async def test_member_cookie_cannot_access_admin_draft_or_mutate_global_key(risk_runtime):
    admin, admin_id = await _login_client("draft-admin", role="admin")
    member, member_id = await _login_client("draft-member", role="member")
    try:
        saved = await admin.post("/api/onboarding/draft", json={
            "model": {"region": "cn", "threatbook_api_key": "admin-original-key"},
            "dismissed": True,
        })
        assert saved.status_code == 200, saved.text
        read = await member.get("/api/onboarding/draft")
        overwrite = await member.post("/api/onboarding/draft", json={
            "model": {"region": "cn", "threatbook_api_key": "member-overwrite-key"},
            "dismissed": True,
            "user_id": admin_id,
        })
        assert [read.status_code, overwrite.status_code] == [403, 403]
        assert risk_runtime.get("threatbook-cn-llm_llm_key") == "admin-original-key"
        assert await Storage.read(["onboarding", "draft", member_id]) is None
        assert (await member.get("/api/onboarding/status")).json()["dismissed"] is False
        assert "admin-original-key" not in read.text + overwrite.text
    finally:
        await admin.aclose()
        await member.aclose()


async def test_admin_cookie_preferences_remain_scoped_to_logged_in_user(risk_runtime):
    alice, alice_id = await _login_client("alice-risk-admin", role="admin")
    bob, bob_id = await _login_client("bob-risk-admin", role="admin")
    try:
        saved = await alice.post("/api/onboarding/draft", json={"dismissed": True})
        assert saved.status_code == 200, saved.text
        assert (await alice.get("/api/onboarding/status")).json()["dismissed"] is True
        assert (await bob.get("/api/onboarding/status")).json()["dismissed"] is False
        assert (await bob.get("/api/onboarding/draft")).json()["dismissed"] is False
        saved = await bob.post("/api/onboarding/draft", json={"dismissed": False, "user_id": alice_id})
        assert saved.status_code == 200, saved.text
        assert (await alice.get("/api/onboarding/status")).json()["dismissed"] is True
        assert (await Storage.read(["onboarding", "draft", alice_id]))["dismissed"] is True
        assert (await Storage.read(["onboarding", "draft", bob_id]))["dismissed"] is False
    finally:
        await alice.aclose()
        await bob.aclose()


async def test_reopening_partial_draft_returns_no_raw_credentials(client, risk_runtime):
    payload = {
        "model": {
            "region": "global", "use_threatbook_model": False,
            "third_party_llm": {
                "provider_id": "azure-openai", "model_id": "", "api_key": "unvalidated-azure-risk-key",
                "base_url": "https://risk.openai.azure.com/",
            },
        },
        "intel": {
            "region": "cn", "use_threatbook_model": False,
            "threatbook_api_key": "unvalidated-intel-risk-key", "threatbook_services_only": True,
        },
        "dismissed": True,
    }
    saved = await client.post("/api/onboarding/draft", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["model_has_key"] is True
    assert saved.json()["intel_has_key"] is True
    reopened = await client.get("/api/onboarding/draft")
    assert reopened.status_code == 200
    assert reopened.json() == saved.json()
    stored = await Storage.read(["onboarding", "draft", "api-token-service"])
    for key in ("unvalidated-azure-risk-key", "unvalidated-intel-risk-key"):
        assert key not in saved.text + reopened.text + json.dumps(stored)
    assert risk_runtime.get("azure-openai_llm_key") == "unvalidated-azure-risk-key"
    assert risk_runtime.get("threatbook_cn_api_key") == "unvalidated-intel-risk-key"


async def test_unknown_provider_rejects_entire_draft_before_saving_other_keys(client, risk_runtime):
    response = await client.post("/api/onboarding/draft", json={
        "model": {
            "region": "global", "use_threatbook_model": False,
            "third_party_llm": {"provider_id": "unknown-risk-provider", "model_id": "", "api_key": "model-risk-key"},
        },
        "intel": {"region": "cn", "threatbook_api_key": "intel-risk-key"},
        "dismissed": True,
    })
    assert response.status_code == 400, response.text
    assert risk_runtime._load() == {}
    assert await Storage.read(["onboarding", "draft", "api-token-service"]) is None
    assert "model-risk-key" not in response.text
    assert "intel-risk-key" not in response.text


async def test_loaded_draft_redacts_legacy_plaintext_and_ignores_unknown_fields(client, risk_runtime):
    """Even manually imported/older draft records must not reveal credential fields."""
    await Storage.write(["onboarding", "draft", "api-token-service"], {
        "model": {
            "region": "global", "use_threatbook_model": False,
            "threatbook_api_key": "legacy-unused-risk-key",
            "third_party_llm": {
                "provider_id": "azure-openai", "model_id": "", "api_key": "legacy-model-risk-key",
                "unexpected_password": "legacy-extra-risk-key",
            },
            "unexpected_password": "legacy-extra-risk-key",
        },
        "intel": {"region": "cn", "threatbook_api_key": "legacy-intel-risk-key"},
        "dismissed": True,
    })

    response = await client.get("/api/onboarding/draft")

    assert response.status_code == 200, response.text
    assert response.json()["dismissed"] is True
    assert response.json()["model"]["third_party_llm"]["api_key"] == ""
    assert response.json()["model"]["threatbook_api_key"] is None
    assert response.json()["intel"]["threatbook_api_key"] is None
    for key in ("legacy-unused-risk-key", "legacy-model-risk-key", "legacy-extra-risk-key", "legacy-intel-risk-key"):
        assert key not in response.text
    assert risk_runtime._load() == {}
