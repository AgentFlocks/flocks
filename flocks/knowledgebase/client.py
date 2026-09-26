"""Same-origin BFF transport to the knowledgebase API, never to RAGFlow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from .errors import KnowledgebaseError, unavailable

_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True)
class Connection:
    base_url: str
    api_token: str
    timeout_seconds: float = 30.0
    max_upload_bytes: int = 32 * 1024 * 1024

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Knowledgebase connection requires an HTTP(S) URL without embedded credentials")
        if len(self.api_token) < 32 or any(ord(char) < 33 or ord(char) > 126 for char in self.api_token):
            raise ValueError("Knowledgebase service token must be nonblank ASCII and at least 32 characters")
        if self.timeout_seconds <= 0 or self.max_upload_bytes <= 0:
            raise ValueError("Knowledgebase request bounds must be positive")

    @property
    def origin(self) -> str:
        return self.base_url.rstrip("/")


class KnowledgebaseClient:
    def __init__(self, connection: Connection, *, transport: httpx.AsyncBaseTransport | None = None):
        self.connection = connection
        self._http = httpx.AsyncClient(
            base_url=connection.origin + "/",
            timeout=connection.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={"Authorization": "Bearer " + connection.api_token},
        )

    async def close(self) -> None:
        await self._http.aclose()

    @staticmethod
    def resource_id(value: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise KnowledgebaseError(422, "invalid_resource_id", "A resource ID is required.")
        return quote(value, safe="")

    def _failure(self, response: httpx.Response) -> KnowledgebaseError:
        if response.status_code in {401, 403}:
            return unavailable("knowledgebase_authentication_failed")
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        if not isinstance(code, str) or not isinstance(message, str):
            return unavailable()
        if self.connection.api_token in code or self.connection.api_token in message:
            return unavailable()
        return KnowledgebaseError(response.status_code, code, message)

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, path.lstrip("/"), **kwargs)
        except httpx.HTTPError:
            raise unavailable() from None
        if response.status_code >= 400:
            raise self._failure(response)
        if response.status_code == 204:
            return None
        try:
            body = response.json()
        except ValueError:
            raise unavailable("knowledgebase_invalid_response") from None
        if not isinstance(body, dict) or "data" not in body:
            raise unavailable("knowledgebase_invalid_response")
        return body["data"]

    async def files(self, *, page: int = 1, page_size: int = 20, q: str | None = None) -> dict:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if q:
            params["q"] = q
        return await self.request("GET", "/v1/files", params=params)

    async def upload(self, filename: str, content: bytes, content_type: str) -> dict:
        if len(content) > self.connection.max_upload_bytes:
            raise KnowledgebaseError(413, "request_too_large", "The file exceeds the upload limit.")
        return await self.request(
            "POST",
            "/v1/files",
            files={"file": (filename, content, content_type or "application/octet-stream")},
        )

    async def content(self, file_id: str) -> tuple[bytes, str]:
        ident = self.resource_id(file_id)
        try:
            response = await self._http.get(f"v1/files/{ident}/content")
        except httpx.HTTPError:
            raise unavailable() from None
        if response.status_code >= 400:
            raise self._failure(response)
        media = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        return response.content, media

    async def delete_file(self, file_id: str) -> None:
        await self.request("DELETE", f"/v1/files/{self.resource_id(file_id)}")

    async def datasets(self, *, page: int = 1, page_size: int = 20, q: str | None = None) -> dict:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if q:
            params["q"] = q
        return await self.request("GET", "/v1/datasets", params=params)

    async def dataset(self, dataset_id: str) -> dict:
        return await self.request("GET", f"/v1/datasets/{self.resource_id(dataset_id)}")

    async def create_dataset(self, payload: dict) -> dict:
        return await self.request("POST", "/v1/datasets", json=payload)

    async def delete_dataset(self, dataset_id: str) -> None:
        await self.request("DELETE", f"/v1/datasets/{self.resource_id(dataset_id)}")

    async def documents(self, dataset_id: str, *, page: int = 1, page_size: int = 20, q: str | None = None) -> dict:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if q:
            params["q"] = q
        return await self.request("GET", f"/v1/datasets/{self.resource_id(dataset_id)}/documents", params=params)

    async def link_files(self, dataset_id: str, file_ids: list[str]) -> dict:
        return await self.request(
            "POST",
            f"/v1/datasets/{self.resource_id(dataset_id)}/files",
            json={"file_ids": file_ids},
        )

    async def remove_documents(self, dataset_id: str, document_ids: list[str]) -> dict:
        return await self.request(
            "DELETE",
            f"/v1/datasets/{self.resource_id(dataset_id)}/documents",
            json={"document_ids": document_ids},
        )

    async def parse(self, dataset_id: str, document_ids: list[str]) -> dict:
        return await self.request(
            "POST",
            f"/v1/datasets/{self.resource_id(dataset_id)}/parse",
            json={"document_ids": document_ids},
        )

    async def retrieve(self, payload: dict) -> dict:
        return await self.request("POST", "/v1/retrieval", json=payload)
