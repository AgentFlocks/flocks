from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from flocks.channel.base import InboundMessage
from flocks.channel.inbound.dispatcher import InboundDispatcher
from flocks.hooks.execution import ExecutionStopped, execute_with_hooks
from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.server import auth
from flocks.server.routes import config as config_routes
from flocks.server.routes import workflow as workflow_routes


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
    class Stopper(HookBase):
        async def action_before(self, _ctx):
            return {"execution": {"stop": True, "detail": "extension stopped operation"}}

    HookPipeline.register("stopper", Stopper())
    payload = {"operation": "mcp.update", "arguments": {"name": "example"}}

    stage_context = await HookPipeline.run_action_before(payload)
    assert stage_context.output == {
        "execution": {"stop": True, "detail": "extension stopped operation"},
    }
    assert stage_context.input == payload

    effect = AsyncMock(return_value={"ok": True})
    with pytest.raises(ExecutionStopped, match="extension stopped operation"):
        await execute_with_hooks(payload, effect)

    effect.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_and_channel_ingress_emit_before_and_after() -> None:
    observed: list[str] = []

    class IngressLifecycle(HookBase):
        async def ingress_before(self, ctx):
            observed.append(ctx.stage)

        async def ingress_after(self, ctx):
            observed.append(ctx.stage)

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
    )
    dispatcher.dedup._seen[message.message_id] = time.monotonic()
    await dispatcher.dispatch(message)

    assert observed == [
        "ingress.before",
        "ingress.after",
        "ingress.before",
        "ingress.after",
    ]


def test_all_config_mutations_and_webhook_trigger_use_lifecycle_wrappers() -> None:
    config_mutations = [
        config_routes.update_ui_config,
        config_routes.upload_ui_favicon,
        config_routes.reset_ui_favicon,
        config_routes.update_config,
    ]

    assert all(hasattr(endpoint, "__wrapped__") for endpoint in config_mutations)
    assert hasattr(workflow_routes.invoke_workflow_webhook_trigger, "__wrapped__")
