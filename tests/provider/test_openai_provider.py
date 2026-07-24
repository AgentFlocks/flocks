from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.provider.provider import ChatMessage, ProviderConfig
from flocks.provider.sdk.openai import OpenAIProvider


async def _stream_from_chunks(*chunks):
    for chunk in chunks:
        yield chunk


class TestOpenAIProviderConfiguration:
    @patch("httpx.AsyncClient")
    @patch("openai.AsyncOpenAI")
    def test_get_client_respects_verify_ssl_false(self, mock_async_openai, mock_http_client):
        provider = OpenAIProvider()
        provider.configure(
            ProviderConfig(
                provider_id=provider.id,
                api_key="test-api-key",
                base_url="https://gateway.internal/v1",
                custom_settings={"verify_ssl": False},
            )
        )

        http_client = MagicMock()
        mock_http_client.return_value = http_client
        mock_async_openai.return_value = MagicMock()

        provider._get_client()

        # Granular timeout supports multimodal payloads; verify fields
        # semantically so minor adjustments to non-critical values don't break.
        assert mock_http_client.call_count == 1
        kwargs = mock_http_client.call_args.kwargs
        assert kwargs["trust_env"] is True
        assert kwargs["verify"] is False
        timeout_arg = kwargs["timeout"]
        assert getattr(timeout_arg, "connect", None) == 30.0
        assert getattr(timeout_arg, "read", None) == 600.0
        assert getattr(timeout_arg, "write", None) == 600.0

        mock_async_openai.assert_called_once_with(
            api_key="test-api-key",
            base_url="https://gateway.internal/v1",
            http_client=http_client,
        )

    def test_configure_invalidates_existing_client_when_credentials_change(self):
        provider = OpenAIProvider()
        stale_client = MagicMock()
        provider._client = stale_client

        provider.configure(
            ProviderConfig(
                provider_id=provider.id,
                api_key="new-api-key",
                base_url="https://new-gateway.internal/v1",
            )
        )

        assert provider._client is None


class TestOpenAIProviderStreamingUsage:
    @pytest.mark.asyncio
    async def test_chat_stream_preserves_nested_reasoning_tokens(self):
        provider = OpenAIProvider()
        create = AsyncMock()
        provider._client = MagicMock()
        provider._client.chat.completions.create = create

        usage_chunk = SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=11,
                completion_tokens=7,
                total_tokens=18,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=5),
            ),
        )
        finish_chunk = SimpleNamespace(
            choices=[SimpleNamespace(delta=None, finish_reason="stop")],
            usage=None,
        )
        create.return_value = _stream_from_chunks(finish_chunk, usage_chunk)

        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                "gpt-5.6-luna",
                [ChatMessage(role="user", content="hello")],
            )
        ]

        assert chunks[-1].usage == {
            "prompt_tokens": 11,
            "completion_tokens": 2,
            "total_tokens": 18,
            "reasoning_tokens": 5,
        }
