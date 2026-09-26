import json
from contextlib import asynccontextmanager

import httpx
import pytest

from flocks.knowledgebase.client import Connection, KnowledgebaseClient
from flocks.knowledgebase.errors import KnowledgebaseError

TOKEN = "t" * 40


@asynccontextmanager
async def client_for(handler, *, base_url="http://ragflow.test", limit=32 * 1024 * 1024):
    client = KnowledgebaseClient(
        Connection(base_url, TOKEN, max_upload_bytes=limit),
        transport=httpx.MockTransport(handler),
    )
    try:
        yield client
    finally:
        await client.close()


def file_page():
    return {"code": 0, "data": {"files": [], "total": 0, "parent_folder": None}}


def test_connection_accepts_one_engine_key():
    connection = Connection(base_url="http://ragflow.test", api_token=TOKEN)
    assert connection.origin == "http://ragflow.test"


@pytest.mark.parametrize("url", ["http://user:secret@ragflow.test", "http://ragflow.test/?token=x", "file:///tmp/data"])
def test_connection_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        Connection(base_url=url, api_token=TOKEN)


@pytest.mark.parametrize("token", ["", "x" * 31, "x" * 32 + "\n", "密" * 32])
def test_connection_keeps_the_engine_key_constraints(token):
    with pytest.raises(ValueError):
        Connection("http://ragflow.test", token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("base_url", "expected_path"),
    [
        ("http://ragflow.test", "/api/v1/files"),
        ("http://ragflow.test/mounted", "/mounted/api/v1/files"),
        ("http://ragflow.test/mounted/api/v1", "/mounted/api/v1/files"),
    ],
)
async def test_client_keeps_the_deployment_prefix_and_engine_key(base_url, expected_path):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == expected_path
        assert dict(request.url.params) == {"page": "1", "page_size": "20"}
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["Accept"] == "application/json"
        return httpx.Response(200, json=file_page())

    async with client_for(handler, base_url=base_url) as client:
        assert client._http.trust_env is False
        assert client._http.follow_redirects is False
        assert await client.files() == {"items": [], "total": 0, "page": 1}


@pytest.mark.asyncio
async def test_dataset_create_and_retrieval_keep_the_ragflow_payloads():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        if request.url.path.endswith("/datasets"):
            return httpx.Response(200, json={"code": 0, "data": {"id": "dataset-a", **payload}})
        return httpx.Response(200, json={"code": 0, "data": {"chunks": [], "total": 100}})

    async with client_for(handler) as client:
        dataset = await client.create_dataset({"name": " Notes ", "description": " Personal "})
        assert dataset == {
            "id": "dataset-a", "name": "Notes", "description": "Personal",
            "document_count": None, "chunk_count": None,
        }
        assert await client.retrieve({"dataset_ids": ["dataset-a"], "keywords": " question "}) == {
            "chunks": [], "total": 0,
        }
    assert requests == [
        ("/api/v1/datasets", {"name": "Notes", "description": "Personal"}),
        ("/api/v1/retrieval", {
            "dataset_ids": ["dataset-a"], "question": "question", "page": 1, "page_size": 5,
            "similarity_threshold": 0.2, "vector_similarity_weight": 0.3,
        }),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, {"code": 401, "message": TOKEN}, "upstream_unavailable"),
        (403, {"code": 403, "message": TOKEN}, "upstream_unavailable"),
        (500, {"code": 0, "message": TOKEN}, "upstream_unavailable"),
        (200, {"code": 102, "message": TOKEN}, "upstream_unavailable"),
        (200, {"code": 0, "data": {"failed_count": 1}}, "upstream_partial_failure"),
        (200, {"code": "0", "data": {}}, "upstream_invalid_response"),
        (200, {"code": False, "data": {}}, "upstream_invalid_response"),
        (200, {"code": 0, "message": 7, "data": {}}, "upstream_invalid_response"),
        (200, {"code": 0, "data": {"files": [], "total": 0}}, "upstream_invalid_response"),
    ],
)
async def test_upstream_errors_keep_the_core_error_boundary(status, body, code):
    async with client_for(lambda request: httpx.Response(status, json=body)) as client:
        with pytest.raises(KnowledgebaseError) as caught:
            await client.files()
    assert caught.value.status == 502
    assert caught.value.code == code
    assert caught.value.public()["error"]["details"] == {}
    assert caught.value.public()["error"]["request_id"] is None
    assert caught.value.__cause__ is None
    assert TOKEN not in f"{caught.value} {caught.value.public()}"


