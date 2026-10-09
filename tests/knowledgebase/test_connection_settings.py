"""Offline route contracts for editing the selected RAGFlow connection."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from flocks.config.config import Config
from flocks.knowledgebase import connection_settings, runtime
from flocks.knowledgebase.client import KnowledgebaseClient
from flocks.knowledgebase.ragflow import RagflowAdapter
from flocks.security.secrets import SecretManager
from flocks.server.auth import require_user
from flocks.server.config_mutation import GLOBAL_CONFIG_MUTATION_LOCK
from flocks.server.routes.knowledgebase import create_router


URL = "https://ragflow.test/mounted/api/v1"
KEY = "new-key-" + "a" * 32
OLD_KEY = "old-key-" + "b" * 32
CREDENTIAL_ID = "knowledgebase_ragflow_api_key"
PATH = "/api/knowledgebase/connection"


@pytest.fixture
def files(tmp_path, monkeypatch):
    """Pin raw config, secrets, and higher-priority sources to this test's temp dir."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    config_dir.mkdir(parents=True)
    config = config_dir / "flocks.json"
    secrets = config_dir / ".secret.json"
    global_config = SimpleNamespace(config_dir=config_dir, config_path=None, config_content=None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "home" / ".flocks"))
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(Config, "get_global", classmethod(lambda cls: global_config))
    monkeypatch.setattr(Config, "get_config_path", classmethod(lambda cls: config_dir))
    monkeypatch.setattr(Config, "get_config_file", classmethod(lambda cls: config))
    monkeypatch.setattr(Config, "get_secret_file", classmethod(lambda cls: secrets))
    monkeypatch.setattr(
        connection_settings, "get_secret_manager", lambda: SecretManager(secret_file=secrets)
    )
    return SimpleNamespace(config=config, secrets=secrets, global_config=global_config)


@pytest.fixture
def app(files):
    app = FastAPI()
    app.include_router(create_router(), prefix="/api")
    # A signed-in ordinary user, not an administrator, may read and update.
    app.dependency_overrides[require_user] = lambda: "member"
    return app


async def request(app, method, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, PATH, **kwargs)


def put(files, *, url=URL, key=KEY):
    return {"provider": "ragflow", "base_url": url, "api_key": key}


def configured(files, *, url=URL, key=OLD_KEY):
    data = {
        "theme": "untouched",
        "api_services": {
            "other": {"apiKey": "{env:DO_NOT_RESOLVE}"},
            "knowledgebase": {
                "enabled": True, "provider": "ragflow", "base_url": url,
                "credential_id": CREDENTIAL_ID, "timeout_seconds": 12,
            },
        },
    }
    files.config.write_text(json.dumps(data), encoding="utf-8")
    files.secrets.write_text(json.dumps({CREDENTIAL_ID: key, "other_key": "unrelated"}), encoding="utf-8")
    return data


def probe(monkeypatch, responder=None):
    """Exercise the actual adapter and its HTTP validation without a socket."""
    calls = []
    adapters = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        assert dict(request.url.params) == {"page": "1", "page_size": "1"}
        if responder is not None:
            return responder(request)
        if request.url.path.endswith("/datasets"):
            return httpx.Response(200, json={"code": 0, "data": [], "total_datasets": 0})
        assert request.url.path.endswith("/files")
        return httpx.Response(200, json={"code": 0, "data": {"files": [], "total": 0, "parent_folder": None}})

    def factory(base_url, api_key, *, timeout, max_content_bytes):
        assert timeout == 12
        adapter = RagflowAdapter(base_url, api_key, timeout=timeout, max_content_bytes=max_content_bytes,
                                 transport=httpx.MockTransport(handle))
        adapters.append(adapter)
        return adapter

    monkeypatch.setattr(connection_settings, "RagflowAdapter", factory)
    return calls, adapters


