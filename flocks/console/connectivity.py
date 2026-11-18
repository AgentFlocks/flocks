"""
Whether this install can reach Console now.

The licence pages offer what works from here: signing in to a cloud account, applying online and syncing
with Console when Console answers; an application file to hand over and a licence file to import when it
does not (an install inside a network that does not reach Console, or one set up with no Console at all).

Console answers ``GET /health``. Any answer from it counts (an older Console without that route answers
404, which still says the way there is open); no answer, a timeout, or a gateway's 5xx in its place does
not. The answer is kept for a short while, so a page that asks on every visit does not ask Console every
time; asking again with ``refresh`` checks now.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from flocks.utils.log import Log

log = Log.create(service="console.connectivity")

#: How long an answer is kept.
CACHE_SECONDS = 30
#: How long Console has to answer: long enough for a slow link, short enough for a page that waits.
TIMEOUT_SECONDS = 4

_lock = asyncio.Lock()
#: (base URL asked, monotonic time asked, answer)
_kept: tuple[str, float, dict[str, Any]] | None = None


def _base_url() -> str:
    from flocks.console.login import ConsoleLoginService

    return ConsoleLoginService.console_base_url()


def _host(base: str) -> str:
    try:
        return urlsplit(base).hostname or ""
    except ValueError:
        return ""


async def _probe(base: str) -> tuple[bool, str | None]:
    """Whether Console answers at *base*, and why not when it does not (for the log, not the page)."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
            resp = await client.get(f"{base}/health")
    except httpx.HTTPError as exc:
        return False, str(exc) or type(exc).__name__
    if resp.status_code >= 500:
        return False, f"HTTP {resp.status_code}"
    return True, None


async def console_connectivity(*, refresh: bool = False) -> dict[str, Any]:
    """``configured``: a Console address is set; ``online``: Console answered just now (or within
    CACHE_SECONDS); ``console_host``: the host asked; ``checked_at``: when it was asked."""
    global _kept
    base = _base_url()
    if not base:
        return {"configured": False, "online": False, "console_host": "", "checked_at": datetime.now(UTC).isoformat()}
    async with _lock:
        if not refresh and _kept is not None:
            kept_base, asked_at, answer = _kept
            if kept_base == base and time.monotonic() - asked_at < CACHE_SECONDS:
                return dict(answer)
        online, why = await _probe(base)
        if not online:
            log.info("console.unreachable", {"host": _host(base), "reason": why})
        answer = {
            "configured": True,
            "online": online,
            "console_host": _host(base),
            "checked_at": datetime.now(UTC).isoformat(),
        }
        _kept = (base, time.monotonic(), answer)
        return dict(answer)


def forget() -> None:
    """Drop the kept answer (tests; a changed address is noticed by itself)."""
    global _kept
    _kept = None