@pytest.mark.asyncio
async def test_transport_failure_is_sanitized_and_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("failed " + TOKEN, request=request)

    async with client_for(handler) as client:
        with pytest.raises(KnowledgebaseError) as caught:
            await client.create_dataset({"name": "Notes"})
    assert len(calls) == 1
    assert caught.value.status == 502
    assert caught.value.code == "upstream_unavailable"
    assert TOKEN not in str(caught.value.public())


@pytest.mark.asyncio
async def test_redirects_are_not_followed():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "http://other.test/files"})

    async with client_for(handler) as client:
        with pytest.raises(KnowledgebaseError) as caught:
            await client.files()
    assert caught.value.code == "upstream_unavailable"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ident", ["", "..", "../secret", "a/b", "a\\b", "a?x=y", "x" * 129])
async def test_resource_ids_are_rejected_before_transport(ident):
    def handler(request):
        pytest.fail("An invalid resource ID must not reach the engine")

    async with client_for(handler) as client:
        with pytest.raises(KnowledgebaseError) as caught:
            await client.content(ident)
    assert (caught.value.status, caught.value.code) == (422, "invalid_resource_id")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["link_files", "remove_documents", "parse"])
@pytest.mark.parametrize("ids", [[], ["a", "a"], ["../a"]])
async def test_invalid_id_batches_never_reach_mutation_endpoints(operation, ids):
    def handler(request):
        pytest.fail("Invalid IDs must not reach the engine")

    async with client_for(handler) as client:
        with pytest.raises(KnowledgebaseError) as caught:
            await getattr(client, operation)("dataset-a", ids)
    assert (caught.value.status, caught.value.code) == (422, "validation_error")


@pytest.mark.asyncio
@pytest.mark.parametrize("disposition", ["attachment", "inline"])
async def test_json_originals_with_error_like_content_are_downloadable(disposition):
    content = b'{"code":102,"message":"original file"}'

    def handler(request):
        assert request.url.path == "/api/v1/files/file-a"
        return httpx.Response(200, content=content, headers={
            "Content-Type": "application/json; charset=utf-8",
            "Content-Disposition": disposition,
        })

    async with client_for(handler) as client:
        assert await client.content("file-a") == (content, "application/json")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "media", "body", "code"),
    [
        (200, "application/json", b'{"code":102}', "upstream_unavailable"),
        (200, "application/problem+json", b'{"code":0}', "upstream_invalid_response"),
        (201, "text/plain", b"file", "upstream_invalid_response"),
        (404, "application/json", b'{"code":404}', "upstream_unavailable"),
        (200, "text/plain\r\nx: y", b"file", "upstream_invalid_response"),
    ],
)
async def test_download_errors_are_not_returned_as_file_content(status, media, body, code):
    async with client_for(lambda request: httpx.Response(
        status, content=body, headers={"Content-Type": media}
    )) as client:
        with pytest.raises(KnowledgebaseError) as caught:
            await client.content("file-a")
    assert (caught.value.status, caught.value.code) == (502, code)


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_download_responses_are_bounded(streamed):
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"12345"
            yield b"67890"

    def handler(request):
        if streamed:
            return httpx.Response(200, stream=Chunks(), headers={"Content-Type": "text/plain"})
        return httpx.Response(200, content=b"1234567890", headers={"Content-Type": "text/plain"})

    async with client_for(handler, limit=8) as client:
        with pytest.raises(KnowledgebaseError) as caught:
            await client.content("file-a")
    assert (caught.value.status, caught.value.code) == (502, "upstream_content_too_large")
