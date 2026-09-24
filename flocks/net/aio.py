"""
aiohttp sessions that follow :mod:`flocks.net.proxy`.

aiohttp speaks HTTP(S) proxies natively (session-level ``proxy=`` / ``proxy_auth=``,
which also covers ``ws_connect``); SOCKS needs ``aiohttp_socks.ProxyConnector``.
A session built here gets the right one for its *target*: the host the session
is going to talk to. Sessions that hop between arbitrary hosts (the web fetch
tool follows redirects by hand) should build one session per hop with
``target_url`` set to that hop.

    async with make_session(target_url="https://api.telegram.org") as session:
        ...

    kwargs = session_kwargs(target_url=url)          # when a library builds the session itself
    aiohttp.ClientSession(**kwargs)
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote, urlsplit, urlunsplit

import aiohttp

from flocks.net.proxy import ProxySettings, get_proxy_settings
from flocks.utils.log import Log

log = Log.create(service="net.aio")


def _split_credentials(proxy_url: str) -> Tuple[str, Optional[str], Optional[str]]:
    """``scheme://user:pass@host:port`` → (``scheme://host:port``, user, pass), percent-decoded."""
    parts = urlsplit(proxy_url)
    if not parts.username and not parts.password:
        return proxy_url, None, None
    hostport = parts.hostname or ""
    if ":" in hostport:
        hostport = f"[{hostport}]"
    if parts.port:
        hostport += f":{parts.port}"
    clean = urlunsplit((parts.scheme, hostport, parts.path, parts.query, parts.fragment))
    return clean, unquote(parts.username) if parts.username else None, unquote(parts.password) if parts.password else None


def proxy_connector(proxy_url: str, *, ssl: Any = None, **connector_kwargs: Any) -> aiohttp.BaseConnector:
    """A SOCKS connector for ``socks5://`` / ``socks5h://`` (the ``h`` = proxy resolves names).

    python-socks does not know the ``socks5h`` spelling and insists on a port, so
    the URL is taken apart here rather than handed to ``ProxyConnector.from_url``.
    """
    from aiohttp_socks import ProxyConnector, ProxyType

    parts = urlsplit(proxy_url)
    scheme = parts.scheme.lower()
    if scheme not in ("socks5", "socks5h"):
        raise ValueError(f"proxy_connector() is for socks5 / socks5h proxies, got {scheme!r}")
    kwargs: Dict[str, Any] = {
        "proxy_type": ProxyType.SOCKS5,
        "host": parts.hostname,
        "port": parts.port or 1080,
        "username": unquote(parts.username) if parts.username else None,
        "password": unquote(parts.password) if parts.password else None,
        # rdns=True means the proxy resolves names (socks5h semantics), which is what
        # people expect from a corporate or VPN egress; plain socks5 resolves locally.
        "rdns": scheme == "socks5h",
    }
    if ssl is not None:
        kwargs["ssl"] = ssl
    kwargs.update(connector_kwargs)
    return ProxyConnector(**kwargs)


def session_kwargs(
    *,
    target_url: Optional[str] = None,
    settings: Optional[ProxySettings] = None,
    use_proxy: bool = True,
    ssl: Any = None,
    connector_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Keyword arguments for ``aiohttp.ClientSession`` implementing the policy.

    - http(s) proxy → ``{"proxy": ..., "proxy_auth": ..., "trust_env": False}``
      (aiohttp's own tunnelling; it would also read credentials from the URL's
      userinfo, they are split into ``proxy_auth`` so the proxy URL stays clean
      in logs and reprs);
    - socks5 / socks5h → ``{"connector": ProxyConnector, "trust_env": False}``;
    - direct → ``{"trust_env": False}`` (plus a plain ``TCPConnector`` when an
      ``ssl`` context or connector options were given), so the ambient
      ``HTTP_PROXY`` never sneaks in twice.
    """
    settings = settings or get_proxy_settings()
    proxy_url = settings.proxy_url_for(target_url) if (use_proxy and target_url) else None
    extra = dict(connector_kwargs or {})
    kwargs: Dict[str, Any] = {"trust_env": False}
    if proxy_url and proxy_url.lower().startswith("socks"):
        kwargs["connector"] = proxy_connector(proxy_url, ssl=ssl, **extra)
        return kwargs
    if proxy_url:
        clean, username, password = _split_credentials(proxy_url)
        kwargs["proxy"] = clean
        if username is not None:
            kwargs["proxy_auth"] = aiohttp.BasicAuth(username, password or "")
    if ssl is not None:
        extra["ssl"] = ssl
    if extra:
        kwargs["connector"] = aiohttp.TCPConnector(**extra)
    return kwargs


def make_session(
    *,
    target_url: Optional[str] = None,
    settings: Optional[ProxySettings] = None,
    use_proxy: bool = True,
    ssl: Any = None,
    connector_kwargs: Optional[Dict[str, Any]] = None,
    **session_kwargs_: Any,
) -> aiohttp.ClientSession:
    """``aiohttp.ClientSession`` for ``target_url`` — proxied or direct per the policy."""
    kwargs = session_kwargs(
        target_url=target_url, settings=settings, use_proxy=use_proxy, ssl=ssl, connector_kwargs=connector_kwargs
    )
    if "connector" in session_kwargs_ and "connector" in kwargs:
        # caller insists on its own connector: keep it, but it will not go through the proxy
        log.warn("net.aio.connector_override", {"target": target_url})
    kwargs.update(session_kwargs_)
    return aiohttp.ClientSession(**kwargs)


__all__ = ["make_session", "proxy_connector", "session_kwargs"]
