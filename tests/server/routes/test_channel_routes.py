from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.channel.base import DeliveryResult
from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.server.routes.channel import SessionSendRequest, channel_session_send, channel_webhook
from fastapi import HTTPException
from starlette.requests import Request


def _webhook_request() -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/channel/test/webhook",
        "raw_path": b"/api/channel/test/webhook",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }

    async def _receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    return Request(scope, _receive)


@pytest.mark.asyncio
async def test_channel_session_send_falls_back_to_latest_channel_binding() -> None:
    latest_binding = SimpleNamespace(
        session_id="ses_new",
        channel_id="wecom",
        account_id="default",
        chat_id="room_1",
    )
    svc = SimpleNamespace(
        list_bindings=AsyncMock(return_value=[latest_binding]),
        latest_active_user_binding=AsyncMock(return_value=latest_binding),
    )
    deliver_result = DeliveryResult(
        channel_id="wecom",
        message_id="msg_new",
        chat_id="room_1",
    )

    with patch(
        "flocks.channel.inbound.session_binding.SessionBindingService",
        return_value=svc,
    ), patch(
        "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
        AsyncMock(return_value=[deliver_result]),
    ) as deliver:
        result = await channel_session_send(
            SessionSendRequest(
                session_id="ses_old",
                text="hello",
                channel_type="wecom",
            )
        )

    assert result["ok"] is True
    assert result["session_id"] == "ses_new"
    assert result["message_ids"] == ["msg_new"]
    svc.latest_active_user_binding.assert_awaited_once_with(
        channel_id="wecom",
        account_id=None,
        chat_id=None,
    )
    deliver.assert_awaited_once()
    assert deliver.await_args.kwargs["session_id"] == "ses_new"


@pytest.mark.asyncio
async def test_channel_session_send_returns_404_when_channel_binding_is_ambiguous() -> None:
    svc = SimpleNamespace(
        list_bindings=AsyncMock(return_value=[]),
        latest_active_user_binding=AsyncMock(return_value=None),
    )

    with patch(
        "flocks.channel.inbound.session_binding.SessionBindingService",
        return_value=svc,
    ), patch(
        "flocks.channel.outbound.deliver.OutboundDelivery.deliver",
        AsyncMock(),
    ) as deliver:
        with pytest.raises(HTTPException) as exc_info:
            await channel_session_send(
                SessionSendRequest(
                    session_id="ses_old",
                    text="hello",
                    channel_type="wecom",
                )
            )

    assert exc_info.value.status_code == 404
    assert "im_send_message(resolve_only=true)" in str(exc_info.value.detail)
    svc.latest_active_user_binding.assert_awaited_once_with(
        channel_id="wecom",
        account_id=None,
        chat_id=None,
    )
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_webhook_rejects_when_signature_verification_fails() -> None:
    class _Plugin:
        requires_signature = True

        async def verify_inbound(self, body, headers):
            return False

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/channel/feishu/webhook",
        "raw_path": b"/api/channel/feishu/webhook",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }
    async def _receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = Request(scope, _receive)

    with patch("flocks.server.routes.channel.default_registry.get", return_value=_Plugin()):
        with pytest.raises(HTTPException) as exc_info:
            await channel_webhook("feishu", request)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_channel_webhook_pro_hook_can_reject_before_plugin_execution(monkeypatch) -> None:
    class _Plugin:
        requires_signature = False
        handle_webhook = AsyncMock(return_value={"ok": True})

    class _ProIngressHook(HookBase):
        async def channel_webhook_before(self, ctx) -> None:
            ctx.output["decision"] = {"action": "deny", "reason": "pro_ingress_denied"}

    HookPipeline.reset()
    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    HookPipeline.register("pro-channel-ingress", _ProIngressHook(), critical=True)
    plugin = _Plugin()
    try:
        with patch("flocks.server.routes.channel.default_registry.get", return_value=plugin):
            with pytest.raises(HTTPException) as exc_info:
                await channel_webhook("test", _webhook_request())
    finally:
        HookPipeline.reset()

    assert exc_info.value.status_code == 403
    plugin.handle_webhook.assert_not_awaited()