@pytest.fixture
def candidates(monkeypatch):
    clients = []

    def construct(connection):
        client = KnowledgebaseClient(connection, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={
                "code": 0, "data": {"files": [], "total": 0, "parent_folder": None},
            })
        ))
        client.close = AsyncMock(wraps=client.close)
        clients.append(client)
        return client

    monkeypatch.setattr(connection_settings, "KnowledgebaseClient", construct)
    return clients


def slow_probe(monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()
    adapters = []

    class Adapter:
        def __init__(self, *args, **kwargs):
            self.close = AsyncMock()
            adapters.append(self)

        async def list_datasets(self, *, page, page_size):
            entered.set()
            await finish.wait()
            return {"datasets": [], "total": 0}

        async def list_files(self, *, page, page_size, keywords=None):
            return {"files": [], "total": 0, "parent_folder": None}

    monkeypatch.setattr(connection_settings, "RagflowAdapter", Adapter)
    return entered, finish, adapters


def assert_error(response, status, code, *private_values):
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert set(response.json()) == {"error"}
    for value in private_values:
        assert value not in response.text


@pytest.mark.asyncio
async def test_get_unconfigured_does_not_create_a_secret_file_or_disclose_credentials(app, files):
    response = await request(app, "GET")
    assert response.status_code == 200
    assert response.json() == {"data": {"provider": None, "base_url": "", "has_api_key": False}}
    assert not files.config.exists()
    assert not files.secrets.exists()


@pytest.mark.asyncio
async def test_save_probes_both_endpoints_then_writes_only_knowledgebase_service(app, files, monkeypatch):
    original = {"theme": "untouched", "api_services": {"other": {"apiKey": "{env:DO_NOT_RESOLVE}"}}}
    files.config.write_text(json.dumps(original), encoding="utf-8")
    files.secrets.write_text(json.dumps({"other_key": "unrelated"}), encoding="utf-8")
    calls, adapters = probe(monkeypatch)

    saved = await request(app, "PUT", json=put(files))
    assert saved.status_code == 200
    assert saved.json() == {"data": {
        "provider": "ragflow", "base_url": URL, "has_api_key": True,
        "applied": True, "restart_required": False,
    }}
    assert [str(req.url) for req in calls] == [
        f"{URL}/datasets?page=1&page_size=1", f"{URL}/files?page=1&page_size=1"
    ]
    assert all(req.headers["authorization"] == f"Bearer {KEY}" for req in calls)
    assert len(adapters) == 1 and adapters[0]._client.is_closed
    saved_config = json.loads(files.config.read_text(encoding="utf-8"))
    assert saved_config == {**original, "api_services": {
        **original["api_services"], "knowledgebase": {
            "enabled": True, "provider": "ragflow", "base_url": URL,
            "credential_id": CREDENTIAL_ID,
        },
    }}
    assert KEY not in files.config.read_text(encoding="utf-8")
    assert json.loads(files.secrets.read_text(encoding="utf-8")) == {
        "other_key": "unrelated", CREDENTIAL_ID: KEY,
    }
    current = await request(app, "GET")
    assert current.json() == {"data": {"provider": "ragflow", "base_url": URL, "has_api_key": True}}
    assert KEY not in current.text and CREDENTIAL_ID not in current.text


@pytest.mark.asyncio
async def test_get_sanitizes_unsupported_provider_and_never_returns_key_or_id(app, files):
    data = configured(files)
    data["api_services"]["knowledgebase"].update(provider="flocksrag", credential_id="custom_credential")
    files.config.write_text(json.dumps(data), encoding="utf-8")
    files.secrets.write_text(json.dumps({"custom_credential": OLD_KEY}), encoding="utf-8")
    response = await request(app, "GET")
    assert response.json() == {"data": {"provider": None, "base_url": URL, "has_api_key": True}}
    assert OLD_KEY not in response.text and "custom_credential" not in response.text


@pytest.mark.asyncio
async def test_get_does_not_reflect_credentials_embedded_in_legacy_url(app, files):
    configured(files, url=f"https://admin:{OLD_KEY}@ragflow.test")
    response = await request(app, "GET")
    assert response.status_code == 200
    assert response.json() == {"data": {"provider": "ragflow", "base_url": "", "has_api_key": True}}
    assert OLD_KEY not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "error", "timeout", "partial", "unauthorized", "invalid_datasets", "invalid_files",
    "dataset_item", "file_item",
])
async def test_failed_probes_do_not_change_config_or_secrets(app, files, monkeypatch, failure):
    configured(files)
    before_config = files.config.read_bytes()
    before_secrets = files.secrets.read_bytes()

    def respond(req):
        if failure == "timeout":
            raise httpx.ReadTimeout(f"upstream timeout {KEY}", request=req)
        if failure == "unauthorized":
            return httpx.Response(401, json={"code": 102, "message": f"wrong credential {KEY}"})
        if failure == "error":
            return httpx.Response(200, json={"code": 102, "message": f"invalid {KEY}"})
        if failure == "invalid_datasets":
            return httpx.Response(200, json={"code": 0, "data": []})
        if req.url.path.endswith("/datasets"):
            if failure == "dataset_item":
                return httpx.Response(200, json={"code": 0, "data": [{}], "total_datasets": 1})
            return httpx.Response(200, json={"code": 0, "data": [], "total_datasets": 0})
        if failure == "file_item":
            return httpx.Response(200, json={"code": 0, "data": {
                "files": [{"type": "file"}], "total": 1, "parent_folder": None,
            }})
        if failure == "partial":
            return httpx.Response(200, json={"code": 0, "data": {
                "files": [], "total": 0, "parent_folder": None, "failed_count": 1,
            }})
        return httpx.Response(200, json={"code": 0, "data": {"files": [], "total": 0}})

    calls, adapters = probe(monkeypatch, respond)
    response = await request(app, "PUT", json=put(files, key=KEY))
    assert_error(response, 502, "connection_test_failed", KEY, OLD_KEY)
    assert len(calls) == (2 if failure in {"partial", "invalid_files", "file_item"} else 1)
    assert len(adapters) == 1 and adapters[0]._client.is_closed
    assert files.config.read_bytes() == before_config
    assert files.secrets.read_bytes() == before_secrets


