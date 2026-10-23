import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from collections.abc import Awaitable, Callable

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope

from flocks.hooks.execution import (
    current_execution_context,
    current_execution_lifecycle_scope,
)
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


@pytest.mark.asyncio
async def test_auth_guard_keeps_ingress_context_and_scope_for_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def apply_auth(request: Request):
        request.state.extension_context = {"workflow_transfer": "opaque-pro-token"}
        observed["scope_during_auth"] = current_execution_lifecycle_scope()
        return None, object(), None

    async def endpoint(_scope, _receive, _send) -> None:
        observed["context"] = current_execution_context()
        observed["scope_during_endpoint"] = current_execution_lifecycle_scope()

    async def no_op_hook(_request: Request, _payload: dict) -> None:
        return None

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    monkeypatch.setattr(server_app, "_run_http_middleware_hooks", no_op_hook)
    monkeypatch.setattr(server_app, "apply_auth_for_request", apply_auth)
    monkeypatch.setattr(server_app, "clear_auth_context", lambda _token: None)

    await server_app._AuthGuardMiddleware(endpoint)(
        _http_scope("/api/session", method="POST"),
        receive,
        lambda _message: None,
    )

    assert observed["context"] == {"workflow_transfer": "opaque-pro-token"}
    assert observed["scope_during_auth"] is observed["scope_during_endpoint"]


def test_production_http_middleware_is_pure_asgi_and_keeps_order(tmp_path) -> None:
    # Import a clean Core app: installed package plugins can add their own
    # middleware, and other tests may already have imported those plugins.
    script = textwrap.dedent("""
        import json
        from types import SimpleNamespace
        from unittest.mock import patch
        from starlette.middleware.base import BaseHTTPMiddleware

        with patch("dotenv.load_dotenv"), patch(
            "flocks.plugin.PluginLoader.load_all",
            return_value=SimpleNamespace(has_critical_entrypoint_failure=False),
        ):
            from flocks.server.app import app

        entries = app.user_middleware
        assert not any(issubclass(entry.cls, BaseHTTPMiddleware) for entry in entries)
        print(json.dumps([entry.cls.__name__ for entry in entries]))
    """)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == [
        "_DeferredCORSMiddleware",
        "_StaticWebUIMiddleware",
        "_AuthGuardMiddleware",
        "_RequestLoggingMiddleware",
        "_InstanceContextMiddleware",
        "_SecurityHeadersMiddleware",
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


@pytest.mark.asyncio
async def test_instance_context_without_directory_uses_process_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Ordinary requests keep the pre-project-management cwd behavior."""
    from flocks.project.instance import Instance

    process_cwd = tmp_path / "server-cwd"
    process_cwd.mkdir()
    monkeypatch.chdir(process_cwd)
    provided_directories: list[str] = []

    async def provide(*, directory, init, fn):
        provided_directories.append(directory)
        return await fn()

    async def endpoint(_scope, _receive, _send) -> None:
        return None

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    monkeypatch.setattr(Instance, "provide", provide)

    await server_app._InstanceContextMiddleware(endpoint)(
        _http_scope("/api/skill"),
        receive,
        send,
    )

    assert provided_directories == [str(process_cwd)]


@pytest.mark.asyncio
async def test_auth_guard_rejection_carries_security_headers_and_request_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The auth guard sits outside the security-header and request-log
    middleware, so a 401 it emits itself must still carry the baseline headers
    and be logged as ``request.complete``."""
    from starlette.exceptions import HTTPException

    sent: list[Message] = []
    logged: list[tuple[str, dict]] = []

    async def hook(_request, _context) -> None:
        return None

    async def apply_auth(_request: Request):
        raise HTTPException(status_code=401, detail="请先登录")

    async def endpoint(_scope, _receive, _send) -> None:  # pragma: no cover - must not run
        raise AssertionError("endpoint must not be reached")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    monkeypatch.setattr(server_app, "_run_http_middleware_hooks", hook)
    monkeypatch.setattr(server_app, "apply_auth_for_request", apply_auth)
    monkeypatch.setattr(server_app.log, "info", lambda event, payload: logged.append((event, payload)))

    await server_app._AuthGuardMiddleware(endpoint)(_http_scope("/api/session"), receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 401
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    for name, value in server_app._SECURITY_HEADERS.items():
        assert headers.get(name.lower()) == value, name
    assert logged and logged[0][0] == "request.complete"
    assert logged[0][1]["path"] == "/api/session" and logged[0][1]["status"] == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path, expected_status", [
    ("/api/session", 503),
    ("/api/channel/example/webhook", 503),
    ("/api/auth/bootstrap-admin", 503),
    ("/health", 200),
    ("/ready", 200),
])
async def test_critical_plugin_failure_blocks_http_effects_but_keeps_health(
    monkeypatch, path, expected_status,
) -> None:
    import httpx
    from starlette.responses import JSONResponse
    from flocks.plugin import PluginLoader
    from flocks.server import auth

    effects = []

    async def endpoint(scope, receive, send):
        effects.append(scope["path"])
        await JSONResponse({"ok": True})(scope, receive, send)

    async def hook(_request, _context):
        return None

    monkeypatch.setattr(PluginLoader, "_runtime_critical_entrypoint_failure", True)
    monkeypatch.setattr(auth, "_auth_context_adapters", {})
    monkeypatch.setattr(auth, "_is_valid_api_token", lambda _token: True)
    monkeypatch.setattr(server_app, "_run_http_middleware_hooks", hook)
    transport = httpx.ASGITransport(app=server_app._AuthGuardMiddleware(endpoint))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, headers={"Authorization": "Bearer valid"})

    assert response.status_code == expected_status
    assert effects == ([path] if expected_status == 200 else [])


@pytest.mark.asyncio
async def test_critical_plugin_failure_blocks_websocket_auth(monkeypatch) -> None:
    from fastapi import HTTPException
    from starlette.requests import HTTPConnection
    from flocks.plugin import PluginLoader
    from flocks.server import auth

    scope = _http_scope("/api/pty/example/connect")
    scope.update(type="websocket", scheme="ws", headers=[(b"authorization", b"Bearer valid")])
    monkeypatch.setattr(PluginLoader, "_runtime_critical_entrypoint_failure", True)
    monkeypatch.setattr(auth, "_auth_context_adapters", {})
    monkeypatch.setattr(auth, "_is_valid_api_token", lambda _token: True)
    with pytest.raises(HTTPException) as caught:
        _blocked, token, _user = await auth.apply_auth_for_request(HTTPConnection(scope))
        auth.clear_auth_context(token)
    assert caught.value.status_code == 503
