from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import pytest

from flocks.provider.provider import ChatMessage, ChatResponse
from flocks.provider.provider import ProviderConfig
from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider


def _build_provider_with_client() -> tuple[OpenAICompatibleProvider, AsyncMock]:
    provider = OpenAICompatibleProvider()
    create = AsyncMock()
    provider._client = MagicMock()
    provider._client.chat.completions.create = create
    return provider, create


def _mock_chat_response(content: str = "Paris"):
    response = MagicMock()
    response.id = "resp_1"
    response.model = "kimi-k2.5"
    response.usage = MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message = MagicMock(content=content)
    response.choices = [choice]
    return response


class TestOpenAICompatibleProviderConfiguration:
    @patch("httpx.AsyncClient")
    @patch("openai.AsyncOpenAI")
    def test_get_client_respects_verify_ssl_false(self, mock_async_openai, mock_http_client):
        provider = OpenAICompatibleProvider()
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

        mock_http_client.assert_called_once_with(verify=False, timeout=120.0)
        mock_async_openai.assert_called_once_with(
            api_key="test-api-key",
            base_url="https://gateway.internal/v1",
            http_client=http_client,
        )


def _chat_response(content: str, model: str = "MiniMax-M2.5") -> ChatResponse:
    return ChatResponse(
        id="resp_fallback",
        model=model,
        content=content,
        finish_reason="stop",
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )


async def _empty_stream():
    if False:
        yield None


async def _stream_from_chunks(*chunks):
    for chunk in chunks:
        yield chunk


class TestOpenAICompatibleProviderTemperature:
    @pytest.mark.asyncio
    async def test_chat_omits_temperature_when_not_provided(self):
        provider, create = _build_provider_with_client()
        create.return_value = _mock_chat_response()

        await provider.chat(
            "kimi-k2.5",
            [ChatMessage(role="user", content="hello")],
            max_tokens=20,
        )

        kwargs = create.await_args.kwargs
        assert "temperature" not in kwargs
        assert kwargs["model"] == "kimi-k2.5"
        assert kwargs["max_tokens"] == 20

    @pytest.mark.asyncio
    async def test_chat_passes_explicit_temperature(self):
        provider, create = _build_provider_with_client()
        create.return_value = _mock_chat_response()

        await provider.chat(
            "kimi-k2.5",
            [ChatMessage(role="user", content="hello")],
            temperature=1.0,
        )

        kwargs = create.await_args.kwargs
        assert kwargs["temperature"] == 1.0


