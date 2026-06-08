from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.engine.raptor import memory_delegate
from flocks.tool.registry import ToolResult


def _chunk(delta: str = "", tool_calls=None, event_type=None):
    """Create a minimal StreamChunk-like object for testing."""
    return SimpleNamespace(delta=delta, tool_calls=tool_calls, event_type=event_type)


@pytest.mark.asyncio
async def test_raptor_memory_delegate_does_not_create_persistent_session(monkeypatch):
    calls = {
        "session_create": 0,
        "message_create": 0,
        "session_loop_run": 0,
    }

    async def fail_session_create(*args, **kwargs):
        calls["session_create"] += 1
        raise AssertionError("memory delegate must not create a persistent session")

    async def fail_message_create(*args, **kwargs):
        calls["message_create"] += 1
        raise AssertionError("memory delegate must not create persistent messages")

    async def fail_session_loop_run(*args, **kwargs):
        calls["session_loop_run"] += 1
        raise AssertionError("memory delegate must not run a child SessionLoop")

    from flocks.session.message import Message
    from flocks.session.session import Session
    from flocks.session.session_loop import SessionLoop

    monkeypatch.setattr(Session, "create", fail_session_create)
    monkeypatch.setattr(Message, "create", fail_message_create)
    monkeypatch.setattr(SessionLoop, "run", fail_session_loop_run)

    monkeypatch.setattr(memory_delegate, "is_delegatable", lambda agent_name: True)

    async def fake_agent_get(agent_name):
        return SimpleNamespace(
            name=agent_name,
            prompt="You are a test delegate.",
            model=None,
            tools=[],
        )

    monkeypatch.setattr(memory_delegate.Agent, "get", fake_agent_get)

    class FakeProvider:
        def is_configured(self):
            return True

        async def chat_stream(self, model_id, messages, **kwargs):
            yield _chunk(delta="delegate completed")

    monkeypatch.setattr(memory_delegate.Provider, "get", lambda provider_id: FakeProvider())

    async def fake_apply_config(provider_id=None, config=None):
        return None

    monkeypatch.setattr(memory_delegate.Provider, "apply_config", fake_apply_config)

    async def fake_build_tool_schema(agent_name):
        return [], None

    monkeypatch.setattr(memory_delegate, "_build_tool_schema", fake_build_tool_schema)
    monkeypatch.setattr(
        memory_delegate,
        "_resolve_compaction_policy",
        lambda provider_id, model_id: SimpleNamespace(
            preemptive_threshold=999999,
            preserve_last=2,
            usable_context=128000,
            summary_max_tokens=4096,
        ),
    )

    async def fake_tool_executor(tool_name, tool_input, call_id, agent_name, metadata_callback=None):
        return ToolResult(success=True, output="unused")

    result = await memory_delegate.run_memory_delegate_task(
        parent_agent="rex",
        provider_id="test-provider",
        model_id="test-model",
        prompt="Complete a simple task.",
        description="test delegate",
        category=None,
        subagent_type="test-subagent",
        load_skills=None,
        abort_event=None,
        metadata_callback=None,
        tool_executor=fake_tool_executor,
    )

    assert result.success is True
    assert result.output == "delegate completed"
    assert calls == {
        "session_create": 0,
        "message_create": 0,
        "session_loop_run": 0,
    }


