from types import SimpleNamespace

import pytest

from flocks.provider.provider import ChatMessage
from flocks.provider.provider import ModelCapabilities, ModelInfo
from flocks.provider.sdk.azure import AzureProvider


class _FakeAzureStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


class _FakeAzureCompletions:
    def __init__(self, chunks):
        self._chunks = chunks
        self.last_request = None

    async def create(self, **kwargs):
        self.last_request = kwargs
        return _FakeAzureStream(self._chunks)


class _FakeAzureClient:
    def __init__(self, chunks):
        self.completions = _FakeAzureCompletions(chunks)
        self.chat = SimpleNamespace(completions=self.completions)


def _chunk(delta=None, finish_reason=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=delta,
                finish_reason=finish_reason,
            )
        ]
    )


def _tool_call_delta(index=0, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_azure_provider_returns_configured_deployment_models():
    provider = AzureProvider()
    provider._config_models = [
        ModelInfo(
            id="customer-prod-deployment",
            name="Customer Production Deployment",
            provider_id="azure",
            capabilities=ModelCapabilities(
                supports_tools=True,
                supports_streaming=True,
                context_window=128000,
                max_tokens=4096,
            ),
        )
    ]

    models = provider.get_models()

    assert [m.id for m in models] == ["customer-prod-deployment"]
    assert models[0].name == "Customer Production Deployment"


def test_azure_provider_returns_fallback_models_without_config():
    provider = AzureProvider()

    models = provider.get_models()

    assert {m.id for m in models} == {"gpt-5.4", "gpt-5-mini"}
    assert all(m.provider_id == "azure" for m in models)


@pytest.mark.asyncio
async def test_azure_chat_stream_emits_tool_calls():
    chunks = [
        _chunk(
            delta=SimpleNamespace(
                content=None,
                tool_calls=[
                    _tool_call_delta(
                        index=0,
                        call_id="call_1",
                        name="delegate_task",
                        arguments='{"subagent_type":"explore",',
                    )
                ],
            )
        ),
        _chunk(
            delta=SimpleNamespace(
                content=None,
                tool_calls=[
                    _tool_call_delta(
                        index=0,
                        arguments='"prompt":"say ok"}',
                    )
                ],
            )
        ),
        _chunk(
            delta=SimpleNamespace(content=None, tool_calls=None),
            finish_reason="tool_calls",
        ),
    ]
    client = _FakeAzureClient(chunks)
    provider = AzureProvider()
    provider._get_client = lambda: client

    emitted = [
        chunk
        async for chunk in provider.chat_stream(
            model_id="gpt-5.4-mini",
            messages=[ChatMessage(role="user", content="call a sub agent")],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "delegate_task",
                        "description": "delegate work",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
    ]

    assert client.completions.last_request["tools"]
    assert len(emitted) == 1
    assert emitted[0].finish_reason == "tool_calls"
    assert emitted[0].tool_calls == [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "delegate_task",
                "arguments": '{"subagent_type":"explore","prompt":"say ok"}',
            },
        }
    ]


@pytest.mark.asyncio
async def test_azure_chat_stream_still_emits_text_chunks():
    client = _FakeAzureClient([
        _chunk(delta=SimpleNamespace(content="hello", tool_calls=None)),
        _chunk(
            delta=SimpleNamespace(content=None, tool_calls=None),
            finish_reason="stop",
        ),
    ])
    provider = AzureProvider()
    provider._get_client = lambda: client

    emitted = [
        chunk
        async for chunk in provider.chat_stream(
            model_id="gpt-5.4-mini",
            messages=[ChatMessage(role="user", content="hi")],
        )
    ]

    assert [chunk.delta for chunk in emitted] == ["hello", ""]
    assert emitted[-1].finish_reason == "stop"
