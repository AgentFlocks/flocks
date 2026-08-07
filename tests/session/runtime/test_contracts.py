"""Tests for replay-safe runtime request contracts."""

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
    first_tools = request.provider_tools()
    first_tools[0]["function"]["name"] = "mutated"

    assert request.provider_tools()[0]["function"]["name"] == "read"
    assert request.provider_options()["reasoning"]["effort"] == "high"
