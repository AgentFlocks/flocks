"""
httpx clients that route every request through :mod:`flocks.net.proxy`.

``httpx``'s own ``trust_env`` / ``mounts`` machinery only understands the
``NO_PROXY`` grammar (hosts and suffixes). The bypass list here also carries CIDR
blocks, ``host:port`` entries and the intranet defaults, so the decision is made
per request by :class:`ProxyRoutingTransport` and the client itself runs with
``trust_env=False`` (no double handling).

    from flocks.net.http import async_client, sync_client

    async with async_client(timeout=30) as client:      # proxy or direct, per URL
        await client.get("https://api.openai.com/v1/models")

    openai = AsyncOpenAI(http_client=async_client(...))  # SDKs that accept httpx clients

SOCKS proxies need ``socksio`` (``httpx[socks]``, already a dependency).
"""

from __future__ import annotations

import ssl
import threading
from typing import Any, Dict, Optional, Union

import httpx

from flocks.net.proxy import ProxySettings, get_proxy_settings

_VerifyType = Union[bool, str, ssl.SSLContext]


def _transport_kwargs(
    *,
    verify: _VerifyType = True,
    cert: Any = None,
    http2: bool = False,
    limits: Optional[httpx.Limits] = None,
    retries: int = 0,
    local_address: Optional[str] = None,
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {"verify": verify, "http2": http2, "retries": retries}
    if cert is not None:
        kwargs["cert"] = cert
    if limits is not None:
        kwargs["limits"] = limits
    if local_address is not None:
        kwargs["local_address"] = local_address
    return kwargs


class ProxyRoutingTransport(httpx.AsyncBaseTransport):
    """Per-request choice between a direct transport and one transport per proxy URL."""

    def __init__(
        self,
        settings: Optional[ProxySettings] = None,
        *,
        verify: _VerifyType = True,
        cert: Any = None,
        http2: bool = False,
        limits: Optional[httpx.Limits] = None,
        retries: int = 0,
        local_address: Optional[str] = None,
    ) -> None:
        self._settings = settings
        self._kwargs = _transport_kwargs(
            verify=verify, cert=cert, http2=http2, limits=limits, retries=retries, local_address=local_address
        )
        self._direct = httpx.AsyncHTTPTransport(**self._kwargs)
        self._proxied: Dict[str, httpx.AsyncHTTPTransport] = {}

    @property
    def settings(self) -> ProxySettings:
        return self._settings or get_proxy_settings()

    @property
    def verify(self) -> _VerifyType:
        """TLS verification handed to every underlying transport (for tests and diagnostics)."""
        return self._kwargs["verify"]

    def _transport_for(self, url: httpx.URL) -> httpx.AsyncHTTPTransport:
        proxy = self.settings.proxy_url_for(str(url))
        if proxy is None:
            return self._direct
        transport = self._proxied.get(proxy)
        if transport is None:
            transport = httpx.AsyncHTTPTransport(proxy=proxy, **self._kwargs)
            self._proxied[proxy] = transport
        return transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._transport_for(request.url).handle_async_request(request)

    async def __aenter__(self) -> "ProxyRoutingTransport":
        await self._direct.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._direct.aclose()
        for transport in self._proxied.values():
            await transport.aclose()
        self._proxied.clear()


class SyncProxyRoutingTransport(httpx.BaseTransport):
    """Blocking twin of :class:`ProxyRoutingTransport` (safe to share between threads)."""

    def __init__(
        self,
        settings: Optional[ProxySettings] = None,
        *,
        verify: _VerifyType = True,
        cert: Any = None,
        http2: bool = False,
        limits: Optional[httpx.Limits] = None,
        retries: int = 0,
        local_address: Optional[str] = None,
    ) -> None:
        self._settings = settings
        self._kwargs = _transport_kwargs(
            verify=verify, cert=cert, http2=http2, limits=limits, retries=retries, local_address=local_address
        )
        self._direct = httpx.HTTPTransport(**self._kwargs)
        self._proxied: Dict[str, httpx.HTTPTransport] = {}
        self._lock = threading.Lock()

    @property
    def settings(self) -> ProxySettings:
        return self._settings or get_proxy_settings()

    @property
    def verify(self) -> _VerifyType:
        """TLS verification handed to every underlying transport (for tests and diagnostics)."""
        return self._kwargs["verify"]

    def _transport_for(self, url: httpx.URL) -> httpx.HTTPTransport:
        proxy = self.settings.proxy_url_for(str(url))
        if proxy is None:
            return self._direct
        with self._lock:  # google-genai's sync client is shared between threads
            transport = self._proxied.get(proxy)
            if transport is None:
                transport = httpx.HTTPTransport(proxy=proxy, **self._kwargs)
                self._proxied[proxy] = transport
        return transport

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._transport_for(request.url).handle_request(request)

    def __enter__(self) -> "SyncProxyRoutingTransport":
        self._direct.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._direct.close()
        with self._lock:
            proxied = list(self._proxied.values())
            self._proxied.clear()
        for transport in proxied:
            transport.close()


def async_client(
    *,
    settings: Optional[ProxySettings] = None,
    use_proxy: bool = True,
    verify: _VerifyType = True,
    cert: Any = None,
    http2: bool = False,
    limits: Optional[httpx.Limits] = None,
    retries: int = 0,
    **client_kwargs: Any,
) -> httpx.AsyncClient:
    """``httpx.AsyncClient`` honouring the proxy policy (``use_proxy=False`` forces direct).

    Everything else (``timeout``, ``headers``, ``event_hooks``, ``base_url`` ...) is passed
    straight to ``httpx.AsyncClient``.
    """
    client_kwargs.setdefault("trust_env", False)
    transport: httpx.AsyncBaseTransport
    if use_proxy:
        transport = ProxyRoutingTransport(settings, verify=verify, cert=cert, http2=http2, limits=limits, retries=retries)
    else:  # direct, but with the same transport knobs (retries included)
        transport = httpx.AsyncHTTPTransport(**_transport_kwargs(verify=verify, cert=cert, http2=http2, limits=limits, retries=retries))
    return httpx.AsyncClient(transport=transport, **client_kwargs)


def sync_client(
    *,
    settings: Optional[ProxySettings] = None,
    use_proxy: bool = True,
    verify: _VerifyType = True,
    cert: Any = None,
    http2: bool = False,
    limits: Optional[httpx.Limits] = None,
    retries: int = 0,
    **client_kwargs: Any,
) -> httpx.Client:
    """``httpx.Client`` honouring the proxy policy (``use_proxy=False`` forces direct)."""
    client_kwargs.setdefault("trust_env", False)
    transport: httpx.BaseTransport
    if use_proxy:
        transport = SyncProxyRoutingTransport(settings, verify=verify, cert=cert, http2=http2, limits=limits, retries=retries)
    else:  # direct, but with the same transport knobs (retries included)
        transport = httpx.HTTPTransport(**_transport_kwargs(verify=verify, cert=cert, http2=http2, limits=limits, retries=retries))
    return httpx.Client(transport=transport, **client_kwargs)


__all__ = ["ProxyRoutingTransport", "SyncProxyRoutingTransport", "async_client", "sync_client"]
