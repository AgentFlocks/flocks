"""Compare the direct client with the frozen Core -> service -> RAGFlow wire.

Only the legacy oracle imports services/knowledgebase, at fixture time. Both
engines use MockTransport; ASGITransport runs the old routes without lifespan.
"""

import json
import re
from contextlib import asynccontextmanager
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI

from flocks.knowledgebase.client import Connection, KnowledgebaseClient
from flocks.knowledgebase.errors import KnowledgebaseError, unavailable


class _LegacyCoreClient:
    """Test-only copy of the original Core HTTP boundary, not the new facade."""

    def __init__(self, connection, transport):
        self.connection = connection
        self._http = httpx.AsyncClient(
            base_url=connection.origin + "/",
            timeout=connection.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={"Authorization": "Bearer " + connection.api_token},
        )

    async def close(self):
        await self._http.aclose()

    @staticmethod
    def resource_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise KnowledgebaseError(422, "invalid_resource_id", "A resource ID is required.")
        return quote(value, safe="")

    def _failure(self, response):
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

    async def request(self, method, path, **kwargs):
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

    async def _list(self, path, *, page=1, page_size=20, q=None):
        params = {"page": page, "page_size": page_size}
        if q:
            params["q"] = q
        return await self.request("GET", path, params=params)

    async def files(self, **kwargs):
        return await self._list("/v1/files", **kwargs)

    async def datasets(self, **kwargs):
        return await self._list("/v1/datasets", **kwargs)

    async def documents(self, dataset_id, **kwargs):
        return await self._list(f"/v1/datasets/{self.resource_id(dataset_id)}/documents", **kwargs)

    async def dataset(self, dataset_id):
        return await self.request("GET", f"/v1/datasets/{self.resource_id(dataset_id)}")

    async def create_dataset(self, payload):
        return await self.request("POST", "/v1/datasets", json=payload)

    async def delete_dataset(self, dataset_id):
        await self.request("DELETE", f"/v1/datasets/{self.resource_id(dataset_id)}")

    async def link_files(self, dataset_id, file_ids):
        return await self.request(
            "POST", f"/v1/datasets/{self.resource_id(dataset_id)}/files", json={"file_ids": file_ids}
        )

    async def remove_documents(self, dataset_id, document_ids):
        return await self.request(
            "DELETE", f"/v1/datasets/{self.resource_id(dataset_id)}/documents", json={"document_ids": document_ids}
        )

    async def parse(self, dataset_id, document_ids):
        return await self.request(
            "POST", f"/v1/datasets/{self.resource_id(dataset_id)}/parse", json={"document_ids": document_ids}
        )

    async def retrieve(self, payload):
        return await self.request("POST", "/v1/retrieval", json=payload)

    async def upload(self, filename, content, content_type):
        if len(content) > self.connection.max_upload_bytes:
            raise KnowledgebaseError(413, "request_too_large", "The file exceeds the upload limit.")
        return await self.request(
            "POST", "/v1/files", files={"file": (filename, content, content_type or "application/octet-stream")}
        )

    async def content(self, file_id):
        ident = self.resource_id(file_id)
        try:
            response = await self._http.get(f"v1/files/{ident}/content")
        except httpx.HTTPError:
            raise unavailable() from None
        if response.status_code >= 400:
            raise self._failure(response)
        return response.content, response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]

    async def delete_file(self, file_id):
        await self.request("DELETE", f"/v1/files/{self.resource_id(file_id)}")


def _envelope(data, **extra):
    return {"code": 0, "data": data, **extra}


def _wire(method, path, *, query=None, body=None, files=None):
    return {
        "method": method,
        "path": "/deployment/api/v1" + path,
        "query": sorted((query or {}).items()),
        "json": body,
        "files": files,
    }


def _record(request):
    media = request.headers.get("content-type", "")
    body = json.loads(request.content) if media.startswith("application/json") else None
    files = None
    if media.startswith("multipart/form-data"):
        # Only discard the random boundary; preserve the filename, media and
        # bytes actually sent to the engine, including legacy wire escaping.
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + media.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + request.content
        )
        files = [
            (part.get_param("name", header="content-disposition"), part.get_filename(),
             part.get_content_type(), part.get_payload(decode=True))
            for part in message.iter_parts()
        ]
    return {
        "method": request.method,
        "path": request.url.path,
        "query": sorted(request.url.params.multi_items()),
        "json": body,
        "files": files,
    }


