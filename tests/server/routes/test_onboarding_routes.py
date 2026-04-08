from __future__ import annotations

import pytest

from flocks.server.routes import onboarding as onboarding_routes


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
            return {"provider_id": "threatbook-cn-llm", "model_id": "qwen3.6-plus"}

        monkeypatch.setattr(onboarding_routes.Config, "resolve_default_llm", fake_resolve)
        monkeypatch.setattr(onboarding_routes, "_llm_provider_has_usable_credentials", lambda _pid: True)

        resp = await client.get("/api/onboarding/status")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["completed"] is True
        assert data["has_default_model"] is True
        assert data["default_model"]["provider_id"] == "threatbook-cn-llm"


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


class TestOnboardingApplyRoutes:
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
                "threatbook_api_key": "tb-key",
            },
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["default_model"]["provider_id"] == "threatbook-cn-llm"
        assert ("provider", "threatbook-cn-llm") in calls
        assert ("service", "threatbook-cn") in calls
        assert ("service_enabled", "threatbook-cn") in calls
        assert ("ensure_mcp", "cn") in calls
        assert ("mcp_credentials", "threatbook_mcp") in calls
        assert ("mcp_connect", "threatbook_mcp") in calls
        assert ("default_model", "threatbook-cn-llm") in calls

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
        assert set(data["skipped"]) == {"threatbook_api"}
        assert ("provider", "openai") in calls
        assert ("default_model", "openai") in calls

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
