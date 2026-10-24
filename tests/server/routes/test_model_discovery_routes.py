"""Discovery authorization and additive, durable batch model imports."""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from flocks.auth.context import AuthUser
from flocks.config.config import Config, ConfigInfo, ProviderConfig
from flocks.config.config_writer import ConfigWriter
from flocks.provider.provider import Provider


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    from flocks.bus import Bus
    from flocks.server.routes import model_discovery as routes

    initial = {
        "provider": {
            "custom-inventory": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Private Gateway",
                "options": {"baseURL": "https://gateway.test/v1", "apiKey": "{secret:gateway-key}"},
                "models": {
                    "existing": {
                        "name": "Hand Edited Name",
                        "context_window": 96000,
                        "supports_vision": True,
                        "supports_tools": False,
                        "input_price": 3.0,
                        "cache_read_price": 0.3,
                        "default_parameters": {"temperature": 0.1},
                    }
                },
            }
        },
        "default_models": {"llm": {"provider_id": "custom-inventory", "model_id": "existing"}},
        "fallback_providers": [{"provider_id": "custom-inventory", "model_id": "existing"}],
        "model_settings": {
            "custom-inventory/existing": {
                "enabled": False,
                "default_parameters": {"temperature": 0.25},
            }
        },
        "custom_extension": {"preserve": True},
    }
    config_file = tmp_path / "flocks.json"
    config_file.write_text(json.dumps(initial), encoding="utf-8")
    monkeypatch.setattr(ConfigWriter, "_get_config_path", lambda: config_file)

    async def get_config():
        return ConfigInfo.model_validate(json.loads(config_file.read_text()))

    monkeypatch.setattr(Config, "get", get_config)
    monkeypatch.setattr(routes, "_ensure_provider_initialized", AsyncMock())
    monkeypatch.setattr(routes, "_load_dynamic_providers", AsyncMock())
    monkeypatch.setattr(Provider, "_providers", {})
    monkeypatch.setattr(Provider, "_models", {})
    monkeypatch.setattr(Provider, "_initialized", True)
    actual_apply_config = Provider.apply_config
    apply_config = AsyncMock()
    monkeypatch.setattr(Provider, "apply_config", apply_config)
    publish = AsyncMock()
    monkeypatch.setattr(Bus, "publish", publish)
    secrets = MagicMock()
    secrets.get.return_value = None
    monkeypatch.setattr("flocks.security.get_secret_manager", lambda: secrets)

    original_write = ConfigWriter._write_raw
    write = MagicMock(side_effect=original_write)
    monkeypatch.setattr(ConfigWriter, "_write_raw", write)
    return SimpleNamespace(
        routes=routes,
        initial=initial,
        config_file=config_file,
        write=write,
        apply_config=apply_config,
        actual_apply_config=actual_apply_config,
        publish=publish,
        secrets=secrets,
    )


@pytest.fixture
async def inventory_client(inventory):
    from flocks.server.app import validation_exception_handler

    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    @app.middleware("http")
    async def authenticated_test_user(request, call_next):
        request.state.auth_user = AuthUser(
            id="inventory-test-user",
            username="inventory-tester",
            role=request.headers.get("x-test-role", "admin"),
        )
        return await call_next(request)

    app.include_router(inventory.routes.router, prefix="/api/provider")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


