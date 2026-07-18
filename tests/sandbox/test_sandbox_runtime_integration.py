"""
Sandbox runtime integration tests.

These tests validate that StreamProcessor can:
1. Enforce sandbox tool policy before tool execution.
2. Inject sandbox context into ToolContext.extra for bash tool execution.
"""

import time
from types import SimpleNamespace

import pytest

from flocks.session.streaming.stream_events import ToolCallEvent, ToolInputStartEvent
from flocks.tool.registry import ToolContext, ToolResult
from flocks.tool.registry import ToolRegistry
from flocks.session.message import AssistantMessageInfo, MessagePath, TokenUsage
from flocks.session.streaming.stream_processor import StreamProcessor


def _build_processor(config_data: dict) -> StreamProcessor:
    """Create a minimal StreamProcessor for sandbox tests."""
    assistant = AssistantMessageInfo(
        id="msg-sandbox-test",
        sessionID="session-sandbox-test",
        role="assistant",
        time={"created": int(time.time() * 1000)},
        parentID="user-msg",
        modelID="test-model",
        providerID="test-provider",
        mode="standard",
        agent="rex",
        path=MessagePath(cwd="/tmp", root="/tmp"),
        tokens=TokenUsage(input=0, output=0, reasoning=0),
    )
    agent = SimpleNamespace(name="rex")
    return StreamProcessor(
        session_id="session-sandbox-test",
        assistant_message=assistant,
        agent=agent,
        config_data=config_data,
        session_key="session-sandbox-test",
        main_session_key="main",
        workspace_dir="/tmp",
    )


@pytest.mark.asyncio
async def test_sandbox_tool_policy_blocks_tool() -> None:
    """Tool should be blocked when not allowed by sandbox tool policy."""
    processor = _build_processor(
        {
            "sandbox": {
                "mode": "on",
                "tools": {
                    "allow": ["read"],
                    "deny": [],
                },
            }
        }
    )

    meta = await processor._resolve_sandbox_meta("bash")

    assert meta["blocked"] is True
    assert "blocked by sandbox tool policy" in (meta["error"] or "")
    assert meta["extra"] == {}


@pytest.mark.asyncio
async def test_sandbox_meta_injects_bash_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bash tool should receive sandbox metadata when sandbox is enabled."""
    processor = _build_processor(
        {
            "sandbox": {
                "mode": "on",
                "workspace_access": "none",
                "docker": {"workdir": "/workspace"},
                "tools": {"allow": ["bash"]},
            }
        }
    )

    from flocks.sandbox.types import SandboxContext, SandboxDockerConfig, SandboxToolPolicy

    async def fake_resolve_sandbox_context(**_kwargs):
        return SandboxContext(
            enabled=True,
            session_key="session-sandbox-test",
            workspace_dir="/tmp/.flocks/sandboxes/session-sandbox-test",
            agent_workspace_dir="/tmp",
            workspace_access="none",
            container_name="flocks-sbx-test",
            container_workdir="/workspace",
            docker=SandboxDockerConfig(env={"FOO": "BAR"}),
            tools=SandboxToolPolicy(allow=["bash"], deny=[]),
        )

    monkeypatch.setattr(
        "flocks.sandbox.context.resolve_sandbox_context",
        fake_resolve_sandbox_context,
    )

    meta = await processor._resolve_sandbox_meta("bash")

    assert meta["blocked"] is False
    assert meta["error"] is None
    assert "sandbox" in meta["extra"]
    assert meta["extra"]["sandbox"]["container_name"] == "flocks-sbx-test"
    assert meta["extra"]["sandbox"]["container_workdir"] == "/workspace"
    assert meta["extra"]["sandbox"]["env"]["FOO"] == "BAR"


@pytest.mark.asyncio
async def test_sandbox_meta_injects_context_for_run_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_workflow should receive sandbox metadata when sandbox is enabled."""
    processor = _build_processor(
        {
            "sandbox": {
                "mode": "on",
                "workspace_access": "none",
                "docker": {"workdir": "/workspace"},
                "tools": {"allow": ["run_workflow"]},
            }
        }
    )

    from flocks.sandbox.types import SandboxContext, SandboxDockerConfig, SandboxToolPolicy

    async def fake_resolve_sandbox_context(**_kwargs):
        return SandboxContext(
            enabled=True,
            session_key="session-sandbox-test",
            workspace_dir="/tmp/.flocks/sandboxes/session-sandbox-test",
            agent_workspace_dir="/tmp",
            workspace_access="none",
            container_name="flocks-sbx-test",
            container_workdir="/workspace",
            docker=SandboxDockerConfig(env={"FOO": "BAR"}),
            tools=SandboxToolPolicy(allow=["run_workflow"], deny=[]),
        )

    monkeypatch.setattr(
        "flocks.sandbox.context.resolve_sandbox_context",
        fake_resolve_sandbox_context,
    )

    meta = await processor._resolve_sandbox_meta("run_workflow")

    assert meta["blocked"] is False
    assert meta["error"] is None
    assert "sandbox" in meta["extra"]
    assert meta["extra"]["sandbox"]["container_name"] == "flocks-sbx-test"
    assert meta["extra"]["sandbox"]["container_workdir"] == "/workspace"
    assert meta["extra"]["sandbox"]["env"]["FOO"] == "BAR"


