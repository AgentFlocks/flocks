"""N02 regression: refresh discovers the declared api_services config field."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from flocks.config.config import ConfigInfo


@pytest.fixture
def api_services_runtime(monkeypatch):
    from flocks.server.routes import provider as routes
    from flocks.tool.registry import ToolRegistry

    cache = {}
    registry_ids = set()
    checked_at = 1_729_000_000

    async def read(key):
        assert key == routes._API_SERVICE_STATUS_KEY
        return deepcopy(cache.get(key))

    async def write(key, value):
        assert key == routes._API_SERVICE_STATUS_KEY
        cache[key] = deepcopy(value)

    async def probe(service_id):
        return {
            "success": True,
            "message": f"Synthetic probe: {service_id}",
            "latency_ms": 7,
            "tool_tested": "synthetic_probe",
        }

    config_get = AsyncMock()
    storage_write = AsyncMock(side_effect=write)
    credential_probe = AsyncMock(side_effect=probe)
    monkeypatch.setattr(routes.Config, "get", config_get)
    monkeypatch.setattr(routes.Storage, "init", AsyncMock())
    monkeypatch.setattr(routes.Storage, "read", AsyncMock(side_effect=read))
    monkeypatch.setattr(routes.Storage, "write", storage_write)
    monkeypatch.setattr(routes, "test_provider_credentials", credential_probe)
    monkeypatch.setattr(routes, "time", SimpleNamespace(time=lambda: checked_at))
    monkeypatch.setattr(ToolRegistry, "init_async", AsyncMock())
    monkeypatch.setattr(ToolRegistry, "get_api_service_ids", lambda: set(registry_ids))
    # No model provider can incidentally make a config-only API service visible.
    monkeypatch.setattr(routes.Provider, "_providers", {})
    monkeypatch.setattr(routes.Provider, "_models", {})
    monkeypatch.setattr(routes.Provider, "_initialized", True)

    return SimpleNamespace(
        routes=routes,
        cache=cache,
        registry_ids=registry_ids,
        checked_at=checked_at,
        config_get=config_get,
        storage_write=storage_write,
        credential_probe=credential_probe,
    )


@pytest.mark.parametrize(
    ("config_values", "registry_ids", "expected_ids"),
    [
        pytest.param(
            {"api_services": {"config-only": {"enabled": True}}},
            set(),
            {"config-only"},
            id="config-only-without-tools-or-models",
        ),
        pytest.param(
            {"api_services": {"config-only": {}, "shared": {}}},
            {"registry-only", "shared"},
            {"config-only", "registry-only", "shared"},
            id="merge-config-and-registry-deduplicated",
        ),
        pytest.param({}, {"registry-only"}, {"registry-only"}, id="default-none"),
        pytest.param(
            {"api_services": None},
            {"registry-only"},
            {"registry-only"},
            id="explicit-none",
        ),
        pytest.param(
            {"api_services": {}},
            {"registry-only"},
            {"registry-only"},
            id="empty-config-services",
        ),
        pytest.param({}, set(), set(), id="no-services"),
    ],
)
async def test_refresh_api_services_keeps_config_and_registry_in_cache(
    api_services_runtime, config_values, registry_ids, expected_ids
):
    runtime = api_services_runtime
    config = ConfigInfo(provider={}, **config_values)
    assert "api_services" not in (config.model_extra or {})
    runtime.config_get.return_value = config
    runtime.registry_ids.update(registry_ids)
    cache_key = runtime.routes._API_SERVICE_STATUS_KEY
    runtime.cache[cache_key] = {
        "statuses": {
            service_id: {"status": "error", "message": "stale"}
            for service_id in expected_ids
        },
        "checked_at": runtime.checked_at - 1,
    }

    result = await runtime.routes.refresh_api_services_status(_admin=object())

    expected_statuses = {
        service_id: {
            "status": "connected",
            "message": f"Synthetic probe: {service_id}",
            "latency_ms": 7,
            "tool_tested": "synthetic_probe",
            "checked_at": runtime.checked_at,
        }
        for service_id in expected_ids
    }
    expected_cache = {
        "statuses": expected_statuses,
        "checked_at": runtime.checked_at,
    }
    # Assert the persisted value first: N02 used to overwrite this with {}.
    assert runtime.cache[cache_key] == expected_cache
    runtime.storage_write.assert_awaited_once_with(cache_key, expected_cache)
    assert result == {
        "statuses": expected_statuses,
        "refreshed_at": runtime.checked_at,
    }
    runtime.credential_probe.assert_has_awaits(
        [call(service_id) for service_id in expected_ids], any_order=True
    )
    assert runtime.credential_probe.await_count == len(expected_ids)
    assert await runtime.routes.get_api_services_status() == expected_statuses
