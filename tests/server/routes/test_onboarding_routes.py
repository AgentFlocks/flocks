from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.auth.context import API_TOKEN_SERVICE_USER_ID, AuthUser
from flocks.server.routes import onboarding as onboarding_routes


REAL_BUILD_THREATBOOK_INTEL_STATUS = onboarding_routes._build_threatbook_intel_status


def _intel_status(**overrides):
    data = {
        "configured": False,
        "region": None,
        "api_configured": False,
        "api_service_id": "threatbook-cn",
        "mcp_configured": False,
        "mcp_connected": False,
        "mcp_status": "not_configured",
        "mcp_name": "threatbook_mcp",
        "service_matrix": {
            "cn": ["api", "mcp"],
            "global": ["api"],
        },
    }
    data.update(overrides)
    return onboarding_routes.ThreatBookIntelStatus(**data)


@pytest.fixture(autouse=True)
def default_threatbook_intel_status(monkeypatch: pytest.MonkeyPatch):
    async def fake_status():
        return _intel_status()

    monkeypatch.setattr(onboarding_routes, "_build_threatbook_intel_status", fake_status)


@pytest.fixture
def draft_secrets(monkeypatch: pytest.MonkeyPatch):
    from flocks import security
    from flocks.server.routes import provider as provider_routes

    class Secrets:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

        def delete(self, key):
            return self.values.pop(key, None) is not None

        def _load(self):
            return dict(self.values)

        def _save(self, values):
            self.values = dict(values)

    secrets = Secrets()
    monkeypatch.setattr(security, "get_secret_manager", lambda: secrets)
    monkeypatch.setattr(onboarding_routes, "get_secret_manager", lambda: secrets)
    monkeypatch.setattr(provider_routes, "_ensure_provider_initialized", AsyncMock())
    monkeypatch.setattr(onboarding_routes.Provider, "get", lambda _provider_id: None)
    monkeypatch.setattr(onboarding_routes.Provider, "_models", dict(onboarding_routes.Provider._models))
    return secrets


