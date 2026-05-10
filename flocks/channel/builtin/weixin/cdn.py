"""
WeChat CDN (novac2c.cdn.weixin.qq.com) URL builders, SSRF protection,
and raw download / upload helpers for AES-encrypted media payloads.

The CDN protocol:
- Inbound media is fetched from ``/c2c/download?encrypted_query_param=...``
  and decrypted client-side with the AES key embedded in the iLink frame.
- Outbound media is encrypted client-side and uploaded with POST to either
  ``/c2c/upload?encrypted_query_param=<upload_param>&filekey=<filekey>``
  or directly to ``upload_full_url`` returned by ``getuploadurl``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional
from urllib.parse import quote, urlparse

if TYPE_CHECKING:
    import aiohttp


# Hosts the channel is allowed to fetch media from. SSRF guard.
_WEIXIN_CDN_ALLOWLIST: frozenset[str] = frozenset(
    {
        "novac2c.cdn.weixin.qq.com",
        "ilinkai.weixin.qq.com",
        "wx.qlogo.cn",
        "thirdwx.qlogo.cn",
        "res.wx.qq.com",
        "mmbiz.qpic.cn",
        "mmbiz.qlogo.cn",
    }
)


def cdn_download_url(cdn_base_url: str, encrypted_query_param: str) -> str:
    return (
        f"{cdn_base_url.rstrip('/')}/download"
        f"?encrypted_query_param={quote(encrypted_query_param, safe='')}"
    )


def cdn_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    return (
        f"{cdn_base_url.rstrip('/')}/upload"
        f"?encrypted_query_param={quote(upload_param, safe='')}"
        f"&filekey={quote(filekey, safe='')}"
    )


def assert_weixin_cdn_url(url: str) -> None:
    """Raise ``ValueError`` if *url* is not on a known WeChat CDN host.

    Used as an SSRF guard before fetching ``full_url`` (which the iLink
    server controls) — without this, a malicious frame could redirect
    downloads to arbitrary internal hosts.
    """
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Unparseable media URL: {url!r}") from exc

    if scheme not in ("http", "https"):
        raise ValueError(
            f"Media URL has disallowed scheme {scheme!r}; only http/https permitted."
        )
    if host not in _WEIXIN_CDN_ALLOWLIST:
        raise ValueError(
            f"Media URL host {host!r} is not in the WeChat CDN allowlist. "
            "Refusing to fetch to prevent SSRF."
        )


async def download_bytes(
    session: "aiohttp.ClientSession",
    *,
    url: str,
    timeout_seconds: float = 60.0,
) -> bytes:
    """GET *url* and return the response body bytes.

    Uses ``asyncio.wait_for`` rather than ``aiohttp.ClientTimeout`` so the
    coroutine can be safely scheduled via ``run_coroutine_threadsafe`` from
    callers running outside the aiohttp event loop.
    """
    async def _do() -> bytes:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()
    return await asyncio.wait_for(_do(), timeout=timeout_seconds)


async def upload_ciphertext(
    session: "aiohttp.ClientSession",
    *,
    ciphertext: bytes,
    upload_url: str,
    timeout_seconds: float = 120.0,
) -> str:
    """POST encrypted bytes to the WeChat CDN, return ``x-encrypted-param`` echo.

    Both the constructed CDN URL (from ``upload_param``) and the direct
    ``upload_full_url`` use POST with the raw ciphertext as the body.
    """
    async def _do() -> str:
        async with session.post(
            upload_url,
            data=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
        ) as resp:
            if resp.status == 200:
                encrypted_param = resp.headers.get("x-encrypted-param")
                if encrypted_param:
                    await resp.read()
                    return encrypted_param
                raw = await resp.text()
                raise RuntimeError(f"CDN upload missing x-encrypted-param header: {raw[:200]}")
            raw = await resp.text()
            raise RuntimeError(f"CDN upload HTTP {resp.status}: {raw[:200]}")
    return await asyncio.wait_for(_do(), timeout=timeout_seconds)


def media_reference(item: dict, key: str) -> dict:
    """Pull the ``.media`` sub-dict out of an item like ``image_item``/``file_item``."""
    return (item.get(key) or {}).get("media") or {}


async def download_and_decrypt_media(
    session: "aiohttp.ClientSession",
    *,
    cdn_base_url: str,
    encrypted_query_param: Optional[str],
    aes_key_b64: Optional[str],
    full_url: Optional[str],
    timeout_seconds: float,
) -> bytes:
    """Fetch + AES-decrypt a single media payload.

    Caller supplies whichever of ``encrypted_query_param`` / ``full_url`` is
    present in the iLink frame.  ``aes_key_b64`` is decoded by ``crypto.parse_aes_key``.
    """
    # Local import to avoid a circular dependency between cdn and crypto.
    from .crypto import aes128_ecb_decrypt, parse_aes_key

    if encrypted_query_param:
        raw = await download_bytes(
            session,
            url=cdn_download_url(cdn_base_url, encrypted_query_param),
            timeout_seconds=timeout_seconds,
        )
    elif full_url:
        assert_weixin_cdn_url(full_url)
        raw = await download_bytes(session, url=full_url, timeout_seconds=timeout_seconds)
    else:
        raise RuntimeError("media item had neither encrypt_query_param nor full_url")

    if aes_key_b64:
        raw = aes128_ecb_decrypt(raw, parse_aes_key(aes_key_b64))
    return raw
