import json
from unittest.mock import MagicMock

import pytest
from starlette.exceptions import HTTPException
from starlette.requests import Request

from flocks.server import app as app_module


def _request(path: str = "/api/test") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        }
    )


@pytest.mark.asyncio
async def test_general_exception_response_does_not_expose_traceback():
    response = await app_module.general_exception_handler(_request(), RuntimeError("secret path detail"))
    body = json.loads(response.body)

    assert response.status_code == 500
    assert body == {
        "error": "InternalServerError",
        "message": "Internal server error",
    }


@pytest.mark.asyncio
async def test_http_4xx_logs_warning(monkeypatch):
    warning = MagicMock()
    error = MagicMock()
    monkeypatch.setattr(app_module.log, "warn", warning)
    monkeypatch.setattr(app_module.log, "error", error)

    response = await app_module.http_exception_handler(
        _request("/missing"),
        HTTPException(status_code=404, detail="Not found"),
    )

    assert response.status_code == 404
    warning.assert_called_once_with(
        "http.error",
        {"path": "/missing", "status": 404, "detail": "Not found"},
    )
    error.assert_not_called()


@pytest.mark.asyncio
async def test_http_5xx_logs_error(monkeypatch):
    warning = MagicMock()
    error = MagicMock()
    monkeypatch.setattr(app_module.log, "warn", warning)
    monkeypatch.setattr(app_module.log, "error", error)

    response = await app_module.http_exception_handler(
        _request("/unavailable"),
        HTTPException(status_code=503, detail="Unavailable"),
    )

    assert response.status_code == 503
    error.assert_called_once_with(
        "http.error",
        {"path": "/unavailable", "status": 503, "detail": "Unavailable"},
    )
    warning.assert_not_called()


@pytest.mark.asyncio
async def test_validation_error_with_raw_bytes_input_stays_422():
    """A non-JSON body makes pydantic echo the raw ``bytes`` back in
    ``errors()[0]["input"]``; the handler must still render a 422 instead of
    blowing up with ``TypeError: Object of type bytes is not JSON serializable``.
    """
    from fastapi.exceptions import RequestValidationError

    exc = RequestValidationError(
        [
            {
                "type": "json_invalid",
                "loc": ("body", 0),
                "msg": "JSON decode error",
                "input": b"username=admin",
                "ctx": {"error": "Expecting value"},
            }
        ]
    )

    response = await app_module.validation_exception_handler(_request("/api/auth/login"), exc)
    body = json.loads(response.body)

    assert response.status_code == 422
    assert body["error"] == "ValidationError"
    assert body["details"][0]["type"] == "json_invalid"
    assert body["details"][0]["input"] == "username=admin"