@pytest.fixture
def clients(monkeypatch):
    source = Path(__file__).resolve().parents[2] / "services" / "knowledgebase" / "src"
    monkeypatch.syspath_prepend(str(source))
    from flocks_knowledgebase.app import create_app
    from flocks_knowledgebase.ragflow import RagflowClient
    from flocks_knowledgebase.service import KnowledgeAPI
    from flocks_knowledgebase.settings import Settings

    @asynccontextmanager
    async def pair(body=None, *, status=200, content=None, headers=None, upload_limit=32 * 1024 * 1024):
        calls = [[], []]

        def transport(index):
            def handler(request):
                assert request.headers["authorization"] == "Bearer " + "e" * 40
                calls[index].append(_record(request))
                if content is not None:
                    return httpx.Response(status, content=content, headers=headers)
                return httpx.Response(status, json=body, headers=headers)
            return httpx.MockTransport(handler)

        connection = Connection("http://engine.test/deployment/api/v1", "e" * 40, max_upload_bytes=upload_limit)
        engine = RagflowClient(
            connection.base_url, connection.api_token, transport=transport(0),
            timeout=connection.timeout_seconds, max_content_bytes=connection.max_upload_bytes,
        )
        settings = Settings(
            api_token="s" * 40, ragflow_base_url=connection.base_url, ragflow_api_key=connection.api_token,
            request_timeout_seconds=connection.timeout_seconds, max_upload_bytes=connection.max_upload_bytes,
            _env_file=None,
        )
        app = create_app(settings, ragflow=engine)
        # No server or lifespan: retain the real routes, validation, middleware,
        # JSON serialization boundary and real legacy engine implementation.
        app.state.service = KnowledgeAPI(engine)
        app.state.ragflow = engine
        old = _LegacyCoreClient(
            Connection("http://legacy.test", "s" * 40, max_upload_bytes=upload_limit), httpx.ASGITransport(app=app)
        )
        new = KnowledgebaseClient(connection, transport=transport(1))
        try:
            yield old, new, calls
        finally:
            await new.close()
            await old.close()
            await engine.close()

    return pair


async def _outcome(call):
    try:
        return "ok", await call
    except KnowledgebaseError as error:
        return "error", error.status, error.public()


def _error(status, code, message):
    return "error", status, {"error": {"code": code, "message": message, "details": {}, "request_id": None}}


_VALIDATION = _error(422, "validation_error", "Invalid request fields.")
_INTERNAL = _error(500, "internal_error", "The service could not complete this request.")
_UPSTREAM_MESSAGE = "The knowledge engine could not complete this operation."
_DATASET = {"id": "d1", "name": "Example", "description": "", "document_count": None, "chunk_count": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args,raw,expected,wire", [
    ("create_dataset", ({"name": "  Example  "},), _envelope({"id": "d1", "name": "Example"}), _DATASET,
     _wire("POST", "/datasets", body={"name": "Example", "description": ""})),
    ("dataset", ("d1",), _envelope({"id": "d1", "name": "Example"}), _DATASET,
     _wire("GET", "/datasets/d1")),
    ("delete_dataset", ("d1",), _envelope(True), None,
     _wire("DELETE", "/datasets", body={"ids": ["d1"]})),
    ("delete_file", ("f1",), _envelope(None), None,
     _wire("DELETE", "/files", body={"ids": ["f1"]})),
    ("link_files", ("d1", ("f1", "f2")), _envelope(True), {"dataset_id": "d1", "file_ids": ["f1", "f2"]},
     _wire("POST", "/files/link-to-datasets", query={"mode": "add"}, body={"file_ids": ["f1", "f2"], "kb_ids": ["d1"]})),
    ("remove_documents", ("d1", ["doc1"]), _envelope(None), {"dataset_id": "d1", "document_ids": ["doc1"]},
     _wire("DELETE", "/datasets/d1/documents", body={"ids": ["doc1"]})),
    ("parse", ("d1", ["doc1"]), _envelope(True), {"dataset_id": "d1", "document_ids": ["doc1"]},
     _wire("POST", "/datasets/d1/documents/parse", body={"document_ids": ["doc1"]})),
])
async def test_business_methods_and_engine_wire(clients, method, args, raw, expected, wire):
    async with clients(raw) as (old, new, calls):
        assert await _outcome(getattr(old, method)(*args)) == await _outcome(getattr(new, method)(*args)) == ("ok", expected)
        assert calls[0] == calls[1] == [wire]