@pytest.mark.asyncio
async def test_blank_key_requires_unchanged_url_and_applies_immediately(app, files, monkeypatch):
    configured(files)
    before_secrets = files.secrets.read_bytes()
    calls, _ = probe(monkeypatch)
    response = await request(app, "PUT", json=put(files, url=f" {URL} ", key="   "))
    assert response.status_code == 200
    assert response.json()["data"] == {
        "provider": "ragflow", "base_url": URL, "has_api_key": True,
        "applied": True, "restart_required": False,
    }
    assert len(calls) == 2
    assert all(req.headers["authorization"] == f"Bearer {OLD_KEY}" for req in calls)
    assert files.secrets.read_bytes() == before_secrets
    assert json.loads(files.config.read_text(encoding="utf-8"))["api_services"]["knowledgebase"]["timeout_seconds"] == 12

    before_config = files.config.read_bytes()
    calls.clear()
    for changed_url in ("https://ragflow.test/another-mount", "https://different.test", "https://ragflow.test:443/mounted/api/v1"):
        rejected = await request(app, "PUT", json=put(files, url=changed_url, key=""))
        assert_error(rejected, 400, "api_key_required", OLD_KEY)
    assert calls == []
    assert files.config.read_bytes() == before_config and files.secrets.read_bytes() == before_secrets


