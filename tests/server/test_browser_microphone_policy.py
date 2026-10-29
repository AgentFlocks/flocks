"""Browser microphone policy is opt-in and reaches the static SPA shell."""

import pytest
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.testclient import TestClient

from flocks.server import app as server_app


@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("path", ["/", "/api/example"])
def test_api_and_static_shell_use_extension_microphone_policy(monkeypatch, allowed, path):
    app = Starlette()
    app.state.browser_microphone_allowed = allowed

    async def example(request):
        return JSONResponse({"ok": True})

    app.add_route("/api/example", example)

    async def static(request):
        return HTMLResponse("<html>Chat</html>") if request.url.path == "/" else None

    monkeypatch.setattr(server_app, "maybe_serve_static_webui", static)
    app.add_middleware(server_app._SecurityHeadersMiddleware)
    app.add_middleware(server_app._StaticWebUIMiddleware)
    expected = "microphone=(self)" if allowed else "microphone=()"
    with TestClient(app) as client:
        response = client.get(path, timeout=5)
        assert response.status_code == 200
        assert expected in response.headers["permissions-policy"]
        assert "camera=()" in response.headers["permissions-policy"]


def test_missing_or_non_boolean_extension_flag_keeps_microphone_disabled():
    app = Starlette()
    assert "microphone=()" in server_app._security_headers_for_scope({"app": app})["Permissions-Policy"]
    app.state.browser_microphone_allowed = "true"
    assert "microphone=()" in server_app._security_headers_for_scope({"app": app})["Permissions-Policy"]
