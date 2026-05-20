from flocks.provider.provider import ChatMessage
from flocks.provider.reasoning_replay import prepare_reasoning_for_replay
from flocks.provider.sdk.openai_base import format_openai_messages


def test_prepare_reasoning_promotes_internal_reasoning_for_promote_policy():
    message = ChatMessage(
        role="assistant",
        content="",
        reasoning="Need to inspect the tool result.",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": "{}"},
            }
        ],
    )

    prepared = prepare_reasoning_for_replay(
        provider_id="alibaba",
        model_id="qwen3-max",
        message=message,
        interleaved={
            "field": "reasoning_content",
            "echo": "tool_calls",
            "cross_provider_policy": "promote",
        },
    )

    assert prepared.reasoning_content == "Need to inspect the tool result."
    assert prepared.reasoning_source == "promoted_reasoning"


def test_prepare_reasoning_uses_placeholder_for_strict_echo_provider():
    message = ChatMessage(
        role="assistant",
        content="",
        reasoning="Prior provider chain of thought",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": "{}"},
            }
        ],
    )

    prepared = prepare_reasoning_for_replay(
        provider_id="deepseek",
        model_id="deepseek-reasoner",
        message=message,
        interleaved={
            "field": "reasoning_content",
            "echo": "tool_calls",
            "placeholder": " ",
            "cross_provider_policy": "placeholder",
        },
    )

    assert prepared.reasoning_content == " "
    assert prepared.reasoning_source == "placeholder"


def test_prepare_reasoning_preserves_reasoning_details():
    details = [{"type": "reasoning.summary", "text": "step", "signature": "sig"}]
    message = ChatMessage(
        role="assistant",
        content="answer",
        reasoning_details=details,
    )

    prepared = prepare_reasoning_for_replay(
        provider_id="minimax",
        model_id="minimax-m2.7",
        message=message,
        interleaved={
            "field": "reasoning_details",
            "echo": "tool_calls",
            "cross_provider_policy": "promote",
        },
    )

    assert prepared.reasoning_details == details
    assert prepared.reasoning_source == "native_reasoning_details"


def test_prepare_reasoning_drops_details_when_target_uses_reasoning_content():
    message = ChatMessage(
        role="assistant",
        content="",
        reasoning="Short internal summary",
        reasoning_details=[{"type": "thinking", "thinking": "opaque provider scratchpad"}],
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": "{}"},
            }
        ],
    )

    prepared = prepare_reasoning_for_replay(
        provider_id="deepseek",
        model_id="deepseek-reasoner",
        message=message,
        interleaved={
            "field": "reasoning_content",
            "echo": "tool_calls",
            "placeholder": " ",
            "cross_provider_policy": "placeholder",
        },
    )

    assert prepared.reasoning_content == " "
    assert prepared.reasoning_details is None
    assert prepared.reasoning_source == "placeholder"


def test_prepare_reasoning_promotes_reasoning_content_into_reasoning_details():
    message = ChatMessage(
        role="assistant",
        content="",
        reasoning_content="Native scratchpad",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": "{}"},
            }
        ],
    )

    prepared = prepare_reasoning_for_replay(
        provider_id="minimax",
        model_id="minimax-m2.7",
        message=message,
        interleaved={
            "field": "reasoning_details",
            "echo": "tool_calls",
            "cross_provider_policy": "promote",
        },
    )

    assert prepared.reasoning_content is None
    assert prepared.reasoning_details == [
        {"type": "reasoning.summary", "text": "Native scratchpad"}
    ]
    assert prepared.reasoning_source == "promoted_reasoning_content"


def test_format_openai_messages_prefers_reasoning_details_over_reasoning_content():
    formatted = format_openai_messages(
        [
            ChatMessage(
                role="assistant",
                content="",
                reasoning="summary",
                reasoning_content="native scratchpad",
                reasoning_details=[{"type": "reasoning.summary", "text": "step"}],
            )
        ]
    )

    assert formatted[0]["reasoning_details"] == [{"type": "reasoning.summary", "text": "step"}]
    assert "reasoning_content" not in formatted[0]


def test_format_openai_messages_serializes_reasoning_content_without_include_reasoning():
    formatted = format_openai_messages(
        [
            ChatMessage(
                role="assistant",
                content="",
                reasoning_content="native scratchpad",
            )
        ]
    )

    assert formatted[0]["reasoning_content"] == "native scratchpad"
