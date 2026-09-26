from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from flocks.knowledgebase import runtime
from flocks.knowledgebase.errors import KnowledgebaseError
from flocks.knowledgebase.flocksrag import FlocksragAdapter


@pytest.fixture(autouse=True)
async def reset_client():
    await runtime.stop()
    yield
    await runtime.stop()


def configured(**overrides):
    return {
        "enabled": True,
        "provider": "ragflow",
        "base_url": "http://ragflow.test/mounted",
        "credential_id": "knowledgebase_ragflow_api_key",
        **overrides,
    }


def settings_from(monkeypatch, services):
    loader = AsyncMock(return_value=SimpleNamespace(api_services=services))
    monkeypatch.setattr(runtime.Config, "get", loader)
    return loader


def forbid_credentials(monkeypatch):
    manager = Mock(side_effect=AssertionError("This configuration must not resolve credentials"))
    monkeypatch.setattr(runtime, "get_secret_manager", manager)
    return manager


@pytest.mark.asyncio
@pytest.mark.parametrize("services", [None, {}, {"knowledgebase": {}}, {"knowledgebase": {"enabled": False}}])
async def test_startup_stays_disabled_and_does_not_require_a_remote(monkeypatch, services):
    settings_from(monkeypatch, services)
    manager = forbid_credentials(monkeypatch)
    assert await runtime.start_from_config() == {"status": "disabled"}
    assert runtime.get_client() is None
    manager.assert_not_called()


@pytest.mark.asyncio
async def test_old_service_environment_is_not_reinterpreted(monkeypatch):
    monkeypatch.setenv("FLOCKS_KNOWLEDGEBASE_URL", "http://old-service.invalid")
    monkeypatch.setenv("FLOCKS_KNOWLEDGEBASE_API_TOKEN", "t" * 32)
    settings_from(monkeypatch, {})
    manager = forbid_credentials(monkeypatch)
    assert await runtime.start_from_config() == {"status": "disabled"}
    manager.assert_not_called()


@pytest.mark.asyncio
async def test_configured_startup_uses_flocks_credentials_without_remote_requests(monkeypatch):
    loader = settings_from(monkeypatch, {"knowledgebase": configured(timeout_seconds=12, max_upload_bytes=4096)})
    secrets = Mock()
    secrets.get.return_value = "t" * 40
    manager = Mock(return_value=secrets)
    monkeypatch.setattr(runtime, "get_secret_manager", manager)
    send = AsyncMock(side_effect=AssertionError("Startup must not call the engine"))
    monkeypatch.setattr(httpx.AsyncClient, "send", send)

    assert await runtime.start_from_config() == {"status": "configured"}
    client = runtime.get_client()
    assert client is not None
    assert client.connection.base_url == "http://ragflow.test/mounted"
    assert client.connection.api_token == "t" * 40
    assert client.connection.timeout_seconds == 12
    assert client.connection.max_upload_bytes == 4096
    assert client._http.trust_env is False
    assert client._http.follow_redirects is False
    secrets.get.assert_called_once_with("knowledgebase_ragflow_api_key")
    manager.assert_called_once_with()
    loader.assert_awaited_once()
    send.assert_not_awaited()

    assert await runtime.start_from_config() == {"status": "already_started"}
    loader.assert_awaited_once()
    manager.assert_called_once_with()
    assert runtime.get_client() is client
    await runtime.stop()
    assert runtime.get_client() is None
    assert client._http.is_closed


@pytest.mark.asyncio
async def test_flocksrag_is_rejected_before_connection_or_secret_resolution(monkeypatch):
    settings_from(monkeypatch, {"knowledgebase": {
        "enabled": True, "provider": "flocksrag", "base_url": object(), "credential_id": object(),
    }})
    manager = forbid_credentials(monkeypatch)
    construct = Mock(side_effect=AssertionError("The placeholder must not construct an HTTP client"))
    monkeypatch.setattr(runtime, "KnowledgebaseClient", construct)
    assert await runtime.start_from_config() == {"status": "disabled", "reason": "not_implemented"}
    assert runtime.get_client() is None
    manager.assert_not_called()
    construct.assert_not_called()


def test_flocksrag_adapter_is_an_explicit_unusable_placeholder():
    assert FlocksragAdapter.implemented is False
    with pytest.raises(KnowledgebaseError) as caught:
        FlocksragAdapter()
    assert (caught.value.status, caught.value.code) == (503, "knowledgebase_not_configured")


@pytest.mark.asyncio
@pytest.mark.parametrize("services", [[], {"knowledgebase": []}, {"knowledgebase": {"enabled": "true"}}])
async def test_invalid_config_shapes_disable_only_knowledgebase(monkeypatch, services):
    settings_from(monkeypatch, services)
    manager = forbid_credentials(monkeypatch)
    assert await runtime.start_from_config() == {"status": "disabled", "reason": "incomplete"}
    assert runtime.get_client() is None
    manager.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"provider": "unknown"}, {"provider": None},
    {"base_url": None}, {"base_url": "http://user:password@ragflow.test"},
    {"base_url": "http://ragflow.test/?token=x"}, {"base_url": "file:///data"},
    {"credential_id": None}, {"credential_id": ""}, {"credential_id": " key "},
    {"credential_id": "{secret:key}"}, {"credential_id": "{env:TOKEN}"},
    {"timeout_seconds": 0}, {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")},
    {"timeout_seconds": True}, {"timeout_seconds": "30"},
    {"max_upload_bytes": 0}, {"max_upload_bytes": True}, {"max_upload_bytes": "1024"},
])
async def test_invalid_connection_options_are_rejected_before_credentials(monkeypatch, overrides):
    settings_from(monkeypatch, {"knowledgebase": configured(**overrides)})
    manager = forbid_credentials(monkeypatch)
    assert await runtime.start_from_config() == {"status": "disabled", "reason": "incomplete"}
    assert runtime.get_client() is None
    manager.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", "short", "t" * 32 + "\n", 123])
async def test_missing_or_invalid_selected_credential_is_not_published(monkeypatch, token):
    settings_from(monkeypatch, {"knowledgebase": configured()})
    secrets = Mock()
    secrets.get.return_value = token
    monkeypatch.setattr(runtime, "get_secret_manager", lambda: secrets)
    status = await runtime.start_from_config()
    assert status == {"status": "disabled", "reason": "incomplete"}
    assert runtime.get_client() is None
    secrets.get.assert_called_once_with("knowledgebase_ragflow_api_key")
    secrets.has.assert_not_called()


@pytest.mark.asyncio
async def test_config_and_secret_errors_stay_sanitized(monkeypatch):
    secret = "private-value-not-for-logs"
    monkeypatch.setattr(runtime.Config, "get", AsyncMock(side_effect=ValueError(secret)))
    assert await runtime.start_from_config() == {"status": "disabled", "reason": "incomplete"}
    settings_from(monkeypatch, {"knowledgebase": configured()})
    monkeypatch.setattr(runtime, "get_secret_manager", Mock(side_effect=OSError(secret)))
    assert await runtime.start_from_config() == {"status": "disabled", "reason": "incomplete"}
    assert runtime.get_client() is None


@pytest.mark.asyncio
async def test_stop_clears_the_singleton_even_if_close_fails(monkeypatch):
    client = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close failed")))
    monkeypatch.setattr(runtime, "_client", client)
    with pytest.raises(RuntimeError, match="close failed"):
        await runtime.stop()
    assert runtime.get_client() is None
    client.close.assert_awaited_once()