class TestOnboardingDraftRoutes:
    @pytest.mark.asyncio
    async def test_invalid_keys_are_saved_without_validation_or_default_model(
        self, client, monkeypatch: pytest.MonkeyPatch, draft_secrets
    ):
        validate = AsyncMock(side_effect=AssertionError("draft must not validate"))
        connect = AsyncMock(side_effect=AssertionError("draft must not connect MCP"))
        set_default = AsyncMock(side_effect=AssertionError("draft must not set a default"))
        monkeypatch.setattr(onboarding_routes, "_validate_onboarding_request", validate)
        monkeypatch.setattr(onboarding_routes, "connect_mcp_server", connect)
        monkeypatch.setattr(onboarding_routes, "set_default_model", set_default)

        response = await client.post("/api/onboarding/draft", json={
            "model": {
                "region": "cn", "use_threatbook_model": True,
                "threatbook_api_key": "invalid-model-key", "threatbook_model_only": True,
            },
            "intel": {
                "region": "cn", "use_threatbook_model": False,
                "threatbook_api_key": "invalid-intel-key", "threatbook_services_only": True,
            },
        })
        assert response.status_code == 200, response.text
        assert response.json()["model_has_key"] is True
        assert response.json()["intel_has_key"] is True
        assert draft_secrets.get("threatbook-cn-llm_llm_key") == "invalid-model-key"
        assert draft_secrets.get("threatbook_cn_api_key") == "invalid-intel-key"
        assert onboarding_routes.ConfigWriter.get_default_model("llm") is None
        assert onboarding_routes.ConfigWriter.get_mcp_server("threatbook_mcp") is None
        assert onboarding_routes.ConfigWriter.get_api_service_raw("threatbook-cn").get("enabled") is not True
        saved = await onboarding_routes.Storage.read(["onboarding", "draft", API_TOKEN_SERVICE_USER_ID])
        assert "invalid-model-key" not in json.dumps(saved)
        assert "invalid-intel-key" not in json.dumps(saved)
        reopened = await client.get("/api/onboarding/draft")
        assert reopened.json() == response.json()
        assert "invalid-model-key" not in reopened.text
        assert "invalid-intel-key" not in reopened.text
        validate.assert_not_awaited()
        connect.assert_not_awaited()
        set_default.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_draft_can_dismiss_and_reenable_onboarding(self, client):
        response = await client.post("/api/onboarding/draft", json={"dismissed": True})
        assert response.status_code == 200, response.text
        assert response.json() == {
            "model": None, "intel": None, "model_has_key": False,
            "intel_has_key": False, "dismissed": True,
        }
        assert (await client.get("/api/onboarding/status")).json()["dismissed"] is True
        response = await client.post("/api/onboarding/draft", json={"dismissed": False})
        assert response.json()["dismissed"] is False

    @pytest.mark.asyncio
    async def test_preferences_are_scoped_to_current_user(self, monkeypatch: pytest.MonkeyPatch):
        alice = AuthUser(id="alice", username="alice", role="admin")
        bob = AuthUser(id="bob", username="bob", role="admin")
        monkeypatch.setattr(onboarding_routes.Config, "resolve_default_llm", AsyncMock(return_value=None))
        await onboarding_routes.save_onboarding_draft(
            onboarding_routes.OnboardingDraftRequest(dismissed=True), _admin=alice,
        )
        assert (await onboarding_routes.get_onboarding_draft(_admin=alice)).dismissed is True
        assert (await onboarding_routes.get_onboarding_draft(_admin=bob)).dismissed is False
        assert (await onboarding_routes.get_onboarding_status(_user=alice)).dismissed is True
        assert (await onboarding_routes.get_onboarding_status(_user=bob)).dismissed is False

    @pytest.mark.asyncio
    async def test_partial_updates_and_blank_keys_preserve_saved_configuration(self, client, draft_secrets):
        model = {
            "region": "cn", "use_threatbook_model": False,
            "third_party_llm": {
                "provider_id": "openai-compatible", "api_key": "saved-key", "model_id": "custom-model",
                "base_url": "https://models.example.test/v1", "provider_name": "My provider",
            },
        }
        response = await client.post("/api/onboarding/draft", json={"model": model, "dismissed": True})
        assert response.status_code == 200, response.text
        model["third_party_llm"] = {
            "provider_id": "openai-compatible", "api_key": "", "model_id": "custom-model",
        }
        response = await client.post("/api/onboarding/draft", json={"model": model, "intel": None})
        assert response.status_code == 200, response.text
        assert response.json()["model"]["third_party_llm"]["base_url"] == "https://models.example.test/v1"
        assert response.json()["model"]["third_party_llm"]["provider_name"] == "My provider"
        assert response.json()["model_has_key"] is True
        assert response.json()["dismissed"] is True
        assert draft_secrets.get("openai-compatible_llm_key") == "saved-key"
        response = await client.post("/api/onboarding/draft", json={"model": None, "dismissed": False})
        assert response.json()["model"]["third_party_llm"]["model_id"] == "custom-model"

    @pytest.mark.asyncio
    async def test_unknown_provider_is_rejected_before_saving_credentials(self, client, draft_secrets):
        response = await client.post("/api/onboarding/draft", json={"model": {
            "region": "cn", "use_threatbook_model": False,
            "third_party_llm": {"provider_id": "invented-provider", "api_key": "bad-key", "model_id": "model"},
        }})
        assert response.status_code == 400, response.text
        assert draft_secrets.values == {}
        assert onboarding_routes.ConfigWriter.get_provider_raw("invented-provider") is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider_id", ["azure-openai", "azure", "openai-compatible"])
    async def test_manual_model_or_azure_deployment_is_saved_without_setting_default(
        self, client, draft_secrets, provider_id
    ):
        response = await client.post("/api/onboarding/draft", json={"model": {
            "region": "global", "use_threatbook_model": False,
            "third_party_llm": {
                "provider_id": provider_id, "api_key": "candidate-key",
                "model_id": "my-production-deployment", "base_url": "https://resource.openai.azure.com/",
            },
        }})
        assert response.status_code == 200, response.text
        raw = onboarding_routes.ConfigWriter.get_provider_raw(provider_id)
        assert "my-production-deployment" in raw["models"]
        assert raw["options"]["baseURL"] == "https://resource.openai.azure.com/"
        if provider_id in {"azure", "azure-openai"}:
            assert raw["npm"] == "@ai-sdk/azure"
        assert onboarding_routes.ConfigWriter.get_default_model("llm") is None

    @pytest.mark.asyncio
    async def test_key_can_be_saved_before_manual_model_is_entered(self, client, draft_secrets):
        response = await client.post("/api/onboarding/draft", json={"model": {
            "region": "global", "use_threatbook_model": False,
            "third_party_llm": {"provider_id": "azure-openai", "api_key": "early-key", "model_id": ""},
        }})
        assert response.status_code == 200, response.text
        assert response.json()["model_has_key"] is True
        assert draft_secrets.get("azure-openai_llm_key") == "early-key"
        assert onboarding_routes.ConfigWriter.get_default_model("llm") is None

    @pytest.mark.asyncio
    async def test_apply_validates_azure_deployment_then_registers_it_as_default(
        self, client, monkeypatch: pytest.MonkeyPatch, draft_secrets
    ):
        probe = AsyncMock(return_value={"success": True, "message": "ok"})
        monkeypatch.setattr(onboarding_routes, "_test_provider_or_service_with_temp_credentials", probe)
        response = await client.post("/api/onboarding/apply", json={
            "region": "global", "use_threatbook_model": False,
            "third_party_llm": {
                "provider_id": "azure-openai", "api_key": "valid-key",
                "model_id": "my-deployment", "base_url": "https://resource.openai.azure.com/",
            },
        })
        assert response.status_code == 200, response.text
        probe.assert_awaited_once_with(
            "azure-openai", "valid-key", model_id="my-deployment",
            base_url="https://resource.openai.azure.com/", provider_name=None,
        )
        assert "my-deployment" in onboarding_routes.ConfigWriter.get_provider_raw("azure-openai")["models"]
        assert onboarding_routes.ConfigWriter.get_default_model("llm") == {
            "provider_id": "azure-openai", "model_id": "my-deployment",
        }

    @pytest.mark.asyncio
    async def test_successful_apply_clears_only_corresponding_draft_section(
        self, client, monkeypatch: pytest.MonkeyPatch, draft_secrets
    ):
        model = {
            "region": "cn", "use_threatbook_model": True,
            "threatbook_api_key": "model-key", "threatbook_model_only": True,
        }
        intel = {
            "region": "cn", "use_threatbook_model": False,
            "threatbook_api_key": "intel-key", "threatbook_services_only": True,
        }
        await client.post("/api/onboarding/draft", json={"model": model, "intel": intel, "dismissed": True})
        monkeypatch.setattr(onboarding_routes, "_apply_onboarding", AsyncMock(return_value=onboarding_routes.OnboardingApplyResponse(
            success=True, message="ok", region="cn", threatbook_enabled=True, configured=["threatbook_llm"], skipped=[],
        )))
        response = await client.post("/api/onboarding/apply", json=model)
        assert response.status_code == 200, response.text
        draft = (await client.get("/api/onboarding/draft")).json()
        assert draft["model"] is None
        assert draft["intel"] is not None
        assert draft["dismissed"] is True


    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure_point", ["intel", "draft_storage", "response"])
    async def test_draft_save_failure_rolls_back_config_secrets_and_runtime(
        self, monkeypatch: pytest.MonkeyPatch, draft_secrets, failure_point
    ):
        from fastapi import HTTPException

        draft_secrets.set("unrelated_key", "keep-me")
        original_config = onboarding_routes.ConfigWriter._read_raw()
        original_models = dict(onboarding_routes.Provider._models)
        original_storage_write = onboarding_routes.Storage.write
        storage_write = AsyncMock(wraps=original_storage_write)
        monkeypatch.setattr(onboarding_routes.Storage, "write", storage_write)
        monkeypatch.setattr(onboarding_routes, "_reload_runtime_state", AsyncMock())
        if failure_point == "intel":
            failure = HTTPException(status_code=500, detail="secret store failure")
            monkeypatch.setattr(onboarding_routes, "set_service_credentials", AsyncMock(side_effect=failure))
        elif failure_point == "draft_storage":
            failure = RuntimeError("draft store unavailable")
            storage_write.side_effect = failure
        else:
            failure = RuntimeError("cannot build draft response")
            monkeypatch.setattr(onboarding_routes, "_draft_response", AsyncMock(side_effect=failure))

        request = onboarding_routes.OnboardingDraftRequest.model_validate({
            "model": {
                "region": "global", "use_threatbook_model": False,
                "third_party_llm": {
                    "provider_id": "azure-openai", "api_key": "new-model-key",
                    "model_id": "rollback-deployment", "base_url": "https://example.test/",
                },
            },
            "intel": {
                "region": "cn", "use_threatbook_model": False,
                "threatbook_api_key": "new-intel-key", "threatbook_services_only": True,
            },
            "dismissed": True,
        })
        with pytest.raises(type(failure)):
            await onboarding_routes.save_onboarding_draft(
                request, _admin=AuthUser(id="alice", username="alice", role="admin"),
            )

        assert onboarding_routes.ConfigWriter._read_raw() == original_config
        assert draft_secrets.values == {"unrelated_key": "keep-me"}
        assert onboarding_routes.Provider._models == original_models
        assert await onboarding_routes.Storage.read(["onboarding", "draft", "alice"]) is None
        if failure_point != "draft_storage":
            storage_write.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure_point", ["read", "write"])
    async def test_committed_apply_stays_successful_if_draft_cleanup_fails(
        self, monkeypatch: pytest.MonkeyPatch, failure_point
    ):
        applied = onboarding_routes.OnboardingApplyResponse(
            success=True, message="ok", region="cn", threatbook_enabled=True,
            configured=["threatbook_llm", "default_llm"], skipped=[],
        )
        monkeypatch.setattr(onboarding_routes, "_apply_onboarding", AsyncMock(return_value=applied))
        if failure_point == "read":
            monkeypatch.setattr(onboarding_routes, "_read_draft", AsyncMock(side_effect=RuntimeError("store unavailable")))
        else:
            monkeypatch.setattr(onboarding_routes.Storage, "write", AsyncMock(side_effect=RuntimeError("store unavailable")))
        response = await onboarding_routes.apply_onboarding(
            onboarding_routes.OnboardingValidateRequest(
                region="cn", threatbook_api_key="already-applied", threatbook_model_only=True,
            ),
            _admin=AuthUser(id="alice", username="alice", role="admin"),
        )
        assert response is applied


