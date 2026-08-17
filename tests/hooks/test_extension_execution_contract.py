from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from flocks.auth.context import AuthUser, get_current_auth_user, set_current_auth_user
from flocks.channel.base import InboundMessage
from flocks.channel.inbound.dispatcher import InboundDispatcher
from flocks.hooks.execution import (
    ExecutionStopped,
    current_execution_context,
    execute_with_hooks,
    execution_context_scope,
)
from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.identity import get_current_subject
from flocks.ingest.kafka.manager import KafkaManager
from flocks.ingest.syslog.manager import SyslogManager
from flocks.plugin import ExtensionPoint, PluginLoader
from flocks.server import auth
import flocks.server.app as server_app_module
from flocks.tool.registry import (
    ParameterType,
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolParameter,
    ToolResult,
    ToolRegistry,
)
from flocks.workflow import service_runtime
from flocks.workflow.triggers.models import TriggerDefinition
from flocks.workflow.triggers.runtime import TriggerRuntime


@pytest.fixture(autouse=True)
def reset_pipeline() -> None:
    HookPipeline.reset()
    HookPipeline._initialized = True
    yield
    HookPipeline.reset()


@pytest.mark.asyncio
async def test_action_stage_is_empty_without_registered_hooks() -> None:
    ctx = await HookPipeline.run_action_before({"operation": "mcp.update"})

    assert ctx.output == {}


@pytest.mark.asyncio
async def test_execution_stop_is_interpreted_only_by_calling_adapter() -> None:
    observed: list[tuple[str, dict]] = []
    opaque_context = {"opaque_binding": object()}

    class Stopper(HookBase):
        async def ingress_before(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))
            return {
                "execution": {
                    "stop": True,
                    "detail": "extension stopped operation",
                },
                "context": opaque_context,
            }

        async def ingress_after(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))

    HookPipeline.register("stopper", Stopper())
    payload = {"operation": "mcp.update", "arguments": {"name": "example"}}

    stage_context = await HookPipeline.run_ingress_before(payload)
    assert stage_context.output == {
        "execution": {"stop": True, "detail": "extension stopped operation"},
        "context": opaque_context,
    }
    assert stage_context.input == payload
    observed.clear()

    effect = AsyncMock(return_value={"ok": True})
    with pytest.raises(ExecutionStopped, match="extension stopped operation"):
        await execute_with_hooks(
            payload,
            effect,
            before=HookPipeline.run_ingress_before,
            after=HookPipeline.run_ingress_after,
        )

    effect.assert_not_awaited()
    assert [stage for stage, _payload in observed] == ["ingress.before", "ingress.after"]
    after_payload = observed[-1][1]
    assert after_payload["outcome"] == "stopped"
    assert isinstance(after_payload["error"], ExecutionStopped)
    assert after_payload["context"] is opaque_context


@pytest.mark.asyncio
async def test_unregistered_action_hook_leaves_operation_and_result_unmodified() -> None:
    payload = {"operation": "tool.execute", "arguments": {"none": None}}
    result = {"raw": object()}
    effect = AsyncMock(return_value=result)

    actual = await execute_with_hooks(payload, effect)

    assert actual is result
    assert payload == {"operation": "tool.execute", "arguments": {"none": None}}
    effect.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_execute_with_hooks_binds_and_resets_valid_neutral_subject() -> None:
    class SubjectLifecycle(HookBase):
        async def ingress_before(self, _ctx):
            return {
                "context": {
                    "subject": {
                        "subject_id": "principal_42",
                        "subject_type": "channel_user",
                    }
                }
            }

    HookPipeline.register("subject-lifecycle", SubjectLifecycle())
    observed = []

    async def effect() -> str:
        observed.append(get_current_subject())
        return "ok"

    assert await execute_with_hooks(
        {"operation": "channel.dispatch"},
        effect,
        before=HookPipeline.run_ingress_before,
        after=HookPipeline.run_ingress_after,
    ) == "ok"
    assert observed[0].subject_id == "principal_42"
    assert get_current_subject() is None


