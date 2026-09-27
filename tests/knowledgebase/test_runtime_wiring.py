import asyncio

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from flocks.knowledgebase import runtime
from flocks.knowledgebase.errors import KnowledgebaseError
from flocks.knowledgebase.flocksrag import FlocksragAdapter


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
    runtime.publish(client, runtime.publication_epoch())
    await runtime.stop()
    assert runtime.get_client() is None
    client.close.assert_awaited_once()


async def test_retired_generation_closes_once_after_last_lease():
    old = SimpleNamespace(close=AsyncMock())
    new = SimpleNamespace(close=AsyncMock())
    epoch = runtime.publication_epoch()
    runtime.publish(old, epoch)
    async with runtime.lease_client() as first:
        async with runtime.lease_client() as second:
            assert first is second is old
            runtime.publish(new, epoch)
            assert runtime.get_client() is new
            async with runtime.lease_client() as latest:
                assert latest is new
            old.close.assert_not_awaited()
        old.close.assert_not_awaited()
    old.close.assert_awaited_once()
    await runtime.stop()
    await runtime.stop()
    old.close.assert_awaited_once()
    new.close.assert_awaited_once()


@pytest.mark.parametrize("outcome", ["error", "cancel"])
async def test_failed_inflight_operation_releases_retired_client(outcome):
    entered = asyncio.Event()
    finish = asyncio.Event()
    old = SimpleNamespace(close=AsyncMock())
    runtime.publish(old, runtime.publication_epoch())

    async def operation():
        async with runtime.lease_client():
            entered.set()
            await finish.wait()
            raise ValueError("operation failed")

    pending = asyncio.create_task(operation())
    await asyncio.wait_for(entered.wait(), 1)
    runtime.publish(SimpleNamespace(close=AsyncMock()), runtime.publication_epoch())
    old.close.assert_not_awaited()
    if outcome == "cancel":
        pending.cancel()
        expected = asyncio.CancelledError
    else:
        finish.set()
        expected = ValueError
    with pytest.raises(expected):
        await pending
    old.close.assert_awaited_once()


async def test_repeated_cancellation_does_not_cancel_retired_cleanup():
    entered, closing, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def close():
        closing.set()
        await finish.wait()

    old = SimpleNamespace(close=AsyncMock(side_effect=close))
    runtime.publish(old, runtime.publication_epoch())

    async def operation():
        async with runtime.lease_client():
            entered.set()
            await asyncio.Event().wait()

    pending = asyncio.create_task(operation())
    await asyncio.wait_for(entered.wait(), 1)
    runtime.publish(SimpleNamespace(close=AsyncMock()), runtime.publication_epoch())
    pending.cancel()
    await asyncio.wait_for(closing.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    tasks = tuple(runtime._closing_tasks)
    assert len(tasks) == 1 and not tasks[0].done()
    finish.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 1)
    old.close.assert_awaited_once()


async def test_shutdown_retires_but_does_not_close_inflight_generation():
    old = SimpleNamespace(close=AsyncMock())
    epoch = runtime.publication_epoch()
    runtime.publish(old, epoch)
    async with runtime.lease_client() as borrowed:
        await runtime.stop()
        assert borrowed is old and runtime.get_client() is None
        old.close.assert_not_awaited()
        with pytest.raises(KnowledgebaseError, match="shutting down"):
            runtime.publish(SimpleNamespace(close=AsyncMock()), epoch)
    old.close.assert_awaited_once()


async def test_shutdown_prevents_late_startup_publication(monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()

    async def load():
        entered.set()
        await finish.wait()
        return SimpleNamespace(api_services={"knowledgebase": configured()})

    candidate = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(runtime.Config, "get", load)
    monkeypatch.setattr(runtime, "KnowledgebaseClient", lambda connection: candidate)
    monkeypatch.setattr(runtime, "get_secret_manager", lambda: SimpleNamespace(get=lambda key: "x" * 32))
    pending = asyncio.create_task(runtime.start_from_config())
    await asyncio.wait_for(entered.wait(), 1)
    await runtime.stop()
    finish.set()
    assert await pending == {"status": "disabled"}
    assert runtime.get_client() is None
    candidate.close.assert_awaited_once()


@pytest.mark.parametrize("cancel", [False, True])
async def test_retrieval_route_lease_covers_session_read(monkeypatch, cancel):
    from fastapi import FastAPI
    from flocks.server.routes import knowledgebase

    entered, finish = asyncio.Event(), asyncio.Event()

    async def selection(*args):
        entered.set()
        await finish.wait()
        return {"dataset_ids": ["dataset-1"]}

    old = SimpleNamespace(close=AsyncMock(), retrieve=AsyncMock(return_value={"chunks": [], "total": 0}))
    new = SimpleNamespace(close=AsyncMock(), retrieve=AsyncMock())
    runtime.publish(old, runtime.publication_epoch())
    monkeypatch.setattr("flocks.knowledgebase.retrieval.get_selection", selection)
    monkeypatch.setattr(knowledgebase, "require_user", lambda request: "owner")
    app = FastAPI()
    app.include_router(knowledgebase.create_router())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        pending = asyncio.create_task(http.post("/knowledgebase/sessions/s1/retrieval", json={"keywords": "hello"}))
        await asyncio.wait_for(entered.wait(), 1)
        runtime.publish(new, runtime.publication_epoch())
        old.close.assert_not_awaited()
        if cancel:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            old.retrieve.assert_not_awaited()
        else:
            finish.set()
            assert (await pending).status_code == 200
            old.retrieve.assert_awaited_once()
    old.close.assert_awaited_once()
    new.retrieve.assert_not_awaited()