_LISTS = [
    ("files", (), "/files", _envelope({"files": [
        {"id": "folder", "type": "folder"},
        {"id": "f1", "name": "Straße", "size": True, "parent_id": 7},
        {"id": "f2", "name": "Other", "size": -1, "parent_id": "folder"},
    ], "total": 9, "parent_folder": None}), [
        {"id": "f1", "name": "Straße", "size": None, "parent_id": None},
        {"id": "f2", "name": "Other", "size": None, "parent_id": "folder"},
    ], 8),
    ("datasets", (), "/datasets", _envelope([
        {"id": "d1", "name": "Straße", "description": 7, "document_count": True, "chunk_count": "4"},
        {"id": "d2", "name": "Other", "description": "kept", "document_count": 0, "chunk_count": 4},
    ], total_datasets=9), [
        {"id": "d1", "name": "Straße", "description": "", "document_count": None, "chunk_count": None},
        {"id": "d2", "name": "Other", "description": "kept", "document_count": 0, "chunk_count": 4},
    ], 9),
    ("documents", ("d1",), "/datasets/d1/documents", _envelope({"docs": [
        {"id": "doc1", "name": "Straße", "run": "RUNNING", "progress": True, "chunk_count": False},
        {"id": "doc2", "name": "Other", "run": "", "status": "waiting", "progress": 1.1, "chunk_count": 3},
    ], "total": 9}), [
        {"id": "doc1", "dataset_id": "d1", "name": "Straße", "status": "RUNNING", "progress": True, "chunk_count": None},
        {"id": "doc2", "dataset_id": "d1", "name": "Other", "status": "waiting", "progress": None, "chunk_count": 3},
    ], 9),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args,path,raw,items,total", _LISTS)
@pytest.mark.parametrize("kwargs,page,size,needle", [
    ({}, 1, 20, None),
    ({"page": 2, "page_size": 3, "q": "STRASSE"}, 2, 3, "STRASSE"),
    ({"page": [1, 2], "q": ""}, 2, 20, None),
    ({"q": 7}, 1, 20, "7"),
])
async def test_current_page_search_projection_and_folder_totals(clients, method, args, path, raw, items, total, kwargs, page, size, needle):
    selected = [item for item in items if needle.casefold() in item["name"].casefold()] if needle else items
    expected = {"items": selected, "total": len(selected) if needle else total, "page": page}
    query = {"page": str(page), "page_size": str(size)}
    if method == "files" and needle:
        query["keywords"] = needle
    async with clients(raw) as (old, new, calls):
        assert await _outcome(getattr(old, method)(*args, **kwargs)) == await _outcome(getattr(new, method)(*args, **kwargs)) == ("ok", expected)
        assert calls[0] == calls[1] == [_wire("GET", path, query=query)]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args", [("files", ()), ("datasets", ()), ("documents", ("d1",))])
@pytest.mark.parametrize("kwargs", [{"page": True}, {"page": 0}, {"page_size": 101}, {"q": "x" * 201}])
async def test_list_validation_uses_old_query_wire_types(clients, method, args, kwargs):
    async with clients() as (old, new, calls):
        assert await _outcome(getattr(old, method)(*args, **kwargs)) == await _outcome(getattr(new, method)(*args, **kwargs)) == _VALIDATION
        assert calls == [[], []]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,expected", [
    ({"name": " \u00a0Example\u00a0 ", "description": "  description  "}, {"name": "Example", "description": "description"}),
    ({"name": "Example\x7f"}, {"name": "Example\x7f", "description": ""}),
    ({"name": " "}, None),
    ({"name": "Example\n"}, None),
    ({"name": "Example", "description": "description\t"}, None),
    ({"name": "Example", "unknown": True}, None),
])
async def test_dataset_strip_controls_and_extra_fields(clients, payload, expected):
    async with clients(_envelope({"id": "d1", "name": "Example"})) as (old, new, calls):
        outcome = ("ok", _DATASET) if expected is not None else _VALIDATION
        assert await _outcome(old.create_dataset(payload)) == await _outcome(new.create_dataset(payload)) == outcome
        assert calls[0] == calls[1] == ([_wire("POST", "/datasets", body=expected)] if expected is not None else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["link_files", "remove_documents", "parse", "retrieve"])
