from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.agent.agent import AgentInfo
from flocks.config.config import Config, ConfigInfo
from flocks.hooks.pipeline import HookPipeline
from flocks.provider.provider import ChatMessage, StreamChunk
from flocks.session.message import Message, MessageRole
from flocks.session.runner import SessionRunner
from flocks.session.session import Session


class _ProviderStub:
    async def chat_stream(self, **kwargs):  # noqa: ANN003
        del kwargs
        yield StreamChunk(
            delta="hook payload ok",
            finish_reason="stop",
            usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        )


@pytest.mark.asyncio
async def test_call_llm_trims_duplicate_hook_payload_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    session = await Session.create(project_id="test_project_hook_payloads", directory="/test/hooks")
    user_msg = await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="hello",
    )
    assistant_msg = await Message.create(
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        content="",
        parentID=user_msg.id,
        modelID="test-model",
        providerID="test-provider",
        agent="rex",
    )

    runner = SessionRunner(
        session=session,
        provider_id="test-provider",
        model_id="test-model",
        agent_name="rex",
    )
    runner._step = 3

    captured_before: list[dict] = []
    captured_after: list[dict] = []

    async def fake_run_llm_before(input_data, output_data=None):  # noqa: ANN001, ANN202
        del output_data
        captured_before.append(input_data)
        return SimpleNamespace(input=input_data, output={})

    async def fake_run_llm_after(input_data, output_data=None):  # noqa: ANN001, ANN202
        captured_after.append({"input": input_data, "output": output_data or {}})
        return SimpleNamespace(input=input_data, output=output_data or {})

    monkeypatch.setattr(HookPipeline, "has_stage_handlers", AsyncMock(return_value=True))
    monkeypatch.setattr(HookPipeline, "run_llm_before", fake_run_llm_before)
    monkeypatch.setattr(HookPipeline, "run_llm_after", fake_run_llm_after)
    monkeypatch.setattr(Config, "get", AsyncMock(return_value=ConfigInfo()))

    result = await runner._call_llm(
        provider=_ProviderStub(),
        messages=[
            ChatMessage(role="system", content="system prompt"),
            ChatMessage(role="user", content="user prompt"),
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file",
                    "parameters": {"type": "object"},
                },
            }
        ],
        agent=AgentInfo(name="rex"),
        assistant_msg=assistant_msg,
    )

    assert result.action == "stop"
    assert result.content == "hook payload ok"

    assert len(captured_before) == 1
    before_input = captured_before[0]
    assert set(before_input) == {
        "sessionID",
        "messageID",
        "workspace",
        "agent",
        "step",
        "model",
        "request",
    }
    assert "messageSummaries" not in before_input["request"]
    assert "toolSummaries" not in before_input["request"]
    assert before_input["request"]["messageCount"] == 2
    assert before_input["request"]["toolCount"] == 1

    assert len(captured_after) == 1
    after_input = captured_after[0]["input"]
    assert set(after_input) == {
        "sessionID",
        "messageID",
        "workspace",
        "agent",
        "step",
        "model",
    }
    assert "request" not in after_input
