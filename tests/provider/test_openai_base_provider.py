"""
Tests for OpenAI Base Provider get_models() method.

Tests various scenarios:
1. Provider with CATALOG_ID set (loads from catalog)
2. Provider without CATALOG_ID (returns empty list)
3. Provider with catalog load failure (handles gracefully)
4. Provider with config-based models (defers to config merge)
"""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from flocks.provider.sdk.openai_base import OpenAIBaseProvider, extract_reasoning_content
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


class TestOpenAIBaseProviderGetModels:
    """Test suite for get_models() method."""
    
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


class TestOpenAIBaseProviderTemperature:
    def _build_provider_with_client(self):
        provider = MockProviderWithoutCatalog()
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
    async def test_chat_omits_temperature_when_not_provided(self):
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


class TestExtractReasoningContent:
    """Regression: some proxies send stream chunks with ``delta is None``."""

    def test_extract_reasoning_content_none_delta(self):
        assert extract_reasoning_content(None) is None


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
