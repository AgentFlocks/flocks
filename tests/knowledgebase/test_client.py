import httpx
import pytest

from flocks.knowledgebase.client import Connection, KnowledgebaseClient
from flocks.knowledgebase.errors import KnowledgebaseError


def test_connection_accepts_one_service_token():
    connection = Connection(base_url="http://kb.local", api_token="a" * 32)
    assert connection.origin == "http://kb.local"


def test_connection_rejects_credentials_in_the_url():
    with pytest.raises(ValueError):
        Connection(base_url="http://user:secret@kb.local", api_token="a" * 32)


@pytest.mark.asyncio
async def test_errors_do_not_echo_the_service_token():
    token = "t" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {token}"
        return httpx.Response(500, json={"error": {"code": "upstream", "message": "failed " + token}})

    client = KnowledgebaseClient(Connection("http://kb.local", token), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(KnowledgebaseError) as caught:
            await client.files()
        assert token not in f"{caught.value} {caught.value.public()}"
    finally:
        await client.close()
