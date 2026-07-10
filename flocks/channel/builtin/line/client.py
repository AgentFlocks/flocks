"""Async LINE Messaging API client."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx


class LineApiError(RuntimeError):
    """LINE API request failed."""

    def __init__(self, message: str, *, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = status_code in {0, 408, 409, 425, 429, 500, 502, 503, 504}


class LineClient:
    def __init__(
        self,
        channel_access_token: str,
        *,
        api_root: str,
        data_api_root: str,
        timeout: float = 30.0,
    ) -> None:
        self._token = channel_access_token
        self._api_root = api_root.rstrip("/")
        self._data_api_root = data_api_root.rstrip("/")
        self._timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def reply(self, reply_token: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return await self._post(
            f"{self._api_root}/v2/bot/message/reply",
            {"replyToken": reply_token, "messages": messages},
        )

    async def push(self, to: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return await self._post(
            f"{self._api_root}/v2/bot/message/push",
            {"to": to, "messages": messages},
        )

    async def get_bot_info(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=min(self._timeout, 10.0)) as client:
            resp = await client.get(
                f"{self._api_root}/v2/bot/info",
                headers={"Authorization": f"Bearer {self._token}"},
            )
        return self._decode(resp, "LINE get bot info failed")

    async def fetch_content(self, message_id: str, *, max_bytes: int) -> tuple[bytes, str]:
        url = f"{self._data_api_root}/v2/bot/message/{message_id}/content"
        chunks: list[bytes] = []
        total = 0
        content_type = "application/octet-stream"
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            async with client.stream(
                "GET",
                url,
                headers={"Authorization": f"Bearer {self._token}"},
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise LineApiError(
                        f"LINE content {resp.status_code}: {body[:200].decode(errors='replace')}",
                        status_code=resp.status_code,
                    )
                content_type = resp.headers.get("content-type") or content_type
                content_length = resp.headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                    raise ValueError(f"LINE inbound media too large: >{max_bytes} bytes")
                async for chunk in resp.aiter_bytes(8192):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"LINE inbound media too large: >{max_bytes} bytes")
                    chunks.append(chunk)
        return b"".join(chunks), content_type.split(";", 1)[0].strip() or "application/octet-stream"

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, headers=self.headers, json=payload)
        except httpx.HTTPError as exc:
            raise LineApiError(f"LINE request failed: {exc}") from exc
        return self._decode(resp, "LINE request failed")

    @staticmethod
    def _decode(resp: httpx.Response, prefix: str) -> dict[str, Any]:
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            message = data.get("message") or data.get("error") or resp.text
            raise LineApiError(
                f"{prefix}: HTTP {resp.status_code}: {str(message)[:200]}",
                status_code=resp.status_code,
            )
        return data if isinstance(data, dict) else {}


async def maybe_get_bot_user_id(client: Optional[LineClient]) -> Optional[str]:
    if client is None:
        return None
    try:
        info = await client.get_bot_info()
    except Exception:
        return None
    user_id = info.get("userId")
    return str(user_id).strip() if user_id else None