@pytest.mark.asyncio
async def test_connection_reads_lower_config_when_jsonc_exists_for_unrelated_options(app, files, monkeypatch):
    configured(files)
    jsonc = files.config.parent / "flocks.jsonc"
    jsonc.write_text('{"theme":"dark"}', encoding="utf-8")
    before_base = files.config.read_bytes()
    calls, _ = probe(monkeypatch)

    current = await request(app, "GET")
    assert current.json() == {"data": {"provider": "ragflow", "base_url": URL, "has_api_key": True}}
    response = await request(app, "PUT", json=put(files, key=""))
    assert response.status_code == 200
    assert len(calls) == 2
    assert all(call.headers["authorization"] == f"Bearer {OLD_KEY}" for call in calls)
    assert files.config.read_bytes() == before_base
    assert json.loads(jsonc.read_text(encoding="utf-8"))["api_services"]["knowledgebase"]["base_url"] == URL
    assert json.loads(jsonc.read_text(encoding="utf-8"))["theme"] == "dark"


@pytest.mark.asyncio
async def test_valid_jsonc_service_replaces_malformed_lower_layer(app, files, monkeypatch):
    files.config.write_text('{"api_services":[]}', encoding="utf-8")
    jsonc = files.config.parent / "flocks.jsonc"
    jsonc.write_text(json.dumps({"api_services": {"knowledgebase": {
        "enabled": True, "provider": "ragflow", "base_url": URL, "credential_id": CREDENTIAL_ID,
    }}}), encoding="utf-8")
    files.secrets.write_text(json.dumps({CREDENTIAL_ID: OLD_KEY}), encoding="utf-8")
    calls, _ = probe(monkeypatch)
    assert (await request(app, "GET")).json()["data"]["has_api_key"] is True
    response = await request(app, "PUT", json=put(files, key=""))
    assert response.status_code == 200
    assert len(calls) == 2
    assert json.loads(files.config.read_text(encoding="utf-8"))["api_services"] == []


@pytest.mark.asyncio
async def test_new_origin_probes_only_with_submitted_key_not_stored_key(app, files, monkeypatch):
    configured(files)
    calls, _ = probe(monkeypatch)
    new_url = "https://replacement.test/ragflow"
    response = await request(app, "PUT", json=put(files, url=new_url, key=KEY))
    assert response.status_code == 200
    assert [req.url.host for req in calls] == ["replacement.test", "replacement.test"]
    assert all(req.headers["authorization"] == f"Bearer {KEY}" for req in calls)
    saved = json.loads(files.config.read_text(encoding="utf-8"))["api_services"]["knowledgebase"]
    stored = json.loads(files.secrets.read_text(encoding="utf-8"))
    assert saved["base_url"] == new_url
    assert saved["credential_id"] != CREDENTIAL_ID
    assert stored[CREDENTIAL_ID] == OLD_KEY
    assert stored[saved["credential_id"]] == KEY
    assert OLD_KEY not in response.text and KEY not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("body,status,code", [
    (["not an object", KEY], 400, "invalid_request"),
    ({"provider": "ragflow", "base_url": URL, "api_key": KEY, "credential_id": KEY}, 400, "invalid_request"),
    ({"provider": "ragflow", "base_url": URL, "api_key": [KEY]}, 400, "invalid_request"),
    ({"provider": "another", "base_url": URL, "api_key": KEY}, 400, "invalid_request"),
    ({"provider": "ragflow", "base_url": "file:///private", "api_key": KEY}, 400, "invalid_request"),
    ({"provider": "ragflow", "base_url": f"https://admin:{KEY}@ragflow.test", "api_key": KEY}, 400, "invalid_request"),
    ({"provider": "ragflow", "base_url": URL, "api_key": "too-short"}, 400, "invalid_request"),
    ({"provider": "ragflow", "base_url": URL}, 400, "api_key_required"),
    ({"provider": "ragflow", "base_url": URL, "api_key": KEY + "z" * 8192}, 413, "request_too_large"),
])
async def test_bad_body_never_probes_writes_or_echoes_key(app, files, monkeypatch, body, status, code):
    calls, _ = probe(monkeypatch)
    response = await request(app, "PUT", json=body)
    assert_error(response, status, code, KEY)
    assert calls == []
    assert not files.config.exists() and not files.secrets.exists()


