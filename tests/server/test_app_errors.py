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
