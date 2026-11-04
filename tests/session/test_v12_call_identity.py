"""Every model and tool call of a session names the same caller, and every
model call outside the chat runner carries its own model call id.

An extension that scopes per-call state to the caller (a redaction mapping
minted for the request, used again on the response and on the tool call the
response produces) needs both, from every place a model is called -- the chat
runner, titles, compaction summaries, workflow nodes and memory extraction.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

import flocks.session.runner as runner_mod
from flocks.auth.context import API_TOKEN_SERVICE_USER_ID, AuthUser
from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.provider.provider import ChatMessage
from flocks.session.caller import (
    SYSTEM_CALLER_ID,
    caller_for_user,
    caller_from_profile,
    session_caller,
)
from flocks.session.lifecycle.compaction.summary import _llm_chat_with_timeout
from flocks.session.llm_hook_utils import model_call_hook_metadata, new_model_call_id
from flocks.session.session import SessionInfo
from flocks.session.tool_execution import build_session_tool_execution_payload
from flocks.transform import register_response_transform, reset_response_transform


@pytest.fixture
def hook_pipeline(monkeypatch: pytest.MonkeyPatch):
    HookPipeline.reset()
    monkeypatch.setattr(
        HookPipeline,
        "ensure_initialized",
        AsyncMock(side_effect=lambda *args, **kwargs: tuple(HookPipeline._hooks)),
    )
    yield HookPipeline
    HookPipeline.reset()


@pytest.fixture(autouse=True)
def _clean_transform():
    reset_response_transform()
    yield
    reset_response_transform()


@pytest.fixture
def owned_session(monkeypatch: pytest.MonkeyPatch):
    """Every session reads as owned by user ``u-owner``."""

    async def _profile(session_id: str):
        return {"owner_user_id": "u-owner", "entry": "webui"}

    monkeypatch.setattr("flocks.session.execution_profile.get_session_execution_profile", _profile)
    monkeypatch.setattr("flocks.session.tool_execution.get_session_execution_profile", _profile)


# ---------------------------------------------------------------------------
# The caller value
# ---------------------------------------------------------------------------

def test_caller_names_the_profile_subject_with_its_type() -> None:
    assert caller_from_profile({"owner_user_id": "u1"}).id == "human:u1"
    assert caller_from_profile({"subject_id": "bot-7", "subject_type": "service"}).id == "service:bot-7"
    assert caller_from_profile({"owner_user_id": API_TOKEN_SERVICE_USER_ID}).id == "service:api-token-service"
    assert caller_from_profile({}).id == SYSTEM_CALLER_ID
    assert caller_from_profile(None).id == SYSTEM_CALLER_ID


def test_caller_for_a_request_user() -> None:
    human = AuthUser(id="u9", username="ann", role="member")
    token = AuthUser(id=API_TOKEN_SERVICE_USER_ID, username=API_TOKEN_SERVICE_USER_ID, role="admin")  # secret-guard: allow (a test user / no key, not a secret)
    assert caller_for_user(human).as_metadata() == {"callerID": "human:u9", "callerType": "human"}
    assert caller_for_user(token).id == "service:api-token-service"
    assert caller_for_user(None).id == SYSTEM_CALLER_ID


@pytest.mark.asyncio
async def test_unreadable_session_is_the_system_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(session_id: str):
        raise RuntimeError("database down")

    monkeypatch.setattr("flocks.session.execution_profile.get_session_execution_profile", _boom)
    assert (await session_caller("ses-x")).id == SYSTEM_CALLER_ID
    assert (await session_caller(None)).id == SYSTEM_CALLER_ID


@pytest.mark.asyncio
async def test_tool_payload_and_model_hooks_name_the_same_caller(owned_session) -> None:
    payload = await build_session_tool_execution_payload(
        session_id="ses-1",
        message_id="msg-1",
        agent="rex",
        tool_name="bash",
        tool_input={"command": "ls"},
        tool_schema={"type": "object"},
    )
    metadata = await model_call_hook_metadata(
        session_id="ses-1", agent="rex", purpose="title_generation", provider_id="p", model_id="m",
    )

    assert payload["callerID"] == metadata["callerID"] == "human:u-owner"
    assert payload["callerType"] == metadata["callerType"] == "human"
    # The caller rides beside the contract, which keeps its own actor record.
    assert payload["tool_execution"]["actor"]["subject"]["id"] == "u-owner"


def test_model_call_ids_are_unique_and_say_what_they_are() -> None:
    first, second = new_model_call_id("memory_extraction"), new_model_call_id("memory_extraction")
    assert first != second and first.startswith("memory_extraction:")
    assert new_model_call_id("").startswith("call:")


# ---------------------------------------------------------------------------
# Call sites
# ---------------------------------------------------------------------------

class _CaptureBefore(HookBase):
    def __init__(self) -> None:
        self.inputs: List[Dict[str, Any]] = []

    async def llm_before(self, ctx) -> None:
        self.inputs.append(json.loads(json.dumps(ctx.input, default=str)))


@pytest.mark.asyncio
async def test_compaction_calls_carry_caller_and_a_fresh_call_id(hook_pipeline, owned_session) -> None:
    capture = _CaptureBefore()
    hook_pipeline.register("capture", capture, order=1)
    client = SimpleNamespace(
        provider_id="p",
        chat=AsyncMock(return_value=SimpleNamespace(content="summary")),
    )
    messages = [ChatMessage(role="user", content="summarise this")]

    await _llm_chat_with_timeout(client, "m", list(messages), 100, session_id="ses-1")
    await _llm_chat_with_timeout(client, "m", list(messages), 100, session_id="ses-1")

    ids = [entry["model_call_id"] for entry in capture.inputs]
    assert len(set(ids)) == 2 and all(i.startswith("compaction_summary:") for i in ids)
    assert {entry["callerID"] for entry in capture.inputs} == {"human:u-owner"}


@pytest.mark.asyncio
async def test_title_call_carries_caller_and_call_id(hook_pipeline, owned_session, monkeypatch) -> None:
    from flocks.session.lifecycle.title import SessionTitle

    capture = _CaptureBefore()
    hook_pipeline.register("capture", capture, order=1)

    class _Provider:
        async def chat_stream(self, model_id, messages, **kwargs):
            yield SimpleNamespace(delta="Rotating keys")

    monkeypatch.setattr(
        "flocks.session.session.Session.get_by_id",
        AsyncMock(return_value=SimpleNamespace(id="ses-1", project_id="proj", title="New session")),
    )
    monkeypatch.setattr("flocks.session.session.Session.update", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "flocks.session.message.Message.list",
        AsyncMock(return_value=[SimpleNamespace(role="user", id="m1")]),
    )
    monkeypatch.setattr(
        "flocks.session.message.Message.parts",
        AsyncMock(return_value=[SimpleNamespace(type="text", text="how do I rotate keys?")]),
    )
    monkeypatch.setattr("flocks.provider.provider.Provider._ensure_initialized", lambda: None)
    monkeypatch.setattr("flocks.provider.provider.Provider.get", lambda provider_id: _Provider())

    title = await SessionTitle.generate_title_after_first_message(session_id="ses-1", model_id="m", provider_id="p")

    assert title == "Rotating keys"
    assert len(capture.inputs) == 1
    entry = capture.inputs[0]
    assert entry["purpose"] == "title_generation"
    assert entry["model_call_id"].startswith("title_generation:")
    assert entry["callerID"] == "human:u-owner"


@pytest.mark.asyncio
async def test_chat_runner_hooks_name_the_session_caller(monkeypatch: pytest.MonkeyPatch, owned_session) -> None:
    from tests.session.test_runner_llm_hooks import _FakeProcessor, _FakeToolAccumulator

    runner = runner_mod.SessionRunner(
        session=SessionInfo.model_construct(
            id="ses-runner-caller", slug="t", project_id="p", directory="/tmp", title="t"
        ),
        provider_id="anthropic",
        model_id="claude-sonnet",
    )
    seen: Dict[str, Any] = {}

    async def _before(payload):
        seen.update(payload)

    monkeypatch.setattr(runner_mod, "StreamProcessor", _FakeProcessor)
    monkeypatch.setattr(runner_mod.HookPipeline, "has_stage_handlers", AsyncMock(return_value=True))
    monkeypatch.setattr(runner_mod.HookPipeline, "run_llm_before", AsyncMock(side_effect=_before))
    monkeypatch.setattr(runner_mod.HookPipeline, "run_llm_after", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod.SessionRunner, "_end_observability", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr("flocks.provider.options.build_provider_options", lambda provider_id, model_id: {})
    monkeypatch.setattr("flocks.session.streaming.tool_accumulator.ToolCallAccumulator", _FakeToolAccumulator)
    monkeypatch.setattr(runner_mod.Message, "update", AsyncMock(return_value=None))
    monkeypatch.setattr(runner_mod, "trace_scope", lambda **kwargs: SimpleNamespace(observation=None))
    monkeypatch.setattr(runner_mod, "generation_scope", lambda **kwargs: SimpleNamespace(observation=None))

    class _Provider:
        def chat_stream(self, **kwargs):
            async def _gen():
                yield SimpleNamespace(
                    delta="hi", reasoning=None, tool_calls=None, event_type=None,
                    finish_reason="stop", usage=None,
                )

            return _gen()

    await runner._call_llm(
        provider=_Provider(),
        messages=[ChatMessage(role="user", content="hello")],
        tools=[],
        agent=SimpleNamespace(name="rex"),
        assistant_msg=SimpleNamespace(id="msg-a"),
    )

    assert seen["callerID"] == "human:u-owner" and seen["callerType"] == "human"
    # The chat runner names the call by its assistant message.
    assert seen["messageID"] == "msg-a"


# ---------------------------------------------------------------------------
# Memory extraction
# ---------------------------------------------------------------------------

class _Msg:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class _MappingTransform:
    def __init__(self) -> None:
        self.mappings: Dict[str, Dict[str, str]] = {}

    @property
    def name(self) -> str:
        return "test-mapping"

    async def open_stream(self, metadata):  # pragma: no cover
        raise AssertionError("not streamed")

    async def transform_text(self, text: str, metadata: Dict[str, Any]) -> str:
        for placeholder, value in self.mappings.get(metadata.get("model_call_id") or "", {}).items():
            text = text.replace(placeholder, value)
        return text

    async def transform_value(self, value, metadata):
        return value


@pytest.mark.asyncio
async def test_memory_extraction_is_redacted_before_sending_and_restored_after(hook_pipeline, owned_session) -> None:
    from flocks.memory.flush import extract_and_save

    transform = _MappingTransform()
    register_response_transform(transform)
    seen: List[Dict[str, Any]] = []

    class _Redact(HookBase):
        async def llm_before(self, ctx) -> None:
            seen.append(json.loads(json.dumps(ctx.input, default=str)))
            request = dict(ctx.input["request"])
            messages = [dict(m) for m in request["messages"]]
            messages[-1]["content"] = messages[-1]["content"].replace("13800138000", "[[P]]")
            request["messages"] = messages
            ctx.output["request"] = request
            transform.mappings[ctx.input["model_call_id"]] = {"[[P]]": "13800138000"}

    hook_pipeline.register("redact", _Redact(), order=1)
    provider = SimpleNamespace(
        provider_id="p",
        chat=AsyncMock(return_value=SimpleNamespace(content="- The user's phone is [[P]]")),
    )

    with patch("flocks.memory.daily.DailyMemory.write_daily", new_callable=AsyncMock) as write_daily:
        await extract_and_save(
            session_id="ses-1",
            summary="fallback summary",
            chat_messages=[_Msg("user", "my phone is 13800138000")],
            model_id="m",
            provider=provider,
            ChatMessage=ChatMessage,
        )

    sent = provider.chat.await_args.kwargs["messages"][0].content
    assert "13800138000" not in sent and "[[P]]" in sent
    assert provider.chat.await_args.kwargs["max_tokens"] == 1500
    assert seen[0]["purpose"] == "memory_extraction" and seen[0]["callerID"] == "human:u-owner"
    saved = json.dumps([call.args for call in write_daily.await_args_list] + [call.kwargs for call in write_daily.await_args_list], default=str)
    assert "13800138000" in saved and "[[P]]" not in saved


@pytest.mark.asyncio
async def test_memory_extraction_is_not_sent_when_the_request_stage_fails(hook_pipeline, owned_session) -> None:
    from flocks.memory.flush import extract_and_save

    class _Broken(HookBase):
        async def llm_before(self, ctx) -> None:
            raise RuntimeError("redaction unavailable")

    hook_pipeline.register("broken", _Broken(), order=1, fail_policy="fail_closed")
    provider = SimpleNamespace(chat=AsyncMock(return_value=SimpleNamespace(content="never")))

    with patch("flocks.memory.daily.DailyMemory.write_daily", new_callable=AsyncMock) as write_daily:
        await extract_and_save(
            session_id="ses-1",
            summary="fallback summary",
            chat_messages=[_Msg("user", "my phone is 13800138000")],
            model_id="m",
            provider=provider,
            ChatMessage=ChatMessage,
        )

    provider.chat.assert_not_awaited()
    # The (already processed) compaction summary is kept instead.
    saved = json.dumps([call.args for call in write_daily.await_args_list] + [call.kwargs for call in write_daily.await_args_list], default=str)
    assert "fallback summary" in saved


@pytest.mark.asyncio
async def test_memory_extraction_keeps_the_summary_when_the_response_cannot_be_restored(hook_pipeline, owned_session) -> None:
    from flocks.memory.flush import extract_and_save

    class _BrokenTransform(_MappingTransform):
        async def transform_text(self, text, metadata):
            raise RuntimeError("restore unavailable")

    register_response_transform(_BrokenTransform())
    provider = SimpleNamespace(
        provider_id="p",
        chat=AsyncMock(return_value=SimpleNamespace(content="- something with [[P]]")),
    )

    with patch("flocks.memory.daily.DailyMemory.write_daily", new_callable=AsyncMock) as write_daily:
        await extract_and_save(
            session_id="ses-1",
            summary="fallback summary",
            chat_messages=[_Msg("user", "hello")],
            model_id="m",
            provider=provider,
            ChatMessage=ChatMessage,
        )

    saved = json.dumps([call.args for call in write_daily.await_args_list] + [call.kwargs for call in write_daily.await_args_list], default=str)
    assert "fallback summary" in saved
    assert "[[P]]" not in saved


@pytest.mark.asyncio
async def test_user_run_shell_command_names_the_session_caller(monkeypatch, owned_session, tmp_path) -> None:
    """A shell command the session's user runs carries the same caller as the
    session's model calls, not the system caller."""
    import asyncio as _asyncio

    from flocks.session.runner import SessionRunner

    observed: list[dict] = []

    class _Capture(HookBase):
        async def tool_before(self, ctx):
            observed.append(json.loads(json.dumps(ctx.input, default=str)))

    HookPipeline.reset()
    HookPipeline._initialized = True
    HookPipeline.register("capture.shell", _Capture())
    process = SimpleNamespace(communicate=AsyncMock(return_value=(b"ok\n", b"")), returncode=0)
    monkeypatch.setattr(
        "flocks.session.runner.Session.get_by_id",
        AsyncMock(return_value=SimpleNamespace(directory=str(tmp_path), project_id="p")),
    )
    monkeypatch.setattr("flocks.session.runner.asyncio.create_subprocess_shell", AsyncMock(return_value=process))
    try:
        await SessionRunner.shell(session_id="ses_1", agent="build", command="echo ok")
    finally:
        HookPipeline.reset()

    assert observed[0]["callerID"] == "human:u-owner"
    profile_entry = observed[0]["tool_execution"]["session"]["entry"]
    assert profile_entry == "session.shell"
