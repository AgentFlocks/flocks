import asyncio
from collections.abc import Awaitable, Callable

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope

from flocks.server import app as server_app


def _http_scope(path: str, *, method: str = "GET") -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
        "state": {},
    }


async def _run_auth_middleware(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hook: Callable[[Request, dict], Awaitable[None]],
    endpoint: ASGIApp,
    receive: Receive,
) -> None:
    async def apply_auth(_request: Request):
        return None, object(), None

    async def send(_message: Message) -> None:
        return None

    monkeypatch.setattr(server_app, "_run_http_middleware_hooks", hook)
    monkeypatch.setattr(server_app, "apply_auth_for_request", apply_auth)
    monkeypatch.setattr(server_app, "clear_auth_context", lambda _token: None)

    await server_app._AuthGuardMiddleware(endpoint)(
        _http_scope("/api/session", method="POST"),
        receive,
        send,
    )


def test_production_http_middleware_is_pure_asgi_and_keeps_order() -> None:
    middleware_classes = [entry.cls for entry in server_app.app.user_middleware]

    assert BaseHTTPMiddleware not in middleware_classes
    assert middleware_classes == [
        server_app._DeferredCORSMiddleware,
        server_app._StaticWebUIMiddleware,
        server_app._AuthGuardMiddleware,
        server_app._RequestLoggingMiddleware,
        server_app._InstanceContextMiddleware,
        server_app._SecurityHeadersMiddleware,
    ]


@pytest.mark.asyncio
async def test_request_log_is_emitted_when_stream_headers_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    never = asyncio.Event()
    response_started = asyncio.Event()
    logged: list[tuple[str, dict]] = []

    async def streaming_app(_scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        response_started.set()
        await never.wait()

    async def receive():
        await never.wait()
        return {"type": "http.disconnect"}

    async def send(_message):
        return None

    monkeypatch.setattr(server_app.log, "info", lambda event, payload: logged.append((event, payload)))
    middleware = server_app._RequestLoggingMiddleware(streaming_app)
    request = asyncio.create_task(middleware(_http_scope("/api/provider"), receive, send))
    try:
        await response_started.wait()

        assert not request.done()
        assert logged[0][0] == "request.complete"
        assert logged[0][1]["status"] == 200
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.asyncio
async def test_auth_hook_request_body_is_replayed_to_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    incoming = iter(
        [
            {"type": "http.request", "body": b"hello ", "more_body": True},
            {"type": "http.request", "body": b"world", "more_body": False},
        ]
    )

    async def receive():
        return next(incoming)

    async def inspect_body(request: Request, _context: dict) -> None:
        seen["hook"] = await request.body()

    async def endpoint(_scope, downstream_receive, _send) -> None:
        body = bytearray()
        messages = []
        while True:
            message = await downstream_receive()
            messages.append(message)
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        seen["endpoint"] = bytes(body)
        seen["message_count"] = len(messages)
        seen["same_body_object"] = messages[0]["body"] is seen["hook"]

    await _run_auth_middleware(
        monkeypatch,
        hook=inspect_body,
        endpoint=endpoint,
        receive=receive,
    )

    assert seen == {
        "hook": b"hello world",
        "endpoint": b"hello world",
        "message_count": 1,
        "same_body_object": True,
    }


@pytest.mark.asyncio
async def test_auth_hook_stream_consumption_keeps_legacy_downstream_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, bytes] = {}
    incoming = iter(
        [
            {"type": "http.request", "body": b"hello ", "more_body": True},
            {"type": "http.request", "body": b"world", "more_body": False},
        ]
    )

    async def receive():
        return next(incoming)

    async def inspect_stream(request: Request, _context: dict) -> None:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if body == b"hello world":
                break
        seen["hook"] = bytes(body)

    async def endpoint(scope, downstream_receive, _send) -> None:
        seen["endpoint"] = await Request(scope, receive=downstream_receive).body()

    await _run_auth_middleware(
        monkeypatch,
        hook=inspect_stream,
        endpoint=endpoint,
        receive=receive,
    )

    assert seen == {"hook": b"hello world", "endpoint": b""}


@pytest.mark.asyncio
async def test_auth_hook_partial_stream_read_continues_from_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, bytes] = {}
    incoming = iter(
        [
            {"type": "http.request", "body": b"hello ", "more_body": True},
            {"type": "http.request", "body": b"world", "more_body": False},
        ]
    )

    async def receive():
        return next(incoming)

    async def inspect_stream(request: Request, _context: dict) -> None:
        async for chunk in request.stream():
            seen["hook"] = chunk
            break

    async def endpoint(scope, downstream_receive, _send) -> None:
        seen["endpoint"] = await Request(scope, receive=downstream_receive).body()

    await _run_auth_middleware(
        monkeypatch,
        hook=inspect_stream,
        endpoint=endpoint,
        receive=receive,
    )

    assert seen == {"hook": b"hello ", "endpoint": b"world"}