@pytest.mark.asyncio
async def test_raptor_memory_delegate_continues_after_tool_intent_text(monkeypatch):
    monkeypatch.setattr(memory_delegate, "is_delegatable", lambda agent_name: True)

    async def fake_agent_get(agent_name):
        return SimpleNamespace(
            name=agent_name,
            prompt="Use tools to inspect device state before answering.",
            model=None,
            tools=[],
        )

    monkeypatch.setattr(memory_delegate.Agent, "get", fake_agent_get)

    class FakeProvider:
        def __init__(self):
            self.calls = 0

        def is_configured(self):
            return True

        async def chat_stream(self, model_id, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield _chunk(delta="I will start by calling `device_context` to inspect the device.")
            elif self.calls == 2:
                yield _chunk(
                    tool_calls=[
                        {
                            "id": "call_1",
                            "function": {
                                "name": "device_context",
                                "arguments": '{"device_id": "dev-1"}',
                            },
                        }
                    ]
                )
            else:
                yield _chunk(delta="Device inspection completed.")

    fake_provider = FakeProvider()
    monkeypatch.setattr(memory_delegate.Provider, "get", lambda provider_id: fake_provider)

    async def fake_apply_config(provider_id=None, config=None):
        return None

    monkeypatch.setattr(memory_delegate.Provider, "apply_config", fake_apply_config)

    async def fake_build_tool_schema(agent_name):
        return [
            {
                "type": "function",
                "function": {
                    "name": "device_context",
                    "description": "Inspect device state.",
                    "parameters": {
                        "type": "object",
                        "properties": {"device_id": {"type": "string"}},
                        "required": ["device_id"],
                    },
                },
            }
        ], None

    monkeypatch.setattr(memory_delegate, "_build_tool_schema", fake_build_tool_schema)
    monkeypatch.setattr(
        memory_delegate,
        "_resolve_compaction_policy",
        lambda provider_id, model_id: SimpleNamespace(
            preemptive_threshold=999999,
            preserve_last=2,
            usable_context=128000,
            summary_max_tokens=4096,
        ),
    )

    executed = []

    async def fake_tool_executor(tool_name, tool_input, call_id, agent_name, metadata_callback=None):
        executed.append((tool_name, tool_input, call_id, agent_name))
        return ToolResult(success=True, output='{"device_id": "dev-1", "status": "ok"}')

    result = await memory_delegate.run_memory_delegate_task(
        parent_agent="rex",
        provider_id="test-provider",
        model_id="test-model",
        prompt="Inspect device dev-1 and summarize the result.",
        description="inspect device",
        category=None,
        subagent_type="test-subagent",
        load_skills=None,
        abort_event=None,
        metadata_callback=None,
        tool_executor=fake_tool_executor,
    )

    assert result.success is True
    assert result.output == "Device inspection completed."
    assert fake_provider.calls == 3
    assert executed == [
        ("device_context", {"device_id": "dev-1"}, "call_1", "test-subagent")
    ]
    assert result.metadata["toolCalls"] == 1


@pytest.mark.asyncio
async def test_raptor_memory_delegate_fails_repeated_tool_intent_without_calls(monkeypatch):
    monkeypatch.setattr(memory_delegate, "is_delegatable", lambda agent_name: True)

    async def fake_agent_get(agent_name):
        return SimpleNamespace(
            name=agent_name,
            prompt="Use tools before answering.",
            model=None,
            tools=[],
        )

    monkeypatch.setattr(memory_delegate.Agent, "get", fake_agent_get)

    class FakeProvider:
        def __init__(self):
            self.calls = 0

        def is_configured(self):
            return True

        async def chat_stream(self, model_id, messages, **kwargs):
            self.calls += 1
            # Return text with tool-intent language; no actual tool_calls.
            yield _chunk(
                delta="I will call the device_context tool to inspect the device now."
            )

    fake_provider = FakeProvider()
    monkeypatch.setattr(memory_delegate.Provider, "get", lambda provider_id: fake_provider)

    async def fake_apply_config(provider_id=None, config=None):
        return None

    monkeypatch.setattr(memory_delegate.Provider, "apply_config", fake_apply_config)

    async def fake_build_tool_schema(agent_name):
        return [
            {
                "type": "function",
                "function": {
                    "name": "device_context",
                    "description": "Inspect device state.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ], None

    monkeypatch.setattr(memory_delegate, "_build_tool_schema", fake_build_tool_schema)
    monkeypatch.setattr(
        memory_delegate,
        "_resolve_compaction_policy",
        lambda provider_id, model_id: SimpleNamespace(
            preemptive_threshold=999999,
            preserve_last=2,
            usable_context=128000,
            summary_max_tokens=4096,
        ),
    )

    async def fake_tool_executor(tool_name, tool_input, call_id, agent_name, metadata_callback=None):
        raise AssertionError("No tool call should be executed")

    result = await memory_delegate.run_memory_delegate_task(
        parent_agent="rex",
        provider_id="test-provider",
        model_id="test-model",
        prompt="Inspect device dev-1 and summarize the result.",
        description="inspect device",
        category=None,
        subagent_type="test-subagent",
        load_skills=None,
        abort_event=None,
        metadata_callback=None,
        tool_executor=fake_tool_executor,
    )

    assert result.success is False
    assert "did not emit a real tool call" in result.error
    assert result.metadata["toolCalls"] == 0
    assert fake_provider.calls == 4


def test_native_delegate_task_has_no_raptor_specific_execution_path():
    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "flocks/tool/agent/delegate_task.py").read_text(encoding="utf-8")

    assert "memory_delegate" not in source
    assert "run_memory_delegate_task" not in source
    assert '"raptor"' not in source
    assert "loop_engine" not in source