@pytest.mark.asyncio
async def test_streamed_oversized_body_is_rejected_before_parsing_or_probing(app, files, monkeypatch):
    calls, _ = probe(monkeypatch)

    async def body():
        yield json.dumps(put(files)).encode()
        yield b" " * 8193

    response = await request(app, "PUT", content=body())
    assert_error(response, 413, "request_too_large", KEY)
    assert calls == []
    assert not files.config.exists() and not files.secrets.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["inline", "config.json", "custom"])
async def test_higher_priority_knowledgebase_config_blocks_read_and_write(app, files, monkeypatch, source):
    override = json.dumps({"api_services": {"knowledgebase": {"provider": "ragflow"}}})
    if source == "inline":
        files.global_config.config_content = override
    elif source == "config.json":
        (files.config.parent / "config.json").write_text(override, encoding="utf-8")
    else:
        custom = files.config.parent / "external.json"
        custom.write_text(override, encoding="utf-8")
        files.global_config.config_path = str(custom)
    calls, _ = probe(monkeypatch)
    for method, kwargs in (("GET", {}), ("PUT", {"json": put(files)})):
        assert_error(await request(app, method, **kwargs), 409, "knowledgebase_config_overridden", KEY)
    assert calls == [] and not files.config.exists() and not files.secrets.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["inline", "config.json", "custom"])
async def test_malformed_higher_priority_api_services_does_not_mask_saved_connection(app, files, monkeypatch, source):
    configured(files)
    override = '{"api_services":[]}'
    if source == "inline":
        files.global_config.config_content = override
    elif source == "config.json":
        (files.config.parent / "config.json").write_text(override, encoding="utf-8")
    else:
        path = files.config.parent / "external.json"
        path.write_text(override, encoding="utf-8")
        files.global_config.config_path = str(path)
    calls, _ = probe(monkeypatch)
    assert_error(await request(app, "GET"), 409, "knowledgebase_config_overridden", KEY)
    assert_error(await request(app, "PUT", json=put(files)), 409, "knowledgebase_config_overridden", KEY)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"timeout_seconds": 0}, {"timeout_seconds": True},
    {"max_upload_bytes": -1}, {"max_upload_bytes": "invalid"},
])
async def test_invalid_retained_runtime_options_block_save_before_probe(app, files, monkeypatch, options):
    data = configured(files)
    data["api_services"]["knowledgebase"].update(options)
    files.config.write_text(json.dumps(data), encoding="utf-8")
    before_secret = files.secrets.read_bytes()
    calls, _ = probe(monkeypatch)
    assert_error(await request(app, "PUT", json=put(files)), 409, "knowledgebase_config_invalid", KEY)
    assert calls == []
    assert json.loads(files.config.read_text(encoding="utf-8")) == data
    assert files.secrets.read_bytes() == before_secret


@pytest.mark.asyncio
async def test_malformed_raw_config_is_not_overwritten(app, files, monkeypatch):
    files.config.write_text('{"api_services":', encoding="utf-8")
    calls, _ = probe(monkeypatch)
    response = await request(app, "PUT", json=put(files))
    assert_error(response, 409, "knowledgebase_config_invalid", KEY)
    assert files.config.read_text(encoding="utf-8") == '{"api_services":'
    assert not files.secrets.exists() and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("old_key", [None, OLD_KEY])
async def test_failed_writer_compensates_new_secret_best_effort(app, files, monkeypatch, old_key):
    if old_key is None:
        original = {"api_services": {"other": {"apiKey": "{secret:other_key}"}}}
        files.config.write_text(json.dumps(original), encoding="utf-8")
    else:
        original = configured(files, key=old_key)
    calls, _ = probe(monkeypatch)

    def fail_write(*args, **kwargs):
        raise OSError(f"cannot save {KEY}")

    monkeypatch.setattr(connection_settings.ConfigWriter, "_write_raw", fail_write)
    response = await request(app, "PUT", json=put(files))
    assert_error(response, 500, "knowledgebase_save_failed", KEY)
    assert len(calls) == 2
    assert json.loads(files.config.read_text(encoding="utf-8")) == original
    assert json.loads(files.secrets.read_text(encoding="utf-8")) == (
        {CREDENTIAL_ID: old_key, "other_key": "unrelated"} if old_key else {}
    )


