"""Shared workflow service port parsing helpers."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Optional
from urllib.parse import urlparse

SERVICE_PORT_KEYS = ("port", "servicePort", "hostPort")
SERVICE_URL_KEYS = ("serviceUrl", "invokeUrl")


def _valid_port(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        port = int(value or 0)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _iter_service_ports(record: Any, keys: Sequence[str]) -> Iterator[int]:
    if not isinstance(record, Mapping):
        return
    for key in keys:
        port = _valid_port(record.get(key))
        if port is not None:
            yield port
    for key in SERVICE_URL_KEYS:
        try:
            port = urlparse(str(record.get(key) or "")).port
        except (TypeError, ValueError):
            port = None
        if port is not None:
            yield port


def resolve_service_port(
    record: Any,
    *,
    keys: Sequence[str] = SERVICE_PORT_KEYS,
) -> Optional[int]:
    """Resolve the first valid explicit or URL-derived port in a service record."""
    return next(_iter_service_ports(record, keys), None)


def collect_service_ports(record: Any) -> set[int]:
    """Collect every valid port represented by a service record."""
    return set(_iter_service_ports(record, SERVICE_PORT_KEYS))
