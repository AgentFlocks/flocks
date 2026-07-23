"""Dependency-light HTTP server for native Strix chat sessions."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import signal
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from strix_chat_api.manager import (
    ChatManager,
    ChatNotFoundError,
    ChatNotReadyError,
    ChatValidationError,
)
from strix_chat_api.routing import chat_route

MAX_REQUEST_BYTES = 1_048_576


class ChatApiServer(ThreadingHTTPServer):
    """HTTP server carrying its manager and optional bearer token."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        manager: ChatManager,
        token: str | None,
    ) -> None:
        super().__init__(server_address, ChatApiHandler)
        self.manager = manager
        self.token = token


class ChatApiHandler(BaseHTTPRequestHandler):
    """Serve the small Strix Chat REST surface."""

    server: ChatApiServer

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        chat_id, action = chat_route(parsed.path)
        if chat_id is None and action == "create":
            self._json(HTTPStatus.OK, self.server.manager.list_chats())
            return
        if chat_id is None or action is not None:
            self._json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})
            return
        after_value = parse_qs(parsed.query).get("after", ["0"])[0]
        try:
            after = max(0, int(after_value))
            self._json(HTTPStatus.OK, self.server.manager.get_chat(chat_id, after=after))
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"detail": "after must be an integer"})
        except ChatNotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"detail": "Chat not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        parsed = urlparse(self.path)
        try:
            payload = self._body()
        except ChatValidationError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
            return

        chat_id, action = chat_route(parsed.path)
        try:
            if chat_id is None and action == "create":
                response = self.server.manager.start_chat(
                    message=str(payload.get("message") or ""),
                    targets=payload.get("targets"),
                    scan_mode=str(payload.get("scan_mode") or "standard"),
                    max_budget_usd=payload.get("max_budget_usd"),
                    model=payload.get("model"),
                )
                self._json(HTTPStatus.ACCEPTED, response)
                return
            if chat_id is not None and action == "message":
                response = self.server.manager.send_message(
                    chat_id,
                    str(payload.get("message") or ""),
                )
                self._json(HTTPStatus.ACCEPTED, response)
                return
        except ChatValidationError as exc:
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"detail": str(exc)})
            return
        except ChatNotReadyError as exc:
            self._json(HTTPStatus.CONFLICT, {"detail": str(exc)})
            return
        except ChatNotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"detail": "Chat not found"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        parsed = urlparse(self.path)
        chat_id, action = chat_route(parsed.path)
        if chat_id is None or action is not None:
            self._json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})
            return
        try:
            self.server.manager.delete_chat(chat_id)
        except ChatNotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"detail": "Chat not found"})
            return
        self._json(HTTPStatus.OK, {"id": chat_id, "deleted": True})

    def _authorized(self) -> bool:
        expected = self.server.token
        if not expected:
            return True
        provided = self.headers.get("Authorization", "")
        valid = provided.startswith("Bearer ") and hmac.compare_digest(
            provided.removeprefix("Bearer ").strip(),
            expected,
        )
        if not valid:
            self._json(HTTPStatus.UNAUTHORIZED, {"detail": "Unauthorized"})
        return valid

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ChatValidationError("Invalid Content-Length") from exc
        if length <= 0:
            return {}
        if length > MAX_REQUEST_BYTES:
            raise ChatValidationError("Request body is too large")
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ChatValidationError("Request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ChatValidationError("Request body must be a JSON object")
        return payload

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode()
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    """Run the local Strix Chat API until interrupted."""
    parser = argparse.ArgumentParser(description="Local HTTP chat API for Strix")
    parser.add_argument("--host", default=os.environ.get("STRIX_CHAT_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("STRIX_CHAT_PORT", "8486")),
    )
    args = parser.parse_args()
    token = os.environ.get("STRIX_CHAT_API_TOKEN") or None
    if args.host not in {"127.0.0.1", "::1", "localhost"} and not token:
        parser.error("STRIX_CHAT_API_TOKEN is required when binding beyond loopback")

    manager = ChatManager()
    server = ChatApiServer((args.host, args.port), manager, token)
    stop = threading.Event()

    def handle_signal(_signum: int, _frame: Any) -> None:
        if stop.is_set():
            return
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    try:
        print(f"Strix Chat API listening on http://{args.host}:{args.port}")  # noqa: T201
        server.serve_forever()
    finally:
        server.server_close()
        manager.close()


if __name__ == "__main__":
    main()
