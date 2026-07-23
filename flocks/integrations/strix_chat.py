"""HTTP client for the local Strix Chat API sidecar."""

from __future__ import annotations

import ipaddress
import os
from typing import Any, Optional
from urllib.parse import urlparse

import httpx


DEFAULT_STRIX_CHAT_BASE_URL = "http://127.0.0.1:8486"


class StrixChatClientError(RuntimeError):
    """Raised when the Strix Chat sidecar cannot fulfill a request."""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class StrixChatClient:
    """Small authenticated client with safe local-only defaults."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("FLOCKS_STRIX_CHAT_BASE_URL") or DEFAULT_STRIX_CHAT_BASE_URL
        ).rstrip("/")
        self.token = token or os.environ.get("FLOCKS_STRIX_CHAT_API_TOKEN")
        _validate_base_url(self.base_url)

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/healthz")

    async def start_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/chat", json=payload)

    async def list_chats(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/chat")

    async def get_chat(self, chat_id: str, *, after: int = 0) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/api/v1/chat/{_chat_id(chat_id)}",
            params={"after": max(0, after)},
        )

    async def send_message(self, chat_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/chat/{_chat_id(chat_id)}/message",
            json=payload,
        )

    async def stop_chat(self, chat_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/v1/chat/{_chat_id(chat_id)}/stop")

    async def delete_chat(self, chat_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/api/v1/chat/{_chat_id(chat_id)}")

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        timeout = httpx.Timeout(30.0, connect=3.0)
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=timeout,
            ) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise StrixChatClientError(
                f"Strix Chat API is unavailable at {self.base_url}: {exc}",
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise StrixChatClientError(
                f"Strix Chat API returned invalid JSON (HTTP {response.status_code})",
            ) from exc
        if response.is_error:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            message = str(detail or f"Strix Chat API returned HTTP {response.status_code}")
            raise StrixChatClientError(message, status_code=response.status_code)
        if not isinstance(payload, dict):
            raise StrixChatClientError("Strix Chat API returned a non-object response")
        return payload


def _validate_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("FLOCKS_STRIX_CHAT_BASE_URL must be an HTTP(S) URL")
    allow_remote = os.environ.get("FLOCKS_STRIX_CHAT_ALLOW_REMOTE", "").lower() in {
        "1",
        "true",
        "yes",
    }
    if allow_remote:
        return
    hostname = parsed.hostname.lower()
    if hostname == "localhost":
        return
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError as exc:
        raise ValueError(
            "Remote Strix Chat URLs require FLOCKS_STRIX_CHAT_ALLOW_REMOTE=true",
        ) from exc
    if not address.is_loopback:
        raise ValueError(
            "Remote Strix Chat URLs require FLOCKS_STRIX_CHAT_ALLOW_REMOTE=true",
        )


def _chat_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or not normalized.startswith("chat-"):
        raise ValueError("Invalid Strix chat id")
    if not all(char.isalnum() or char == "-" for char in normalized):
        raise ValueError("Invalid Strix chat id")
    return normalized