@pytest.mark.asyncio
async def test_apply_rollback_restores_provider_registries_and_previous_instance_config(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.provider.provider import BaseProvider, ProviderConfig

    provider = BaseProvider("existing-provider", "Original name")
    original_credentials = ProviderConfig(provider_id="existing-provider", api_key="original-key")
    provider._config = original_credentials
    old_model = object()
    monkeypatch.setattr(onboarding_routes.Provider, "_providers", {provider.id: provider})
    monkeypatch.setattr(onboarding_routes.Provider, "_models", {"original-model": old_model})
    monkeypatch.setattr(onboarding_routes.Provider, "_initialized", True)
    monkeypatch.setattr(onboarding_routes, "_snapshot_config_and_secret_state", lambda: ({}, {}))
    monkeypatch.setattr(onboarding_routes, "_restore_config_and_secret_state", AsyncMock())

    with pytest.raises(RuntimeError, match="failed after mutation"):
        async with onboarding_routes._rollback_on_apply_failure():
            provider.configure(ProviderConfig(provider_id=provider.id, api_key="rejected-key"))
            provider.name = "Changed name"
            provider._config_models = [object()]
            onboarding_routes.Provider._providers["new-provider"] = BaseProvider("new-provider", "New")
            onboarding_routes.Provider._models["new-model"] = object()
            raise RuntimeError("failed after mutation")

    assert onboarding_routes.Provider._providers == {provider.id: provider}
    assert onboarding_routes.Provider._models == {"original-model": old_model}
    assert provider._config is original_credentials
    assert provider._config_models == []
    assert provider.name == "Original name"


@pytest.mark.asyncio
async def test_apply_rollback_restores_mcp_runtime(monkeypatch: pytest.MonkeyPatch):
    config_snapshot = {"mcp": {"threatbook_mcp": {"url": "old"}}}
    secret_snapshot = {"threatbook_mcp_key": "old-key"}
    previous_config = {"type": "remote", "url": "old"}
    restore = AsyncMock()
    reload_runtime = AsyncMock()

    monkeypatch.setattr(
        onboarding_routes,
        "_snapshot_config_and_secret_state",
        lambda: (config_snapshot, secret_snapshot),
    )
    monkeypatch.setattr(
        onboarding_routes,
        "_load_raw_mcp_server_config",
        lambda _name: previous_config,
    )
    monkeypatch.setattr(
        onboarding_routes.MCP,
        "status",
        AsyncMock(
            return_value={
                "threatbook_mcp": SimpleNamespace(
                    status=onboarding_routes.McpStatus.CONNECTED
                ),
            }
        ),
    )
    monkeypatch.setattr(onboarding_routes, "_restore_threatbook_mcp_setup", restore)
    monkeypatch.setattr(onboarding_routes, "_reload_runtime_state", reload_runtime)

    with pytest.raises(RuntimeError, match="apply failed"):
        async with onboarding_routes._rollback_on_apply_failure("threatbook_mcp"):
            raise RuntimeError("apply failed")

    restore.assert_awaited_once_with(
        "threatbook_mcp",
        config_snapshot,
        secret_snapshot,
        previous_config,
        True,
    )
    reload_runtime.assert_awaited_once()


@pytest.mark.asyncio
async def test_registered_provider_validation_uses_isolated_instance(
    monkeypatch: pytest.MonkeyPatch,
):
    registered_provider = object()
    probe = AsyncMock(return_value={"success": True})

    monkeypatch.setattr(onboarding_routes.Provider, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(
        onboarding_routes.Provider,
        "get",
        lambda provider_id: registered_provider if provider_id == "openai" else None,
    )
    monkeypatch.setattr(onboarding_routes, "_test_provider_credentials_impl", probe)

    result = await onboarding_routes._test_provider_or_service_with_temp_credentials(
        "openai",
        "candidate-key",
        model_id="gpt-4o",
        base_url="https://example.test/v1",
    )

    assert result == {"success": True}
    probe.assert_awaited_once()
    _, body = probe.await_args.args
    assert body.model_id == "gpt-4o"
    assert probe.await_args.kwargs == {
        "api_key_override": "candidate-key",
        "isolated_provider": True,
        "base_url_override": "https://example.test/v1",
    }


class TestOnboardingStatusRoutes:
    @pytest.mark.asyncio
    async def test_status_incomplete_when_no_default_model(self, client, monkeypatch: pytest.MonkeyPatch):
        async def fake_resolve():
            return None

        monkeypatch.setattr(onboarding_routes.Config, "resolve_default_llm", fake_resolve)

        resp = await client.get("/api/onboarding/status")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["completed"] is False
        assert data["has_default_model"] is False
        assert data["default_model"] is None
        assert data["threatbook_intel"]["configured"] is False
        assert data["threatbook_intel"]["service_matrix"]["cn"] == ["api", "mcp"]

    @pytest.mark.asyncio
    async def test_status_incomplete_when_default_without_credentials(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_resolve():
            return {"provider_id": "openai", "model_id": "gpt-4o"}

        monkeypatch.setattr(onboarding_routes.Config, "resolve_default_llm", fake_resolve)
        monkeypatch.setattr(onboarding_routes, "_llm_provider_has_usable_credentials", lambda _pid: False)

        resp = await client.get("/api/onboarding/status")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["completed"] is False
        assert data["has_default_model"] is True
        assert data["default_model"] == {"provider_id": "openai", "model_id": "gpt-4o"}

    @pytest.mark.asyncio
    async def test_status_complete_when_default_and_credentials(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_resolve():
            return {"provider_id": "threatbook-cn-llm", "model_id": "minimax-m2.7"}

        monkeypatch.setattr(onboarding_routes.Config, "resolve_default_llm", fake_resolve)
        monkeypatch.setattr(onboarding_routes, "_llm_provider_has_usable_credentials", lambda _pid: True)

        resp = await client.get("/api/onboarding/status")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["completed"] is True
        assert data["has_default_model"] is True
        assert data["default_model"]["provider_id"] == "threatbook-cn-llm"
        assert data["default_model"]["model_id"] == "minimax-m2.7"

    @pytest.mark.asyncio
    async def test_status_includes_threatbook_intel_configuration(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_resolve():
            return {"provider_id": "openai", "model_id": "gpt-4o"}

        async def fake_intel_status():
            return _intel_status(
                configured=True,
                region="cn",
                api_configured=True,
                mcp_configured=True,
                mcp_connected=True,
                mcp_status="connected",
            )

        monkeypatch.setattr(onboarding_routes.Config, "resolve_default_llm", fake_resolve)
        monkeypatch.setattr(onboarding_routes, "_llm_provider_has_usable_credentials", lambda _pid: True)
        monkeypatch.setattr(onboarding_routes, "_build_threatbook_intel_status", fake_intel_status)

        resp = await client.get("/api/onboarding/status")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["threatbook_intel"]["configured"] is True
        assert data["threatbook_intel"]["region"] == "cn"
        assert data["threatbook_intel"]["api_configured"] is True
        assert data["threatbook_intel"]["mcp_connected"] is True

    @pytest.mark.asyncio
    async def test_intel_status_ignores_legacy_global_mcp_for_global_api(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_api_credentials(service_id: str):
            return service_id == "threatbook-io"

        async def fake_mcp_status(name: str):
            return {
                "status": "connected",
                "configured": True,
                "connected": True,
                "has_credential": True,
            }

        monkeypatch.setattr(onboarding_routes, "_api_service_has_credentials", fake_api_credentials)
        monkeypatch.setattr(onboarding_routes, "_detect_mcp_status", fake_mcp_status)
        monkeypatch.setattr(
            onboarding_routes.ConfigWriter,
            "get_mcp_server",
            lambda name: {
                "url": "https://mcp.threatbook.io/mcp?apikey={secret:threatbook_mcp_key}",
            },
        )

        status = await REAL_BUILD_THREATBOOK_INTEL_STATUS()

        assert status.region == "global"
        assert status.api_service_id == "threatbook-io"
        assert status.mcp_connected is False
        assert status.mcp_status == "not_required"
        assert status.service_matrix["global"] == ["api"]


class TestOnboardingValidateRoutes:
    @pytest.mark.asyncio
    async def test_validate_returns_region_mismatch_when_other_region_probe_succeeds(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_test_provider(provider_id: str, api_key: str, **kwargs):
            if provider_id in {"threatbook-cn-llm", "threatbook-cn"}:
                return {"success": False, "message": f"{provider_id} failed", "error": "invalid"}
            if provider_id == "threatbook-io":
                return {"success": True, "message": "global api ok"}
            raise AssertionError(f"unexpected provider_id: {provider_id}")

        async def fake_test_mcp(region: str, api_key: str):
            return {"success": False, "message": "mcp failed", "error": "invalid"}

        monkeypatch.setattr(
            onboarding_routes,
            "_test_provider_or_service_with_temp_credentials",
            fake_test_provider,
        )
        monkeypatch.setattr(
            onboarding_routes,
            "_test_mcp_with_temp_key",
            fake_test_mcp,
        )

        resp = await client.post(
            "/api/onboarding/validate",
            json={
                "region": "cn",
                "use_threatbook_model": True,
                "threatbook_api_key": "tb-key",
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is False
        assert data["can_apply"] is False
        assert data["error_code"] == "region_mismatch"
        assert data["suggested_region"] == "global"
        assert data["threatbook_region_match"] is False

    @pytest.mark.asyncio
    async def test_validate_requires_key_when_threatbook_model_selected(
        self, client
    ):
        resp = await client.post(
            "/api/onboarding/validate",
            json={
                "region": "global",
                "use_threatbook_model": True,
                "threatbook_api_key": "",
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == "missing_threatbook_key"
        assert data["resource_results"]["threatbook_llm"]["success"] is False

    @pytest.mark.asyncio
    async def test_validate_allows_third_party_model_without_threatbook_key(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_test_provider(provider_id: str, api_key: str, **kwargs):
            assert provider_id == "openai"
            return {"success": True, "message": "openai ok"}

        monkeypatch.setattr(
            onboarding_routes,
            "_test_provider_or_service_with_temp_credentials",
            fake_test_provider,
        )

        resp = await client.post(
            "/api/onboarding/validate",
            json={
                "region": "global",
                "use_threatbook_model": False,
                "third_party_llm": {
                    "provider_id": "openai",
                    "api_key": "sk-test",
                    "model_id": "gpt-4o",
                },
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["can_apply"] is True
        assert data["threatbook_enabled"] is False
        assert data["third_party_llm_valid"] is True

    @pytest.mark.asyncio
    async def test_validate_optional_threatbook_services_ignore_llm_validation(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_test_provider(provider_id: str, api_key: str, **kwargs):
            if provider_id == "threatbook-cn":
                return {"success": True, "message": "tb api ok"}
            if provider_id == "openai":
                return {"success": True, "message": "openai ok"}
            raise AssertionError(f"unexpected provider_id: {provider_id}")

        async def fake_test_mcp(region: str, api_key: str):
            assert region == "cn"
            return {"success": True, "message": "mcp ok"}

        monkeypatch.setattr(
            onboarding_routes,
            "_test_provider_or_service_with_temp_credentials",
            fake_test_provider,
        )
        monkeypatch.setattr(
            onboarding_routes,
            "_test_mcp_with_temp_key",
            fake_test_mcp,
        )

        resp = await client.post(
            "/api/onboarding/validate",
            json={
                "region": "cn",
                "use_threatbook_model": False,
                "threatbook_services_only": True,
                "threatbook_api_key": "tb-key",
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["can_apply"] is True
        assert "threatbook_llm" not in data["resource_results"]
        assert data["resource_results"]["threatbook_api"]["success"] is True
        assert data["resource_results"]["threatbook_mcp"]["success"] is True

    @pytest.mark.asyncio
    async def test_validate_global_intelligence_configures_api_without_mcp(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_test_provider(provider_id: str, api_key: str, **kwargs):
            assert provider_id == "threatbook-io"
            assert kwargs["service"] is True
            return {"success": True, "message": "global api ok"}

        async def fake_test_mcp(region: str, api_key: str):
            raise AssertionError("International intelligence must not test MCP")

        monkeypatch.setattr(
            onboarding_routes,
            "_test_provider_or_service_with_temp_credentials",
            fake_test_provider,
        )
        monkeypatch.setattr(onboarding_routes, "_test_mcp_with_temp_key", fake_test_mcp)

        resp = await client.post(
            "/api/onboarding/validate",
            json={
                "region": "global",
                "use_threatbook_model": False,
                "threatbook_services_only": True,
                "threatbook_api_key": "tb-global-key",
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert set(data["resource_results"]) == {"threatbook_api"}

    @pytest.mark.asyncio
    async def test_validate_threatbook_model_only_skips_api_and_mcp_validation(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_test_provider(provider_id: str, api_key: str, **kwargs):
            assert provider_id == "threatbook-cn-llm"
            assert kwargs["model_id"] == "deepseek-v4-flash-0731"
            return {"success": True, "message": "tb model ok"}

        async def fake_test_mcp(region: str, api_key: str):
            raise AssertionError("MCP should not be tested for model-only setup")

        monkeypatch.setattr(
            onboarding_routes,
            "_test_provider_or_service_with_temp_credentials",
            fake_test_provider,
        )
        monkeypatch.setattr(
            onboarding_routes,
            "_test_mcp_with_temp_key",
            fake_test_mcp,
        )

        resp = await client.post(
            "/api/onboarding/validate",
            json={
                "region": "cn",
                "use_threatbook_model": True,
                "threatbook_model_only": True,
                "threatbook_api_key": "tb-key",
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["can_apply"] is True
        assert data["resource_results"]["threatbook_llm"]["success"] is True
        assert "threatbook_api" not in data["resource_results"]
        assert "threatbook_mcp" not in data["resource_results"]


class TestOnboardingApplyRoutes:
    def test_threatbook_region_presets_use_deepseek_v4_flash_0731(self):
        assert onboarding_routes.ONBOARDING_REGION_PRESETS["cn"]["threatbook_default_model_id"] == (
            "deepseek-v4-flash-0731"
        )
        assert onboarding_routes.ONBOARDING_REGION_PRESETS["global"]["threatbook_default_model_id"] == (
            "deepseek-v4-flash-0731"
        )
        assert onboarding_routes.ONBOARDING_REGION_PRESETS["global"]["activation_url"] == (
            "https://i.threatbook.io/flocks/activate"
        )
        assert onboarding_routes.ONBOARDING_REGION_PRESETS["global"]["threatbook_mcp_endpoint"] is None
        assert onboarding_routes.ONBOARDING_REGION_PRESETS["global"]["requires_mcp"] is False
        assert onboarding_routes._get_threatbook_resources(
            "global", include_llm=False, include_services=True
        ) == ["threatbook_api"]

    def test_ensure_threatbook_mcp_config_uses_explicit_secret_reference(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        captured = {}

        def fake_add_mcp_server(name: str, config: dict):
            captured["name"] = name
            captured["config"] = config

        def fake_save_mcp_config(name: str, config: dict):
            captured["saved_name"] = name
            captured["saved_config"] = config

        monkeypatch.setattr(
            onboarding_routes.ConfigWriter,
            "add_mcp_server",
            fake_add_mcp_server,
        )
        monkeypatch.setattr(
            onboarding_routes,
            "save_mcp_config",
            fake_save_mcp_config,
        )

        onboarding_routes._ensure_threatbook_mcp_config("cn")

        assert captured["name"] == "threatbook_mcp"
        assert captured["saved_name"] == "threatbook_mcp"
        assert captured["config"]["url"] == (
            "https://mcp.threatbook.cn/mcp?apikey={secret:threatbook_mcp_key}"
        )

        onboarding_routes._ensure_threatbook_mcp_config("global")
        assert captured["config"]["url"] == (
            "https://mcp.threatbook.cn/mcp?apikey={secret:threatbook_mcp_key}"
        )

    @pytest.mark.asyncio
    async def test_apply_cn_threatbook_model_configures_llm_api_mcp_and_default(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        calls: list[tuple[str, str]] = []

        async def fake_validate(request):
            return onboarding_routes.OnboardingValidateResponse(
                success=True,
                can_apply=True,
                threatbook_enabled=True,
                threatbook_key_valid=True,
                threatbook_region_match=True,
                suggested_region=None,
                error_code=None,
                message="ok",
                threatbook_resources=["threatbook_llm", "threatbook_api", "threatbook_mcp"],
                third_party_llm_valid=None,
                resource_results={},
            )

        async def fake_set_provider_credentials(provider_id, request):
            calls.append(("provider", provider_id))
            return {"success": True}

        async def fake_set_service_credentials(provider_id, request):
            calls.append(("service", provider_id))
            return {"success": True}

        async def fake_update_api_service(provider_id, request):
            assert request.enabled is True
            calls.append(("service_enabled", provider_id))
            return {"success": True}

        def fake_ensure_mcp(region: str):
            calls.append(("ensure_mcp", region))

        async def fake_set_mcp_credentials(name: str, request):
            assert request.secret_id == "threatbook_mcp_key"
            calls.append(("mcp_credentials", name))
            return {"success": True}

        async def fake_connect_mcp(name: str):
            calls.append(("mcp_connect", name))
            return True

        async def fake_set_default_model(model_type, body):
            calls.append(("default_model", body.provider_id, body.model_id))
            return {"provider_id": body.provider_id, "model_id": body.model_id}

        monkeypatch.setattr(onboarding_routes, "_validate_onboarding_request", fake_validate)
        monkeypatch.setattr(onboarding_routes, "set_provider_credentials", fake_set_provider_credentials)
        monkeypatch.setattr(onboarding_routes, "set_service_credentials", fake_set_service_credentials)
        monkeypatch.setattr(onboarding_routes, "update_api_service", fake_update_api_service)
        monkeypatch.setattr(onboarding_routes, "_ensure_threatbook_mcp_config", fake_ensure_mcp)
        monkeypatch.setattr(onboarding_routes, "set_mcp_credentials", fake_set_mcp_credentials)
        monkeypatch.setattr(onboarding_routes, "connect_mcp_server", fake_connect_mcp)
        monkeypatch.setattr(onboarding_routes, "set_default_model", fake_set_default_model)

        resp = await client.post(
            "/api/onboarding/apply",
            json={
                "region": "cn",
                "use_threatbook_model": True,
                "threatbook_api_key": "tb-key",
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["default_model"]["provider_id"] == "threatbook-cn-llm"
        assert data["default_model"]["model_id"] == "deepseek-v4-flash-0731"
        assert ("provider", "threatbook-cn-llm") in calls
        assert ("service", "threatbook-cn") in calls
        assert ("service_enabled", "threatbook-cn") in calls
        assert ("ensure_mcp", "cn") in calls
        assert ("mcp_credentials", "threatbook_mcp") in calls
        assert ("mcp_connect", "threatbook_mcp") in calls
        assert ("default_model", "threatbook-cn-llm", "deepseek-v4-flash-0731") in calls

    @pytest.mark.asyncio
    async def test_apply_threatbook_model_only_does_not_configure_intel_services(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        calls: list[tuple[str, str]] = []

        async def fake_validate(request):
            assert request.threatbook_model_only is True
            return onboarding_routes.OnboardingValidateResponse(
                success=True,
                can_apply=True,
                threatbook_enabled=True,
                threatbook_key_valid=True,
                threatbook_region_match=True,
                suggested_region=None,
                error_code=None,
                message="ok",
                threatbook_resources=["threatbook_llm"],
                third_party_llm_valid=None,
                resource_results={},
            )

        async def fake_set_provider_credentials(provider_id, request):
            calls.append(("provider", provider_id))
            return {"success": True}

        async def fake_set_service_credentials(provider_id, request):
            calls.append(("service", provider_id))
            return {"success": True}

        async def fake_update_api_service(provider_id, request):
            calls.append(("service_enabled", provider_id))
            return {"success": True}

        def fake_ensure_mcp(region: str):
            calls.append(("ensure_mcp", region))

        async def fake_set_mcp_credentials(name: str, request):
            calls.append(("mcp_credentials", name))
            return {"success": True}

        async def fake_connect_mcp(name: str):
            calls.append(("mcp_connect", name))
            return True

        async def fake_set_default_model(model_type, body):
            calls.append(("default_model", body.provider_id))
            return {"provider_id": body.provider_id, "model_id": body.model_id}

        monkeypatch.setattr(onboarding_routes, "_validate_onboarding_request", fake_validate)
        monkeypatch.setattr(onboarding_routes, "set_provider_credentials", fake_set_provider_credentials)
        monkeypatch.setattr(onboarding_routes, "set_service_credentials", fake_set_service_credentials)
        monkeypatch.setattr(onboarding_routes, "update_api_service", fake_update_api_service)
        monkeypatch.setattr(onboarding_routes, "_ensure_threatbook_mcp_config", fake_ensure_mcp)
        monkeypatch.setattr(onboarding_routes, "set_mcp_credentials", fake_set_mcp_credentials)
        monkeypatch.setattr(onboarding_routes, "connect_mcp_server", fake_connect_mcp)
        monkeypatch.setattr(onboarding_routes, "set_default_model", fake_set_default_model)

        resp = await client.post(
            "/api/onboarding/apply",
            json={
                "region": "cn",
                "use_threatbook_model": True,
                "threatbook_model_only": True,
                "threatbook_api_key": "tb-key",
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert ("provider", "threatbook-cn-llm") in calls
        assert ("default_model", "threatbook-cn-llm") in calls
        assert ("service", "threatbook-cn") not in calls
        assert ("service_enabled", "threatbook-cn") not in calls
        assert ("ensure_mcp", "cn") not in calls
        assert ("mcp_credentials", "threatbook_mcp") not in calls
        assert ("mcp_connect", "threatbook_mcp") not in calls
        assert data["configured"] == ["threatbook_llm", "default_llm"]

    @pytest.mark.asyncio
    async def test_apply_returns_400_when_validation_fails(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        async def fake_validate(request):
            return onboarding_routes.OnboardingValidateResponse(
                success=False,
                can_apply=False,
                threatbook_enabled=False,
                threatbook_key_valid=False,
                threatbook_region_match=None,
                suggested_region=None,
                error_code="missing_threatbook_key",
                message="validation failed",
                threatbook_resources=["threatbook_llm", "threatbook_api"],
                third_party_llm_valid=None,
                resource_results={},
            )

        monkeypatch.setattr(onboarding_routes, "_validate_onboarding_request", fake_validate)

        resp = await client.post(
            "/api/onboarding/apply",
            json={
                "region": "cn",
                "use_threatbook_model": True,
                "threatbook_api_key": "",
            },
        )

        assert resp.status_code == 400, resp.text
        assert "validation failed" in resp.text

    @pytest.mark.asyncio
    async def test_apply_global_third_party_model_without_threatbook_key_skips_threatbook(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        calls: list[tuple[str, str]] = []

        async def fake_validate(request):
            return onboarding_routes.OnboardingValidateResponse(
                success=True,
                can_apply=True,
                threatbook_enabled=False,
                threatbook_key_valid=None,
                threatbook_region_match=None,
                suggested_region=None,
                error_code=None,
                message="ok",
                threatbook_resources=["threatbook_api"],
                third_party_llm_valid=True,
                resource_results={},
            )

        async def fake_set_provider_credentials(provider_id, request):
            calls.append(("provider", provider_id))
            return {"success": True}

        async def fake_set_default_model(model_type, body):
            calls.append(("default_model", body.provider_id))
            return {"provider_id": body.provider_id, "model_id": body.model_id}

        monkeypatch.setattr(onboarding_routes, "_validate_onboarding_request", fake_validate)
        monkeypatch.setattr(onboarding_routes, "set_provider_credentials", fake_set_provider_credentials)
        monkeypatch.setattr(onboarding_routes, "set_default_model", fake_set_default_model)

        resp = await client.post(
            "/api/onboarding/apply",
            json={
                "region": "global",
                "use_threatbook_model": False,
                "third_party_llm": {
                    "provider_id": "openai",
                    "api_key": "sk-test",
                    "model_id": "gpt-4o",
                },
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["threatbook_enabled"] is False
        assert data["skipped"] == ["threatbook_api"]
        assert ("provider", "openai") in calls
        assert ("default_model", "openai") in calls

    @pytest.mark.asyncio
    async def test_apply_global_intelligence_key_configures_api_only(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        calls: list[tuple[str, str]] = []

        async def fake_validate(request):
            return onboarding_routes.OnboardingValidateResponse(
                success=True,
                can_apply=True,
                threatbook_enabled=True,
                threatbook_key_valid=True,
                threatbook_region_match=True,
                suggested_region=None,
                error_code=None,
                message="ok",
                threatbook_resources=["threatbook_api"],
                third_party_llm_valid=None,
                resource_results={},
            )

        async def fake_set_service_credentials(provider_id, request):
            calls.append(("service", provider_id))
            return {"success": True}

        async def fake_update_api_service(provider_id, request):
            calls.append(("service_enabled", provider_id))
            return {"success": True}

        def fail_ensure_mcp(region: str):
            raise AssertionError("International intelligence must not configure MCP")

        monkeypatch.setattr(onboarding_routes, "_validate_onboarding_request", fake_validate)
        monkeypatch.setattr(onboarding_routes, "set_service_credentials", fake_set_service_credentials)
        monkeypatch.setattr(onboarding_routes, "update_api_service", fake_update_api_service)
        monkeypatch.setattr(onboarding_routes, "_ensure_threatbook_mcp_config", fail_ensure_mcp)

        resp = await client.post(
            "/api/onboarding/apply",
            json={
                "region": "global",
                "use_threatbook_model": False,
                "threatbook_services_only": True,
                "threatbook_api_key": "tb-global-key",
            },
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["configured"] == ["threatbook_api"]
        assert calls == [
            ("service", "threatbook-io"),
            ("service_enabled", "threatbook-io"),
        ]

    @pytest.mark.asyncio
    async def test_apply_cn_third_party_model_with_threatbook_key_only_configures_api_mcp(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        calls: list[tuple[str, str]] = []

        async def fake_validate(request):
            return onboarding_routes.OnboardingValidateResponse(
                success=True,
                can_apply=True,
                threatbook_enabled=True,
                threatbook_key_valid=True,
                threatbook_region_match=True,
                suggested_region=None,
                error_code=None,
                message="ok",
                threatbook_resources=["threatbook_api", "threatbook_mcp"],
                third_party_llm_valid=True,
                resource_results={},
            )

        async def fake_set_provider_credentials(provider_id, request):
            calls.append(("provider", provider_id))
            return {"success": True}

        async def fake_set_service_credentials(provider_id, request):
            calls.append(("service", provider_id))
            return {"success": True}

        async def fake_update_api_service(provider_id, request):
            assert request.enabled is True
            calls.append(("service_enabled", provider_id))
            return {"success": True}

        def fake_ensure_mcp(region: str):
            calls.append(("ensure_mcp", region))

        async def fake_set_mcp_credentials(name: str, request):
            assert request.secret_id == "threatbook_mcp_key"
            calls.append(("mcp_credentials", name))
            return {"success": True}

        async def fake_connect_mcp(name: str):
            calls.append(("mcp_connect", name))
            return True

        async def fake_set_default_model(model_type, body):
            calls.append(("default_model", body.provider_id))
            return {"provider_id": body.provider_id, "model_id": body.model_id}

        monkeypatch.setattr(onboarding_routes, "_validate_onboarding_request", fake_validate)
        monkeypatch.setattr(onboarding_routes, "set_provider_credentials", fake_set_provider_credentials)
        monkeypatch.setattr(onboarding_routes, "set_service_credentials", fake_set_service_credentials)
        monkeypatch.setattr(onboarding_routes, "update_api_service", fake_update_api_service)
        monkeypatch.setattr(onboarding_routes, "_ensure_threatbook_mcp_config", fake_ensure_mcp)
        monkeypatch.setattr(onboarding_routes, "set_mcp_credentials", fake_set_mcp_credentials)
        monkeypatch.setattr(onboarding_routes, "connect_mcp_server", fake_connect_mcp)
        monkeypatch.setattr(onboarding_routes, "set_default_model", fake_set_default_model)

        resp = await client.post(
            "/api/onboarding/apply",
            json={
                "region": "cn",
                "use_threatbook_model": False,
                "threatbook_services_only": True,
                "threatbook_api_key": "tb-key",
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert ("provider", "threatbook-cn-llm") not in calls
        assert ("service", "threatbook-cn") in calls
        assert ("service_enabled", "threatbook-cn") in calls
        assert ("ensure_mcp", "cn") in calls
        assert ("mcp_credentials", "threatbook_mcp") in calls
        assert ("mcp_connect", "threatbook_mcp") in calls
        assert ("provider", "openai") not in calls
        assert ("default_model", "openai") not in calls
        assert "default_llm" not in data["skipped"]
