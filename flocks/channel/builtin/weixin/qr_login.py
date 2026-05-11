"""
iLink Bot QR-code login flow for the Weixin channel.

Two API endpoints are exposed by the Flocks server so the web UI can:

1. ``POST /api/channel/weixin/qr-login/start``
   → Call ``ilink/bot/get_bot_qrcode`` (no token required — this *is* the
     pre-auth step).  Returns ``{qrcode_value, qrcode_url}`` so the frontend
     can render a QR code with e.g. ``qrcode.react``.

2. ``GET /api/channel/weixin/qr-login/status?qrcode=<hex>``
   → Poll ``ilink/bot/get_qrcode_status``.  Returns
     ``{status, account_id, token}`` where ``status`` is one of:
       "waiting"  — waiting for scan
       "scaned"   — phone scanned, waiting for phone confirmation tap
       "confirmed"— login complete; ``account_id`` and ``token`` populated
       "expired"  — QR code expired; frontend should call /start again

These helpers are pure async functions that accept an explicit ``base_url``
so callers can override the iLink endpoint without touching global state.
"""

from __future__ import annotations

import json
import ssl
from typing import Optional

from .config import (
    EP_GET_QR_STATUS,
    EP_GET_BOT_QR,
    ILINK_BASE_URL,
    ILINK_APP_ID,
    ILINK_APP_CLIENT_VERSION,
    CHANNEL_VERSION,
    QR_TIMEOUT_MS,
)


# ---------------------------------------------------------------------------
# HTTP helpers (no aiohttp session shared with the channel — login creates
# a throwaway session so it doesn't interfere with the poll loop)
# ---------------------------------------------------------------------------

def _make_ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return True  # aiohttp default


def _login_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }


async def _api_get(base_url: str, endpoint: str) -> dict:
    """Simple GET against the iLink API with a short timeout."""
    import aiohttp

    url = f"{base_url.rstrip('/')}/{endpoint}"
    timeout = aiohttp.ClientTimeout(total=QR_TIMEOUT_MS / 1000)
    connector = aiohttp.TCPConnector(ssl=_make_ssl_ctx())
    async with aiohttp.ClientSession(
        trust_env=True, connector=connector
    ) as session:
        async with session.get(
            url, headers=_login_headers(), timeout=timeout
        ) as resp:
            raw = await resp.text()
            if not resp.ok:
                raise RuntimeError(
                    f"iLink GET {endpoint} HTTP {resp.status}: {raw[:200]}"
                )
            return json.loads(raw)


# ---------------------------------------------------------------------------
# Public helpers called by the route handlers
# ---------------------------------------------------------------------------

async def start_qr_login(
    base_url: str = ILINK_BASE_URL,
    bot_type: str = "3",
) -> dict:
    """Request a fresh QR code from iLink.

    Returns ``{"qrcode_value": str, "qrcode_url": str}`` where
    - ``qrcode_value`` is the raw hex token used to poll status
    - ``qrcode_url``   is the WeChat mini-app URL to encode in the rendered QR
    """
    resp = await _api_get(
        base_url,
        f"{EP_GET_BOT_QR}?bot_type={bot_type}",
    )
    qrcode_value: str = str(resp.get("qrcode") or "")
    qrcode_url: str = str(resp.get("qrcode_img_content") or "")
    if not qrcode_value:
        raise RuntimeError(
            f"iLink get_bot_qrcode returned no qrcode field: {resp}"
        )
    # WeChat must scan the full mini-app URL, not the raw hex token.
    scan_data = qrcode_url if qrcode_url else qrcode_value
    return {
        "qrcode_value": qrcode_value,
        "qrcode_url": scan_data,
    }


async def poll_qr_status(
    qrcode_value: str,
    base_url: str = ILINK_BASE_URL,
) -> dict:
    """Poll the QR code status once.

    Returns one of::

        {"status": "waiting"}
        {"status": "scaned"}
        {"status": "expired"}
        {"status": "redirect", "redirect_base_url": "https://..."}
        {"status": "confirmed", "account_id": "...", "token": "...",
         "base_url": "https://..."}

    ``redirect`` is returned when iLink routes the account to a regional node.
    The frontend must pass the new ``redirect_base_url`` as ``base_url`` for all
    subsequent calls so that the final ``confirmed`` response comes from the
    correct node.  It must also persist ``base_url`` from ``confirmed`` into the
    channel config — otherwise the long-poll loop will connect to the wrong node.

    The caller (route handler) is responsible for looping / error handling.
    """
    resp = await _api_get(
        base_url,
        f"{EP_GET_QR_STATUS}?qrcode={qrcode_value}",
    )
    status: str = str(resp.get("status") or "waiting").lower()

    if status == "confirmed":
        account_id = str(resp.get("ilink_bot_id") or "")
        token = str(resp.get("bot_token") or "")
        # iLink returns the canonical base_url for this account on confirmed.
        # This may differ from ILINK_BASE_URL for accounts on regional nodes.
        confirmed_base_url = str(resp.get("baseurl") or "").rstrip("/") or base_url
        if not account_id or not token:
            raise RuntimeError(
                f"QR confirmed but missing credentials: {resp}"
            )
        return {
            "status": "confirmed",
            "account_id": account_id,
            "token": token,
            "base_url": confirmed_base_url,
        }

    if status == "scaned_but_redirect":
        redirect_host = str(resp.get("redirect_host") or "").strip()
        redirect_base_url = (
            f"https://{redirect_host}" if redirect_host else base_url
        )
        return {"status": "redirect", "redirect_base_url": redirect_base_url}

    if status == "scaned":
        return {"status": "scaned"}
    if status == "expired":
        return {"status": "expired"}
    return {"status": "waiting"}
