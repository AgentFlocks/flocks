"""Slack's aiohttp traffic through the outbound proxy policy (:mod:`flocks.net`).

slack_sdk reads ``HTTPS_PROXY`` on its own and hands it to aiohttp's per-request
``proxy=``, which only understands HTTP proxies: a SOCKS URL there makes aiohttp
speak HTTP to a SOCKS port and hang until the timeout. Every client built here
therefore carries an explicit session from :func:`flocks.net.aio.make_session`
(http *and* socks, whitelist applied to ``slack.com``) and has ``proxy`` pinned to
``None`` so the SDK never re-reads the environment.
"""

from __future__ import annotations

from typing import Any, Optional

from flocks.net.aio import make_session

SLACK_API_URL = "https://slack.com"
# Socket Mode websockets live on wss-primary/wss-backup.slack.com; the whitelist
# decision is per host, so a ``.slack.com`` bypass rule covers both.
SLACK_SOCKET_URL = "wss://wss-primary.slack.com"


def build_web_client(token: Optional[str], *, client_cls: Any = None, **kwargs: Any):
    """``AsyncWebClient`` whose session follows the proxy policy.

    The caller owns the session: close it with :func:`close_web_client` (the SDK
    only closes sessions it created itself). ``client_cls`` lets the channel pass
    its own (possibly stubbed) ``AsyncWebClient`` symbol.
    """
    if client_cls is None:
        from slack_sdk.web.async_client import AsyncWebClient as client_cls

    client = client_cls(token=token, session=make_session(target_url=SLACK_API_URL), **kwargs)
    client.proxy = None  # routing lives in the session's connector
    return client


async def close_web_client(client: Any) -> None:
    session = getattr(client, "session", None)
    if session is not None and not session.closed:
        await session.close()


async def route_socket_mode(handler: Any) -> None:
    """Swap the Socket Mode client's own ``aiohttp.ClientSession`` for a policy-routed one.

    ``slack_sdk.socket_mode.aiohttp.SocketModeClient`` builds a bare session in its
    constructor with no way to inject a connector; replacing the attribute before
    the first ``connect`` is the supported-by-shape way in (the SDK reads it only at
    ``ws_connect`` time and closes whatever is there on ``close``).
    """
    client = getattr(handler, "client", None)
    if client is None:  # not the SDK handler (tests / custom adapters): nothing to route
        return
    stale = getattr(client, "aiohttp_client_session", None)
    client.aiohttp_client_session = make_session(target_url=SLACK_SOCKET_URL)
    client.proxy = None
    if stale is not None and not stale.closed:
        await stale.close()


__all__ = ["SLACK_API_URL", "SLACK_SOCKET_URL", "build_web_client", "close_web_client", "route_socket_mode"]