@pytest.mark.asyncio
async def test_interrupted_config_write_never_sends_new_key_to_old_origin(app, files, monkeypatch):
    original = configured(files)
    calls, _ = probe(monkeypatch)

    class Interrupted(BaseException):
        pass

    def interrupt_write(*args, **kwargs):
        raise Interrupted()

    monkeypatch.setattr(connection_settings.ConfigWriter, "_write_raw", interrupt_write)
    with pytest.raises(Interrupted):
        await request(app, "PUT", json=put(files, url="https://new-origin.test", key=KEY))
    assert len(calls) == 2
    assert json.loads(files.config.read_text(encoding="utf-8")) == original
    stored = json.loads(files.secrets.read_text(encoding="utf-8"))
    assert stored[CREDENTIAL_ID] == OLD_KEY
    assert any(value == KEY for key, value in stored.items() if key != CREDENTIAL_ID)


@pytest.mark.asyncio
async def test_slow_request_body_does_not_hold_the_global_config_lock(app, files):
    first_chunk = asyncio.Event()
    finish_body = asyncio.Event()
    payload = json.dumps({"provider": "flocksrag", "base_url": URL, "api_key": KEY}).encode()

    async def chunks():
        yield payload[:20]
        first_chunk.set()
        await finish_body.wait()
        yield payload[20:]

    pending = asyncio.create_task(request(app, "PUT", content=chunks()))
    try:
        await asyncio.wait_for(first_chunk.wait(), 1)
        async with asyncio.timeout(1):
            async with GLOBAL_CONFIG_MUTATION_LOCK.hold():
                pass
    finally:
        finish_body.set()
    assert_error(await pending, 400, "invalid_request", KEY)
    assert not files.config.exists() and not files.secrets.exists()


@pytest.mark.asyncio
async def test_probe_does_not_hold_global_config_lock(app, files, monkeypatch):
    started = asyncio.Event()
    finish = asyncio.Event()

    class SlowAdapter:
        async def list_datasets(self, *, page, page_size):
            started.set()
            await finish.wait()
            return {"datasets": [], "total": 0}

        async def list_files(self, *, page, page_size, keywords=None):
            return {"files": [], "total": 0, "parent_folder": None}

        async def close(self):
            pass

    monkeypatch.setattr(connection_settings, "RagflowAdapter", lambda *args, **kwargs: SlowAdapter())
    pending = asyncio.create_task(request(app, "PUT", json=put(files)))
    try:
        await asyncio.wait_for(started.wait(), 1)
        async with asyncio.timeout(1):
            async with GLOBAL_CONFIG_MUTATION_LOCK.hold():
                pass
    finally:
        finish.set()
    assert (await pending).status_code == 200


@pytest.mark.asyncio
async def test_save_rejects_config_changes_that_win_while_probing(app, files, monkeypatch):
    external = {"theme": "changed-during-probe"}

    def respond(req):
        if req.url.path.endswith("/datasets"):
            files.config.write_text(json.dumps(external), encoding="utf-8")
            return httpx.Response(200, json={"code": 0, "data": [], "total_datasets": 0})
        return httpx.Response(200, json={"code": 0, "data": {"files": [], "total": 0, "parent_folder": None}})

    calls, _ = probe(monkeypatch, respond)
    response = await request(app, "PUT", json=put(files))
    assert_error(response, 409, "knowledgebase_config_changed", KEY)
    assert len(calls) == 2
    assert json.loads(files.config.read_text(encoding="utf-8")) == external
    assert not files.secrets.exists()


