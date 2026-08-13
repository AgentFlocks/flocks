"""Regression coverage for channel lifecycle hooks after simplification."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.server.routes.channel import SendMessageRequest, channel_send, channel_webhook


@pytest.fixture(autouse=True)
def _reset_hooks() -> None:
    HookPipeline.reset()
    HookPipeline._initialized = True
    yield
    HookPipeline.reset()


@pytest.mark.asyncio
async def test_public_webhook_is_processed_by_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    handled = AsyncMock(return_value={"ok": True})

    class _Plugin:
        async def webhook_authentication_evidence(self, body, headers):
            assert body == b"{}"
            assert headers == {"x-test": "1"}
            return {"plugin_authenticated": True}

        handle_webhook = handled

    class _Request:
        headers = {"x-test": "1"}

        async def body(self):
            return b"{}"

    monkeypatch.setattr(
        "flocks.server.routes.channel.default_registry.get",
        lambda _channel_id: _Plugin(),
    )

    result = await channel_webhook("example", _Request())

    assert result == {"ok": True}
    handled.assert_awaited_once()


@pytest.mark.asyncio
async def test_channel_send_can_be_blocked_by_outbound_before_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send_text_mock = AsyncMock()

    class _BlockOutbound(HookBase):
        async def channel_outbound_before(self, ctx):
            ctx.output["blocked"] = True

    class _Plugin:
        rate_limit = (1, 1)
        text_chunk_limit = 1000

        def format_message(self, text: str) -> str:
            return text

        def chunk_text(self, text: str, _limit: int):
            return [text]

        async def send_text(self, _ctx):
            return await send_text_mock(_ctx)

    HookPipeline.register("test.channel.outbound", _BlockOutbound(), critical=True)
    monkeypatch.setattr(
        "flocks.channel.outbound.deliver.default_registry.get",
        lambda _channel_id: _Plugin(),
    )

    result = await channel_send(
        SendMessageRequest(channel_id="example", to="target", text="message")
    )

    assert result["ok"] is True
    assert result["message_ids"] == []
    send_text_mock.assert_not_awaited()
