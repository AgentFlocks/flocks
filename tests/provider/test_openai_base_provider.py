"""
Tests for OpenAI Base Provider get_models() method.

Tests various scenarios:
1. Provider with CATALOG_ID set (loads from catalog)
2. Provider without CATALOG_ID (returns empty list)
3. Provider with catalog load failure (handles gracefully)
4. Provider with config-based models (defers to config merge)
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch, MagicMock

import pytest

import flocks.provider.sdk.openai_base as openai_base_module
from flocks.provider.sdk.openai_base import (
    OpenAIBaseProvider,
    build_reasoning_metadata,
    extract_reasoning_content,
    extract_reasoning_content_with_source,
    extract_reasoning_details,
)
from flocks.provider.provider import ModelInfo, ModelCapabilities, ProviderConfig


class MockProviderWithCatalog(OpenAIBaseProvider):
    """Mock provider with CATALOG_ID set."""
    
    DEFAULT_BASE_URL = "https://api.test.com/v1"
    ENV_API_KEY = ["TEST_API_KEY"]
    ENV_BASE_URL = "TEST_BASE_URL"
    CATALOG_ID = "test-provider"
    
    def __init__(self):
        super().__init__(provider_id="test-provider", name="Test Provider")


class MockProviderWithoutCatalog(OpenAIBaseProvider):
    """Mock provider without CATALOG_ID."""
    
    DEFAULT_BASE_URL = "https://api.custom.com/v1"
    ENV_API_KEY = ["CUSTOM_API_KEY"]
    ENV_BASE_URL = "CUSTOM_BASE_URL"
    CATALOG_ID = ""  # No catalog
    
    def __init__(self):
        super().__init__(provider_id="custom-provider", name="Custom Provider")


class MockProviderPrefersCompletionTokens(MockProviderWithoutCatalog):
    """Mock OpenAI-compatible provider that opts into newer token naming."""

    PREFER_MAX_COMPLETION_TOKENS = True


class TestOpenAIBaseProviderGetModels:
    """Test suite for get_models() method."""

    def test_default_http_timeout_values(self):
        """OpenAI-style providers share fail-fast read and long write timeouts."""
        timeout = openai_base_module.DEFAULT_HTTP_TIMEOUT

        assert timeout.connect == 30.0
        assert timeout.read == 180.0
        assert timeout.write == 1800.0
        assert timeout.pool == 60.0
    
    def test_get_models_with_catalog_success(self):
        """Test get_models() returns configured models."""
        provider = MockProviderWithCatalog()

        provider._config_models = [
            ModelInfo(
                id="test-model-1",
                name="Test Model 1",
                provider_id="test-provider",
                capabilities=ModelCapabilities(
                    supports_tools=True,
                    supports_vision=False,
                    supports_streaming=True,
                    supports_reasoning=False,
                    context_window=128000,
                    max_tokens=4096,
                ),
            )
        ]

        models = provider.get_models()

        assert len(models) == 1
        assert isinstance(models[0], ModelInfo)
        assert models[0].id == "test-model-1"
        assert models[0].name == "Test Model 1"
        assert models[0].provider_id == "test-provider"
        assert models[0].capabilities.supports_tools is True
        assert models[0].capabilities.supports_vision is False
        assert models[0].capabilities.supports_streaming is True
        assert models[0].capabilities.context_window == 128000
        assert models[0].capabilities.max_tokens == 4096

    def test_get_models_with_multiple_models(self):
        """Test get_models() returns multiple configured models."""
        provider = MockProviderWithCatalog()

        provider._config_models = [
            ModelInfo(
                id=f"test-model-{i}",
                name=f"Test Model {i}",
                provider_id="test-provider",
                capabilities=ModelCapabilities(
                    supports_tools=True,
                    supports_vision=i == 2,
                    supports_streaming=True,
                    supports_reasoning=False,
                    context_window=100000,
                    max_tokens=4096,
                ),
            )
            for i in range(3)
        ]

        models = provider.get_models()

        assert len(models) == 3
        assert models[0].id == "test-model-0"
        assert models[1].id == "test-model-1"
        assert models[2].id == "test-model-2"
        assert models[2].capabilities.supports_vision is True
        assert models[0].capabilities.supports_vision is False

    def test_get_models_without_catalog(self):
        """Test get_models() for provider without CATALOG_ID."""
        provider = MockProviderWithoutCatalog()

        models = provider.get_models()

        # Should return empty list when no configured models
        assert models == []
        assert isinstance(models, list)

    def test_get_models_catalog_import_error(self):
        """Test get_models() is independent of catalog imports."""
        provider = MockProviderWithCatalog()

        with patch('flocks.provider.model_catalog.get_provider_model_definitions') as mock_get_defs:
            mock_get_defs.side_effect = ImportError("Cannot import module")

            models = provider.get_models()

            assert models == []
            mock_get_defs.assert_not_called()

    def test_get_models_catalog_returns_none(self):
        """Test get_models() ignores catalog results when not configured."""
        provider = MockProviderWithCatalog()

        with patch('flocks.provider.model_catalog.get_provider_model_definitions') as mock_get_defs:
            mock_get_defs.return_value = None

            models = provider.get_models()

            assert models == []
            mock_get_defs.assert_not_called()

    def test_get_models_catalog_returns_empty_list(self):
        """Test get_models() ignores empty catalog results."""
        provider = MockProviderWithCatalog()

        with patch('flocks.provider.model_catalog.get_provider_model_definitions') as mock_get_defs:
            mock_get_defs.return_value = []

            models = provider.get_models()

            assert models == []
            mock_get_defs.assert_not_called()

    def test_get_models_catalog_generic_exception(self):
        """Test get_models() does not depend on catalog lookups."""
        provider = MockProviderWithCatalog()

        with patch('flocks.provider.model_catalog.get_provider_model_definitions') as mock_get_defs:
            mock_get_defs.side_effect = ValueError("Invalid catalog data")

            models = provider.get_models()

            assert models == []
            mock_get_defs.assert_not_called()

    def test_get_models_with_config_models(self):
        """Test get_models() when provider has config models loaded."""
        provider = MockProviderWithoutCatalog()

        # Simulate Provider.apply_config() loading models into _config_models
        from flocks.provider.provider import ModelInfo, ModelCapabilities
        provider._config_models = [
            ModelInfo(
                id="custom-model-1",
                name="Custom Model 1",
                provider_id="custom-provider",
                capabilities=ModelCapabilities(
                    supports_tools=True,
                    supports_vision=False,
                    supports_streaming=True,
                    supports_reasoning=False,
                    max_tokens=4096,
                    context_window=128000
                )
            ),
            ModelInfo(
                id="custom-model-2",
                name="Custom Model 2",
                provider_id="custom-provider",
                capabilities=ModelCapabilities(
                    supports_tools=True,
                    supports_vision=True,
                    supports_streaming=True,
                    supports_reasoning=False,
                    max_tokens=8192,
                    context_window=200000
                )
            )
        ]

        models = provider.get_models()

        # Should return config models
        assert len(models) == 2
        assert models[0].id == "custom-model-1"
        assert models[0].name == "Custom Model 1"
        assert models[1].id == "custom-model-2"
        assert models[1].name == "Custom Model 2"
        assert models[1].capabilities.supports_vision is True

    def test_get_models_missing_supports_reasoning(self):
        """Test get_models() preserves configured models without reasoning support."""
        provider = MockProviderWithCatalog()

        provider._config_models = [
            ModelInfo(
                id="old-model",
                name="Old Model",
                provider_id="test-provider",
                capabilities=ModelCapabilities(
                    supports_tools=True,
                    supports_vision=False,
                    supports_streaming=True,
                    supports_reasoning=False,
                    context_window=4096,
                    max_tokens=2048,
                ),
            )
        ]

        models = provider.get_models()

        assert len(models) == 1
        assert models[0].capabilities.supports_reasoning is False

    def test_get_models_with_reasoning_support(self):
        """Test get_models() preserves configured reasoning support."""
        provider = MockProviderWithCatalog()

        provider._config_models = [
            ModelInfo(
                id="reasoning-model",
                name="Reasoning Model",
                provider_id="test-provider",
                capabilities=ModelCapabilities(
                    supports_tools=True,
                    supports_vision=True,
                    supports_streaming=True,
                    supports_reasoning=True,
                    context_window=200000,
                    max_tokens=8192,
                ),
            )
        ]

        models = provider.get_models()

        assert len(models) == 1
        assert models[0].capabilities.supports_reasoning is True
        assert models[0].capabilities.supports_vision is True


class TestOpenAIBaseProviderConfiguration:
    """Test provider configuration and initialization."""
    
    def test_provider_initialization(self):
        """Test basic provider initialization."""
        provider = MockProviderWithCatalog()
        
        assert provider.id == "test-provider"
        assert provider.name == "Test Provider"
        assert provider.DEFAULT_BASE_URL == "https://api.test.com/v1"
        assert provider.CATALOG_ID == "test-provider"
    
    def test_provider_without_api_key(self):
        """Test provider initialization without API key."""
        provider = MockProviderWithCatalog()
        
        # Should not raise on initialization
        assert provider._api_key is None or provider._api_key == ""
    
    def test_is_configured_without_key(self):
        """Test is_configured() returns False without API key."""
        provider = MockProviderWithCatalog()
        
        # Clear any environment API key
        provider._api_key = None
        provider._config = None
        
        assert provider.is_configured() is False
    
    def test_is_configured_with_key(self):
        """Test is_configured() returns True with API key."""
        provider = MockProviderWithCatalog()
        
        provider._api_key = "test-api-key-123"
        
        assert provider.is_configured() is True
    
    def test_is_configured_with_config(self):
        """Test is_configured() uses config API key."""
        provider = MockProviderWithCatalog()
        provider._api_key = None
        
        mock_config = Mock(spec=ProviderConfig)
        mock_config.api_key = "config-api-key"
        provider._config = mock_config
        
        assert provider.is_configured() is True
    
    @patch.dict('os.environ', {'TEST_API_KEY': 'env-api-key'})
    def test_resolve_env_key(self):
        """Test API key resolution from environment."""
        provider = MockProviderWithCatalog()
        
        # Should have resolved from TEST_API_KEY env var
        assert provider._api_key == 'env-api-key'
    
    @patch.dict('os.environ', {'TEST_BASE_URL': 'https://custom.api.com'})
    def test_resolve_base_url_from_env(self):
        """Test base URL resolution from environment."""
        provider = MockProviderWithCatalog()
        
        assert provider._base_url == 'https://custom.api.com'
    
    def test_resolve_base_url_default(self):
        """Test base URL uses default when env not set."""
        provider = MockProviderWithCatalog()
        
        # Remove env var effect
        provider._base_url = provider.DEFAULT_BASE_URL
        
        assert provider._base_url == "https://api.test.com/v1"

    @patch("httpx.AsyncClient")
    @patch("openai.AsyncOpenAI")
    def test_get_client_respects_verify_ssl_false(self, mock_async_openai, mock_http_client):
        """verify_ssl=false should disable certificate verification for self-hosted gateways."""
        provider = MockProviderWithCatalog()
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

        # Granular timeout supports multimodal payloads; ``trust_env`` defaults
        # to True so corporate egress proxies work out of the box.
        # See ``OpenAIBaseProvider._get_client``.
        assert mock_http_client.call_count == 1
        kwargs = mock_http_client.call_args.kwargs
        assert kwargs["verify"] is False
        assert kwargs["trust_env"] is True
        timeout_arg = kwargs["timeout"]
        # Either an httpx.Timeout instance or compatible object: assert the
        # connect/read/write components rather than equality so future tweaks
        # to non-essential pool durations don't break the test.
        assert getattr(timeout_arg, "connect", None) == 30.0
        assert getattr(timeout_arg, "read", None) == 180.0
        assert getattr(timeout_arg, "write", None) == 1800.0

        mock_async_openai.assert_called_once_with(
            api_key="test-api-key",
            base_url="https://gateway.internal/v1",
            http_client=http_client,
        )


class TestOpenAIBaseProviderTemperature:
    def _build_provider_with_client(self):
        provider = MockProviderWithoutCatalog()
        create = AsyncMock()
        provider._client = MagicMock()
        provider._client.chat.completions.create = create
        return provider, create

    def _build_completion_preferring_provider_with_client(self):
        provider = MockProviderPrefersCompletionTokens()
        create = AsyncMock()
        provider._client = MagicMock()
        provider._client.chat.completions.create = create
        return provider, create

    @staticmethod
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

    @pytest.mark.asyncio
    async def test_chat_uses_max_tokens_by_default_when_max_tokens_provided(self):
        provider, create = self._build_provider_with_client()
        create.return_value = self._mock_chat_response()

        from flocks.provider.provider import ChatMessage

        await provider.chat(
            "kimi-k2.5",
            [ChatMessage(role="user", content="hello")],
            max_tokens=20,
        )

        kwargs = create.await_args.kwargs
        assert "temperature" not in kwargs
        assert kwargs["model"] == "kimi-k2.5"
        assert kwargs["max_tokens"] == 20
        assert "max_completion_tokens" not in kwargs

    @pytest.mark.asyncio
    async def test_chat_prefers_max_completion_tokens_when_provider_opts_in(self):
        provider, create = self._build_completion_preferring_provider_with_client()
        create.return_value = self._mock_chat_response()

        from flocks.provider.provider import ChatMessage

        await provider.chat(
            "gpt-5.4",
            [ChatMessage(role="user", content="hello")],
            max_completion_tokens=20,
        )

        kwargs = create.await_args.kwargs
        assert kwargs["max_completion_tokens"] == 20
        assert "max_tokens" not in kwargs

    @pytest.mark.asyncio
    async def test_chat_passes_explicit_temperature(self):
        provider, create = self._build_provider_with_client()
        create.return_value = self._mock_chat_response()

        from flocks.provider.provider import ChatMessage

        await provider.chat(
            "kimi-k2.5",
            [ChatMessage(role="user", content="hello")],
            temperature=1.0,
        )

        kwargs = create.await_args.kwargs
        assert kwargs["temperature"] == 1.0

    @pytest.mark.asyncio
    async def test_chat_accepts_direct_max_completion_tokens_kwarg(self):
        provider, create = self._build_provider_with_client()
        create.return_value = self._mock_chat_response()

        from flocks.provider.provider import ChatMessage

        await provider.chat(
            "gpt-5.4",
            [ChatMessage(role="user", content="hello")],
            max_completion_tokens=33,
        )

        kwargs = create.await_args.kwargs
        assert kwargs["max_completion_tokens"] == 33
        assert "max_tokens" not in kwargs

    @pytest.mark.asyncio
    async def test_chat_treats_explicit_max_completion_tokens_as_authoritative(self):
        provider, create = self._build_provider_with_client()
        create.return_value = self._mock_chat_response()

        from flocks.provider.provider import ChatMessage

        await provider.chat(
            "gpt-5.4",
            [ChatMessage(role="user", content="hello")],
            max_tokens=20,
            max_completion_tokens=33,
        )

        kwargs = create.await_args.kwargs
        assert kwargs["max_completion_tokens"] == 33
        assert "max_tokens" not in kwargs

    @pytest.mark.asyncio
    async def test_chat_falls_back_to_max_tokens_when_completion_variant_is_rejected(self):
        provider, create = self._build_provider_with_client()
        create.side_effect = [
            ValueError("unsupported parameter: max_completion_tokens"),
            self._mock_chat_response(),
        ]

        from flocks.provider.provider import ChatMessage

        await provider.chat(
            "gpt-5.4",
            [ChatMessage(role="user", content="hello")],
            max_completion_tokens=20,
        )

        assert create.await_count == 2
        assert create.await_args_list[0].kwargs["max_completion_tokens"] == 20
        assert "max_tokens" not in create.await_args_list[0].kwargs
        assert create.await_args_list[1].kwargs["max_tokens"] == 20
        assert "max_completion_tokens" not in create.await_args_list[1].kwargs

    @pytest.mark.asyncio
    async def test_chat_does_not_fallback_for_completion_token_value_errors(self):
        provider, create = self._build_provider_with_client()
        create.side_effect = ValueError("max_completion_tokens must be <= 4096")

        from flocks.provider.provider import ChatMessage

        with pytest.raises(ValueError, match="max_completion_tokens must be <= 4096"):
            await provider.chat(
                "gpt-5.4",
                [ChatMessage(role="user", content="hello")],
                max_completion_tokens=200000,
            )

        assert create.await_count == 1

    def test_summarise_messages_compacts_long_history(self):
        summary = openai_base_module._summarise_messages(
            [
                {"role": "system", "content": "a" * 10},
                {"role": "user", "content": "b" * 20},
                {"role": "assistant", "content": "c" * 30},
                {"role": "tool", "content": "d" * 40},
                {"role": "assistant", "content": "e" * 50},
                {"role": "user", "content": "f" * 60},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,abcd"},
                        },
                    ],
                },
                "skipped",
            ]
        )

        assert summary["message_count"] == 8
        assert summary["role_counts"] == {
            "system": 1,
            "user": 3,
            "assistant": 2,
            "tool": 1,
        }
        assert summary["multimodal_messages"] == 1
        assert summary["block_type_counts"] == {"text": 1, "image_url": 1}
        assert summary["skipped_types"] == {"str": 1}
        assert len(summary["tail"]) == 6
        assert summary["tail"][-1] == {
            "role": "user",
            "content_kind": "blocks",
            "block_count": 2,
            "block_types": {"text": 1, "image_url": 1},
            "text_chars": 5,
            "image_url_chars": 26,
            "content_chars": 31,
        }

    @pytest.mark.asyncio
    async def test_chat_logs_compact_message_summary(self):
        provider, create = self._build_provider_with_client()
        create.return_value = self._mock_chat_response()
        info = Mock()

        from flocks.provider.provider import ChatMessage

        with patch.object(openai_base_module, "log", Mock(info=info)):
            await provider.chat(
                "kimi-k2.5",
                [
                    ChatMessage(role="system", content="system prompt"),
                    ChatMessage(role="user", content="hello"),
                    ChatMessage(role="assistant", content="world"),
                ],
                max_tokens=20,
            )

        event, payload = info.call_args.args
        assert event == "openai_base.chat.request"
        assert "message_summary" in payload
        assert "message_shapes" not in payload
        assert payload["message_summary"]["message_count"] == 3
        assert payload["message_summary"]["role_counts"] == {
            "system": 1,
            "user": 1,
            "assistant": 1,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("model_id", "extra_body"),
        [
            ("kimi-k2.6", {"thinking": {"type": "enabled"}}),
            ("kimi-k2.7-code", {"thinking": {"type": "enabled"}}),
            ("kimi-k3", {"reasoning_effort": "max"}),
        ],
    )
    async def test_chat_logs_effective_extra_body_thinking_state(
        self,
        model_id,
        extra_body,
    ):
        provider, create = self._build_provider_with_client()
        create.return_value = self._mock_chat_response()
        info = Mock()

        from flocks.provider.provider import ChatMessage

        with patch.object(openai_base_module, "log", Mock(info=info)):
            await provider.chat(
                model_id,
                [ChatMessage(role="user", content="hello")],
                extra_body=extra_body,
            )

        _event, payload = info.call_args.args
        assert payload["thinking_enabled"] is True

    @pytest.mark.asyncio
    async def test_kimi_k3_uses_max_completion_tokens(self):
        provider, create = self._build_provider_with_client()
        create.return_value = self._mock_chat_response()

        from flocks.provider.provider import ChatMessage

        await provider.chat(
            "kimi-k3",
            [ChatMessage(role="user", content="hello")],
            max_tokens=131072,
        )

        kwargs = create.await_args.kwargs
        assert kwargs["max_completion_tokens"] == 131072
        assert "max_tokens" not in kwargs


class TestExtractReasoningContent:
    """Regression: some proxies send stream chunks with ``delta is None``."""

    def test_extract_reasoning_content_none_delta(self):
        assert extract_reasoning_content(None) is None

    def test_extract_reasoning_content_with_source_uses_model_extra(self):
        delta = SimpleNamespace(model_extra={"reasoning_content": "deep thought"})
        reasoning, source = extract_reasoning_content_with_source(delta)
        assert reasoning == "deep thought"
        assert source == "reasoning_content"

    def test_extract_reasoning_details_preserves_dicts(self):
        details = [{"type": "reasoning.summary", "text": "step", "signature": "sig"}]
        delta = SimpleNamespace(model_extra={"reasoning_details": details})
        assert extract_reasoning_details(delta) == details

    def test_build_reasoning_metadata_prefers_reasoning_details_field(self):
        metadata = build_reasoning_metadata(
            provider_id="openrouter",
            model_id="minimax-m2.7",
            reasoning_details=[{"type": "reasoning.summary", "text": "step"}],
        )
        assert metadata["reasoningField"] == "reasoning_details"
        assert metadata["providerID"] == "openrouter"


async def _stream_from_chunks(*chunks):
    for chunk in chunks:
        yield chunk


class TestOpenAIBaseProviderStreamingUsage:
    @staticmethod
    def _build_provider_with_stream():
        provider = MockProviderWithoutCatalog()
        create = AsyncMock()
        provider._client = MagicMock()
        provider._client.chat.completions.create = create
        return provider, create

    @staticmethod
    def _build_completion_preferring_provider_with_stream():
        provider = MockProviderPrefersCompletionTokens()
        create = AsyncMock()
        provider._client = MagicMock()
        provider._client.chat.completions.create = create
        return provider, create

    @pytest.mark.asyncio
    async def test_chat_stream_includes_usage_and_attaches_to_terminal_chunk(self):
        provider, create = self._build_provider_with_stream()

        content_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="hello", tool_calls=None),
                    finish_reason=None,
                )
            ],
            usage=None,
        )
        usage_chunk = SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        )
        finish_chunk = SimpleNamespace(
            choices=[SimpleNamespace(delta=None, finish_reason="stop")],
            usage=None,
        )
        create.return_value = _stream_from_chunks(content_chunk, usage_chunk, finish_chunk)

        from flocks.provider.provider import ChatMessage

        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                "kimi-k2.5",
                [ChatMessage(role="user", content="hello")],
            )
        ]

        kwargs = create.await_args.kwargs
        assert kwargs["stream_options"] == {"include_usage": True}
        assert chunks[-1].finish_reason == "stop"
        assert chunks[-1].usage == {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        }

    @pytest.mark.asyncio
    async def test_chat_stream_preserves_nested_reasoning_tokens(self):
        provider, create = self._build_provider_with_stream()

        content_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="hello", tool_calls=None),
                    finish_reason=None,
                )
            ],
            usage=None,
        )
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
        create.return_value = _stream_from_chunks(
            content_chunk, usage_chunk, finish_chunk
        )

        from flocks.provider.provider import ChatMessage

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

    @pytest.mark.asyncio
    async def test_chat_stream_emits_trailing_usage_when_usage_only_chunk_arrives_after_finish(self):
        provider, create = self._build_provider_with_stream()

        content_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="hello", tool_calls=None),
                    finish_reason=None,
                )
            ],
            usage=None,
        )
        finish_chunk = SimpleNamespace(
            choices=[SimpleNamespace(delta=None, finish_reason="stop")],
            usage=None,
        )
        usage_chunk = SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        )
        create.return_value = _stream_from_chunks(content_chunk, finish_chunk, usage_chunk)

        from flocks.provider.provider import ChatMessage

        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                "kimi-k2.5",
                [ChatMessage(role="user", content="hello")],
            )
        ]

        assert any(chunk.finish_reason == "stop" for chunk in chunks)
        assert chunks[-1].usage == {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        }
        assert chunks[-1].finish_reason is None

    @pytest.mark.asyncio
    async def test_chat_stream_retries_without_stream_options_when_unsupported(self):
        provider, create = self._build_provider_with_stream()

        content_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="hello", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8),
        )
        create.side_effect = [
            ValueError("unknown parameter: stream_options.include_usage"),
            _stream_from_chunks(content_chunk),
        ]

        from flocks.provider.provider import ChatMessage

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
            "completion_tokens": 3,
            "total_tokens": 8,
        }

    @pytest.mark.asyncio
    async def test_chat_stream_falls_back_to_max_tokens_when_completion_variant_is_rejected(self):
        provider, create = self._build_completion_preferring_provider_with_stream()

        create.side_effect = [
            ValueError("unsupported parameter: max_completion_tokens"),
            _stream_from_chunks(
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="hello", tool_calls=None),
                            finish_reason="stop",
                        )
                    ],
                    usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8),
                )
            ),
        ]

        from flocks.provider.provider import ChatMessage

        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                "gpt-5.4",
                [ChatMessage(role="user", content="hello")],
                max_tokens=20,
            )
        ]

        assert create.await_count == 2
        assert create.await_args_list[0].kwargs["max_completion_tokens"] == 20
        assert "max_tokens" not in create.await_args_list[0].kwargs
        assert create.await_args_list[1].kwargs["max_tokens"] == 20
        assert "max_completion_tokens" not in create.await_args_list[1].kwargs
        assert chunks[-1].usage == {
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "total_tokens": 8,
        }

    @pytest.mark.asyncio
    async def test_chat_stream_chains_completion_and_usage_fallbacks(self):
        provider, create = self._build_completion_preferring_provider_with_stream()

        create.side_effect = [
            ValueError("unsupported parameter: max_completion_tokens"),
            ValueError("unsupported parameter: include_usage"),
            _stream_from_chunks(
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content="hello", tool_calls=None),
                            finish_reason="stop",
                        )
                    ],
                    usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8),
                )
            ),
        ]

        from flocks.provider.provider import ChatMessage

        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                "gpt-5.4",
                [ChatMessage(role="user", content="hello")],
                max_tokens=20,
            )
        ]

        assert create.await_count == 3
        assert create.await_args_list[0].kwargs["max_completion_tokens"] == 20
        assert "stream_options" in create.await_args_list[0].kwargs
        assert create.await_args_list[1].kwargs["max_tokens"] == 20
        assert "stream_options" in create.await_args_list[1].kwargs
        assert create.await_args_list[2].kwargs["max_tokens"] == 20
        assert "stream_options" not in create.await_args_list[2].kwargs
        assert chunks[-1].usage == {
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "total_tokens": 8,
        }


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
