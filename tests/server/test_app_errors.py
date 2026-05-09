import json

import pytest
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