@pytest.mark.asyncio
async def test_read_keeps_and_save_replaces_the_running_client(app, files, monkeypatch):
    active = SimpleNamespace(close=AsyncMock())
    runtime.publish(active, runtime.publication_epoch())
    calls, _ = probe(monkeypatch)
    assert (await request(app, "GET")).status_code == 200
    assert runtime.get_client() is active
    response = await request(app, "PUT", json=put(files))
    assert response.status_code == 200 and response.json()["data"]["restart_required"] is False
    assert len(calls) == 2
    assert runtime.get_client() is not active
    assert runtime.get_client().connection.base_url == URL
    assert not runtime.get_client()._http.is_closed
    await asyncio.sleep(0)
    active.close.assert_awaited_once()


async def test_first_save_is_ready_and_uses_fresh_formal_client(app, files, monkeypatch, candidates):
    calls, adapters = probe(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/api/knowledgebase/status")).json()["data"]["ready"] is False
        saved = await http.put(PATH, json=put(files))
        assert saved.json()["data"]["applied"] is True
        assert (await http.get("/api/knowledgebase/status")).json()["data"] == {"ready": True, "configured": True}
        assert (await http.get("/api/knowledgebase/files")).status_code == 200
    client = runtime.get_client()
    assert client is candidates[0]
    assert client.connection.timeout_seconds == 30
    assert client.connection.max_upload_bytes == 32 * 1024 * 1024
    assert client._adapter is not adapters[0] and adapters[0]._client.is_closed
    assert not client._http.is_closed and len(calls) == 2


async def test_save_publishes_without_closing_a_borrowed_client(app, files, monkeypatch, candidates):
    active = SimpleNamespace(close=AsyncMock())
    runtime.publish(active, runtime.publication_epoch())
    probe(monkeypatch)
    async with runtime.lease_client() as borrowed:
        response = await request(app, "PUT", json=put(files))
        assert response.status_code == 200 and response.json()["data"]["applied"] is True
        assert borrowed is active
        assert runtime.get_client() is candidates[0]
        active.close.assert_not_awaited()
        async with runtime.lease_client() as latest:
            assert latest is candidates[0]
    active.close.assert_awaited_once()
    candidates[0].close.assert_not_awaited()


async def test_replacement_preserves_formal_limits_not_probe_timeout(app, files, monkeypatch, candidates):
    data = configured(files)
    data["api_services"]["knowledgebase"].update(timeout_seconds=73, max_upload_bytes=123456)
    files.config.write_text(json.dumps(data), encoding="utf-8")
    probe(monkeypatch)
    assert (await request(app, "PUT", json=put(files))).status_code == 200
    assert candidates[0].connection.timeout_seconds == 73
    assert candidates[0].connection.max_upload_bytes == 123456


@pytest.mark.parametrize("failure", ["construct", "probe", "write", "secret", "conflict"])
async def test_failed_save_preserves_live_client_and_closes_candidate(app, files, monkeypatch, candidates, failure):
    configured(files)
    active = SimpleNamespace(close=AsyncMock())
    runtime.publish(active, runtime.publication_epoch())
    before = files.config.read_bytes()

    def fail(*args, **kwargs):
        raise OSError(KEY)

    if failure == "probe":
        probe(monkeypatch, lambda req: httpx.Response(401))
        status, code = 502, "connection_test_failed"
    else:
        probe(monkeypatch)
        status, code = 500, "knowledgebase_save_failed"
    if failure == "construct":
        monkeypatch.setattr(connection_settings, "KnowledgebaseClient", fail)
    elif failure == "write":
        monkeypatch.setattr(connection_settings.ConfigWriter, "_write_raw", fail)
    elif failure == "secret":
        monkeypatch.setattr(SecretManager, "set", fail)
    elif failure == "conflict":
        original = connection_settings._load_settings
        count = 0

        def changed():
            nonlocal count
            count += 1
            path, data, settings = original()
            if count == 2:
                data["theme"] = "concurrent"
            return path, data, settings

        monkeypatch.setattr(connection_settings, "_load_settings", changed)
        status, code = 409, "knowledgebase_config_changed"
    response = await request(app, "PUT", json=put(files))
    assert_error(response, status, code, KEY, OLD_KEY)
    assert runtime.get_client() is active
    active.close.assert_not_awaited()
    assert files.config.read_bytes() == before
    assert len(candidates) == (0 if failure == "construct" else 1)
    for client in candidates:
        assert client._http.is_closed
        client.close.assert_awaited_once()


@pytest.mark.parametrize("close_error", [False, True])
async def test_save_does_not_wait_for_or_fail_on_old_close(app, files, monkeypatch, candidates, close_error, caplog):
    finish = asyncio.Event()

    async def close():
        await finish.wait()
        if close_error:
            raise RuntimeError(KEY)

    active = SimpleNamespace(close=AsyncMock(side_effect=close))
    runtime.publish(active, runtime.publication_epoch())
    probe(monkeypatch)
    try:
        response = await asyncio.wait_for(request(app, "PUT", json=put(files)), 1)
        assert response.status_code == 200 and response.json()["data"]["applied"] is True
        assert runtime.get_client() is candidates[0]
        assert not candidates[0]._http.is_closed
        assert runtime._closing_tasks
    finally:
        finish.set()
        await asyncio.gather(*tuple(runtime._closing_tasks))
    active.close.assert_awaited_once()
    assert KEY not in caplog.text
    if close_error:
        assert "Knowledgebase client cleanup failed." in caplog.text


@pytest.mark.parametrize("action", ["cancel", "stop", "stop_and_start"])
async def test_late_probe_cannot_publish_after_cancel_or_shutdown(app, files, monkeypatch, candidates, action):
    active = SimpleNamespace(close=AsyncMock())
    runtime.publish(active, runtime.publication_epoch())
    entered, finish, adapters = slow_probe(monkeypatch)
    pending = asyncio.create_task(request(app, "PUT", json=put(files)))
    await asyncio.wait_for(entered.wait(), 1)
    if action == "cancel":
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert runtime.get_client() is active
        active.close.assert_not_awaited()
    else:
        await runtime.stop()
        if action == "stop_and_start":
            monkeypatch.setattr(Config, "get", AsyncMock(return_value=SimpleNamespace(api_services=None)))
            assert await runtime.start_from_config() == {"status": "disabled"}
        finish.set()
        assert_error(await pending, 503, "knowledgebase_stopping", KEY)
        assert runtime.get_client() is None
        active.close.assert_awaited_once()
    assert not files.config.exists() and not files.secrets.exists()
    adapters[0].close.assert_awaited_once()
    candidates[0].close.assert_awaited_once()
    assert candidates[0]._http.is_closed


async def test_concurrent_saves_publish_only_the_persisted_winner(app, files, monkeypatch, candidates):
    entered, finish, _ = slow_probe(monkeypatch)
    first = asyncio.create_task(request(app, "PUT", json=put(files)))
    await asyncio.wait_for(entered.wait(), 1)
    entered.clear()
    second = asyncio.create_task(request(app, "PUT", json=put(files, url="https://second.test", key=OLD_KEY)))
    await asyncio.wait_for(entered.wait(), 1)
    finish.set()
    responses = await asyncio.gather(first, second)
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = runtime.get_client()
    saved = json.loads(files.config.read_text(encoding="utf-8"))["api_services"]["knowledgebase"]
    stored = json.loads(files.secrets.read_text(encoding="utf-8"))
    assert winner.connection.base_url == saved["base_url"]
    assert winner.connection.api_token == stored[saved["credential_id"]]
    winner.close.assert_not_awaited()
    loser = next(client for client in candidates if client is not winner)
    loser.close.assert_awaited_once()
    assert loser._http.is_closed
