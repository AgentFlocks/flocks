from flocks.provider.provider import ChatMessage
from flocks.provider.sdk.deepseek import DeepSeekProvider


def test_deepseek_formats_assistant_reasoning_content_for_replay():
    formatted = DeepSeekProvider._format_messages(
        [
            ChatMessage(
                role="assistant",
                content="",
                reasoning="Need to inspect the tool result.",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": "{}",
                        },
                    }
                ],
            ),
            ChatMessage(
                role="tool",
                content="Cloudy 7~13C",
                tool_call_id="call_1",
                name="get_weather",
            ),
        ]
    )

    assert formatted[0]["role"] == "assistant"
    assert formatted[0]["reasoning_content"] == "Need to inspect the tool result."
    assert formatted[0]["tool_calls"][0]["function"]["name"] == "get_weather"
    assert "reasoning_content" not in formatted[1]