def saved(inventory):
    return json.loads(inventory.config_file.read_text(encoding="utf-8"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,body",
    [
        ("discover-models", {"api_key": "unsaved-secret", "base_url": "https://candidate.test/v1"}),
        ("import-models", {"models": [{"id": "new-model", "name": "New Model"}]}),
    ],
)
async def test_members_cannot_discover_or_import(operation, body, inventory, inventory_client, monkeypatch):
    discover = AsyncMock()
    monkeypatch.setattr(inventory.routes, "discover_models", discover)
    response = await inventory_client.post(
        f"/api/provider/custom-inventory/{operation}",
        json=body,
        headers={"x-test-role": "member"},
    )

    assert response.status_code == 403
    assert saved(inventory) == inventory.initial
    inventory.write.assert_not_called()
    discover.assert_not_awaited()


@pytest.mark.asyncio
async def test_discovery_forwards_transient_credentials_and_returns_structured_errors(
    inventory,
    inventory_client,
    monkeypatch,
):
    result = {
        "source": "none",
        "status": "error",
        "code": "authentication_failed",
        "message": "The provider rejected the credentials.",
        "fetched_at": "2026-10-21T10:00:00+00:00",
        "truncated": False,
        "models": [],
    }
    discover = AsyncMock(return_value=result)
    monkeypatch.setattr(inventory.routes, "discover_models", discover)

    response = await inventory_client.post(
        "/api/provider/custom-inventory/discover-models",
        json={
            "api_key": "transient-secret",
            "base_url": "https://candidate.test/v1",
        },
    )

    assert response.status_code == 200
    assert response.json() == result
    discover.assert_awaited_once_with(
        "custom-inventory",
        api_key="transient-secret",
        base_url="https://candidate.test/v1",
    )
    assert saved(inventory) == inventory.initial
    inventory.write.assert_not_called()
    inventory.apply_config.assert_not_awaited()
    assert "transient-secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,body",
    [
        ("discover-models", {}),
        ("import-models", {"models": [{"id": "new-model", "name": "New Model"}]}),
    ],
)
async def test_unknown_provider_is_a_client_error(operation, body, inventory, inventory_client):
    response = await inventory_client.post(f"/api/provider/not-a-provider/{operation}", json=body)

    assert response.status_code == 404
    inventory.write.assert_not_called()