@pytest.mark.parametrize("ids", [[], [""], ["duplicate", "duplicate"], ["bad/id"]])
async def test_empty_invalid_and_duplicate_batch_ids(clients, method, ids):
    args = ({"dataset_ids": ids, "keywords": "question"},) if method == "retrieve" else ("d1", ids)
    async with clients() as (old, new, calls):
        assert await _outcome(getattr(old, method)(*args)) == await _outcome(getattr(new, method)(*args)) == _VALIDATION
        assert calls == [[], []]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,rest", [
    ("content", ()), ("delete_file", ()), ("dataset", ()), ("delete_dataset", ()),
    ("documents", ()), ("link_files", (["f1"],)), ("remove_documents", (["doc1"],)), ("parse", (["doc1"],)),
])
@pytest.mark.parametrize("ident", ["", "../bad"])
async def test_core_resource_id_guard_is_unchanged(clients, method, rest, ident):
    async with clients() as (old, new, calls):
        expected = _error(422, "invalid_resource_id", "A resource ID is required.")
        assert await _outcome(getattr(old, method)(ident, *rest)) == await _outcome(getattr(new, method)(ident, *rest)) == expected
        assert calls == [[], []]


_CHUNKS = [
    {"id": "c1", "content": "first", "dataset_id": "d1", "document_id": "doc1", "document_keyword": "", "docnm_kwd": "fallback", "similarity": True},
    {"content": "outside", "dataset_id": "other"},
    {"content": 7, "dataset_id": "d1"},
    None,
    {"content": "second", "dataset_id": "d1", "document_name": "second.txt", "similarity": 0.4},
    {"content": "third", "dataset_id": "d1", "document_keyword": 123, "document_name": "not selected", "similarity": "0.3"},
]
_VIEWS = [
    {"id": "c1", "content": "first", "dataset_id": "d1", "document_id": "doc1", "document_name": "fallback", "similarity": True},
    {"id": "", "content": "second", "dataset_id": "d1", "document_id": "", "document_name": "second.txt", "similarity": 0.4},
    {"id": "", "content": "third", "dataset_id": "d1", "document_id": "", "document_name": "", "similarity": None},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides,question,top_k", [
    ({}, "question", 5),
    ({"top_k": 1}, "question", 1),
    ({"keywords": " \t\n "}, "", 5),
])
async def test_retrieval_defaults_scope_and_total_before_truncation(clients, overrides, question, top_k):
    payload = {"dataset_ids": ("d1",), "keywords": "  question  ", **overrides}
    expected_wire = _wire("POST", "/retrieval", body={
        "dataset_ids": ["d1"], "question": question, "page": 1, "page_size": top_k,
        "similarity_threshold": 0.2, "vector_similarity_weight": 0.3,
    })
    async with clients(_envelope({"chunks": _CHUNKS, "total": 999})) as (old, new, calls):
        expected = ("ok", {"chunks": _VIEWS[:top_k], "total": 3})
        assert await _outcome(old.retrieve(payload)) == await _outcome(new.retrieve(payload)) == expected
        assert calls[0] == calls[1] == [expected_wire]


