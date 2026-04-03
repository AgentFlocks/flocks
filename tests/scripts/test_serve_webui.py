"""Tests for the lightweight WebUI static file server."""

from __future__ import annotations

import importlib.util
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen


def load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "serve_webui.py"
    spec = importlib.util.spec_from_file_location("serve_webui", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


serve_webui = load_module()


@contextmanager
def run_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def make_frontend_handler(dist_dir: Path, proxy_target: str | None = None):
    parsed_target = serve_webui.parse_proxy_target(proxy_target)

    def handler(*args, **kwargs):
        return serve_webui.SPARequestHandler(
            *args,
            directory=str(dist_dir),
            proxy_target=parsed_target,
            **kwargs,
        )

    return handler


def test_resolve_request_path_returns_existing_asset(tmp_path):
    dist_dir = tmp_path / "dist"
    asset_file = dist_dir / "assets" / "app.js"
    asset_file.parent.mkdir(parents=True)
    asset_file.write_text("console.log('ok');", encoding="utf-8")
    (dist_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    resolved = serve_webui.resolve_request_path(dist_dir, "/assets/app.js")

    assert resolved == asset_file.resolve()


def test_resolve_request_path_falls_back_to_index_for_spa_route(tmp_path):
    dist_dir = tmp_path / "dist"
    index_file = dist_dir / "index.html"
    dist_dir.mkdir()
    index_file.write_text("<html></html>", encoding="utf-8")

    resolved = serve_webui.resolve_request_path(dist_dir, "/settings/profile")

    assert resolved == index_file.resolve()


def test_resolve_request_path_preserves_missing_asset_path(tmp_path):
    dist_dir = tmp_path / "dist"
    index_file = dist_dir / "index.html"
    dist_dir.mkdir()
    index_file.write_text("<html></html>", encoding="utf-8")

    resolved = serve_webui.resolve_request_path(dist_dir, "/assets/missing.js")

    assert resolved == (dist_dir / "assets" / "missing.js").resolve()


def test_proxy_forwards_api_requests_to_backend(tmp_path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    seen: dict[str, str] = {}

    class BackendHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen["path"] = self.path
            seen["x_forwarded_host"] = self.headers.get("X-Forwarded-Host", "")
            body = json.dumps({"status": "healthy"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A003
            return

    with run_server(BackendHandler) as backend_server:
        proxy_target = f"http://127.0.0.1:{backend_server.server_port}"
        with run_server(make_frontend_handler(dist_dir, proxy_target=proxy_target)) as frontend_server:
            with urlopen(
                f"http://127.0.0.1:{frontend_server.server_port}/api/health?full=1",
                timeout=5,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))

    assert payload == {"status": "healthy"}
    assert seen["path"] == "/api/health?full=1"
    assert seen["x_forwarded_host"] == f"127.0.0.1:{frontend_server.server_port}"


def test_server_keeps_spa_fallback_for_non_api_routes(tmp_path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html>spa-shell</html>", encoding="utf-8")

    with run_server(make_frontend_handler(dist_dir)) as frontend_server:
        with urlopen(f"http://127.0.0.1:{frontend_server.server_port}/settings/profile", timeout=5) as response:
            body = response.read().decode("utf-8")

    assert response.status == 200
    assert "spa-shell" in body


def test_proxy_streams_sse_responses(tmp_path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    event_payload = b"data: {\"type\":\"ping\"}\n\n"

    class BackendHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):  # noqa: N802
            assert self.path == "/api/event"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(event_payload)))
            self.end_headers()
            self.wfile.write(event_payload)
            self.wfile.flush()

        def log_message(self, format, *args):  # noqa: A003
            return

    with run_server(BackendHandler) as backend_server:
        proxy_target = f"http://127.0.0.1:{backend_server.server_port}"
        with run_server(make_frontend_handler(dist_dir, proxy_target=proxy_target)) as frontend_server:
            with urlopen(f"http://127.0.0.1:{frontend_server.server_port}/api/event", timeout=5) as response:
                body = response.read()
                content_type = response.headers["Content-Type"]
                cache_control = response.headers["Cache-Control"]

    assert body == event_payload
    assert "text/event-stream" in content_type
    assert cache_control == "no-cache"
