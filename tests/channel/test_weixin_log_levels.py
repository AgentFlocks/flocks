from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import flocks.channel.builtin.weixin.channel as weixin_module


@pytest.mark.asyncio
async def test_group_policy_note_logs_info(monkeypatch):
    sessions = [MagicMock(), MagicMock()]
    info_log = MagicMock()
    warn_log = MagicMock()
    warning_log = MagicMock()

    monkeypatch.setattr(weixin_module, "AIOHTTP_AVAILABLE", True)
    monkeypatch.setattr(weixin_module, "CRYPTO_AVAILABLE", True)
    monkeypatch.setattr(
        weixin_module,
        "aiohttp",
        SimpleNamespace(
            ClientTimeout=MagicMock(return_value=MagicMock()),
            ClientSession=MagicMock(side_effect=sessions),
        ),
    )
    monkeypatch.setattr(weixin_module.ilink, "make_ssl_connector", MagicMock(return_value=None))
    monkeypatch.setattr(weixin_module, "ContextTokenStore", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(weixin_module, "MessageDedup", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(weixin_module, "MediaCache", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(weixin_module.log, "info", info_log)
    monkeypatch.setattr(weixin_module.log, "warn", warn_log)
    monkeypatch.setattr(weixin_module.log, "warning", warning_log)

    channel = weixin_module.WeixinChannel()
    monkeypatch.setattr(channel, "_poll_loop", AsyncMock(return_value=None))
    monkeypatch.setattr(channel, "_close_sessions", AsyncMock(return_value=None))

    await channel.start(
        {"token": "token", "accountId": "account", "groupPolicy": "all"},
        AsyncMock(),
    )

    assert any(call.args and call.args[0] == "weixin.group_policy.note" for call in info_log.call_args_list)
    assert not any(
        call.args and call.args[0] == "weixin.group_policy.note"
        for call in warn_log.call_args_list + warning_log.call_args_list
    )