class TestOpenAICompatibleProviderMiniMaxFallback:
    def test_is_minimax_empty_response_target_matches_supported_aliases(self):
        assert OpenAICompatibleProvider._is_minimax_empty_response_target("MiniMax-M2.5") is True
        assert OpenAICompatibleProvider._is_minimax_empty_response_target("minimax_m2.7") is True
        assert OpenAICompatibleProvider._is_minimax_empty_response_target("custom-minimax-m2.5-prod") is True
        assert OpenAICompatibleProvider._is_minimax_empty_response_target("foo/minimax-m2.7-202506") is True
        assert OpenAICompatibleProvider._is_minimax_empty_response_target("gpt-4o-mini") is False

    @pytest.mark.asyncio
    async def test_minimax_empty_stream_recovers_with_chat_fallback(self, monkeypatch):
        provider, create = _build_provider_with_client()
        create.return_value = _empty_stream()
        provider.chat = AsyncMock(return_value=_chat_response("Recovered from fallback"))
        sleep_mock = AsyncMock()
        monkeypatch.setattr("flocks.provider.sdk.openai_compatible.asyncio.sleep", sleep_mock)

        chunks = [
            chunk async for chunk in provider.chat_stream(
                "MiniMax-M2.5",
                [ChatMessage(role="user", content="hello")],
            )
        ]

        assert [chunk.delta for chunk in chunks] == ["Recovered from fallback"]
        sleep_mock.assert_awaited_once_with(3)
        provider.chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_minimax_empty_stream_retries_without_tools_after_blank_fallback(self, monkeypatch):
        provider, create = _build_provider_with_client()
        create.return_value = _empty_stream()
        provider.chat = AsyncMock(side_effect=[
            _chat_response("   "),
            _chat_response("Recovered without tools"),
        ])
        sleep_mock = AsyncMock()
        monkeypatch.setattr("flocks.provider.sdk.openai_compatible.asyncio.sleep", sleep_mock)
        tools = [{"type": "function", "function": {"name": "foo", "parameters": {"type": "object"}}}]

        chunks = [
            chunk async for chunk in provider.chat_stream(
                "MiniMax-M2.7",
                [ChatMessage(role="user", content="hello")],
                tools=tools,
            )
        ]

        assert [chunk.delta for chunk in chunks] == ["Recovered without tools"]
        assert sleep_mock.await_count == 2
        assert provider.chat.await_count == 2
        assert provider.chat.await_args_list[0].kwargs["tools"] == tools
        assert "tools" not in provider.chat.await_args_list[1].kwargs

    @pytest.mark.asyncio
    async def test_minimax_empty_stream_raises_after_fallback_chain(self, monkeypatch):
        provider, create = _build_provider_with_client()
        create.return_value = _empty_stream()
        provider.chat = AsyncMock(side_effect=[
            _chat_response(""),
            _chat_response("   "),
        ])
        sleep_mock = AsyncMock()
        monkeypatch.setattr("flocks.provider.sdk.openai_compatible.asyncio.sleep", sleep_mock)
        tools = [{"type": "function", "function": {"name": "foo", "parameters": {"type": "object"}}}]

        with pytest.raises(ValueError, match="MiniMax target model returned empty output after provider fallback chain"):
            _ = [
                chunk async for chunk in provider.chat_stream(
                    "MiniMax-M2.5",
                    [ChatMessage(role="user", content="hello")],
                    tools=tools,
                )
            ]

        assert sleep_mock.await_count == 2
        assert provider.chat.await_count == 2

    @pytest.mark.asyncio
    async def test_non_target_model_skips_minimax_retry_wait(self, monkeypatch):
        provider, create = _build_provider_with_client()
        create.return_value = _empty_stream()
        provider.chat = AsyncMock(return_value=_chat_response("Recovered generic fallback", model="gpt-4o-mini"))
        sleep_mock = AsyncMock()
        monkeypatch.setattr("flocks.provider.sdk.openai_compatible.asyncio.sleep", sleep_mock)

        chunks = [
            chunk async for chunk in provider.chat_stream(
                "gpt-4o-mini",
                [ChatMessage(role="user", content="hello")],
            )
        ]

        assert [chunk.delta for chunk in chunks] == ["Recovered generic fallback"]
        sleep_mock.assert_not_awaited()


class TestOpenAICompatibleProviderStreamingUsage:
    @pytest.mark.asyncio
    async def test_chat_stream_includes_usage_in_terminal_chunk(self):
        provider, create = _build_provider_with_client()
        create.return_value = _stream_from_chunks(
            SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(prompt_tokens=13, completion_tokens=8, total_tokens=21),
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="hello", tool_calls=None),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            ),
        )

        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                "kimi-k2.5",
                [ChatMessage(role="user", content="hello")],
            )
        ]

        assert create.await_args.kwargs["stream_options"] == {"include_usage": True}
        assert chunks[-1].finish_reason == "stop"
        assert chunks[-1].usage == {
            "prompt_tokens": 13,
            "completion_tokens": 8,
            "total_tokens": 21,
        }

    @pytest.mark.asyncio
    async def test_chat_stream_emits_trailing_usage_when_usage_only_chunk_arrives_after_finish(self):
        provider, create = _build_provider_with_client()
        create.return_value = _stream_from_chunks(
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="hello", tool_calls=None),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(prompt_tokens=13, completion_tokens=8, total_tokens=21),
            ),
        )

        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                "kimi-k2.5",
                [ChatMessage(role="user", content="hello")],
            )
        ]

        assert any(chunk.finish_reason == "stop" for chunk in chunks)
        assert chunks[-1].usage == {
            "prompt_tokens": 13,
            "completion_tokens": 8,
            "total_tokens": 21,
        }
        assert chunks[-1].finish_reason is None

    @pytest.mark.asyncio
    async def test_chat_stream_retries_without_stream_options_when_unsupported(self):
        provider, create = _build_provider_with_client()
        create.side_effect = [
            ValueError("unsupported parameter: include_usage"),
            _stream_from_chunks(
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="hello", tool_calls=None),
                            finish_reason="stop",
                        )
                    ],
                    usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7),
                )
            ),
        ]

        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                "kimi-k2.5",
                [ChatMessage(role="user", content="hello")],
            )
        ]

        assert create.await_count == 2
        assert "stream_options" in create.await_args_list[0].kwargs
        assert "stream_options" not in create.await_args_list[1].kwargs
        assert chunks[-1].usage == {
            "prompt_tokens": 5,
            "completion_tokens": 2,
            "total_tokens": 7,
        }