@pytest.mark.asyncio
async def test_batch_import_preserves_existing_configuration_and_writes_once(inventory, inventory_client):
    response = await inventory_client.post(
        "/api/provider/custom-inventory/import-models",
        json={
            "models": [
                {"id": "existing", "name": "Overwrite Attempt", "context_window": 2000, "supports_tools": True},
                {
                    "id": "vendor/new-model:latest",
                    "name": "New Model",
                    "context_window": 64000,
                    "max_output_tokens": 8192,
                    "supports_vision": False,
                    "supports_tools": True,
                },
                {"id": "bare-model", "name": "Bare Model"},
            ]
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["added"] == ["vendor/new-model:latest", "bare-model"]
    assert result["skipped"] == ["existing"]
    assert result["failed"] == []
    after = saved(inventory)
    provider = after["provider"]["custom-inventory"]
    original_provider = inventory.initial["provider"]["custom-inventory"]
    assert provider["models"]["existing"] == original_provider["models"]["existing"]
    assert provider["options"] == original_provider["options"]
    assert provider["model_sync"] == "manual"
    assert provider["models"]["vendor/new-model:latest"]["supports_vision"] is False
    assert provider["models"]["vendor/new-model:latest"]["max_output_tokens"] == 8192
    for field in (
        "supports_vision",
        "supports_tools",
        "supports_reasoning",
        "input_price",
        "output_price",
        "context_window",
    ):
        assert field not in provider["models"]["bare-model"]
    for key in ("default_models", "fallback_providers", "model_settings", "custom_extension"):
        assert after[key] == inventory.initial[key]
    inventory.write.assert_called_once()
    inventory.apply_config.assert_awaited_once()
    inventory.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_import_is_idempotent_and_deduplicates_the_batch(inventory, inventory_client):
    body = {
        "models": [
            {"id": "unique", "name": "First Name"},
            {"id": "unique", "name": "Second Name"},
        ]
    }

    first = await inventory_client.post("/api/provider/custom-inventory/import-models", json=body)
    second = await inventory_client.post("/api/provider/custom-inventory/import-models", json=body)

    assert first.status_code == second.status_code == 200
    assert first.json()["added"] == ["unique"]
    assert second.json()["added"] == []
    assert "unique" in second.json()["skipped"]
    assert saved(inventory)["provider"]["custom-inventory"]["models"]["unique"]["name"] == "First Name"


@pytest.mark.asyncio
async def test_concurrent_imports_do_not_lose_models_or_overwrite_existing(inventory, inventory_client):
    bodies = [
        {"models": [{"id": "shared", "name": "Shared"}, {"id": "first", "name": "First"}]},
        {"models": [{"id": "shared", "name": "Other Shared"}, {"id": "second", "name": "Second"}]},
    ]

    responses = await asyncio.gather(
        *[inventory_client.post("/api/provider/custom-inventory/import-models", json=body) for body in bodies]
    )

    assert all(response.status_code == 200 for response in responses)
    assert sum("shared" in response.json()["added"] for response in responses) == 1
    assert set(saved(inventory)["provider"]["custom-inventory"]["models"]) == {"existing", "shared", "first", "second"}
    assert saved(inventory)["default_models"] == inventory.initial["default_models"]


@pytest.mark.asyncio
async def test_first_import_does_not_choose_a_default_model(inventory, inventory_client):
    data = copy.deepcopy(inventory.initial)
    data["provider"]["custom-inventory"]["models"] = {}
    data.pop("default_models")
    data["fallback_providers"] = []
    inventory.config_file.write_text(json.dumps(data), encoding="utf-8")

    response = await inventory_client.post(
        "/api/provider/custom-inventory/import-models",
        json={
            "models": [
                {"id": "only-model", "name": "Only Model"},
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert not saved(inventory).get("default_models")
    assert saved(inventory)["fallback_providers"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [
        {"id": "", "name": "Empty ID"},
        {"id": "bad\nmodel", "name": "Control Character"},
        {"id": "too-long-" + "x" * 513, "name": "Too Long"},
        {"id": "bad-limits", "name": "Bad Limits", "context_window": -1},
    ],
)
async def test_invalid_import_items_do_not_mutate_config(model, inventory, inventory_client):
    response = await inventory_client.post("/api/provider/custom-inventory/import-models", json={"models": [model]})

    assert response.status_code == 422, response.text
    inventory.write.assert_not_called()
    assert saved(inventory) == inventory.initial


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["import-models", "model-inventory"])
@pytest.mark.parametrize("model_id", ["text-embedding-3-small", "whisper-1"])
async def test_known_non_chat_models_cannot_be_imported_by_direct_requests(operation, model_id, inventory, inventory_client):
    body = {"models": [{"id": "valid-chat", "name": "Chat"}, {"id": model_id, "name": model_id}]}
    if operation == "model-inventory":
        body["deleted"] = ["existing"]
    response = await inventory_client.post(f"/api/provider/custom-inventory/{operation}", json=body)
    assert response.status_code == 422, response.text
    assert "not a chat model" in response.json()["detail"]
    assert saved(inventory) == inventory.initial
    inventory.write.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["import-models", "model-inventory"])
async def test_control_character_name_uses_production_validation_handler_without_mutation(operation, inventory, inventory_client):
    response = await inventory_client.post(f"/api/provider/custom-inventory/{operation}", json={
        "models": [{"id": "valid-chat", "name": "PR69 bad\tname"}],
    })
    assert response.status_code == 422, response.text
    error = response.json()
    assert error["error"] == "ValidationError"
    assert error["details"][0]["loc"] == ["body", "models", 0, "name"]
    assert "control characters" in error["details"][0]["msg"]
    assert isinstance(error["details"][0]["ctx"]["error"], str)
    assert saved(inventory) == inventory.initial
    inventory.write.assert_not_called()


def test_manual_inventory_preserves_empty_and_partial_selection_after_catalog_sync(inventory):
    from flocks.provider.model_catalog import sync_catalog_models_to_config

    data = {
        "provider": {
            "openai": {"model_sync": "manual", "models": {}},
            "anthropic": {
                "model_sync": "manual",
                "models": {
                    "my-private-model": {"name": "Hand Chosen"},
                },
            },
        },
    }
    inventory.config_file.write_text(json.dumps(data), encoding="utf-8")

    assert sync_catalog_models_to_config() == 0
    assert saved(inventory) == data
    assert sync_catalog_models_to_config() == 0
    assert saved(inventory) == data


def test_catalog_sync_keeps_manual_retired_models_and_their_references(inventory, monkeypatch):
    from flocks.provider import model_catalog

    monkeypatch.setattr(
        model_catalog, "get_retired_model_ids", lambda pid: frozenset({"old-model"}) if pid == "openai" else frozenset()
    )
    data = {
        "provider": {"openai": {"model_sync": "manual", "models": {"old-model": {"name": "Still Needed"}}}},
        "default_models": {"llm": {"provider_id": "openai", "model_id": "old-model"}},
        "fallback_providers": [{"provider_id": "openai", "model_id": "old-model"}],
        "model_settings": {"openai/old-model": {"enabled": False}},
    }
    inventory.config_file.write_text(json.dumps(data), encoding="utf-8")

    model_catalog.sync_catalog_models_to_config()

    assert saved(inventory) == data


def test_legacy_provider_keeps_automatic_catalog_sync(inventory):
    from flocks.provider.model_catalog import get_provider_model_definitions, sync_catalog_models_to_config

    inventory.config_file.write_text(json.dumps({"provider": {"openai": {"models": {}}}}), encoding="utf-8")

    added = sync_catalog_models_to_config()

    expected = {model.id for model in get_provider_model_definitions("openai")}
    assert expected
    assert added == len(expected)
    assert set(saved(inventory)["provider"]["openai"]["models"]) == expected


@pytest.mark.parametrize("models", [{}, {"deployment-private": {"name": "Private Deployment"}}])
def test_manual_azure_inventory_survives_runtime_reconstruction(models, inventory):
    from flocks.provider.sdk.azure import AzureProvider

    config = ProviderConfig.model_validate({"models": models, "model_sync": "manual"})
    provider = AzureProvider()

    Provider.apply_provider_config(provider, config)

    assert {model.id for model in provider.get_model_definitions()} == set(models)


@pytest.mark.asyncio
async def test_import_registers_and_populates_a_new_dynamic_provider(inventory, inventory_client, monkeypatch):
    from flocks.server.routes import provider as provider_routes

    monkeypatch.setattr(inventory.routes, "_load_dynamic_providers", provider_routes._load_dynamic_providers)
    monkeypatch.setattr(Provider, "apply_config", inventory.actual_apply_config)

    response = await inventory_client.post(
        "/api/provider/custom-inventory/import-models",
        json={
            "models": [
                {"id": "private-new", "name": "Private New"},
            ]
        },
    )

    assert response.status_code == 200, response.text
    provider = Provider._providers["custom-inventory"]
    assert {model.id for model in provider.get_model_definitions()} == {"existing", "private-new"}
    assert {model.id for model in provider.get_models()} == {"existing", "private-new"}
    assert provider._model_sync_manual is True


@pytest.mark.asyncio
@pytest.mark.parametrize("skip_bootstrap", [False, True])
async def test_new_provider_credentials_preserve_legacy_or_explicit_selection_mode(
    skip_bootstrap,
    inventory,
    monkeypatch,
):
    from flocks.provider.model_catalog import get_provider_model_definitions
    from flocks.server.routes import provider as provider_routes

    monkeypatch.setattr(provider_routes, "_get_provider_custom_settings", lambda provider: {})
    response = await provider_routes.set_provider_credentials(
        "openai",
        provider_routes.ProviderCredentialRequest(api_key="saved-key", skip_catalog_bootstrap=skip_bootstrap),
    )

    assert response["success"] is True
    provider = saved(inventory)["provider"]["openai"]
    if skip_bootstrap:
        assert provider["models"] == {}
        assert provider["model_sync"] == "manual"
    else:
        assert set(provider["models"]) == {model.id for model in get_provider_model_definitions("openai")}
        assert provider.get("model_sync") != "manual"


@pytest.mark.asyncio
async def test_import_notifies_real_sse_subscribers_with_provider_identity_only(
    inventory, inventory_client, monkeypatch
):
    from flocks.auth.context import API_TOKEN_SERVICE_USER_ID, reset_current_auth_user, set_current_auth_user
    from flocks.server.routes.event import EventBroadcaster

    broadcaster = EventBroadcaster()
    monkeypatch.setattr(EventBroadcaster, "_instance", broadcaster)
    admin = AuthUser(id="browser-admin", username="admin", role="admin")
    member = AuthUser(id="browser-member", username="member", role="member")
    admin_queue = await broadcaster.subscribe(admin)
    member_queue = await broadcaster.subscribe(member)
    token = set_current_auth_user(AuthUser(id=API_TOKEN_SERVICE_USER_ID, username="api-service", role="admin"))
    try:
        response = await inventory_client.post(
            "/api/provider/custom-inventory/import-models",
            json={"models": [{"id": "sse-new-model", "name": "SSE New Model"}]},
        )
    finally:
        reset_current_auth_user(token)

    assert response.status_code == 200, response.text
    assert admin_queue.get_nowait() == {"type": "provider.updated", "properties": {"provider_id": "custom-inventory"}}
    assert admin_queue.empty()
    assert member_queue.empty()
    inventory.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_inventory_all_off_keeps_models_metadata_and_routing(inventory, inventory_client):
    response = await inventory_client.post('/api/provider/custom-inventory/model-inventory', json={
        'models': [{'id': 'existing', 'name': 'Ignored remote name', 'enabled': False}], 'deleted': [],
    })
    assert response.status_code == 200
    data = saved(inventory)
    assert data['provider']['custom-inventory']['models'] == inventory.initial['provider']['custom-inventory']['models']
    assert data['default_models'] == inventory.initial['default_models']
    assert data['fallback_providers'] == inventory.initial['fallback_providers']
    assert data['model_settings']['custom-inventory/existing']['enabled'] is False
    assert data['model_settings']['custom-inventory/existing']['default_parameters'] == {'temperature': 0.25}
    assert inventory.write.call_count == 1


@pytest.mark.asyncio
async def test_inventory_auto_edits_and_explicit_deletion_are_atomic(inventory, inventory_client):
    response = await inventory_client.post('/api/provider/custom-inventory/model-inventory', json={
        'models': [{'id': 'new', 'name': 'New', 'enabled': True, 'supports_streaming': True},
                   {'id': 'off', 'name': 'Off', 'enabled': False}], 'deleted': ['existing'],
    })
    assert response.status_code == 200
    data = saved(inventory)
    assert set(data['provider']['custom-inventory']['models']) == {'new'}
    assert data['default_models'] == {}
    assert data['fallback_providers'] == []
    assert 'custom-inventory/existing' not in data['model_settings']
    assert data['provider']['custom-inventory']['models']['new'] == {'name': 'New', 'supports_streaming': True}
    assert inventory.write.call_count == 1


@pytest.mark.asyncio
async def test_inventory_auto_removes_override_without_touching_other_metadata(inventory, inventory_client):
    response = await inventory_client.post('/api/provider/custom-inventory/model-inventory', json={
        'models': [{'id': 'existing', 'name': 'Edited', 'enabled': True, 'edited': True,
                    'context_window': None, 'max_output_tokens': None, 'supports_tools': False}],
    })
    assert response.status_code == 200
    model = saved(inventory)['provider']['custom-inventory']['models']['existing']
    assert 'context_window' not in model and 'max_output_tokens' not in model
    assert model['name'] == 'Edited' and model['supports_tools'] is False
    assert model['input_price'] == 3.0 and model['cache_read_price'] == 0.3
    assert model['supports_vision'] is True
    response = await inventory_client.get('/api/provider/custom-inventory/model-inventory')
    assert response.status_code == 200
    assert response.json()['models'][0]['enabled'] is True
    assert 'context_window' not in response.json()['models'][0]


@pytest.mark.asyncio
async def test_inventory_shows_effective_capabilities_without_materializing_runtime_limits(inventory, inventory_client):
    Provider._providers['custom-inventory'] = SimpleNamespace(get_model_definitions=lambda: [SimpleNamespace(
        id='existing', capabilities=SimpleNamespace(supports_reasoning=False, supports_streaming=True, context_window=128000)
    )])
    response = await inventory_client.get('/api/provider/custom-inventory/model-inventory')
    assert response.status_code == 200
    model = response.json()['models'][0]
    assert model['supports_tools'] is False and model['supports_vision'] is True
    assert model['supports_reasoning'] is False and model['supports_streaming'] is True
    assert model['context_window'] == 96000 and 'max_output_tokens' not in model
    inventory.write.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['model-inventory', 'check-key'])
async def test_inventory_and_key_checks_remain_admin_only(operation, inventory, inventory_client):
    response = await inventory_client.post(f'/api/provider/custom-inventory/{operation}', json={}, headers={'x-test-role': 'member'})
    assert response.status_code == 403
    assert saved(inventory) == inventory.initial


@pytest.mark.asyncio
@pytest.mark.parametrize('control_code,status', [('authentication_failed', 'valid'), ('models_discovered', 'unverified')])
async def test_key_check_requires_authentication_evidence_and_never_saves(control_code, status, inventory, inventory_client, monkeypatch):
    discover = AsyncMock(side_effect=[{'source': 'api', 'status': 'success', 'code': 'models_discovered'},
                                     {'source': 'api', 'status': 'success', 'code': control_code}])
    monkeypatch.setattr(inventory.routes, 'discover_models', discover)
    response = await inventory_client.post('/api/provider/custom-inventory/check-key', json={'api_key': 'candidate-key', 'base_url': 'https://candidate.test/v1'})
    assert response.status_code == 200 and response.json()['status'] == status
    assert 'models' not in response.json()
    assert discover.await_args_list[0].kwargs['api_key'] == 'candidate-key'
    assert discover.await_args_list[1].kwargs['api_key'] != 'candidate-key'
    assert saved(inventory) == inventory.initial
    inventory.write.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_id', ['threatbook-cn-llm', 'threatbook-io-llm'])
@pytest.mark.parametrize('source,result_status,code,models,status,expected_code', [
    ('api', 'success', 'models_discovered', [{'id': 'live-model'}], 'valid', 'model_list_verified'),
    ('api', 'success', 'models_discovered', [], 'valid', 'model_list_verified'),
    ('catalog', 'fallback', 'catalog_fallback', [{'id': 'reference-model'}], 'unverified', 'catalog_fallback'),
    ('none', 'error', 'authentication_failed', [], 'invalid', 'authentication_failed'),
    ('none', 'error', 'invalid_response', [], 'unverified', 'invalid_response'),
    ('none', 'error', 'connection_timeout', [], 'unverified', 'connection_timeout'),
])
async def test_threatbook_key_check_uses_one_live_inventory_request_without_saving(
    provider_id, source, result_status, code, models, status, expected_code, inventory, inventory_client, monkeypatch
):
    discover = AsyncMock(return_value={'source': source, 'status': result_status, 'code': code, 'models': models})
    monkeypatch.setattr(inventory.routes, 'discover_models', discover)
    response = await inventory_client.post(f'/api/provider/{provider_id}/check-key', json={
        'api_key': 'candidate-key', 'base_url': 'https://configured.test/v1',
    })
    assert response.status_code == 200
    assert response.json() == {'status': status, 'code': expected_code}
    discover.assert_awaited_once_with(provider_id, api_key='candidate-key', base_url='https://configured.test/v1')
    assert saved(inventory) == inventory.initial
    inventory.write.assert_not_called()
    inventory.apply_config.assert_not_awaited()
    assert Provider._providers == {}


@pytest.mark.asyncio
async def test_key_check_rejects_invalid_credentials_without_control_or_save(inventory, inventory_client, monkeypatch):
    discover = AsyncMock(return_value={'source': 'none', 'status': 'error', 'code': 'authentication_failed'})
    monkeypatch.setattr(inventory.routes, 'discover_models', discover)
    response = await inventory_client.post('/api/provider/custom-inventory/check-key', json={'api_key': 'bad-key'})
    assert response.json() == {'status': 'invalid', 'code': 'authentication_failed'}
    assert discover.await_count == 1
    inventory.write.assert_not_called()
