"""Tests for replay-safe runtime request contracts."""

from flocks.provider.provider import ChatMessage
from flocks.session.runtime.contracts import (
    ModelRequest,
)


def test_model_request_freezes_and_isolates_provider_payloads() -> None:
    message = {"role": "user", "content": ["hello"]}
    tool = {"type": "function", "function": {"name": "read"}}
    options = {"reasoning": {"effort": "high"}}
    request = ModelRequest(
        provider_id="provider",
        model_id="model",
        messages=(message,),
        tools=(tool,),
        options=options,
    )

    tool["function"]["name"] = "write"
    options["reasoning"]["effort"] = "low"
    message["content"].append("mutated source")
    first_messages = request.provider_messages()
    first_messages[0]["content"].append("mutated provider view")
    first_tools = request.provider_tools()
    first_tools[0]["function"]["name"] = "mutated"

    assert request.provider_messages()[0]["content"] == ["hello"]
    assert request.provider_tools()[0]["function"]["name"] == "read"
    assert request.provider_options()["reasoning"]["effort"] == "high"


def test_model_request_reuses_owned_chat_messages_for_provider_calls() -> None:
    """Provider calls get a fresh list without copying the full history."""
    message = ChatMessage(
        role="user",
        content=[{"type": "text", "text": "large history entry"}],
    )
    request = ModelRequest(
        provider_id="provider",
        model_id="model",
        messages=(message,),
        tools=(),
        options={},
    )

    first_messages = request.provider_messages()
    second_messages = request.provider_messages()

    assert first_messages is not second_messages
    assert request.messages[0] is message
    assert first_messages[0] is message
    assert second_messages[0] is message