@pytest.mark.asyncio
async def test_agent_tool_result_diff_when_sandbox_on_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Same agent tool call should execute differently with sandbox mode on/off.

    - mode=off: bash runs on host path
    - mode=on: bash runs on sandbox path
    """
    processor_off = _build_processor({"sandbox": {"mode": "off"}})
    off_meta = await processor_off._resolve_sandbox_meta("bash")

    processor_on = _build_processor({"sandbox": {"mode": "on"}})
    from flocks.sandbox.types import SandboxContext, SandboxDockerConfig, SandboxToolPolicy

    async def fake_resolve_sandbox_context(**_kwargs):
        return SandboxContext(
            enabled=True,
            session_key="session-sandbox-test",
            workspace_dir="/tmp/.flocks/sandboxes/session-sandbox-test",
            agent_workspace_dir="/tmp",
            workspace_access="none",
            container_name="flocks-sbx-test",
            container_workdir="/workspace",
            docker=SandboxDockerConfig(env={"FOO": "BAR"}),
            tools=SandboxToolPolicy(allow=["bash"], deny=[]),
        )

    monkeypatch.setattr(
        "flocks.sandbox.context.resolve_sandbox_context",
        fake_resolve_sandbox_context,
    )
    on_meta = await processor_on._resolve_sandbox_meta("bash")

    call_routes = []

    async def fake_host(**_kwargs):
        call_routes.append("host")
        return ToolResult(success=True, output="host-route", metadata={"route": "host"})

    async def fake_sandbox(**_kwargs):
        call_routes.append("sandbox")
        return ToolResult(success=True, output="sandbox-route", metadata={"route": "sandbox"})

    monkeypatch.setattr("flocks.tool.code.bash._execute_host", fake_host)
    monkeypatch.setattr("flocks.tool.code.bash._execute_sandboxed", fake_sandbox)

    from flocks.tool.code.bash import bash_tool

    result_off = await bash_tool(
        ctx=ToolContext(
            session_id="s-off",
            message_id="m-off",
            extra=off_meta["extra"],
        ),
        command="echo test",
    )
    result_on = await bash_tool(
        ctx=ToolContext(
            session_id="s-on",
            message_id="m-on",
            extra=on_meta["extra"],
        ),
        command="echo test",
    )

    assert result_off.success
    assert result_on.success
    assert result_off.output == "host-route"
    assert result_on.output == "sandbox-route"
    assert call_routes == ["host", "sandbox"]


@pytest.mark.asyncio
async def test_stream_processor_tool_call_diff_when_sandbox_on_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    End-to-end through StreamProcessor tool-call events:
    compare ctx.extra passed to tool execution in sandbox off vs on.
    """
    captured_extras = []

    async def fake_execute(cls, tool_name: str, ctx=None, **kwargs):
        captured_extras.append((tool_name, dict(ctx.extra or {})))
        return ToolResult(success=True, output="ok", metadata={"tool": tool_name})

    async def fake_store_part(*args, **kwargs):
        return None

    async def fake_parts(*args, **kwargs):
        return []

    monkeypatch.setattr(ToolRegistry, "execute", classmethod(fake_execute))
    monkeypatch.setattr("flocks.session.message.Message.store_part", fake_store_part)
    monkeypatch.setattr("flocks.session.message.Message.parts", fake_parts)

    processor_off = _build_processor({"sandbox": {"mode": "off"}})
    await processor_off.process_event(ToolInputStartEvent(id="call-off", tool_name="bash"))
    await processor_off.process_event(
        ToolCallEvent(
            tool_call_id="call-off",
            tool_name="bash",
            input={"command": "echo off"},
        )
    )

    processor_on = _build_processor({"sandbox": {"mode": "on"}})
    from flocks.sandbox.types import SandboxContext, SandboxDockerConfig, SandboxToolPolicy

    async def fake_resolve_sandbox_context(**_kwargs):
        return SandboxContext(
            enabled=True,
            session_key="session-sandbox-test",
            workspace_dir="/tmp/.flocks/sandboxes/session-sandbox-test",
            agent_workspace_dir="/tmp",
            workspace_access="none",
            container_name="flocks-sbx-test",
            container_workdir="/workspace",
            docker=SandboxDockerConfig(env={"FOO": "BAR"}),
            tools=SandboxToolPolicy(allow=["bash"], deny=[]),
        )

    monkeypatch.setattr(
        "flocks.sandbox.context.resolve_sandbox_context",
        fake_resolve_sandbox_context,
    )

    await processor_on.process_event(ToolInputStartEvent(id="call-on", tool_name="bash"))
    await processor_on.process_event(
        ToolCallEvent(
            tool_call_id="call-on",
            tool_name="bash",
            input={"command": "echo on"},
        )
    )

    assert len(captured_extras) == 2
    assert captured_extras[0][0] == "bash"
    assert captured_extras[0][1] == {}
    assert "sandbox" in captured_extras[1][1]
    assert captured_extras[1][1]["sandbox"]["container_name"] == "flocks-sbx-test"
