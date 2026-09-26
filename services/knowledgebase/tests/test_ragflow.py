import httpx
import pytest
from flocks_knowledgebase.errors import UpstreamError
from flocks_knowledgebase.ragflow import RagflowClient

KEY = "secret-upstream-key-never-expose"


@pytest.fixture
async def make_client():
    clients = []

    def factory(handler, **kwargs):
        base_url = kwargs.pop("base_url", "https://engine.invalid/mounted")
        client = RagflowClient(base_url, KEY, transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.close()


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://engine.invalid", b"/api/v1/files"),
        ("https://engine.invalid/mounted", b"/mounted/api/v1/files"),
        ("https://engine.invalid/mounted/api/v1", b"/mounted/api/v1/files"),
    ],
)
async def test_client_keeps_the_deployment_prefix_and_service_key(make_client, base_url, expected):
    def handler(request):
        assert request.url.path.encode() == expected
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        return httpx.Response(200, json={"code": 0, "data": {"files": [], "total": 0, "parent_folder": None}})

    client = make_client(handler, base_url=base_url)
    assert await client.list_files() == {"files": [], "total": 0, "parent_folder": None}


async def test_dataset_create_and_retrieval_use_existing_ragflow_resources(make_client):
    def handler(request):
        if request.url.path.endswith("/datasets") and request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"id": "ds1", "name": "Notes"}})
        if request.url.path.endswith("/retrieval"):
            return httpx.Response(200, json={"code": 0, "data": {"chunks": [], "total": 0}})
        raise AssertionError(request.url.path)

    client = make_client(handler, base_url="https://engine.invalid")
    assert (await client.create_dataset({"name": "Notes"}))["id"] == "ds1"
    assert await client.retrieve({"dataset_ids": ["ds1"], "question": "q"}) == {"chunks": [], "total": 0}


async def test_upstream_failures_do_not_echo_the_api_key(make_client):
    def handler(request):
        return httpx.Response(500, json={"code": 500, "message": "key " + KEY})

    client = make_client(handler, base_url="https://engine.invalid")
    with pytest.raises(UpstreamError) as caught:
        await client.list_files()
    rendered = f"{caught.value} {caught.value.details}"
    assert KEY not in rendered
    assert caught.value.__cause__ is None


async def test_dot_segments_are_rejected(make_client):
    client = make_client(lambda request: httpx.Response(500), base_url="https://engine.invalid")
    with pytest.raises(Exception):
        await client.download_file("../secret")
