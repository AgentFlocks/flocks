import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette import formparsers

from flocks.server.routes import knowledgebase

_BOUNDARY = b"knowledgebase-upload-test"
_HEADER = (
    b"--" + _BOUNDARY + b"\r\n"
    b'Content-Disposition: form-data; name="file"; filename="note.txt"\r\n'
    b"Content-Type: text/plain\r\n\r\n"
)
_END = b"\r\n--" + _BOUNDARY + b"--\r\n"
_MEDIA = b"multipart/form-data; boundary=" + _BOUNDARY


def make_app(monkeypatch, *, limit=1024, configured=True):
    created = {"id": "f1", "name": "note.txt", "size": 3, "parent_id": None}
    client = SimpleNamespace(
        connection=SimpleNamespace(max_upload_bytes=limit),
        upload=AsyncMock(return_value=created),
        link_files=AsyncMock(return_value={"dataset_id": "d1", "file_ids": ["f1"]}),
    )
    monkeypatch.setattr(knowledgebase, "get_client", lambda: client if configured else None)
    app = FastAPI()
    app.include_router(knowledgebase.create_router(), prefix="/api")
    app.dependency_overrides[knowledgebase.require_user] = lambda: "owner"
    return app, client


async def request(app, chunks, *, declared=None, media=_MEDIA, path="/api/knowledgebase/files"):
    reads = 0
    messages = []
    headers = [(b"content-type", media)]
    if declared is not None:
        headers.append((b"content-length", str(declared).encode()))

    async def receive():
        nonlocal reads
        if reads >= len(chunks):
            return {"type": "http.disconnect"}
        chunk = chunks[reads]
        reads += 1
        return {"type": "http.request", "body": chunk, "more_body": reads < len(chunks)}

    async def send(message):
        messages.append(message)

    await app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": headers,
        "server": ("testserver", 80), "client": ("client", 123),
    }, receive, send)
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, json.loads(body), reads


def too_large():
    return {"error": {
        "code": "request_too_large", "message": "The file exceeds the upload limit.",
        "request_id": None, "details": {},
    }}


@pytest.mark.asyncio
@pytest.mark.parametrize("declared,status,body", [
    (2048, 413, too_large()),
    ("invalid", 400, {"error": {
        "code": "invalid_request", "message": "The upload length is invalid.",
        "request_id": None, "details": {},
    }}),
])
async def test_declared_length_is_checked_before_receiving_or_parsing(monkeypatch, declared, status, body):
    app, client = make_app(monkeypatch)

    def forbidden_file(*args, **kwargs):
        pytest.fail("Rejected headers must not create multipart files")

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", forbidden_file)
    assert await request(app, [_HEADER, b"x" * 2048, _END], declared=declared) == (status, body, 0)
    client.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfigured_upload_does_not_consume_the_body(monkeypatch):
    app, client = make_app(monkeypatch, configured=False)
    status, body, reads = await request(app, [_HEADER, b"abc", _END])
    assert status == 503
    assert body["error"]["code"] == "knowledgebase_not_configured"
    assert reads == 0
    client.upload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [None, 1])
async def test_stream_overflow_stops_early_and_closes_partial_files(monkeypatch, declared):
    limit = 1024
    app, client = make_app(monkeypatch, limit=limit)
    original = formparsers.SpooledTemporaryFile
    files = []

    def track_file(*args, **kwargs):
        kwargs["max_size"] = 1  # Exercise cleanup after the file has rolled to temporary disk.
        file = original(*args, **kwargs)
        files.append(file)
        return file

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", track_file)
    chunks = [_HEADER, b"abc", b"x" * (limit + 512 * 1024), _END]
    try:
        status, body, reads = await request(app, chunks, declared=declared)
        assert (status, body) == (413, too_large())
        assert reads == 3 < len(chunks)
        assert files and all(file._rolled for file in files)
        assert all(file.closed for file in files)
        client.upload.assert_not_awaited()
    finally:
        for file in files:
            file.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_length", [False, True])
async def test_normal_upload_preserves_contents_and_response(monkeypatch, with_length):
    app, client = make_app(monkeypatch)
    chunks = [_HEADER, b"abc", _END]
    declared = sum(map(len, chunks)) if with_length else None
    status, body, reads = await request(app, chunks, declared=declared)
    assert status == 201
    assert body == {"data": {"id": "f1", "name": "note.txt", "size": 3, "parent_id": None}}
    assert reads == len(chunks)
    client.upload.assert_awaited_once_with("note.txt", b"abc", "text/plain")


@pytest.mark.asyncio
async def test_actual_file_limit_is_still_checked_for_an_undeclared_body(monkeypatch):
    app, client = make_app(monkeypatch, limit=1024)
    chunks = [_HEADER, b"x" * 1025, _END]
    status, body, reads = await request(app, chunks)
    assert (status, body) == (413, too_large())
    assert reads == len(chunks)  # Below the body allowance, the existing per-file check applies.
    client.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_declared_multipart_threshold_is_not_changed(monkeypatch):
    app, client = make_app(monkeypatch, limit=1024)
    chunks = [_HEADER, b"x" * 1024, _END]
    declared = sum(map(len, chunks))
    assert await request(app, chunks, declared=declared) == (413, too_large(), 0)
    client.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_multipart_errors_are_not_reclassified_as_overflow(monkeypatch):
    app, client = make_app(monkeypatch)
    status, body, _ = await request(app, [b"invalid"], media=b"multipart/form-data")
    assert status == 400
    assert "boundary" in body["detail"].lower()
    client.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_routes_do_not_gain_the_upload_body_limit(monkeypatch):
    app, client = make_app(monkeypatch, limit=1024)
    payload = json.dumps({"file_ids": ["f1"], "unknown": "x" * (1024 + 512 * 1024)}).encode()
    status, body, reads = await request(
        app, [payload], declared=len(payload), media=b"application/json",
        path="/api/knowledgebase/datasets/d1/files",
    )
    assert status == 200
    assert body == {"data": {"dataset_id": "d1", "file_ids": ["f1"]}}
    assert reads == 1
    client.link_files.assert_awaited_once_with("d1", ["f1"])
