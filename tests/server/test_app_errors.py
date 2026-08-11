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
