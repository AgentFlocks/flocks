"""Slack clients follow the outbound proxy policy (http *and* socks), not slack_sdk's own env lookup."""

from __future__ import annotations

import pytest

from flocks.net import proxy as proxy_mod
from flocks.net.proxy import ProxySettings
from tests.net.proxy_fixture import EchoServer, RecordingProxy

pytest.importorskip("slack_sdk")

from flocks.channel.builtin.slack.transport import build_web_client, close_web_client, route_socket_mode  # noqa: E402


@pytest.fixture
async def stack(monkeypatch):
    proxy = await RecordingProxy().start()
    target = await EchoServer().start()
    for name in proxy_mod.PROXY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    try:
        yield proxy, target
    finally:
        proxy_mod.set_proxy_settings_override(None)
        await proxy.stop()
        await target.stop()


async def test_web_client_tunnels_through_socks_and_ignores_env(stack, monkeypatch):
    proxy, target = stack
    # slack_sdk would read this and hand a SOCKS URL to aiohttp's http-only ``proxy=``
    monkeypatch.setenv("HTTPS_PROXY", proxy.socks_url)
    proxy_mod.set_proxy_settings_override(ProxySettings.build(mode="manual", all=proxy.socks_url, bypass_private=False, bypass=["<-loopback>"]))

    client = build_web_client("xoxb-test", base_url=f"{target.url}/")
    try:
        assert client.proxy is None
        response = await client.api_call("api.test", http_verb="GET")
        assert response["ok"] is True
    finally:
        await close_web_client(client)
    assert proxy.targets == [("socks5", "127.0.0.1", target.port)]
    assert client.session.closed


async def test_web_client_goes_direct_when_slack_is_whitelisted(stack):
    proxy, target = stack
    proxy_mod.set_proxy_settings_override(
        ProxySettings.build(mode="manual", all=proxy.http_url, bypass_private=False, bypass=["<-loopback>", ".slack.com"])
    )
    client = build_web_client("xoxb-test", base_url=f"{target.url}/")
    try:
        response = await client.api_call("api.test", http_verb="GET")
        assert response["ok"] is True
    finally:
        await close_web_client(client)
    assert proxy.targets == [] and target.hits == 1


async def test_route_socket_mode_replaces_sdk_session(stack):
    proxy, _ = stack
    proxy_mod.set_proxy_settings_override(ProxySettings.build(mode="manual", all=proxy.socks_url, bypass_private=False, bypass=["<-loopback>"]))
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from aiohttp_socks import ProxyConnector

    web = build_web_client("xoxb-test")
    handler = AsyncSocketModeHandler(AsyncApp(client=web, signing_secret="x"), "xapp-test")
    stale = handler.client.aiohttp_client_session
    try:
        await route_socket_mode(handler)
        assert stale.closed
        assert isinstance(handler.client.aiohttp_client_session.connector, ProxyConnector)
        assert handler.client.proxy is None
    finally:
        await handler.client.aiohttp_client_session.close()
        await close_web_client(web)


async def test_route_socket_mode_tolerates_foreign_handlers():
    await route_socket_mode(object())
