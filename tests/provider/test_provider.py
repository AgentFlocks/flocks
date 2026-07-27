"""
Tests for provider module
"""

import pytest
from types import SimpleNamespace
from flocks.provider.interleaved import (
    REASONING_TRANSPORT_ANTHROPIC_MESSAGES,
    REASONING_TRANSPORT_GENERIC_CHAT,
    resolve_interleaved_capability,
    resolve_reasoning_transport,
)
from flocks.provider.provider import (
    Provider,
    ChatMessage,
    ModelInfo,
    ProviderType,
)
from flocks.provider.model_catalog import get_provider_model_definitions


@pytest.mark.asyncio
async def test_provider_initialization():
    """Test provider system initialization"""
    await Provider.init()
    
    providers = Provider.list_providers()
    assert len(providers) > 0
    assert "anthropic" in providers
    assert "openai" in providers
    assert "google" in providers


@pytest.mark.asyncio
async def test_list_models():
    """Test listing all models"""
    await Provider.init()
    
    models = Provider.list_models()
    assert len(models) > 0
    
    # Check model structure
    model = models[0]
    assert isinstance(model, ModelInfo)
    assert model.id
    assert model.name
    assert model.provider_id


@pytest.mark.asyncio
async def test_list_models_by_provider():
    """Test listing models for specific provider"""
    await Provider.init()

    anthropic_models = get_provider_model_definitions("anthropic")
    assert len(anthropic_models) > 0
    assert all(m.provider_id == "anthropic" for m in anthropic_models)

    openai_models = get_provider_model_definitions("openai")
    assert len(openai_models) > 0
    assert all(m.provider_id == "openai" for m in openai_models)


@pytest.mark.asyncio
async def test_get_provider():
    """Test getting a provider by ID"""
    await Provider.init()
    
    anthropic = Provider.get("anthropic")
    assert anthropic is not None
    assert anthropic.id == "anthropic"
    assert anthropic.name == "Anthropic"
    
    unknown = Provider.get("unknown")
    assert unknown is None


@pytest.mark.asyncio
async def test_get_model():
    """Test getting a model by ID"""
    await Provider.init()

    # Test Anthropic model
    claude = next(
        (m for m in get_provider_model_definitions("anthropic") if m.id == "claude-sonnet-4-6"),
        None,
    )
    assert claude is not None
    assert claude.id == "claude-sonnet-4-6"
    assert claude.provider_id == "anthropic"
    assert claude.capabilities.supports_streaming
    assert claude.capabilities.supports_tools

    # Test OpenAI model
    gpt5 = next(
        (m for m in get_provider_model_definitions("openai") if m.id == "gpt-5.4"),
        None,
    )
    assert gpt5 is not None
    assert gpt5.provider_id == "openai"

    # Test unknown model
    unknown = next(
        (m for m in get_provider_model_definitions("openai") if m.id == "unknown-model"),
        None,
    )
    assert unknown is None


def test_resolve_model_prefers_provider_specific_runtime_model(monkeypatch):
    provider_model = SimpleNamespace(
        id="shared-model",
        capabilities=SimpleNamespace(interleaved={"field": "reasoning_content"}),
    )
    wrong_global_model = SimpleNamespace(
        id="shared-model",
        capabilities=SimpleNamespace(interleaved={"field": "reasoning_details"}),
    )
    fake_provider = SimpleNamespace(
        get_model_definitions=lambda: [provider_model],
        get_models=lambda: [],
        _config_models=[],
    )

    monkeypatch.setattr(Provider, "_initialized", True)
    monkeypatch.setattr(Provider, "_providers", {"deepseek": fake_provider})
    monkeypatch.setattr(Provider, "_models", {"shared-model": wrong_global_model})

    resolved = Provider.resolve_model("deepseek", "shared-model")

    assert resolved is provider_model
    assert resolved.capabilities.interleaved["field"] == "reasoning_content"


def test_dynamic_openai_compatible_provider_prefers_max_completion_tokens(monkeypatch):
    monkeypatch.setattr(Provider, "_providers", {})
    monkeypatch.setattr(Provider, "_models", {})

    monkeypatch.setattr(
        "flocks.config.config_writer.ConfigWriter.get_all_providers",
        lambda: {
            "custom-gpt5": {
                "name": "Custom GPT5",
                "npm": "@ai-sdk/openai-compatible",
                "options": {"baseURL": "https://example.test/v1"},
            }
        },
    )
    monkeypatch.setattr(
        "flocks.provider.credential.get_api_key",
        lambda _provider_id: "test-key",
    )

    Provider._load_dynamic_providers()

    provider = Provider._providers["custom-gpt5"]
    assert provider.PREFER_MAX_COMPLETION_TOKENS is True