@pytest.mark.asyncio
async def test_execute_with_hooks_forwards_successful_after_subject_to_sink() -> None:
    """An after-stage hook can provide neutral request context after auth."""

    class AfterSubject(HookBase):
        async def ingress_after(self, _ctx):
            return {
                "context": {
                    "subject": {
                        "subject_id": "authenticated-local-user",
                        "subject_type": "human",
                    }
                }
            }

    HookPipeline.register("after-subject", AfterSubject())
    sunk_subjects = []
    auth_result = (None, object(), object())

    assert await execute_with_hooks(
        {"operation": "auth.request", "transport": "http"},
        AsyncMock(return_value=auth_result),
        before=HookPipeline.run_ingress_before,
        after=HookPipeline.run_ingress_after,
        subject_sink=sunk_subjects.append,
    ) is auth_result

    assert len(sunk_subjects) == 1
    assert sunk_subjects[0].subject_id == "authenticated-local-user"


@pytest.mark.asyncio
async def test_execute_with_hooks_forwards_after_opaque_context_to_sink() -> None:
    """An extension context can survive auth without OSS interpreting it."""

    class AfterContext(HookBase):
        async def ingress_after(self, _ctx):
            return {
                "context": {
                    "workflow_transfer": "opaque-pro-token",
                    "extension_marker": "value",
                }
            }

    HookPipeline.register("after-context", AfterContext())
    sunk_contexts: list[dict] = []

    assert await execute_with_hooks(
        {"operation": "auth.request", "transport": "http"},
        AsyncMock(return_value="authenticated"),
        before=HookPipeline.run_ingress_before,
        after=HookPipeline.run_ingress_after,
        context_sink=sunk_contexts.append,
    ) == "authenticated"

    assert sunk_contexts == [
        {
            "workflow_transfer": "opaque-pro-token",
            "extension_marker": "value",
        }
    ]


@pytest.mark.asyncio
async def test_execute_with_hooks_merges_before_context_mapping_into_after() -> None:
    """Lifecycle adapters preserve arbitrary hook context without interpreting it."""

    opaque_before_value = object()
    opaque_payload_value = object()
    opaque_context = {
        "opaque_binding": opaque_before_value,
        "subject": {"subject_id": "p-1"},
        "shared": "before",
    }
    observed_after_payloads: list[dict] = []

    class ContextLifecycle(HookBase):
        async def ingress_before(self, _ctx):
            return {"context": opaque_context}

        async def ingress_after(self, ctx):
            observed_after_payloads.append(dict(ctx.input))

    HookPipeline.register("context-lifecycle", ContextLifecycle())

    assert await execute_with_hooks(
        {
            "operation": "channel.dispatch",
            "context": {
                "opaque_payload": opaque_payload_value,
                "shared": "payload",
            },
        },
        AsyncMock(return_value="ok"),
        before=HookPipeline.run_ingress_before,
        after=HookPipeline.run_ingress_after,
    ) == "ok"

    after_context = observed_after_payloads[0]["context"]
    assert after_context["opaque_binding"] is opaque_before_value
    assert after_context["opaque_payload"] is opaque_payload_value
    assert after_context["shared"] == "before"


@pytest.mark.asyncio
async def test_execute_with_hooks_forwards_before_context_to_after_on_cancellation() -> None:
    """Cancellation still runs the paired generic after lifecycle stage."""

    opaque_context = {"opaque_binding": object()}
    observed_after_payloads: list[dict] = []

    class ContextLifecycle(HookBase):
        async def ingress_before(self, _ctx):
            return {"context": opaque_context}

        async def ingress_after(self, ctx):
            observed_after_payloads.append(dict(ctx.input))

    async def cancelled_effect() -> None:
        raise asyncio.CancelledError()

    HookPipeline.register("cancelled-context-lifecycle", ContextLifecycle())

    with pytest.raises(asyncio.CancelledError):
        await execute_with_hooks(
            {"operation": "channel.dispatch"},
            cancelled_effect,
            before=HookPipeline.run_ingress_before,
            after=HookPipeline.run_ingress_after,
        )

    assert observed_after_payloads[0]["context"] is opaque_context


@pytest.mark.asyncio
async def test_execute_with_hooks_runs_after_when_before_hook_raises() -> None:
    """A critical before-hook failure still reaches generic lifecycle cleanup."""

    observed_after_payloads: list[dict] = []

    class Recorder(HookBase):
        async def ingress_before(self, _ctx):
            return {"context": {"opaque_binding": object()}}

        async def ingress_after(self, ctx):
            observed_after_payloads.append(dict(ctx.input))

    class CriticalFailure(HookBase):
        async def ingress_before(self, _ctx):
            raise RuntimeError("critical before hook failed")

    HookPipeline.register("before-failure-recorder", Recorder())
    HookPipeline.register("before-failure", CriticalFailure(), critical=True)

    with pytest.raises(RuntimeError, match="critical before hook failed"):
        await execute_with_hooks(
            {"operation": "channel.dispatch"},
            AsyncMock(),
            before=HookPipeline.run_ingress_before,
            after=HookPipeline.run_ingress_after,
        )

    assert observed_after_payloads[0]["outcome"] == "error"
    assert isinstance(observed_after_payloads[0]["error"], RuntimeError)


