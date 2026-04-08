import pytest

from flocks.agent.agent import AgentInfo
from flocks.agent.registry import Agent
from flocks.provider.provider import ChatMessage, Provider
from flocks.session.message import Message, MessageRole, ToolPart, ToolStateCompleted
from flocks.session.runner import SessionRunner, StepResult
from flocks.session.session import Session
from flocks.utils.id import Identifier


@pytest.mark.asyncio
async def test_runner_does_not_disable_tools_after_tool_only_assistant_message(monkeypatch):
    """
    Regression test.

    When the last assistant message contains only tool results (e.g. `question`)
    and no non-empty text, the runner must NOT disable tools. Otherwise, multi-step
    flows that require follow-up tool calls (like generating workflow.json after
    user confirms) can get stuck.
    """

    session = await Session.create(project_id="test_project_tool_only", directory="/test/dir")

    user_1 = await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="start",
    )

    assistant = await Message.create(
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        content="",  # Empty text part -> has_text == False in runner
        parentID=user_1.id,
        modelID="test-model",
        providerID="test-provider",
        agent="rex",
    )

    # Add a completed tool result part (simulates AskQuestion completion)
    tool_part = ToolPart(
        id=Identifier.ascending("part"),
        sessionID=session.id,
        messageID=assistant.id,
        callID="call_question_1",
        tool="question",
        state=ToolStateCompleted(
            input={"questions": [{"question": "continue?", "options": ["yes", "no"]}]},
            output="User has answered your questions: ...",
            title="Asked 1 question",
            metadata={"answers": [["yes"]]},
            time={"start": 0, "end": 1},
        ),
    )
    await Message.store_part(session.id, assistant.id, tool_part)

    user_2 = await Message.create(
        session_id=session.id,
        role=MessageRole.USER,
        content="确认并生成 JSON",
    )

    messages = [user_1, assistant, user_2]

    class DummyProvider:
        def is_configured(self) -> bool:
            return True

    async def fake_apply_config(*args, **kwargs) -> None:
        return None

    async def fake_agent_get(name: str):
        return AgentInfo(name=name)

    sentinel_tools = [{"type": "function", "function": {"name": "write", "description": "", "parameters": {}}}]
    captured = {}

    async def fake_build_system_prompts(self, agent):  # noqa: ANN001
        return []

    async def fake_build_callable_tool_schema(self, agent, messages=None):  # noqa: ANN001
        del agent, messages
        return list(sentinel_tools)

    async def fake_to_chat_messages(self, _messages, _system_prompts):  # noqa: ANN001
        return [ChatMessage(role="user", content="test")]

    async def fake_call_llm(self, provider, messages, tools, agent, assistant_msg):  # noqa: ANN001
        captured["tools"] = tools
        return StepResult(action="stop", content="ok")

    monkeypatch.setattr(Provider, "get", lambda _provider_id: DummyProvider())
    monkeypatch.setattr(Provider, "apply_config", fake_apply_config)
    monkeypatch.setattr(Agent, "get", fake_agent_get)
    monkeypatch.setattr(SessionRunner, "_build_system_prompts", fake_build_system_prompts)
    monkeypatch.setattr(SessionRunner, "_build_callable_tool_schema", fake_build_callable_tool_schema)
    monkeypatch.setattr(SessionRunner, "_to_chat_messages", fake_to_chat_messages)
    monkeypatch.setattr(SessionRunner, "_call_llm", fake_call_llm)

    runner = SessionRunner(session=session, provider_id="test-provider", model_id="test-model", agent_name="rex")
    runner._step = 2  # ensure reminder wrapping branch doesn't break assumptions

    result = await runner._process_step(messages=messages, last_user=user_2)
    assert result.action == "stop"

    # Critical assertion: tools must remain available (not cleared to []).
    assert captured["tools"] == sentinel_tools