@pytest.mark.asyncio
async def test_http_exception_headers_are_preserved():
    """A route that attaches headers to its HTTPException (Retry-After on a
    login 429) must see them on the rendered response."""
    response = await app_module.http_exception_handler(
        _request("/api/auth/login"),
        HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试", headers={"Retry-After": "900"}),
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "900"
    assert json.loads(response.body) == {"error": "HTTPException", "message": "登录失败次数过多，请稍后再试"}


class _Refused(Exception):
    pass


class _Wrapper(RuntimeError):
    pass


@pytest.fixture
def error_responses(monkeypatch):
    from flocks import extensions

    registry: dict = {}
    monkeypatch.setattr(extensions, "_error_responses", registry)
    return registry


@pytest.mark.asyncio
async def test_an_extension_answers_an_error_it_registered_for(error_responses):
    from starlette.responses import JSONResponse

    from flocks.extensions import register_error_response, unregister_error_response

    async def answer(request, exc):
        return JSONResponse(status_code=503, content={"error": "Refused", "message": str(exc)})

    register_error_response(_Refused, answer)
    direct = await app_module.general_exception_handler(_request(), _Refused("not now"))
    assert direct.status_code == 503 and json.loads(direct.body) == {"error": "Refused", "message": "not now"}

    # Behind another error too: a store wrapping the driver's error still gets the answer.
    try:
        try:
            raise _Refused("behind")
        except _Refused as cause:
            raise _Wrapper("store unavailable") from cause
    except _Wrapper as wrapped:
        behind = await app_module.general_exception_handler(_request(), wrapped)
    assert behind.status_code == 503 and json.loads(behind.body)["message"] == "behind"

    other = await app_module.general_exception_handler(_request(), RuntimeError("something else"))
    assert other.status_code == 500

    unregister_error_response(_Refused)
    assert (await app_module.general_exception_handler(_request(), _Refused("x"))).status_code == 500


@pytest.mark.asyncio
async def test_a_failing_or_silent_extension_handler_leaves_the_500(error_responses, monkeypatch):
    from flocks.extensions import register_error_response

    warning = MagicMock()
    monkeypatch.setattr(app_module.log, "warn", warning)

    def broken(request, exc):
        raise ValueError("handler bug")

    register_error_response(_Refused, broken)
    response = await app_module.general_exception_handler(_request(), _Refused("x"))
    assert response.status_code == 500
    assert json.loads(response.body) == {"error": "InternalServerError", "message": "Internal server error"}
    warning.assert_called_once()

    register_error_response(_Refused, lambda request, exc: None)
    assert (await app_module.general_exception_handler(_request(), _Refused("x"))).status_code == 500


def test_an_answer_registered_after_the_server_started_reaches_real_requests(error_responses):
    """Through the real ASGI dispatch: the stack is built before the extension registers.

    That is the order in the server (extensions load inside the lifespan), and
    the reason this is an extension point rather than add_exception_handler.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.responses import JSONResponse

    from flocks.extensions import register_error_response, unregister_error_response

    probe = FastAPI()
    probe.add_exception_handler(Exception, app_module.general_exception_handler)

    @probe.get("/refused")
    async def refused():
        raise _Refused("not now")

    @probe.get("/wrapped")
    async def wrapped():
        try:
            raise _Refused("behind")
        except _Refused as cause:
            raise _Wrapper("store unavailable") from cause

    with TestClient(probe, raise_server_exceptions=False) as client:
        assert client.get("/refused").status_code == 500  # the stack is built now

        async def answer(request, exc):
            return JSONResponse(status_code=503, content={"error": "Refused", "message": str(exc)})

        register_error_response(_Refused, answer)
        assert client.get("/refused").json() == {"error": "Refused", "message": "not now"}
        response = client.get("/wrapped")
        assert (response.status_code, response.json()["message"]) == (503, "behind")

        unregister_error_response(_Refused)
        assert client.get("/refused").status_code == 500



def test_the_server_says_it_is_serving_before_extensions_load(monkeypatch):
    """A CLI command that loads the same extensions is not the server; the server's lifespan says so first."""
    from fastapi.testclient import TestClient

    from flocks import extensions

    monkeypatch.setattr(extensions, "_serving", False)
    seen = []
    monkeypatch.setattr(app_module, "_prepare_during_startup", lambda app: seen.append(extensions.serving()))
    assert extensions.serving() is False
    with TestClient(app_module.app):
        assert extensions.serving() is True
    assert seen and seen[0] is True, "set before the extensions.prepare phase"
    assert extensions.serving() is False, "and no longer once the server has stopped"


def test_a_start_that_fails_leaves_the_process_not_serving(monkeypatch):
    from fastapi.testclient import TestClient

    from flocks import extensions

    monkeypatch.setattr(extensions, "_serving", False)

    def broken(app):
        raise RuntimeError("an extension refused to start")

    monkeypatch.setattr(app_module, "_prepare_during_startup", broken)
    with pytest.raises(Exception):
        with TestClient(app_module.app):
            pass
    assert extensions.serving() is False



def test_a_registered_refusal_is_a_response_not_a_crash(error_responses):
    """Answered before the outermost error middleware, which would re-raise for the server to log."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.responses import JSONResponse

    from flocks.extensions import register_error_response

    probe = FastAPI()
    probe.add_exception_handler(Exception, app_module.general_exception_handler)
    probe.add_middleware(app_module._ExtensionErrorResponseMiddleware)

    @probe.get("/refused")
    async def refused():
        raise _Refused("not now")

    @probe.get("/crash")
    async def crash():
        raise RuntimeError("a real crash")

    async def answer(request, exc):
        return JSONResponse(status_code=503, content={"error": "Refused", "message": str(exc)})

    register_error_response(_Refused, answer)
    # raise_server_exceptions (the default) re-raises whatever reached the outermost error middleware.
    with TestClient(probe) as client:
        response = client.get("/refused")
        assert (response.status_code, response.json()) == (503, {"error": "Refused", "message": "not now"})
        with pytest.raises(RuntimeError):
            client.get("/crash")  # anything no extension answers still reaches the catch-all


def test_the_server_answers_refusals_before_its_catch_all():
    middlewares = [m.cls for m in app_module.app.user_middleware]
    assert middlewares[-1] is app_module._ExtensionErrorResponseMiddleware, "innermost: the others see a response"



def test_a_handler_is_asked_once_per_error(error_responses):
    """The inner middleware asks first; when it passes, the catch-all must not ask the same handler again."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from flocks.extensions import register_error_response

    asked = []
    probe = FastAPI()
    probe.add_exception_handler(Exception, app_module.general_exception_handler)
    probe.add_middleware(app_module._ExtensionErrorResponseMiddleware)

    @probe.get("/passed-on")
    async def passed_on():
        raise _Refused("not mine to answer")

    register_error_response(_Refused, lambda request, exc: asked.append(str(exc)))  # answers None
    with TestClient(probe, raise_server_exceptions=False) as client:
        assert client.get("/passed-on").status_code == 500
    assert asked == ["not mine to answer"]
