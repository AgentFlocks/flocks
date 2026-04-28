from flocks.provider.provider import ChatMessage
from flocks.session.runner import SessionRunner, ToolCall


def test_build_langfuse_request_payload_keeps_full_messages_and_system_prompt() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "parameters": {"type": "object"},
            },
        }
    ]
    messages = [
        ChatMessage(role="system", content="system prompt"),
        ChatMessage(role="user", content="user question"),
        ChatMessage(
            role="assistant",
            content="calling tool",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": "{\"path\":\"/tmp/demo.txt\"}",
                    },
                }
            ],
        ),
    ]

    payload = SessionRunner._build_langfuse_request_payload(
        step=3,
        messages=messages,
        request_tools=tools,
        available_tools=tools,
        provider_options={"temperature": 0.1, "max_tokens": 1024},
    )

    assert payload["step"] == 3
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"] == "system prompt"
    assert payload["messages"][1]["content"] == "user question"
    assert payload["messages"][2]["tool_calls"][0]["function"]["arguments"] == "{\"path\":\"/tmp/demo.txt\"}"
    assert payload["request_tools"] == tools
    assert payload["available_tools"] == tools
    assert payload["provider_options"] == {"temperature": 0.1, "max_tokens": 1024}


def test_build_langfuse_response_payload_keeps_full_content_reasoning_and_tool_arguments() -> None:
    full_content = "assistant output " * 80
    full_reasoning = "reasoning output " * 60
    tool_calls = [
        ToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "/tmp/demo.txt", "offset": 1, "limit": 1000},
        )
    ]

    payload = SessionRunner._build_langfuse_response_payload(
        action="continue",
        content=full_content,
        reasoning=full_reasoning,
        finish_reason="tool_calls",
        tool_calls=tool_calls,
    )

    assert payload["action"] == "continue"
    assert payload["content"] == full_content
    assert payload["reasoning"] == full_reasoning
    assert payload["finish_reason"] == "tool_calls"
    assert payload["tool_calls"] == [
        {
            "id": "call_1",
            "name": "read_file",
            "arguments": {"path": "/tmp/demo.txt", "offset": 1, "limit": 1000},
        }
    ]
