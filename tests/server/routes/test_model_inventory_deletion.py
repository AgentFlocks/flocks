"""Explicit model deletion is atomic, durable, and scoped to one provider."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI

from flocks.config.config import ProviderConfig as ProviderSettings
from flocks.config.config_writer import ConfigWriter
from flocks.provider.provider import ModelCapabilities, ModelInfo, Provider, ProviderConfig
from flocks.server.auth import require_admin


@pytest.fixture
def deletion_inventory(tmp_path, monkeypatch):
    from flocks.bus import Bus
    from flocks.provider.sdk.azure import AzureProvider
    from flocks.server.routes import custom_provider, model, provider as provider_routes

    config_path = tmp_path / "flocks.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ConfigWriter, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(Provider, "_initialized", True)
    monkeypatch.setattr(Provider, "_providers", {})
    monkeypatch.setattr(Provider, "_models", {})
    initialize = AsyncMock()
    monkeypatch.setattr(provider_routes, "_ensure_provider_initialized", initialize)
    publish = AsyncMock()
    monkeypatch.setattr(Bus, "publish", publish)
    actual_write = ConfigWriter._write_raw
    write = MagicMock(side_effect=actual_write)
    monkeypatch.setattr(ConfigWriter, "_write_raw", write)

    def seed(model_id="vendor/model?variant#tag", remaining=None):
        models = {model_id: {"name": "Remove Me"}, **(remaining or {})}
        identity = f"azure/{model_id}"
        other_identity = f"other/{model_id}"
        data = {
            "provider": {
                "azure": {
                    "npm": "@ai-sdk/azure",
                    "options": {"apiKey": "{secret:do-not-resolve}", "baseURL": "https://azure.test"},
                    "models": models,
                },
                "other": {"models": {model_id: {"name": "Keep Other Provider"}}},
            },
            "default_models": {
                "llm": {"provider_id": "azure", "model_id": model_id},
                "title": {"provider_id": "azure", "model_id": model_id},
                "compaction": {"provider_id": "other", "model_id": model_id},
                "default_parameters": {"temperature": 0.3},
            },
            "model": identity,
            "small_model": identity,
            "smallModel": identity,
            "model_settings": {
                identity: {"enabled": False, "default_parameters": {"temperature": 0.5}},
                other_identity: {"enabled": False, "default_parameters": {"temperature": 0.7}},
            },
            "fallback_providers": [
                {"provider_id": "azure", "model_id": model_id},
                {"provider_id": "other", "model_id": model_id},
                {"provider_id": "azure", "model_id": "different-model"},
            ],
            "unrelated": {"keep": True},
        }
        config_path.write_text(json.dumps(data), encoding="utf-8")
        runtime = AzureProvider()
        runtime.configure(
            ProviderConfig(provider_id="azure", api_key="already-resolved-key", base_url="https://azure.test")
        )
        Provider.apply_provider_config(runtime, ProviderSettings.model_validate({"models": models}))
        Provider._providers["azure"] = runtime
        # A global model ID can point to another provider. Deleting azure's
        # entry must not evict that other provider's cache entry.
        other_model = ModelInfo(
            id=model_id,
            name="Other Runtime Model",
            provider_id="other",
            capabilities=ModelCapabilities(),
        )
        Provider._models[model_id] = other_model
        return data, runtime, other_model

    app = FastAPI()
    app.dependency_overrides[require_admin] = lambda: object()
    app.include_router(model.router, prefix="/api/model")
    app.include_router(custom_provider.router, prefix="/api/custom")
    return SimpleNamespace(
        config_path=config_path,
        seed=seed,
        app=app,
        write=write,
        publish=publish,
        initialize=initialize,
    )


def saved(inventory):
    return json.loads(inventory.config_path.read_text(encoding="utf-8"))


@pytest.fixture
async def deletion_client(deletion_inventory):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=deletion_inventory.app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.parametrize("route", ["/api/model/v2/definitions", "/api/custom/models"])
@pytest.mark.parametrize(
    "model_id", ["namespace/model", "model?revision=1", "model#release", "namespace/model?revision#release"]
)
@pytest.mark.asyncio
async def test_delete_routes_accept_encoded_model_identity_and_clear_last_runtime_model(
    route, model_id, deletion_inventory, deletion_client
):
    initial, runtime, other_model = deletion_inventory.seed(model_id)
    credentials_before = runtime._config
    response = await deletion_client.delete(f"{route}/azure/{quote(model_id, safe='')}")

    assert response.status_code == 204, response.text
    after = saved(deletion_inventory)
    assert after["provider"]["azure"]["models"] == {}
    assert after["provider"]["azure"]["model_sync"] == "manual"
    assert after["provider"]["azure"]["options"] == initial["provider"]["azure"]["options"]
    assert runtime._config is credentials_before
    assert runtime._model_sync_manual is True
    assert runtime.get_models() == []
    assert runtime.get_model_definitions() == []
    assert Provider.list_models("azure") == []
    assert Provider._models[model_id] is other_model
    assert after["provider"]["other"] == initial["provider"]["other"]
    assert after["default_models"] == {
        "compaction": initial["default_models"]["compaction"],
        "default_parameters": {"temperature": 0.3},
    }
    assert after["model_settings"] == {
        f"other/{model_id}": initial["model_settings"][f"other/{model_id}"],
    }
    assert after["fallback_providers"] == initial["fallback_providers"][1:]
    assert all(field not in after for field in ("model", "small_model", "smallModel"))
    assert after["unrelated"] == initial["unrelated"]
    deletion_inventory.write.assert_called_once()
    deletion_inventory.publish.assert_awaited_once()
    assert deletion_inventory.publish.await_args.args[0].type == "provider.updated"
    assert deletion_inventory.publish.await_args.args[1] == {"provider_id": "azure"}


@pytest.mark.parametrize("route", ["/api/model/v2/definitions", "/api/custom/models"])
@pytest.mark.asyncio
async def test_missing_model_does_not_change_inventory_policy_or_references(route, deletion_inventory, deletion_client):
    initial, _, _ = deletion_inventory.seed()
    response = await deletion_client.delete(f"{route}/azure/not-present")

    assert response.status_code == 404
    assert saved(deletion_inventory) == initial
    deletion_inventory.write.assert_not_called()
    deletion_inventory.publish.assert_not_awaited()
    deletion_inventory.initialize.assert_not_awaited()


@pytest.mark.asyncio
async def test_removing_one_model_preserves_other_inventory_metadata_and_legacy_references(
    deletion_inventory, deletion_client
):
    keep = {"kept-model": {"name": "My Name", "supports_tools": False, "input_price": 1.3}}
    initial, runtime, _ = deletion_inventory.seed("remove-me", keep)
    data = copy.deepcopy(initial)
    data["model"] = "other/remove-me"
    data["small_model"] = "azure/kept-model"
    data["smallModel"] = "azure/kept-model"
    deletion_inventory.config_path.write_text(json.dumps(data), encoding="utf-8")

    response = await deletion_client.delete("/api/model/v2/definitions/azure/remove-me")

    assert response.status_code == 204, response.text
    after = saved(deletion_inventory)
    assert after["provider"]["azure"]["models"] == keep
    assert after["model"] == "other/remove-me"
    assert after["small_model"] == after["smallModel"] == "azure/kept-model"
    assert [model.id for model in runtime.get_models()] == ["kept-model"]
    assert runtime.get_models()[0].capabilities.supports_tools is False
    deletion_inventory.write.assert_called_once()


def test_legacy_catalog_deletion_survives_startup_sync_and_runtime_reconstruction(deletion_inventory):
    from flocks.provider.model_catalog import get_provider_model_definitions, sync_catalog_models_to_config
    from flocks.provider.sdk.openai import OpenAIProvider

    catalog_id = get_provider_model_definitions("openai")[0].id
    deletion_inventory.config_path.write_text(
        json.dumps(
            {
                "provider": {"openai": {"models": {catalog_id: {"name": "Former Catalog Model"}}}},
            }
        ),
        encoding="utf-8",
    )

    assert ConfigWriter.remove_model("openai", catalog_id) is True
    assert sync_catalog_models_to_config() == 0
    assert sync_catalog_models_to_config() == 0
    raw = saved(deletion_inventory)["provider"]["openai"]
    assert raw == {"models": {}, "model_sync": "manual"}
    runtime = OpenAIProvider()
    Provider.apply_provider_config(runtime, ProviderSettings.model_validate(raw))
    assert runtime.get_models() == []
    assert runtime.get_model_definitions() == []
    deletion_inventory.write.assert_called_once()


@pytest.mark.parametrize("route", ["/api/model/v2/definitions", "/api/custom/models"])
@pytest.mark.asyncio
async def test_delete_notifies_real_sse_subscriber_in_request_context(
    route, deletion_inventory, deletion_client, monkeypatch
):
    from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
    from flocks.server.routes.event import EventBroadcaster

    deletion_inventory.seed("sse-delete")
    broadcaster = EventBroadcaster()
    monkeypatch.setattr(EventBroadcaster, "_instance", broadcaster)
    actor = AuthUser(id="editing-admin", username="editor", role="admin")
    other = AuthUser(id="unrelated-admin", username="other", role="admin")
    actor_queue = await broadcaster.subscribe(actor)
    other_queue = await broadcaster.subscribe(other)
    token = set_current_auth_user(actor)
    try:
        response = await deletion_client.delete(f"{route}/azure/sse-delete")
    finally:
        reset_current_auth_user(token)

    assert response.status_code == 204, response.text
    assert actor_queue.get_nowait() == {"type": "provider.updated", "properties": {"provider_id": "azure"}}
    assert actor_queue.empty()
    assert other_queue.empty()
    deletion_inventory.publish.assert_awaited_once()