@pytest.mark.asyncio
async def test_execute_with_hooks_resets_neutral_subject_on_cancellation() -> None:
    class SubjectLifecycle(HookBase):
        async def ingress_before(self, _ctx):
            return {
                "context": {
                    "subject": {
                        "subject_id": "principal_42",
                        "subject_type": "channel_user",
                    }
                }
            }

    HookPipeline.register("subject-lifecycle", SubjectLifecycle())

    async def effect() -> None:
        assert get_current_subject() is not None
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await execute_with_hooks(
            {"operation": "channel.dispatch"},
            effect,
            before=HookPipeline.run_ingress_before,
            after=HookPipeline.run_ingress_after,
        )

    assert get_current_subject() is None


@pytest.mark.asyncio
async def test_channel_dispatcher_does_not_effect_after_critical_plugin_failure(
    monkeypatch,
) -> None:
    monkeypatch.setattr(PluginLoader, "_runtime_critical_entrypoint_failure", True)
    dispatcher = InboundDispatcher()
    dispatcher._dispatch = AsyncMock()
    message = InboundMessage(
        channel_id="test",
        account_id="default",
        message_id="critical-plugin-message",
        sender_id="sender-1",
        text="must not dispatch",
    )

    with pytest.raises(ExecutionStopped, match="critical plugin entrypoint failure"):
        await dispatcher.dispatch(message)

    dispatcher._dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_main_server_critical_loader_result_stops_channel_without_a_hook(
    monkeypatch,
) -> None:
    """A failed main-server plugin load cannot leave Channel ingress open."""

    class _CriticalResult:
        has_critical_entrypoint_failure = True
        critical_entrypoint_failures = ["declared-critical-plugin"]

    def load_critical(**_kwargs):
        PluginLoader._runtime_critical_entrypoint_failure = True
        return _CriticalResult()

    monkeypatch.setattr(PluginLoader, "load_all", load_critical)
    server_app_module._load_installed_package_plugins()
    dispatcher = InboundDispatcher()
    dispatcher._dispatch = AsyncMock()

    try:
        with pytest.raises(ExecutionStopped, match="critical plugin entrypoint failure"):
            await dispatcher.dispatch(
                InboundMessage(
                    channel_id="test",
                    account_id="default",
                    message_id="main-server-critical-plugin-message",
                    sender_id="sender-1",
                    text="must not dispatch",
                )
            )
        dispatcher._dispatch.assert_not_awaited()
    finally:
        PluginLoader.clear_runtime_critical_entrypoint_failure()
        server_app_module.app.state.critical_plugin_entrypoint_failure = False
        server_app_module.app.state.critical_plugin_entrypoint_failures = ()


@pytest.mark.asyncio
async def test_execution_context_can_be_cleared_at_ownership_boundary() -> None:
    with execution_context_scope({"workflow_transfer": "opaque-transfer"}):
        with execution_context_scope({}, inherit=False):
            assert current_execution_context() == {}


@pytest.mark.asyncio
async def test_tool_execution_is_unchanged_without_hooks() -> None:
    observed: list[str] = []

    async def handler(_ctx: ToolContext, value: str) -> ToolResult:
        observed.append(value)
        return ToolResult(success=True, output=value)

    tool = Tool(
        info=ToolInfo(
            name="context-extra-no-hook",
            description="Neutral tool execution without hooks",
            category=ToolCategory.CUSTOM,
            parameters=[ToolParameter(name="value", type=ParameterType.STRING)],
        ),
        handler=handler,
    )

    result = await tool.execute(
        ToolContext("session-1", "message-1", extra={"opaque": "value"}),
        value="ok",
    )

    assert result.model_dump() == {
        "success": True,
        "output": "ok",
        "error": None,
        "metadata": {},
        "title": None,
        "truncated": False,
        "attachments": None,
    }
    assert observed == ["ok"]
