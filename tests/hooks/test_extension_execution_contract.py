from __future__ import annotations

from io import BytesIO
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile
from starlette.requests import Request

from flocks.channel.base import InboundMessage
from flocks.channel.inbound.dispatcher import InboundDispatcher
from flocks.hooks.execution import ExecutionStopped, execute_with_hooks
from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.ingest.kafka.manager import KafkaManager
from flocks.ingest.syslog.manager import SyslogManager
from flocks.server import auth
from flocks.server.routes import config as config_routes
from flocks.server.routes import mcp as mcp_routes
from flocks.server.routes import workflow as workflow_routes
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

    class Stopper(HookBase):
        async def ingress_before(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))
            return {"execution": {"stop": True, "detail": "extension stopped operation"}}

        async def ingress_after(self, ctx):
            observed.append((ctx.stage, dict(ctx.input)))

    HookPipeline.register("stopper", Stopper())
    payload = {"operation": "mcp.update", "arguments": {"name": "example"}}

    stage_context = await HookPipeline.run_ingress_before(payload)
    assert stage_context.output == {
        "execution": {"stop": True, "detail": "extension stopped operation"},
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
