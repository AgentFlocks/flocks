"""Asynchronous, credential-safe adapter for the RAGFlow v0.27.2 REST API."""

from __future__ import annotations

import json as jsonlib
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, BinaryIO
from urllib.parse import quote

import httpx

from .errors import KBError, UpstreamError


class RagflowClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
        max_content_bytes: int = 33554432,
    ):
        try:
            url = httpx.URL(base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.host
                or url.userinfo
                or url.query
                or url.fragment
                or not math.isfinite(timeout)
                or timeout <= 0
                or type(max_content_bytes) is not int
                or max_content_bytes <= 0
            ):
                raise ValueError
            self._client = httpx.AsyncClient(
                base_url=str(url).rstrip("/") + "/",
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                timeout=timeout,
                transport=transport,
                trust_env=False,
                follow_redirects=False,
            )
        except Exception:
            raise KBError(
                500, "invalid_upstream_configuration", "The knowledge engine configuration is invalid."
            ) from None
        self._max_content_bytes = max_content_bytes

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            raise UpstreamError() from None

    @staticmethod
    def _segment(value: str) -> str:
        if not isinstance(value, str) or not value:
            raise KBError(400, "invalid_request", "A resource identifier is required.")
        # quote() leaves dots unescaped; standalone dot segments must not change the path.
        try:
            return quote(value, safe="") if value not in {".", ".."} else value.replace(".", "%2E")
        except UnicodeError:
            raise KBError(400, "invalid_request", "The resource identifier is invalid.") from None

    def _path(self, path: str) -> str:
        """Join below the deployment prefix, never relative to the origin root."""
        if not isinstance(path, str) or path.startswith("//"):
            raise KBError(400, "invalid_request", "An API-relative path is required.")
        relative = path.lstrip("/")
        parsed = httpx.URL(relative)
        if parsed.scheme or parsed.host or parsed.query or parsed.fragment or "\\" in relative:
            raise KBError(400, "invalid_request", "An API-relative path is required.")
        if any(part in {".", ".."} for part in relative.split("/")):
            raise KBError(400, "invalid_request", "An API-relative path is required.")
        # Accept an explicitly configured API base as well as an origin/deployment base.
        if self._client.base_url.path.rstrip("/").endswith("/api/v1") and relative.startswith("api/v1/"):
            relative = relative[len("api/v1/") :]
        return relative

    @asynccontextmanager
    async def _stream(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Any = None,
        files: Any = None,
        data: Any = None,
        authenticated: bool = True,
    ) -> AsyncIterator[httpx.Response]:
        # Keep transport, serialization, decoding and caller-owned file errors behind
        # this boundary. Never attach the HTTPX exception (which can contain secrets).
        try:
            request = self._client.build_request(
                method, self._path(path), json=json, params=params, files=files, data=data
            )
            if not authenticated:
                request.headers.pop("Authorization", None)
            response = await self._client.send(request, stream=True)
            try:
                yield response
            finally:
                await response.aclose()
        except KBError:
            raise
        except Exception:
            raise UpstreamError() from None

    async def _read_limited(self, response: httpx.Response) -> bytes:
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                size = int(length)
            except ValueError:
                raise UpstreamError("upstream_invalid_response") from None
            if size < 0:
                raise UpstreamError("upstream_invalid_response")
            if size > self._max_content_bytes:
                raise UpstreamError("upstream_content_too_large")
        content = bytearray()
        async for chunk in response.aiter_bytes():
            if len(chunk) > self._max_content_bytes - len(content):
                raise UpstreamError("upstream_content_too_large")
            content.extend(chunk)
        return bytes(content)

    @staticmethod
    def _json(content: bytes) -> Any:
        try:
            return jsonlib.loads(content)
        except (ValueError, UnicodeError, RecursionError):
            raise UpstreamError("upstream_invalid_response") from None

    @staticmethod
    def _has_partial_errors(envelope: dict[str, Any]) -> bool:
        # Inspect result-level errors only, not arbitrary document/chunk metadata.
        data = envelope.get("data")
        results = [envelope, *(data if isinstance(data, list) else [data])]
        return any(
            isinstance(result, dict)
            and any(
                result.get(key)
                for key in ("error", "errors", "partial_errors", "failures", "failed_ids", "failed_count")
            )
            for result in results
        )

    @classmethod
    def _envelope(cls, status: int, content: bytes, *, dataset_lookup: bool = False) -> dict[str, Any]:
        try:
            envelope = cls._json(content)
        except UpstreamError:
            if dataset_lookup and status == 404:
                raise KBError(404, "not_found", "The dataset was not found.") from None
            if not 200 <= status < 300:
                raise UpstreamError(upstream_code=status) from None
            raise
        upstream_code = envelope.get("code") if isinstance(envelope, dict) else None
        if type(upstream_code) is not int:
            upstream_code = None
        if dataset_lookup:
            message = envelope.get("message") if isinstance(envelope, dict) else None
            missing = isinstance(message, str) and (
                message == "Invalid Dataset ID"
                or (
                    message.startswith("User '")
                    and "' lacks permission for dataset '" in message
                    and message.endswith("'")
                )
            )
            if status == 404 or (200 <= status < 300 and (upstream_code == 404 or (upstream_code == 102 and missing))):
                raise KBError(404, "not_found", "The dataset was not found.")
        if isinstance(envelope, dict) and cls._has_partial_errors(envelope):
            raise UpstreamError("upstream_partial_failure", upstream_code=upstream_code)
        if not 200 <= status < 300:
            raise UpstreamError(upstream_code=upstream_code if upstream_code not in {None, 0} else status)
        if not isinstance(envelope, dict) or upstream_code is None:
            raise UpstreamError("upstream_invalid_response")
        if upstream_code != 0:
            raise UpstreamError(upstream_code=upstream_code)
        if "message" in envelope and not isinstance(envelope["message"], str):
            raise UpstreamError("upstream_invalid_response")
        return envelope

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Any = None,
        files: Any = None,
        data: Any = None,
    ) -> dict:
        """Return the full, validated envelope, including top-level pagination."""
        async with self._stream(method, path, json=json, params=params, files=files, data=data) as response:
            return self._envelope(response.status_code, await self._read_limited(response))

    @staticmethod
    def _object(envelope: dict, *, mutation: bool = False) -> dict:
        data = envelope.get("data")
        if mutation and (data is None or data is True):
            return {}
        if not isinstance(data, dict):
            raise UpstreamError("upstream_invalid_response")
        return data

    @staticmethod
    def _list(envelope: dict) -> list[dict]:
        data = envelope.get("data")
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise UpstreamError("upstream_invalid_response")
        return data

    @classmethod
    def _page(cls, envelope: dict, items_key: str) -> dict:
        data = cls._object(envelope)
        cls._list({"data": data.get(items_key)})
        if type(data.get("total")) is not int or data["total"] < 0:
            raise UpstreamError("upstream_invalid_response")
        return data

    @staticmethod
    def _mutation(envelope: dict) -> None:
        if envelope.get("data") is False:
            raise UpstreamError("upstream_invalid_response")

    @staticmethod
    def _ids(ids: list[str]) -> list[str]:
        # Several upstream DELETE endpoints treat omitted/empty IDs as "delete all".
        if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item for item in ids):
            raise KBError(400, "invalid_request", "At least one resource identifier is required.")
        return ids

    async def health(self) -> dict:
        """Read the public health endpoint; its response has no API envelope."""
        async with self._stream("GET", "/api/v1/system/healthz", authenticated=False) as response:
            if not 200 <= response.status_code < 300:
                raise UpstreamError(upstream_code=response.status_code)
            result = self._json(await self._read_limited(response))
            if not isinstance(result, dict) or "code" in result:
                raise UpstreamError("upstream_invalid_response")
            return result

    async def list_files(
        self, parent_id: str | None = None, *, page: int = 1, page_size: int = 100, keywords: str | None = None
    ) -> dict:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if parent_id is not None:
            params["parent_id"] = parent_id
        if keywords is not None:
            params["keywords"] = keywords
        result = self._page(await self.request("GET", "/api/v1/files", params=params), "files")
        if "parent_folder" not in result or not isinstance(result["parent_folder"], (dict, type(None))):
            raise UpstreamError("upstream_invalid_response")
        return result

    async def upload_file(
        self,
        filename: str,
        content: bytes | BinaryIO,
        parent_id: str | None = None,
        content_type: str = "application/octet-stream",
    ) -> dict:
        result = self._list(
            await self.request(
                "POST",
                "/api/v1/files",
                files=[("file", (filename, content, content_type))],
                data={"parent_id": parent_id} if parent_id is not None else None,
            )
        )
        if len(result) != 1:
            raise UpstreamError("upstream_invalid_response")
        return result[0]

    async def delete_files(self, ids: list[str]) -> dict:
        return self._object(await self.request("DELETE", "/api/v1/files", json={"ids": self._ids(ids)}), mutation=True)

    async def download_file(self, file_id: str) -> tuple[bytes, str]:
        async with self._stream("GET", f"/api/v1/files/{self._segment(file_id)}") as response:
            content = await self._read_limited(response)
            if not 200 <= response.status_code < 300:
                self._envelope(response.status_code, content)
            if response.status_code != 200:
                raise UpstreamError("upstream_invalid_response")
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            if any(ord(char) < 32 or ord(char) == 127 for char in content_type):
                raise UpstreamError("upstream_invalid_response")
            media_type = content_type.split(";", 1)[0].strip().lower()
            disposition = response.headers.get("Content-Disposition", "").split(";", 1)[0].strip().lower()
            if disposition not in {"attachment", "inline"} and (
                media_type == "application/json" or media_type.endswith("+json")
            ):
                # RAGFlow uses JSON for download errors, but an uploaded JSON file
                # (even one containing {"code": 102}) is identified by disposition.
                self._envelope(response.status_code, content)
                raise UpstreamError("upstream_invalid_response")
            return content, content_type

    async def list_datasets(self, *, page: int = 1, page_size: int = 100) -> dict:
        envelope = await self.request("GET", "/api/v1/datasets", params={"page": page, "page_size": page_size})
        datasets = self._list(envelope)
        total = envelope.get("total_datasets")
        if type(total) is not int or total < 0:
            raise UpstreamError("upstream_invalid_response")
        return {"datasets": datasets, "total": total}

    async def get_dataset(self, id: str) -> dict:
        async with self._stream("GET", f"/api/v1/datasets/{self._segment(id)}") as response:
            envelope = self._envelope(response.status_code, await self._read_limited(response), dataset_lookup=True)
            return self._object(envelope)

    async def create_dataset(self, payload: dict) -> dict:
        return self._object(await self.request("POST", "/api/v1/datasets", json=payload))

    async def delete_dataset(self, id: str) -> None:
        self._mutation(await self.request("DELETE", "/api/v1/datasets", json={"ids": self._ids([id])}))

    async def link_files(self, file_ids: list[str], dataset_ids: list[str]) -> None:
        # This acknowledges scheduling only; callers must poll for created documents.
        self._mutation(
            await self.request(
                "POST",
                "/api/v1/files/link-to-datasets",
                params={"mode": "add"},
                json={"file_ids": self._ids(file_ids), "kb_ids": self._ids(dataset_ids)},
            )
        )

    def _documents_path(self, dataset_id: str) -> str:
        return f"/api/v1/datasets/{self._segment(dataset_id)}/documents"

    async def list_documents(self, dataset_id: str, *, page: int = 1, page_size: int = 100) -> dict:
        return self._page(
            await self.request("GET", self._documents_path(dataset_id), params={"page": page, "page_size": page_size}),
            "docs",
        )

    async def parse_documents(self, dataset_id: str, doc_ids: list[str]) -> dict:
        return self._object(
            await self.request(
                "POST", self._documents_path(dataset_id) + "/parse", json={"document_ids": self._ids(doc_ids)}
            ),
            mutation=True,
        )

    async def remove_documents(self, dataset_id: str, doc_ids: list[str]) -> dict:
        return self._object(
            await self.request("DELETE", self._documents_path(dataset_id), json={"ids": self._ids(doc_ids)}),
            mutation=True,
        )

    async def retrieve(self, payload: dict) -> dict:
        return self._object(await self.request("POST", "/api/v1/retrieval", json=payload))
