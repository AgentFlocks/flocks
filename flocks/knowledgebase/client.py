"""Core knowledgebase interface backed directly by the RAGFlow adapter."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ValidationError
from starlette.datastructures import Headers, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import JSONResponse

from .errors import KBError, KnowledgebaseError, unavailable
from .ragflow import RagflowAdapter
from .schemas import DatasetCreate, Documents, LinkFiles, ListQuery, Retrieval
from .service import KnowledgeAPI

_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def validate_connection_options(base_url: str, timeout_seconds: float, max_upload_bytes: int) -> None:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Knowledgebase connection requires an HTTP(S) URL without embedded credentials")
    if (
        not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or type(max_upload_bytes) is not int
        or max_upload_bytes <= 0
    ):
        raise ValueError("Knowledgebase request bounds must be positive")


@dataclass(frozen=True)
class Connection:
    base_url: str
    api_token: str
    timeout_seconds: float = 30.0
    max_upload_bytes: int = 32 * 1024 * 1024

    def __post_init__(self):
        validate_connection_options(self.base_url, self.timeout_seconds, self.max_upload_bytes)
        if (
            not isinstance(self.api_token, str)
            or len(self.api_token) < 32
            or any(ord(char) < 33 or ord(char) > 126 for char in self.api_token)
        ):
            raise ValueError("Knowledge engine credential must be nonblank ASCII and at least 32 characters")

    @property
    def origin(self) -> str:
        return self.base_url.rstrip("/")


class KnowledgebaseClient:
    def __init__(self, connection: Connection, *, transport: httpx.AsyncBaseTransport | None = None):
        self.connection = connection
        self._adapter = RagflowAdapter(
            connection.origin,
            connection.api_token,
            timeout=connection.timeout_seconds,
            transport=transport,
            max_content_bytes=connection.max_upload_bytes,
        )
        self._http = self._adapter._client
        self._api = KnowledgeAPI(self._adapter)

    async def close(self) -> None:
        try:
            await self._adapter.close()
        except KBError as exc:
            raise KnowledgebaseError(exc.status, exc.code, exc.message) from None

    @staticmethod
    def resource_id(value: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise KnowledgebaseError(422, "invalid_resource_id", "A resource ID is required.")
        return quote(value, safe="")

    @staticmethod
    def _validate(model: type[BaseModel], value: Any) -> Any:
        try:
            return model.model_validate(value)
        except ValidationError:
            raise KnowledgebaseError(422, "validation_error", "Invalid request fields.") from None

    def _payload(self, model: type[BaseModel], payload: Any) -> Any:
        # Preserve the former JSON request boundary before applying the same schemas.
        request = httpx.Request("POST", "http://knowledgebase.invalid/", json=payload)
        if len(request.content) > self.connection.max_upload_bytes + 512 * 1024:
            raise KnowledgebaseError(413, "request_too_large", "The request exceeds the configured body limit.")
        if not request.content:
            raise KnowledgebaseError(422, "validation_error", "Invalid request fields.")
        return self._validate(model, json.loads(request.content))

    @classmethod
    def _query(cls, page: int, page_size: int, q: str | None) -> ListQuery:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if q:
            params["q"] = q
        query = httpx.QueryParams(str(httpx.QueryParams(params)))
        return cls._validate(ListQuery, dict(query.multi_items()))

    async def _call(self, operation: Awaitable[Any], *, download: bool = False) -> Any:
        try:
            result = await operation
            if download:
                body, media = result
                return body, media.split(";", 1)[0].strip() or "application/octet-stream"
            # The removed service serialized responses before Core consumed them.
            return json.loads(JSONResponse(result).body)
        except KBError as exc:
            raise KnowledgebaseError(exc.status, exc.code, exc.message) from None
        except Exception:
            raise KnowledgebaseError(500, "internal_error", "The service could not complete this request.") from None

    async def _upload_metadata(self, filename: str, content_type: str, content_size: int) -> tuple[str, str]:
        # Round-trip only headers, not file bytes, through the former multipart boundary.
        request = httpx.Request(
            "POST",
            "http://knowledgebase.invalid/",
            files={"file": (filename, b"", content_type or "application/octet-stream")},
        )
        encoded = request.read()
        if len(encoded) + content_size > self.connection.max_upload_bytes + 512 * 1024:
            raise KnowledgebaseError(413, "request_too_large", "The request exceeds the configured body limit.")

        async def stream():
            yield encoded

        try:
            form = await MultiPartParser(Headers(request.headers), stream()).parse()
        except MultiPartException:
            raise unavailable() from None
        try:
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise KnowledgebaseError(422, "validation_error", "Invalid request fields.")
            return file.filename or "upload", file.content_type or "application/octet-stream"
        finally:
            await form.close()

    async def files(self, *, page: int = 1, page_size: int = 20, q: str | None = None) -> dict:
        query = self._query(page, page_size, q)
        return await self._call(self._api.list_files(
            page=query.page, page_size=query.page_size, keywords=query.q or None
        ))

    async def upload(self, filename: str, content: bytes, content_type: str) -> dict:
        if len(content) > self.connection.max_upload_bytes:
            raise KnowledgebaseError(413, "request_too_large", "The file exceeds the upload limit.")
        name, media = await self._upload_metadata(filename, content_type, len(content))
        if "/" in name or "\\" in name or name in {".", ".."} or len(name) > 255:
            raise KnowledgebaseError(400, "invalid_request", "A file name is required.")
        return await self._call(self._api.upload(name, content, media))

    async def content(self, file_id: str) -> tuple[bytes, str]:
        ident = self.resource_id(file_id)
        return await self._call(self._api.download(ident), download=True)

    async def delete_file(self, file_id: str) -> None:
        ident = self.resource_id(file_id)
        await self._call(self._api.delete_file(ident))

    async def datasets(self, *, page: int = 1, page_size: int = 20, q: str | None = None) -> dict:
        query = self._query(page, page_size, q)
        return await self._call(self._api.list_datasets(
            page=query.page, page_size=query.page_size, keywords=query.q or None
        ))

    async def dataset(self, dataset_id: str) -> dict:
        ident = self.resource_id(dataset_id)
        return await self._call(self._api.get_dataset(ident))

    async def create_dataset(self, payload: dict) -> dict:
        model = self._payload(DatasetCreate, payload)
        return await self._call(self._api.create_dataset(model))

    async def delete_dataset(self, dataset_id: str) -> None:
        ident = self.resource_id(dataset_id)
        await self._call(self._api.delete_dataset(ident))

    async def documents(self, dataset_id: str, *, page: int = 1, page_size: int = 20, q: str | None = None) -> dict:
        ident = self.resource_id(dataset_id)
        query = self._query(page, page_size, q)
        return await self._call(self._api.list_documents(
            ident, page=query.page, page_size=query.page_size, keywords=query.q or None
        ))

    async def link_files(self, dataset_id: str, file_ids: list[str]) -> dict:
        ident = self.resource_id(dataset_id)
        model = self._payload(LinkFiles, {"file_ids": file_ids})
        return await self._call(self._api.link_files(ident, model))

    async def remove_documents(self, dataset_id: str, document_ids: list[str]) -> dict:
        ident = self.resource_id(dataset_id)
        model = self._payload(Documents, {"document_ids": document_ids})
        return await self._call(self._api.remove_documents(ident, model))

    async def parse(self, dataset_id: str, document_ids: list[str]) -> dict:
        ident = self.resource_id(dataset_id)
        model = self._payload(Documents, {"document_ids": document_ids})
        return await self._call(self._api.parse(ident, model))

    async def retrieve(self, payload: dict) -> dict:
        model = self._payload(Retrieval, payload)
        return await self._call(self._api.retrieve(model))
