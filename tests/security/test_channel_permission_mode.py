from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.channel.base import ChatType, InboundMessage
from flocks.channel.inbound import session_binding as sb_mod
from flocks.channel.inbound.dispatcher import _enforce_visible_agent


def _msg() -> InboundMessage:
    return InboundMessage(
        channel_id="feishu",
        account_id="default",
        message_id="msg-1",
        sender_id="ou_sender_1",
        sender_name="Sender",
        chat_id="oc_group_1",
        chat_type=ChatType.GROUP,
        text="hello",
    )


@pytest.mark.asyncio
async def test_least_priv_owner_uses_channel_principal(monkeypatch):
    monkeypatch.setenv("FLOCKS_CHANNEL_LEAST_PRIV", "1")
    with patch("flocks.auth.service.AuthService.has_users", new=AsyncMock()) as has_users:
        owner_kwargs = await sb_mod.resolve_channel_session_owner_kwargs(
            msg=_msg(),
            permission_mode="readonly",
        )
    assert "owner_user_id" not in owner_kwargs
    assert owner_kwargs["owner_subject_id"].startswith("channel:feishu:default:ou_sender_1")
    assert owner_kwargs["permission_mode"] == "readonly"
    has_users.assert_not_awaited()


@pytest.mark.asyncio
async def test_least_priv_owner_uses_mapping_when_present(monkeypatch):
    monkeypatch.setenv("FLOCKS_CHANNEL_LEAST_PRIV", "1")
    mapped = {
        "owner_user_id": "usr_ops",
        "owner_username": "ops-user",
        "tenant_id": "tenant-a",
        "department": "soc",
        "role": "member",
    }
    with patch.object(sb_mod, "_resolve_identity_mapping", new=AsyncMock(return_value=mapped)):
        owner_kwargs = await sb_mod.resolve_channel_session_owner_kwargs(
            msg=_msg(),
            permission_mode="readonly",
        )
    assert owner_kwargs["owner_user_id"] == "usr_ops"
    assert owner_kwargs["owner_username"] == "ops-user"
    assert owner_kwargs["owner_subject_id"] == "user:usr_ops"
    assert owner_kwargs["permission_mode"] == "readonly"


def test_enforce_visible_agents():
    assert _enforce_visible_agent("a", ["a", "b"]) == "a"
    assert _enforce_visible_agent("x", ["a", "b"]) == "a"
    assert _enforce_visible_agent(None, ["a", "b"]) == "a"
    assert _enforce_visible_agent("x", []) == "x"
