from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from flocks.integrations.strix_chat import StrixChatClient, StrixChatClientError


def test_strix_chat_client_defaults_to_loopback() -> None:
    with patch.dict(os.environ, {}, clear=True):
        client = StrixChatClient()
    assert client.base_url == "http://127.0.0.1:8486"


def test_strix_chat_client_rejects_remote_url_by_default() -> None:
    with pytest.raises(ValueError, match="ALLOW_REMOTE"):
        StrixChatClient(base_url="https://strix.example.com")


def test_strix_chat_client_allows_explicit_remote_url() -> None:
    with patch.dict(os.environ, {"FLOCKS_STRIX_CHAT_ALLOW_REMOTE": "true"}):
        client = StrixChatClient(base_url="https://strix.example.com")
    assert client.base_url == "https://strix.example.com"


@pytest.mark.asyncio
async def test_strix_chat_client_forwards_bearer_token() -> None:
    response = httpx.Response(
        200,
        json={"status": "ok"},
        request=httpx.Request("GET", "http://127.0.0.1:8486/healthz"),
    )
    request = AsyncMock(return_value=response)
    client_context = AsyncMock()
    client_context.__aenter__.return_value.request = request
    with patch(
        "flocks.integrations.strix_chat.httpx.AsyncClient",
        return_value=client_context,
    ) as client_factory:
        client = StrixChatClient(token="secret")
        assert await client.health() == {"status": "ok"}
    assert client_factory.call_args.kwargs["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_strix_chat_client_surfaces_sidecar_error() -> None:
    response = httpx.Response(
        409,
        json={"detail": "Strix agent is still starting"},
        request=httpx.Request("POST", "http://127.0.0.1:8486/api/v1/chat/chat-abc/message"),
    )
    client_context = AsyncMock()
    client_context.__aenter__.return_value.request = AsyncMock(return_value=response)
    with patch("flocks.integrations.strix_chat.httpx.AsyncClient", return_value=client_context):
        client = StrixChatClient()
        with pytest.raises(StrixChatClientError) as error:
            await client.send_message("chat-abc", {"message": "hello"})
    assert error.value.status_code == 409
    assert "still starting" in str(error.value)