@pytest.mark.asyncio
@pytest.mark.parametrize("filename,wire_name", [
    ("note.txt", "note.txt"), ("résumé.json", "résumé.json"),
    ('quote".txt', "quote%22.txt"), ("line\r\n.txt", "line%0D%0A.txt"),
    (r"C:\fakepath\note.txt", "note.txt"),
])
async def test_upload_preserves_legacy_multipart_filename(clients, filename, wire_name):
    raw = _envelope([{"id": "f1", "name": wire_name, "size": 3}])
    async with clients(raw) as (old, new, calls):
        expected = ("ok", {"id": "f1", "name": wire_name, "size": 3, "parent_id": None})
        assert await _outcome(old.upload(filename, b"abc", "")) == await _outcome(new.upload(filename, b"abc", "")) == expected
        assert calls[0] == calls[1] == [_wire("POST", "/files", files=[("file", wire_name, "application/octet-stream", b"abc")])]


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["../note.txt", ".", "..", "x" * 256])
async def test_invalid_upload_names_do_not_reach_engine(clients, filename):
    async with clients() as (old, new, calls):
        expected = _error(400, "invalid_request", "A file name is required.")
        assert await _outcome(old.upload(filename, b"abc", "text/plain")) == await _outcome(new.upload(filename, b"abc", "text/plain")) == expected
        assert calls == [[], []]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args,status,raw,code,message", [
    ("datasets", (), 401, {"code": 401, "message": "sensitive engine text"}, "upstream_unavailable", _UPSTREAM_MESSAGE),
    ("datasets", (), 403, {"code": 403}, "upstream_unavailable", _UPSTREAM_MESSAGE),
    ("dataset", ("missing",), 404, {"message": "private dataset name"}, "not_found", "The dataset was not found."),
    ("dataset", ("missing",), 200, {"code": 102, "message": "Invalid Dataset ID"}, "not_found", "The dataset was not found."),
    ("link_files", ("d1", ["f1"]), 200, _envelope({"failed_ids": ["f1"]}), "upstream_partial_failure", _UPSTREAM_MESSAGE),
    ("datasets", (), 404, {"code": 404}, "upstream_unavailable", _UPSTREAM_MESSAGE),
])
async def test_public_errors_drop_internal_details_and_request_ids(clients, method, args, status, raw, code, message):
    async with clients(raw, status=status) as (old, new, calls):
        expected = _error(404 if code == "not_found" else 502, code, message)
        assert await _outcome(getattr(old, method)(*args)) == await _outcome(getattr(new, method)(*args)) == expected
        assert calls[0] == calls[1]
        assert len(calls[0]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity"])
async def test_nonfinite_response_remains_a_safe_500(clients, number):
    content = ('{"code":0,"data":{"chunks":[{"content":"hit","dataset_id":"d1","similarity":' + number + '}]}}').encode()
    async with clients(content=content, headers={"content-type": "application/json"}) as (old, new, calls):
        payload = {"dataset_ids": ["d1"], "keywords": "question"}
        assert await _outcome(old.retrieve(payload)) == await _outcome(new.retrieve(payload)) == _INTERNAL
        assert calls[0] == calls[1]
        assert len(calls[0]) == 1


@asynccontextmanager
async def _bff(monkeypatch, client):
    from flocks.server.routes import knowledgebase

    with monkeypatch.context() as patch:
        patch.setattr(knowledgebase, "get_client", lambda: client)
        app = FastAPI()
        app.include_router(knowledgebase.create_router(), prefix="/api")
        app.dependency_overrides[knowledgebase.require_user] = lambda: "owner"
        # Session retrieval calls require_user directly, unlike the other routes.
        patch.setattr(knowledgebase, "require_user", lambda request=None: "owner")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bff.test") as http:
            yield http


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_json_original_download_and_bff_attachment_headers(clients, monkeypatch, inline):
    body = b'{"code":102,"message":"this is the uploaded JSON document"}'
    async with clients(content=body, headers={
        "content-type": "application/json; charset=utf-8", "content-disposition": 'attachment; filename="original.json"',
    }) as (old, new, calls):
        assert await _outcome(old.content("f1")) == await _outcome(new.content("f1")) == ("ok", (body, "application/json"))
        replies = []
        for client in (old, new):
            async with _bff(monkeypatch, client) as http:
                response = await http.get("/api/knowledgebase/files/f1/content", params={"inline": str(inline).lower()})
            replies.append((response.status_code, response.content, dict(response.headers)))
        assert replies[0] == replies[1]
        status, content, headers = replies[0]
        assert (status, content) == (200, body)
        assert headers["content-type"] == "application/octet-stream"
        assert headers["content-disposition"] == "attachment"
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["content-security-policy"] == "frame-ancestors 'none'"
        assert calls[0] == calls[1] == [_wire("GET", "/files/f1")] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload,raw,status,expected", [
    ("POST", "/datasets", {"name": " Example "}, _envelope({"id": "d1", "name": "Example"}), 201, _DATASET),
    ("POST", "/datasets/d1/files", {"file_ids": ["f1"]}, _envelope(True), 200, {"dataset_id": "d1", "file_ids": ["f1"]}),
    ("DELETE", "/datasets/d1/documents", {"document_ids": ["doc1"]}, _envelope(None), 200, {"dataset_id": "d1", "document_ids": ["doc1"]}),
    ("POST", "/datasets/d1/parse", {"document_ids": ["doc1"]}, _envelope(True), 200, {"dataset_id": "d1", "document_ids": ["doc1"]}),
    ("POST", "/sessions/s1/retrieval", {"keywords": "question", "dataset": ["d1"]}, _envelope({"chunks": []}), 200, {"chunks": [], "total": 0}),
])
async def test_bff_still_ignores_extra_body_fields(clients, monkeypatch, method, path, payload, raw, status, expected):
    monkeypatch.setattr("flocks.knowledgebase.retrieval.get_selection", AsyncMock(return_value={"dataset_ids": ["d1"]}))
    async with clients(raw) as (old, new, calls):
        replies = []
        for client in (old, new):
            async with _bff(monkeypatch, client) as http:
                response = await http.request(method, "/api/knowledgebase" + path, json={**payload, "unknown": "ignored"})
            replies.append((response.status_code, response.json()))
        assert replies[0] == replies[1] == (status, {"data": expected})
        assert calls[0] == calls[1]
        assert len(calls[0]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,raw,keywords,expected", [
    (401, {"code": 401, "message": "private"}, "question", _error(502, "upstream_unavailable", _UPSTREAM_MESSAGE)),
    (200, _envelope({"chunks": [{"content": "hit", "dataset_id": "d1", "similarity": float("nan")}]}), "question", _INTERNAL),
    (200, _envelope({"chunks": []}), "question\x01", _VALIDATION),
])
async def test_bff_and_tool_keep_sanitized_errors(clients, monkeypatch, status, raw, keywords, expected):
    from flocks.tool import knowledgebase

    monkeypatch.setattr("flocks.knowledgebase.retrieval.get_selection", AsyncMock(return_value={"dataset_ids": ["d1"]}))
    monkeypatch.setattr("flocks.agent.registry.Agent.get", AsyncMock(return_value=SimpleNamespace(tools=["rag_retrieve"], permission=None)))
    # Raw JSON is intentional: Response(json=...) itself rejects nonfinite data.
    async with clients(status=status, content=json.dumps(raw).encode(), headers={"content-type": "application/json"}) as (old, new, calls):
        replies, tools = [], []
        for client in (old, new):
            async with _bff(monkeypatch, client) as http:
                response = await http.post("/api/knowledgebase/sessions/s1/retrieval", json={"keywords": keywords})
            replies.append((response.status_code, response.json()))
            monkeypatch.setattr(knowledgebase, "get_client", lambda: client)
            monkeypatch.setattr(knowledgebase, "get_current_auth_user", lambda: "owner")
            ctx = SimpleNamespace(agent="helper", session_id="s1", ask=AsyncMock())
            result = await knowledgebase.rag_retrieve_tool(ctx, keywords)
            tools.append(result.model_dump())
            ctx.ask.assert_awaited_once_with(permission="rag_retrieve", patterns=["*"])
        assert replies[0] == replies[1] == (expected[1], expected[2])
        assert tools[0] == tools[1]
        assert tools[0]["success"] is False
        assert tools[0]["error"] == expected[2]["error"]["message"]
        assert tools[0]["metadata"] == {"code": expected[2]["error"]["code"]}
        assert calls[0] == calls[1]
        assert len(calls[0]) == (0 if expected == _VALIDATION else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,field", [
    ("POST", "/datasets/d1/files", "file_ids"),
    ("DELETE", "/datasets/d1/documents", "document_ids"),
    ("POST", "/datasets/d1/parse", "document_ids"),
])
async def test_large_json_keeps_the_old_size_error_before_id_validation(clients, monkeypatch, method, path, field):
    limit = 1024
    payload = {field: ["x" * (limit + 512 * 1024)]}
    expected = _error(413, "request_too_large", "The request exceeds the configured body limit.")
    async with clients(upload_limit=limit) as (old, new, calls):
        replies = []
        for client in (old, new):
            async with _bff(monkeypatch, client) as http:
                response = await http.request(method, "/api/knowledgebase" + path, json=payload)
            replies.append((response.status_code, response.json()))
        assert replies[0] == replies[1] == (expected[1], expected[2])
        assert calls == [[], []]


@pytest.mark.asyncio
async def test_large_ignored_bff_field_does_not_gain_a_new_global_limit(clients, monkeypatch):
    limit = 1024
    payload = {"file_ids": ["f1"], "unknown": "x" * (limit + 512 * 1024)}
    async with clients(_envelope(True), upload_limit=limit) as (old, new, calls):
        replies = []
        for client in (old, new):
            async with _bff(monkeypatch, client) as http:
                response = await http.post("/api/knowledgebase/datasets/d1/files", json=payload)
            replies.append((response.status_code, response.json()))
        assert replies[0] == replies[1] == (200, {"data": {"dataset_id": "d1", "file_ids": ["f1"]}})
        assert calls[0] == calls[1]
        assert len(calls[0]) == 1
