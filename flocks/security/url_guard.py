"""URL validation helpers for server-side HTTP requests."""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
}
_BLOCKED_IPV4_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_LEGACY_IPV4_RE = re.compile(
    r"^(?:0[xX][0-9A-Fa-f]+|[0-9]+)"
    r"(?:\.(?:0[xX][0-9A-Fa-f]+|[0-9]+)){0,3}$"
)


def _normalized_hostname(hostname: str | None) -> str:
    if not hostname:
        return ""
    return hostname.strip().rstrip(".").lower()


def _parse_ip_literal(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        # System resolvers and aiohttp accept legacy IPv4 forms such as
        # 127.1, 2130706433, and 017700000001. Normalize them before applying
        # the restricted-network checks.
        if not _LEGACY_IPV4_RE.match(hostname):
            return None
        try:
            packed = socket.inet_aton(hostname)
        except OSError:
            return None
        ip = ipaddress.ip_address(socket.inet_ntoa(packed))

    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip


def _blocked_ip(hostname: str) -> bool:
    ip = _parse_ip_literal(hostname)
    if ip is None:
        return False

    if isinstance(ip, ipaddress.IPv4Address):
        if any(ip in network for network in _BLOCKED_IPV4_NETWORKS):
            return True

    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_public_http_url_syntax(url: str) -> tuple[str | None, str | None]:
    """Return (error, hostname) after syntax and literal-host validation."""
    if not isinstance(url, str) or not url.strip():
        return "URL is required", None

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        return "URL must start with http:// or https://", None

    hostname = _normalized_hostname(parsed.hostname)
    return _validate_public_hostname(hostname)


def _validate_public_hostname(hostname: str) -> tuple[str | None, str | None]:
    """Return (error, hostname) after hostname and literal-IP validation."""
    if not hostname:
        return "URL must include a hostname", None

    if hostname in _BLOCKED_HOSTNAMES or hostname.endswith(".localhost"):
        return "URL host is not allowed for server-side requests", None

    if _blocked_ip(hostname):
        return "URL resolves to a restricted network address", None

    return None, hostname


def validate_public_http_url(url: str) -> str | None:
    """Return an error message when *url* is unsafe by syntax/literal host."""
    error, _hostname = _validate_public_http_url_syntax(url)
    return error


def validate_public_resolved_host(host: str) -> str | None:
    """Return an error when a resolved peer address is restricted."""
    if _blocked_ip(_normalized_hostname(host)):
        return "URL resolves to a restricted network address"

    return None


class GuardedResolver:
    """aiohttp resolver that rejects DNS results pointing at restricted IPs."""

    def __init__(self, inner: Any | None = None) -> None:
        import aiohttp

        self._inner = inner or aiohttp.DefaultResolver()

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[Any]:
        error, hostname = _validate_public_hostname(_normalized_hostname(host))
        if error:
            raise OSError(error)

        results = await self._inner.resolve(hostname or host, port, family)
        for item in results:
            resolved = item.get("host") if isinstance(item, dict) else getattr(item, "host", None)
            if resolved:
                resolved_error = validate_public_resolved_host(str(resolved))
                if resolved_error:
                    raise OSError(resolved_error)
        return results

    async def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close:
            result = close()
            if hasattr(result, "__await__"):
                await result


def _resolved_host_from_item(item: Any) -> str | None:
    return item.get("host") if isinstance(item, dict) else getattr(item, "host", None)


class GuardedTCPConnector:
    """aiohttp TCPConnector that validates both requested and resolved hosts."""

    def __new__(cls, **kwargs: Any):
        import aiohttp

        class _Connector(aiohttp.TCPConnector):
            async def _resolve_host(self, host: str, port: int, traces: Any = None) -> list[Any]:
                error, hostname = _validate_public_hostname(_normalized_hostname(host))
                if error:
                    raise OSError(error)

                results = await super()._resolve_host(hostname or host, port, traces=traces)
                for item in results:
                    resolved = _resolved_host_from_item(item)
                    if resolved:
                        resolved_error = validate_public_resolved_host(str(resolved))
                        if resolved_error:
                            raise OSError(resolved_error)
                return results

        kwargs.setdefault("resolver", GuardedResolver())
        return _Connector(**kwargs)


def guarded_tcp_connector(**kwargs: Any):
    """Create an aiohttp connector that validates DNS results before connect."""
    return GuardedTCPConnector(**kwargs)


def is_public_http_url(url: str) -> bool:
    """Return True when *url* is allowed for server-side HTTP requests."""
    return validate_public_http_url(url) is None


__all__ = [
    "GuardedResolver",
    "GuardedTCPConnector",
    "guarded_tcp_connector",
    "is_public_http_url",
    "validate_public_http_url",
    "validate_public_resolved_host",
]
