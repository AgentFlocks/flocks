"""
Tests for provider module
"""

import pytest
from types import SimpleNamespace
from flocks.provider.provider import (
    Provider,
    ChatMessage,
    ModelInfo,
    ProviderType,
)


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
    
    anthropic_models = Provider.list_models(provider_id="anthropic")
    assert len(anthropic_models) > 0
    assert all(m.provider_id == "anthropic" for m in anthropic_models)
    
    openai_models = Provider.list_models(provider_id="openai")
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
    claude = Provider.get_model("claude-3-5-sonnet-20241022")
    assert claude is not None
    assert claude.id == "claude-3-5-sonnet-20241022"
    assert claude.provider_id == "anthropic"
    assert claude.capabilities.supports_streaming
    assert claude.capabilities.supports_tools
    
    # Test OpenAI model
    gpt4 = Provider.get_model("gpt-4-turbo-preview")
    assert gpt4 is not None
    assert gpt4.provider_id == "openai"
    
    # Test unknown model
    unknown = Provider.get_model("unknown-model")
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
    provider_model = SimpleNamespace(
        id="deepseek-chat",
        capabilities=SimpleNamespace(interleaved=None),
    )
    fake_provider = SimpleNamespace(
        get_model_definitions=lambda: [provider_model],
        get_models=lambda: [],
        _config_models=[],
        _config=SimpleNamespace(base_url="https://api.deepseek.com/v1"),
    )

    monkeypatch.setattr(Provider, "_initialized", True)
    monkeypatch.setattr(Provider, "_providers", {"custom-demo": fake_provider})
    monkeypatch.setattr(Provider, "_models", {})

    resolved = Provider.resolve_model("custom-demo", "deepseek-chat")

    assert resolved is provider_model
    assert resolved.capabilities.interleaved is None


@pytest.mark.asyncio
async def test_provider_models():
    """Test provider model listing"""
    
    await Provider.init()
    
    anthropic = Provider.get("anthropic")
    models = anthropic.get_models()
    
    assert len(models) > 0
    assert all(isinstance(m, ModelInfo) for m in models)
    assert all(m.provider_id == "anthropic" for m in models)
    
    # Check that Claude models are present
    model_ids = [m.id for m in models]
    assert "claude-3-5-sonnet-20241022" in model_ids
    assert "claude-3-opus-20240229" in model_ids


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
    claude = Provider.get_model("claude-3-5-sonnet-20241022")
    assert claude.capabilities.supports_streaming
    assert claude.capabilities.supports_tools
    assert claude.capabilities.supports_vision
    assert claude.capabilities.max_tokens == 8192
    assert claude.capabilities.context_window == 200000
    
    # Test GPT-4 capabilities (may come from OpenAI or Gateway provider)
    gpt4 = Provider.get_model("gpt-4")
    assert gpt4.capabilities.supports_streaming
    assert gpt4.capabilities.supports_tools
    # max_tokens varies by provider (8192 for OpenAI, 4096 for Gateway)
    assert gpt4.capabilities.max_tokens in [4096, 8192]


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