@pytest.mark.asyncio
async def test_auth_hook_terminal_empty_break_does_not_block_downstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, bytes] = {}
    incoming = iter(
        [
            {"type": "http.request", "body": b"hello", "more_body": False},
        ]
    )

    async def receive():
        return next(incoming)

    async def inspect_stream(request: Request, _context: dict) -> None:
        async for chunk in request.stream():
            if not chunk:
                break

    async def endpoint(scope, downstream_receive, _send) -> None:
        seen["endpoint"] = await Request(scope, receive=downstream_receive).body()

    await _run_auth_middleware(
        monkeypatch,
        hook=inspect_stream,
        endpoint=endpoint,
        receive=receive,
    )

    assert seen == {"endpoint": b""}


@pytest.mark.asyncio
async def test_auth_hook_observed_disconnect_is_replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Message] = []
    incoming = iter(
        [
            {"type": "http.request", "body": b"hello", "more_body": False},
            {"type": "http.disconnect"},
        ]
    )
    never = asyncio.Event()

    async def receive():
        try:
            return next(incoming)
        except StopIteration:
            await never.wait()
            return {"type": "http.disconnect"}

    async def inspect_disconnect(request: Request, _context: dict) -> None:
        await request.body()
        assert await request.is_disconnected()

    async def endpoint(_scope, downstream_receive, _send) -> None:
        seen.append(await downstream_receive())
        seen.append(await downstream_receive())
        seen.append(await downstream_receive())

    await _run_auth_middleware(
        monkeypatch,
        hook=inspect_disconnect,
        endpoint=endpoint,
        receive=receive,
    )

    assert seen == [
        {"type": "http.request", "body": b"hello", "more_body": False},
        {"type": "http.disconnect"},
        {"type": "http.disconnect"},
    ]


@pytest.mark.asyncio
async def test_auth_without_body_read_passes_original_receive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_called = False

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def no_hooks(_request: Request, _context: dict) -> None:
        return None

    async def endpoint(_scope, downstream_receive, _send) -> None:
        nonlocal endpoint_called
        endpoint_called = True
        assert downstream_receive is receive

    await _run_auth_middleware(
        monkeypatch,
        hook=no_hooks,
        endpoint=endpoint,
        receive=receive,
    )

    assert endpoint_called


@pytest.mark.asyncio
async def test_long_lived_streams_do_not_spawn_base_http_middleware_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    never = asyncio.Event()
    all_streams_started = asyncio.Event()
    cleared_tokens: list[object] = []
    started_streams = 0

    async def stream():
        nonlocal started_streams
        started_streams += 1
        if started_streams == 11:
            all_streams_started.set()
        yield b"data: connected\n\n"
        await never.wait()

    async def endpoint(_request: Request) -> StreamingResponse:
        return StreamingResponse(stream(), media_type="text/event-stream")

    async def apply_auth(_request: Request):
        return None, object(), None

    async def no_static_response(_request: Request):
        return None

    monkeypatch.setattr(server_app, "apply_auth_for_request", apply_auth)
    monkeypatch.setattr(server_app, "clear_auth_context", cleared_tokens.append)
    monkeypatch.setattr(server_app, "maybe_serve_static_webui", no_static_response)
    monkeypatch.setattr(server_app, "_should_log_request", lambda *_args: False)

    app = Starlette(routes=[Route("/global/event", endpoint)])
    app.add_middleware(server_app._SecurityHeadersMiddleware)
    app.add_middleware(server_app._InstanceContextMiddleware)
    app.add_middleware(server_app._RequestLoggingMiddleware)
    app.add_middleware(server_app._AuthGuardMiddleware)
    app.add_middleware(server_app._StaticWebUIMiddleware)

    scope = _http_scope("/global/event")

    async def receive():
        await never.wait()
        return {"type": "http.disconnect"}

    async def send(_message):
        return None

    requests = [asyncio.create_task(app(dict(scope), receive, send)) for _ in range(11)]
    try:
        await asyncio.wait_for(all_streams_started.wait(), timeout=1)

        base_http_tasks = [task for task in asyncio.all_tasks() if "BaseHTTPMiddleware" in task.get_coro().__qualname__]

        assert started_streams == 11
        assert all(not request.done() for request in requests)
        assert base_http_tasks == []
        assert cleared_tokens == []
    finally:
        for request in requests:
            request.cancel()
        await asyncio.gather(*requests, return_exceptions=True)

    assert len(cleared_tokens) == 11
