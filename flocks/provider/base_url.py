"""Validation for user-supplied provider / API-service base URLs.

Only ``http`` / ``https`` URLs with a host are accepted.  Link-local
addresses (cloud metadata endpoints such as ``169.254.169.254``) and the
unspecified address are rejected outright.  Loopback and RFC1918 ranges stay
allowed because self-hosted gateways (Ollama, vLLM, internal proxies) are a
supported deployment, but they are logged so operators can audit them.
"""

from __future__ import annotations

import ipaddress
from typing import Optional
from urllib.parse import urlsplit

from flocks.utils.log import Log

log = Log.create(service="provider.base_url")

_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTNAMES = {"metadata.google.internal", "metadata"}


class BaseURLValidationError(ValueError):
    """Raised when a base URL is malformed or points at a forbidden target."""


def validate_provider_base_url(value: str, *, field: str = "base_url") -> str:
    """Return the stripped URL or raise :class:`BaseURLValidationError`."""
    url = (value or "").strip()
    if not url:
        raise BaseURLValidationError(f"{field} must not be empty")

    try:
        parts = urlsplit(url)
    except ValueError as exc:  # e.g. an unterminated IPv6 literal
        raise BaseURLValidationError(f"{field} is not a valid URL") from exc
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise BaseURLValidationError(f"{field} must use http or https")

    try:
        host = parts.hostname
    except ValueError as exc:  # malformed IPv6 literal / port
        raise BaseURLValidationError(f"{field} is not a valid URL") from exc
    if not host:
        raise BaseURLValidationError(f"{field} must include a host")
    try:
        parts.port  # noqa: B018 - raises ValueError on an invalid port
    except ValueError as exc:
        raise BaseURLValidationError(f"{field} has an invalid port") from exc

    if host.lower() in _BLOCKED_HOSTNAMES:
        raise BaseURLValidationError(f"{field} must not point at a metadata endpoint")

    address: Optional[ipaddress._BaseAddress]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None

    if address is not None:
        if address.is_link_local or address.is_unspecified:
            raise BaseURLValidationError(
                f"{field} must not point at a link-local or unspecified address"
            )
        if address.is_loopback or address.is_private:
            log.warning("provider.base_url.private_network", {
                "field": field,
                "host": host,
            })
    elif host.lower() in {"localhost"} or host.lower().endswith((".local", ".internal")):
        log.warning("provider.base_url.private_network", {
            "field": field,
            "host": host,
        })

    return url
