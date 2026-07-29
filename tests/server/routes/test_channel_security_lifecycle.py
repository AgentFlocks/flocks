"""Regression coverage for neutral Channel execution lifecycle hooks."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.server.routes.channel import SendMessageRequest, channel_send, channel_webhook


@pytest.fixture(autouse=True)
def _reset_hooks() -> None:
    HookPipeline.reset()
    yield
    HookPipeline.reset()


@pytest.mark.asyncio
async def test_public_webhook_stops_before_plugin_handler_when_extension_denies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public webhook must expose neutral authentication evidence pre-effect."""

    handled = AsyncMock(return_value={"ok": True})

    class _Plugin:
        async def webhook_authentication_evidence(self, body, headers):
            assert body == b"{}"
            assert headers == {"x-test": "1"}
            return {"plugin_authenticated": True}

        handle_webhook = handled

    class _DenyWebhook(HookBase):
        async def channel_webhook_before(self, ctx):
            assert ctx.input["entry"] == "channel_webhook"
            assert ctx.input["channel_id"] == "example"
            assert ctx.input["authentication"] == {"plugin_authenticated": True}
            ctx.output["execution"] = {"stop": True, "detail": "denied by extension"}

    class _Request:
        headers = {"x-test": "1"}

        async def body(self):
            return b"{}"

    HookPipeline.register("test.channel.webhook", _DenyWebhook(), critical=True)
    monkeypatch.setattr(
        "flocks.server.routes.channel.default_registry.get",
        lambda _channel_id: _Plugin(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await channel_webhook("example", _Request())

    assert exc_info.value.status_code == 403
    handled.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_send_stops_before_outbound_delivery_when_action_hook_denies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Channel control actions must enter the generic action lifecycle."""

    deliver = AsyncMock()

    class _DenyAction(HookBase):
        async def action_before(self, ctx):
            assert ctx.input["operation"] == "channel.channel_send"
            assert ctx.input["resource"] == {"type": "channel", "id": "send"}
            ctx.output["execution"] = {"stop": True, "detail": "denied by extension"}

    HookPipeline.register("test.channel.action", _DenyAction(), critical=True)
    monkeypatch.setattr(
        "flocks.channel.outbound.deliver.OutboundDelivery.deliver", deliver,
    )

    with pytest.raises(HTTPException) as exc_info:
        await channel_send(
            SendMessageRequest(channel_id="example", to="target", text="message")
        )

    assert exc_info.value.status_code == 403
    deliver.assert_not_awaited()
