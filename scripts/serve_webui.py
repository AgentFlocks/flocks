#!/usr/bin/env python3
"""Serve the built WebUI bundle with SPA fallback and backend proxying."""

from __future__ import annotations

import argparse
import http.client
import posixpath
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import SplitResult, unquote, urlsplit

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

SUPPORTED_PROXY_METHODS = ("DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT")


def parse_proxy_target(proxy_target: str | None) -> SplitResult | None:
    """Parse and validate an optional backend proxy target."""
    if not proxy_target:
        return None
    parsed = urlsplit(proxy_target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Unsupported proxy target: {proxy_target}")
    return parsed


def resolve_request_path(root: Path, request_path: str) -> Path:
    """Resolve a request path to a file under root with SPA fallback."""
    parsed_path = urlsplit(request_path).path or "/"
    normalized = posixpath.normpath(unquote(parsed_path))
    relative_path = normalized.lstrip("/")
    candidate = (root / relative_path).resolve()

    if candidate != root and root not in candidate.parents:
        return root / "index.html"

    if candidate.is_dir():
        index_file = candidate / "index.html"
        if index_file.exists():
            return index_file

    if candidate.exists():
        return candidate

    if Path(relative_path).suffix:
        return candidate

    return root / "index.html"


def should_proxy_request(request_path: str) -> bool:
    """Return True when the request should be forwarded to the backend."""
    request_route = urlsplit(request_path).path or "/"
    return request_route == "/api" or request_route.startswith("/api/") or request_route == "/event"


class SPARequestHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves static assets and falls back to index.html."""

    protocol_version = "HTTP/1.1"

    def __init__(
        self,
        *args,
        directory: str | None = None,
        proxy_target: SplitResult | None = None,
        **kwargs,
    ):
        self.root = Path(directory or ".").resolve()
        self.proxy_target = proxy_target
        super().__init__(*args, directory=str(self.root), **kwargs)

    def translate_path(self, path: str) -> str:
        return str(resolve_request_path(self.root, path))

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle_method()

    def do_GET(self) -> None:  # noqa: N802
        self._handle_method()

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle_method()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._handle_method()

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle_method()

    def do_POST(self) -> None:  # noqa: N802
        self._handle_method()

    def do_PUT(self) -> None:  # noqa: N802
        self._handle_method()

    def _handle_method(self) -> None:
        if self.command not in SUPPORTED_PROXY_METHODS:
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED, "Unsupported method")
            return
        if self.proxy_target is not None and should_proxy_request(self.path):
            self._proxy_request()
            return
        if self.command == "GET":
            super().do_GET()
            return
        if self.command == "HEAD":
            super().do_HEAD()
            return
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED, "Unsupported method for static content")

    def _proxy_request(self) -> None:
        if self.proxy_target is None:
            self.send_error(HTTPStatus.BAD_GATEWAY, "Proxy target is not configured")
            return
        connection_cls = http.client.HTTPSConnection if self.proxy_target.scheme == "https" else http.client.HTTPConnection
        upstream_port = self.proxy_target.port or (443 if self.proxy_target.scheme == "https" else 80)
        upstream_path = self._build_upstream_path()
        upstream_body = self._read_request_body()
        upstream_headers = self._build_upstream_headers()
        connection = connection_cls(self.proxy_target.hostname, upstream_port, timeout=60)
        response: http.client.HTTPResponse | None = None
        try:
            connection.request(self.command, upstream_path, body=upstream_body, headers=upstream_headers)
            response = connection.getresponse()
            content_type = response.getheader("Content-Type", "")
            is_event_stream = "text/event-stream" in content_type.lower()
            self.send_response(response.status, response.reason)
            header_names: set[str] = set()
            for header_name, header_value in response.getheaders():
                normalized_name = header_name.lower()
                if normalized_name in HOP_BY_HOP_HEADERS:
                    continue
                header_names.add(normalized_name)
                self.send_header(header_name, header_value)
            if is_event_stream:
                if "cache-control" not in header_names:
                    self.send_header("Cache-Control", "no-cache")
                if "x-accel-buffering" not in header_names:
                    self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            if self.command == "HEAD":
                return
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        except OSError as error:
            self.send_error(HTTPStatus.BAD_GATEWAY, f"Failed to proxy request: {error}")
        finally:
            if response is not None:
                response.close()
            connection.close()

    def _build_upstream_path(self) -> str:
        request_target = urlsplit(self.path)
        base_path = self.proxy_target.path.rstrip("/") if self.proxy_target is not None else ""
        upstream_path = f"{base_path}{request_target.path}" or "/"
        if request_target.query:
            return f"{upstream_path}?{request_target.query}"
        return upstream_path

    def _build_upstream_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        for header_name, header_value in self.headers.items():
            if header_name.lower() in HOP_BY_HOP_HEADERS or header_name.lower() == "host":
                continue
            headers[header_name] = header_value
        client_host = self.client_address[0] if self.client_address else ""
        forwarded_for = self.headers.get("X-Forwarded-For")
        headers["X-Forwarded-For"] = f"{forwarded_for}, {client_host}" if forwarded_for else client_host
        headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        headers["X-Forwarded-Proto"] = "https" if getattr(self.server, "server_port", 0) == 443 else "http"
        return headers

    def _read_request_body(self) -> bytes | None:
        content_length = self.headers.get("Content-Length")
        if not content_length:
            return None
        try:
            length = int(content_length)
        except ValueError:
            return None
        return self.rfile.read(length) if length > 0 else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve built WebUI assets.")
    parser.add_argument("--directory", required=True, help="Directory containing the built WebUI.")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind.")
    parser.add_argument("--port", type=int, default=5173, help="Port to bind.")
    parser.add_argument("--proxy-target", default=None, help="Optional backend target for /api and /event requests.")
    args = parser.parse_args()

    root = Path(args.directory).resolve()
    index_file = root / "index.html"
    if not index_file.exists():
        raise SystemExit(f"WebUI build artifact not found: {index_file}")
    try:
        proxy_target = parse_proxy_target(args.proxy_target)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    handler = lambda *handler_args, **handler_kwargs: SPARequestHandler(  # noqa: E731
        *handler_args,
        directory=str(root),
        proxy_target=proxy_target,
        **handler_kwargs,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
