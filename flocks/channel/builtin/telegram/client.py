"""
Shared async HTTP client for Telegram Bot API.

A single ``httpx.AsyncClient`` is reused across all requests to enable
TCP connection reuse.  Created lazily on first use and can be closed via
``close_http_client()``.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx


_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_HTTP_LOCK = asyncio.Lock()


async def get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None and not _HTTP_CLIENT.is_closed:
        return _HTTP_CLIENT
    async with _HTTP_LOCK:
        if _HTTP_CLIENT is not None and not _HTTP_CLIENT.is_closed:
            return _HTTP_CLIENT
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        return _HTTP_CLIENT


async def close_http_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        try:
            await _HTTP_CLIENT.aclose()
        except Exception:
            pass
        _HTTP_CLIENT = None
