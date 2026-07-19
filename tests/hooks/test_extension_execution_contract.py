from __future__ import annotations

import asyncio
from io import BytesIO
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile
from starlette.requests import Request
from starlette.responses import Response

from flocks.channel.base import InboundMessage
from flocks.channel.inbound.dispatcher import InboundDispatcher
from flocks.hooks.execution import ExecutionStopped, execute_with_hooks
from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.identity import get_current_subject
from flocks.ingest.kafka.manager import KafkaManager
from flocks.ingest.syslog.manager import SyslogManager
from flocks.plugin import ExtensionPoint, PluginLoader
from flocks.server import auth
import flocks.server.app as server_app_module
from flocks.server.app import auth_guard_middleware
from flocks.server.routes import config as config_routes
from flocks.server.routes import mcp as mcp_routes
from flocks.server.routes import workflow as workflow_routes
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
async def test_untrusted_subject_context_never_bypasses_http_authentication() -> None:
    class UntrustedContextHook(HookBase):
        async def ingress_before(self, _ctx):
            return {
                "context": {
                    "subject": {
                        "subject_id": "untrusted-hook",
                        "subject_type": "caller_metadata",
                        "attributes": {"role": "admin"},
                    }
                }
            }

    HookPipeline.register("untrusted-context", UntrustedContextHook())
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/api/config",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )

    with pytest.raises(HTTPException, match="API Token"):
        await auth.apply_auth_for_request(request)

    assert request.state.subject.subject_id == "untrusted-hook"
    assert not hasattr(request.state, "auth_user")
    assert get_current_subject() is None


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
async def test_main_server_critical_plugin_state_returns_503_before_auth(monkeypatch) -> None:
    class _CriticalResult:
        has_critical_entrypoint_failure = True
        critical_entrypoint_failures = ["declared-critical-plugin"]

    monkeypatch.setattr(
        PluginLoader,
        "load_all",
        lambda **_kwargs: _CriticalResult(),
    )
    server_app_module._load_installed_package_plugins()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/health",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "app": server_app_module.app,
        }
    )
    call_next = AsyncMock(return_value=Response(status_code=204))

    response = await auth_guard_middleware(request, call_next)

    assert response.status_code == 503
    assert server_app_module.app.state.critical_plugin_entrypoint_failure is True
    call_next.assert_not_awaited()
    server_app_module.app.state.critical_plugin_entrypoint_failure = False
    server_app_module.app.state.critical_plugin_entrypoint_failures = ()


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
async def test_scoped_critical_entrypoint_failure_stops_tool_registry_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A critical entrypoint found by scoped loading blocks a real tool call."""

    class _CriticalEntryPoint:
        name = "scoped-critical-plugin"

        @staticmethod
        def load():
            raise ImportError("critical plugin dependency unavailable")

    class _EntryPoints:
        @staticmethod
        def select(*, group: str):
            if group == "flocks.plugins.critical":
                return [_CriticalEntryPoint()]
            assert group == "flocks.plugins"
            return []

    monkeypatch.setattr(
        "flocks.plugin.loader.importlib.metadata.entry_points",
        lambda: _EntryPoints(),
    )
    monkeypatch.setattr(
        PluginLoader,
        "_extension_points",
        {
            "TOOLS": ExtensionPoint(
                attr_name="TOOLS",
                subdir="tools",
                consumer=lambda _items, _source: None,
            )
        },
    )
    monkeypatch.setattr(PluginLoader, "_runtime_critical_entrypoint_failure", False)

    PluginLoader.load_extension(
        "TOOLS",
        project_dir=tmp_path,
        load_entry_points=True,
    )

    executed = False

    async def handler(_ctx: ToolContext, value: str) -> ToolResult:
        nonlocal executed
        executed = True
        return ToolResult(success=True, output=value)

    tool = Tool(
        info=ToolInfo(
            name="scoped-critical-entrypoint-tool",
            description="must not execute after scoped critical plugin failure",
            category=ToolCategory.CUSTOM,
            parameters=[ToolParameter(name="value", type=ParameterType.STRING, required=True)],
        ),
        handler=handler,
    )
    monkeypatch.setattr(ToolRegistry, "_initialized", True)
    monkeypatch.setattr(ToolRegistry, "_tools", {tool.info.name: tool})
    monkeypatch.setattr(ToolRegistry, "_failure_state", {})

    result = await ToolRegistry.execute(
        tool.info.name,
        ToolContext(session_id="session-1", message_id="message-1"),
        value="must not execute",
    )

    assert PluginLoader.has_runtime_critical_entrypoint_failure() is True
    assert result.success is False
    assert result.error == "critical plugin entrypoint failure"
    assert executed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("load_mode", ["scoped", "all"])
async def test_entrypoint_metadata_scan_failure_stops_tool_registry_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    load_mode: str,
) -> None:
    """An unreadable entrypoint index cannot leave lifecycle effects open."""

    def _scan_error():
        raise RuntimeError("entrypoint metadata unavailable")

    monkeypatch.setattr(
        "flocks.plugin.loader.importlib.metadata.entry_points",
        _scan_error,
    )
    monkeypatch.setattr(
        PluginLoader,
        "_extension_points",
        {
            "TOOLS": ExtensionPoint(
                attr_name="TOOLS",
                subdir="tools",
                consumer=lambda _items, _source: None,
            )
        },
    )
    monkeypatch.setattr(PluginLoader, "_runtime_critical_entrypoint_failure", False)

    if load_mode == "scoped":
        PluginLoader.load_extension(
            "TOOLS",
            project_dir=tmp_path,
            load_entry_points=True,
        )
    else:
        result = PluginLoader.load_all(project_dir=tmp_path)
        assert result.has_critical_entrypoint_failure is True

    executed = False

    async def handler(_ctx: ToolContext, value: str) -> ToolResult:
        nonlocal executed
        executed = True
        return ToolResult(success=True, output=value)

    tool = Tool(
        info=ToolInfo(
            name=f"entrypoint-metadata-scan-{load_mode}",
            description="must not execute after entrypoint metadata scan failure",
            category=ToolCategory.CUSTOM,
            parameters=[ToolParameter(name="value", type=ParameterType.STRING, required=True)],
        ),
        handler=handler,
    )
    monkeypatch.setattr(ToolRegistry, "_initialized", True)
    monkeypatch.setattr(ToolRegistry, "_tools", {tool.info.name: tool})
    monkeypatch.setattr(ToolRegistry, "_failure_state", {})

    execution = await ToolRegistry.execute(
        tool.info.name,
        ToolContext(session_id="session-1", message_id="message-1"),
        value="must not execute",
    )

    assert PluginLoader.has_runtime_critical_entrypoint_failure() is True
    assert execution.success is False
    assert execution.error == "critical plugin entrypoint failure"
    assert executed is False


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
async def test_tool_lifecycle_preserves_original_arguments_before_remapping_and_coercion() -> None:
    observed: list[dict] = []
    handler_kwargs: dict = {}

    class LifecycleRecorder(HookBase):
        async def action_before(self, ctx):
            observed.append(dict(ctx.input))

    async def handler(_ctx: ToolContext, **kwargs) -> ToolResult:
        handler_kwargs.update(kwargs)
        return ToolResult(success=True, output="ok")

    HookPipeline.register("lifecycle-recorder", LifecycleRecorder())
    tool = Tool(
        info=ToolInfo(
            name="raw-lifecycle-arguments",
            description="Preserve raw lifecycle arguments",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(name="stringValue", type=ParameterType.STRING),
                ToolParameter(name="mappingValue", type=ParameterType.STRING),
                ToolParameter(name="listValue", type=ParameterType.STRING),
            ],
        ),
        handler=handler,
    )
    raw_mapping = {"nested": [1]}
    raw_list = ["item", {"enabled": True}]

    result = await tool.execute(
        ToolContext(session_id="session-1", message_id="message-1"),
        string_value=None,
        mapping_value=raw_mapping,
        list_value=raw_list,
    )

    assert result.success is True
    lifecycle_arguments = observed[0]["tool"]["input"]
    assert lifecycle_arguments["string_value"] is None
    assert lifecycle_arguments["mapping_value"] is raw_mapping
    assert lifecycle_arguments["list_value"] is raw_list
    assert handler_kwargs["stringValue"] == "None"
    assert json.loads(handler_kwargs["mappingValue"]) == raw_mapping
    assert json.loads(handler_kwargs["listValue"]) == raw_list


@pytest.mark.asyncio
async def test_tool_lifecycle_forwards_context_extra_as_opaque_carrier() -> None:
    observed: list[dict] = []

    class LifecycleRecorder(HookBase):
        async def action_before(self, ctx):
            observed.append(dict(ctx.input))

    async def handler(_ctx: ToolContext, value: str) -> ToolResult:
        return ToolResult(success=True, output=value)

    HookPipeline.register("lifecycle-recorder", LifecycleRecorder())
    tool = Tool(
        info=ToolInfo(
            name="context-extra-carrier",
            description="Forward neutral tool context extra",
            category=ToolCategory.CUSTOM,
            parameters=[ToolParameter(name="value", type=ParameterType.STRING)],
        ),
        handler=handler,
    )
    context_extra = {
        "subject": {"subject_id": "principal-1", "subject_type": "human"},
        "parent_ceiling": {"tools": ["read"]},
        "opaque": {"value": object()},
    }

    result = await tool.execute(
        ToolContext("session-1", "message-1", extra=context_extra), value="ok"
    )

    assert result.success is True
    assert observed[0]["tool_context_extra"] == context_extra
    assert observed[0]["tool_context_extra"] is not context_extra
    assert observed[0]["tool_context_extra"]["opaque"] is context_extra["opaque"]


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


@pytest.mark.asyncio
async def test_http_and_channel_ingress_emit_before_and_after() -> None:
    observed: list[tuple[str, dict]] = []

    class IngressLifecycle(HookBase):
        async def ingress_before(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))

        async def ingress_after(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))

    HookPipeline.register("ingress-lifecycle", IngressLifecycle())
    request = Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/health",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    })
    await auth.apply_auth_for_request(request)

    dispatcher = InboundDispatcher()
    message = InboundMessage(
        channel_id="test",
        account_id="default",
        message_id="duplicate-message",
        sender_id="sender-1",
        text="original transport text",
        mention_text="mention-only text",
        raw={"provider": "test", "event": "original"},
    )
    dispatcher.dedup._seen[message.message_id] = time.monotonic()
    await dispatcher.dispatch(message)

    assert [stage for stage, _payload in observed] == [
        "ingress.before",
        "ingress.after",
        "ingress.before",
        "ingress.after",
    ]
    assert observed[0][1]["request"] is request
    channel_payload = observed[2][1]
    assert channel_payload["message"] is message
    assert channel_payload["text"] == "original transport text"
    assert channel_payload["evidence"] is message.raw


@pytest.mark.asyncio
async def test_wrapped_control_actions_stop_and_preserve_raw_arguments() -> None:
    observed: list[tuple[str, dict]] = []

    class Stopper(HookBase):
        async def action_before(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))
            return {"execution": {"stop": True}}

        async def action_after(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))

    HookPipeline.register("stopper", Stopper())
    ui_request = config_routes.UIConfigUpdateRequest(displayName=None)
    favicon = UploadFile(filename="site.ico", file=BytesIO(b"favicon"))
    config_data = {"channels": None}
    mcp_request = mcp_routes.McpAddRequest(name="example", config={"url": None})
    workflow_request = workflow_routes.WorkflowCreateRequest(
        name="raw workflow",
        workflowJson={"nodes": []},
    )
    webhook_request = Request({
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": "/webhook/workflows/workflow-1/trigger-1",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    })
    operations = [
        (config_routes.update_ui_config, (ui_request,), "request", ui_request),
        (config_routes.upload_ui_favicon, (favicon,), "file", favicon),
        (config_routes.reset_ui_favicon, (), None, None),
        (config_routes.update_config, (config_data,), "config_data", config_data),
        (mcp_routes.add_mcp_server, (mcp_request,), "request", mcp_request),
        (workflow_routes.create_workflow, (workflow_request,), "req", workflow_request),
        (
            workflow_routes.invoke_workflow_webhook_trigger,
            ("workflow-1", "trigger-1", webhook_request),
            "request",
            webhook_request,
        ),
    ]

    for endpoint, args, argument_name, argument_value in operations:
        with pytest.raises(ExecutionStopped):
            await endpoint(*args)
        before_payload = observed[-1][1]
        if argument_name is not None:
            assert before_payload["arguments"][argument_name] is argument_value

    assert [stage for stage, _payload in observed] == [
        stage
        for _endpoint, _args, _argument_name, _argument_value in operations
        for stage in ("action.before", "action.after")
    ]
    assert all(payload["outcome"] == "stopped" for stage, payload in observed if stage == "action.after")


@pytest.mark.asyncio
async def test_trigger_and_ingest_lifecycles_preserve_raw_trigger_and_event() -> None:
    observed: list[tuple[str, dict]] = []

    class LifecycleRecorder(HookBase):
        async def action_before(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))

        async def action_after(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))

    HookPipeline.register("lifecycle-recorder", LifecycleRecorder())
    runtime = TriggerRuntime()
    trigger = TriggerDefinition(id="trigger-1", type="kafka", source={"topic": "topic-1"})
    mapped_inputs = {"input": {"raw": True}}
    runtime._execute_workflow_effect = AsyncMock(return_value={"executed": True})

    result = await runtime._execute_workflow(
        workflow_id="workflow-1",
        workflow_json={},
        trigger=trigger,
        mapped_inputs=mapped_inputs,
    )

    assert result == {"executed": True}
    assert observed[0][1]["trigger"] is trigger
    assert observed[0][1]["inputs"] is mapped_inputs

    kafka = KafkaManager()
    kafka._dispatcher.dispatch = AsyncMock(return_value=None)
    kafka_message = {"event": "kafka"}
    await kafka._trigger_workflow(
        "workflow-1",
        {},
        kafka_message,
        "message",
        trigger=trigger,
    )

    syslog = SyslogManager()
    syslog._dispatcher.dispatch = AsyncMock(return_value=None)
    syslog_message = {"event": "syslog"}
    syslog_trigger = TriggerDefinition(id="trigger-2", type="syslog")
    await syslog._trigger_workflow(
        "workflow-1",
        {},
        syslog_message,
        "message",
        trigger=syslog_trigger,
    )

    before_payloads = [payload for stage, payload in observed if stage == "action.before"]
    assert [payload["operation"] for payload in before_payloads] == [
        "workflow.trigger.execute",
        "workflow.trigger.kafka",
        "workflow.trigger.syslog",
    ]
    assert before_payloads[1]["trigger"] is trigger
    assert before_payloads[1]["event"].raw is kafka_message
    assert before_payloads[2]["trigger"] is syslog_trigger
    assert before_payloads[2]["event"].raw is syslog_message
    assert [stage for stage, _payload in observed] == [
        stage
        for _operation in before_payloads
        for stage in ("action.before", "action.after")
    ]


@pytest.mark.asyncio
async def test_workflow_service_emits_lifecycle_with_raw_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, dict]] = []

    class LifecycleRecorder(HookBase):
        async def action_before(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))

        async def action_after(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))

    HookPipeline.register("lifecycle-recorder", LifecycleRecorder())
    app = service_runtime.create_service_app(
        workflow_json={},
        workflow_id="workflow-1",
        release_id="release-1",
    )
    app.state.mcp_ready = True
    invoke = next(route.endpoint for route in app.routes if route.path == "/invoke")
    req = service_runtime.InvokeRequest(inputs={"raw": {"value": 1}}, request_id="request-1")
    monkeypatch.setattr(service_runtime, "build_workflow_tool_context", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        service_runtime.asyncio,
        "to_thread",
        AsyncMock(return_value=SimpleNamespace(status="SUCCEEDED", run_id="run-1", outputs={}, error=None)),
    )

    response = await invoke(req)

    assert response["status"] == "SUCCEEDED"
    assert [stage for stage, _payload in observed] == ["action.before", "action.after"]
    assert observed[0][1]["operation"] == "workflow.service.invoke"
    assert observed[0][1]["inputs"] is req.inputs
