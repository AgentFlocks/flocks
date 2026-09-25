"""Completed results are reusable only for identical work in one user turn."""

from types import SimpleNamespace
import importlib
from unittest.mock import AsyncMock

import pytest

from flocks.session.message import Message, MessageRole, ToolPart, ToolStateCompleted
from flocks.tool.agent.delegate_task import _find_completed_delegate


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "new-user-turn", "prompt", "model", "load_skills"])
async def test_delegate_reuse_requires_same_turn_and_full_request(monkeypatch, change):
    request = {"subagent_type": "reviewer", "description": "Review code", "prompt": "Review revision A",
               "model": "demo/model-a", "load_skills": ["review"]}
    part = ToolPart(sessionID="parent", messageID="old-assistant", callID="old-call", tool="delegate_task",
                    state=ToolStateCompleted(input=dict(request), output="result A", title="Review code",
                                             metadata={}, time={}))
    messages = [SimpleNamespace(id="user-a", role=MessageRole.USER),
                SimpleNamespace(id="old-assistant", role=MessageRole.ASSISTANT)]
    if change == "new-user-turn":
        messages.append(SimpleNamespace(id="user-b", role=MessageRole.USER))
    elif change == "prompt":
        request["prompt"] = "Review revision B"
    elif change == "model":
        request["model"] = "demo/model-b"
    elif change == "load_skills":
        request["load_skills"] = ["other-review"]
    messages.append(SimpleNamespace(id="current", role=MessageRole.ASSISTANT))
    monkeypatch.setattr(Message, "list", AsyncMock(return_value=messages))
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=[part]))
    result = await _find_completed_delegate(
        "parent", "current", request["subagent_type"], request["description"],
        prompt=request["prompt"], model=request["model"], load_skills=request["load_skills"],
    )
    if change is None:
        assert result is not None and "result A" in result.output
    else:
        assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("model, expected", [
    ("demo/new-model", ("demo", "new-model")),
    ("new-model", (None, "new-model")),
    (None, (None, None)),
])
async def test_continuing_delegate_forwards_explicit_model(monkeypatch, model, expected):
    from flocks.tool.registry import ToolContext

    delegate = importlib.import_module("flocks.tool.agent.delegate_task")
    session = SimpleNamespace(id="child", agent="reviewer", directory="/workspace/project")
    loop_run = AsyncMock(return_value=SimpleNamespace(action="stop", last_message=None))
    monkeypatch.setattr(delegate.Session, "get_by_id", AsyncMock(return_value=session))
    monkeypatch.setattr(delegate.Message, "create", AsyncMock())
    monkeypatch.setattr(delegate.SessionLoop, "run", loop_run)
    result = await delegate.delegate_task_tool(
        ToolContext(session_id="parent", message_id="message", agent="rex"),
        session_id="child", prompt="Continue the review", model=model,
    )
    assert result.success
    assert loop_run.await_args.args == ("child",)
    assert (loop_run.await_args.kwargs["provider_id"], loop_run.await_args.kwargs["model_id"]) == expected