def test_resolve_model_infers_interleaved_for_runtime_discovered_reasoning_model(monkeypatch):
    provider_model = SimpleNamespace(
        id="qwen3-max",
        capabilities=SimpleNamespace(interleaved=None),
    )
    fake_provider = SimpleNamespace(
        get_model_definitions=lambda: [provider_model],
        get_models=lambda: [],
        _config_models=[],
        _config=SimpleNamespace(base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"),
    )

    monkeypatch.setattr(Provider, "_initialized", True)
    monkeypatch.setattr(Provider, "_providers", {"openai-compatible": fake_provider})
    monkeypatch.setattr(Provider, "_models", {})

    resolved = Provider.resolve_model("openai-compatible", "qwen3-max")

    assert resolved is provider_model
    assert resolved.capabilities.interleaved == {
        "field": "reasoning_content",
        "echo": "tool_calls",
        "cross_provider_policy": "promote",
    }


def test_resolve_model_does_not_infer_interleaved_for_non_reasoning_model(monkeypatch):
    """A model whose id does NOT match any series token in
    ``infer_interleaved_capability`` and has no catalog ``interleaved`` should
    stay non-reasoning — the series-token inference must not fabricate a
    capability for it.

    Uses ``gpt-4-turbo`` as the model id: it is not in any of the
    ``_PROMOTE_REASONING_CONTENT_TOKENS`` / ``_STRICT_REASONING_CONTENT_TOKENS``
    lists, the provider id ``custom-demo`` does not match any of the
    special-case provider prefixes, and the base_url does not match any of
    the provider-hosting hints.
    """
    provider_model = SimpleNamespace(
        id="gpt-4-turbo",
        capabilities=SimpleNamespace(interleaved=None),
    )
    fake_provider = SimpleNamespace(
        get_model_definitions=lambda: [provider_model],
        get_models=lambda: [],
        _config_models=[],
        _config=SimpleNamespace(base_url="https://api.openai.com/v1"),
    )

    monkeypatch.setattr(Provider, "_initialized", True)
    monkeypatch.setattr(Provider, "_providers", {"custom-demo": fake_provider})
    monkeypatch.setattr(Provider, "_models", {})

    resolved = Provider.resolve_model("custom-demo", "gpt-4-turbo")

    assert resolved is provider_model
    assert resolved.capabilities.interleaved is None


@pytest.mark.parametrize(
    ("provider_id", "model_id", "base_url", "field"),
    [
        ("openai-compatible", "qwen3-235b-a22b-thinking", "https://api.example.com/v1", "reasoning_content"),
        ("openai-compatible", "kimi-k2-thinking-turbo", "https://api.example.com/v1", "reasoning_content"),
        ("openai-compatible", "kimi-k3", "https://api.example.com/v1", "reasoning_content"),
        ("openai-compatible", "deepseek-v4-pro", "https://api.deepseek.com/v1", "reasoning_content"),
        ("openai-compatible", "glm-4.7", "https://api.example.com/v1", "reasoning_content"),
        ("openai-compatible", "minimax-m3", "https://api.example.com/v1", "reasoning_details"),
        ("openai-compatible", "gemini-3.1-pro-preview", "https://api.example.com/v1", "reasoning_details"),
        ("openai-compatible", "step-3.5-flash", "https://api.example.com/v1", "reasoning_content"),
        ("google-vertex-anthropic", "claude-sonnet-4-6", "https://example.com", "thinking"),
    ],
)
def test_resolve_model_infers_all_hermes_interleaved_families(
    monkeypatch,
    provider_id,
    model_id,
    base_url,
    field,
):
    provider_model = SimpleNamespace(
        id=model_id,
        capabilities=SimpleNamespace(interleaved=None),
    )
    fake_provider = SimpleNamespace(
        get_model_definitions=lambda: [provider_model],
        get_models=lambda: [],
        _config_models=[],
        _config=SimpleNamespace(base_url=base_url),
    )

    monkeypatch.setattr(Provider, "_initialized", True)
    monkeypatch.setattr(Provider, "_providers", {provider_id: fake_provider})
    monkeypatch.setattr(Provider, "_models", {})

    resolved = Provider.resolve_model(provider_id, model_id)

    assert resolved is provider_model
    assert resolved.capabilities.interleaved["field"] == field


def test_resolve_interleaved_capability_prefers_explicit_metadata():
    resolved = resolve_interleaved_capability(
        provider_id="openai-compatible",
        model_id="claude-sonnet-4-6",
        explicit_capability={
            "field": "reasoning_details",
            "echo": "tool_calls",
            "cross_provider_policy": "promote",
        },
        base_url="https://api.example.com/v1",
    )

    assert resolved == {
        "field": "reasoning_details",
        "echo": "tool_calls",
        "cross_provider_policy": "promote",
    }


def test_resolve_reasoning_transport_is_independent_from_interleaved_field():
    assert resolve_reasoning_transport(
        provider_id="anthropic",
        model_id="claude-sonnet-4-6",
    ) == REASONING_TRANSPORT_ANTHROPIC_MESSAGES
    assert resolve_reasoning_transport(
        provider_id="openai-compatible",
        model_id="claude-sonnet-4-6",
        base_url="https://api.example.com/v1",
    ) == REASONING_TRANSPORT_GENERIC_CHAT


@pytest.mark.asyncio
async def test_provider_models():
    """Test provider model listing"""
    
    await Provider.init()
    
    models = get_provider_model_definitions("anthropic")
    
    assert len(models) > 0
    assert all(m.provider_id == "anthropic" for m in models)
    
    # Check that Claude models are present
    model_ids = [m.id for m in models]
    assert "claude-sonnet-4-6" in model_ids
    assert "claude-opus-4-6" in model_ids


@pytest.mark.asyncio
async def test_chat_message_creation():
    """Test creating chat messages"""
    message = ChatMessage(role="user", content="Hello")
    assert message.role == "user"
    assert message.content == "Hello"
    
    system_message = ChatMessage(role="system", content="You are a helpful assistant")
    assert system_message.role == "system"


@pytest.mark.asyncio
async def test_model_capabilities():
    """Test model capabilities"""
    await Provider.init()
    
    # Test Claude 3.5 Sonnet capabilities
    claude = next(
        (m for m in get_provider_model_definitions("anthropic") if m.id == "claude-sonnet-4-6"),
        None,
    )
    assert claude.capabilities.supports_streaming
    assert claude.capabilities.supports_tools
    assert claude.capabilities.supports_vision
    assert claude.limits.max_output_tokens == 1000000
    assert claude.limits.context_window == 1000000
    
    # Test GPT-5 capabilities
    gpt5 = next(
        (m for m in get_provider_model_definitions("openai") if m.id == "gpt-5.4"),
        None,
    )
    assert gpt5.capabilities.supports_streaming
    assert gpt5.capabilities.supports_tools
    assert gpt5.limits.max_output_tokens == 1050000


# Note: Actual API call tests require API keys and should be run separately
# These are marked as integration tests

@pytest.mark.integration
@pytest.mark.requires_anthropic_key
@pytest.mark.asyncio
async def test_anthropic_chat_actual():
    """Test actual Anthropic API call (requires API key)"""
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    
    await Provider.init()
    
    messages = [
        ChatMessage(role="user", content="Say 'Hello World' and nothing else")
    ]
    
    try:
        response = await Provider.chat(
            model_id="claude-3-haiku-20240307",
            messages=messages,
            max_tokens=100,
        )
    except Exception as e:
        if "429" in str(e) or "rate" in str(e).lower() or "RateLimitError" in type(e).__name__:
            pytest.skip(f"Rate limited: {e}")
        raise
    
    assert response.content
    assert "hello" in response.content.lower()
    assert response.usage["total_tokens"] > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_chat_actual():
    """Test actual OpenAI API call (requires API key)"""
    import os
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    
    await Provider.init()
    
    messages = [
        ChatMessage(role="user", content="Say 'Hello World' and nothing else")
    ]
    
    response = await Provider.chat(
        model_id="gpt-3.5-turbo",
        messages=messages,
        max_tokens=100,
    )
    
    assert response.content
    assert "hello" in response.content.lower()
    assert response.usage["total_tokens"] > 0
