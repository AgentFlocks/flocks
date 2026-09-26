import logging
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .errors import KBError
from .ragflow import RagflowClient
from .routes import router
from .service import KnowledgeAPI
from .settings import Settings

logger = logging.getLogger(__name__)


def error_response(error: KBError, request_id: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": error.code, "message": error.message, "details": error.details, "request_id": request_id}},
        status_code=error.status,
        headers={"WWW-Authenticate": "Bearer"} if error.status == 401 else None,
    )


class RequestBoundary:
    def __init__(self, app, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        candidate = headers.get(b"x-request-id", b"").decode("ascii", errors="ignore")
        request_id = candidate if re.fullmatch(r"[A-Za-z0-9-]{1,64}", candidate) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = False
        count = 0
        exceeded = False
        replacement = None
        replacement_sent = False

        async def safe_send(message):
            nonlocal started, replacement, replacement_sent
            if message["type"] == "http.response.start":
                started = True
                if exceeded:
                    replacement = error_response(
                        KBError(413, "request_too_large", "The request exceeds the configured body limit."), request_id
                    )
                    message = {"type": "http.response.start", "status": 413, "headers": replacement.raw_headers}
                response_headers = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"x-request-id"]
                response_headers.append((b"x-request-id", request_id.encode()))
                response_headers.append((b"x-content-type-options", b"nosniff"))
                message = {**message, "headers": response_headers}
            elif message["type"] == "http.response.body" and replacement is not None:
                if replacement_sent:
                    return
                replacement_sent = True
                message = {"type": "http.response.body", "body": replacement.body, "more_body": False}
            await send(message)

        async def limited_receive():
            nonlocal count, exceeded
            message = await receive()
            if message["type"] == "http.request":
                count += len(message.get("body", b""))
                if count > self.max_body_bytes:
                    exceeded = True
                    raise KBError(413, "request_too_large", "The request exceeds the configured body limit.")
            return message

        try:
            length = headers.get(b"content-length")
            if length is not None:
                try:
                    parsed_length = int(length)
                    if parsed_length < 0:
                        raise ValueError("negative length")
                    too_large = parsed_length > self.max_body_bytes
                except ValueError:
                    raise KBError(400, "invalid_content_length", "Invalid request length.") from None
                if too_large:
                    raise KBError(413, "request_too_large", "The request exceeds the configured body limit.")
            await self.app(scope, limited_receive, safe_send)
        except KBError as exc:
            if not started:
                await error_response(exc, request_id)(scope, receive, safe_send)
        except Exception:
            logger.error("Request failed: request_id=%s", request_id)
            if not started:
                await error_response(
                    KBError(500, "internal_error", "The service could not complete this request."), request_id
                )(scope, receive, safe_send)


def create_app(settings: Settings | None = None, *, ragflow=None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = ragflow or RagflowClient(
            settings.ragflow_base_url,
            settings.ragflow_api_key.get_secret_value(),
            timeout=settings.request_timeout_seconds,
            max_content_bytes=settings.max_upload_bytes,
        )
        app.state.service = KnowledgeAPI(engine)
        app.state.ragflow = engine
        try:
            yield
        finally:
            if ragflow is None:
                await engine.close()

    app = FastAPI(title="Flocks Knowledgebase service", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(RequestBoundary, max_body_bytes=settings.max_upload_bytes + 512 * 1024)

    @app.exception_handler(KBError)
    async def service_error(request: Request, exc: KBError):
        return error_response(exc, getattr(request.state, "request_id", ""))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = {"fields": [{"location": list(error["loc"]), "type": error["type"]} for error in exc.errors()]}
        return error_response(
            KBError(422, "validation_error", "Invalid request fields.", details),
            getattr(request.state, "request_id", ""),
        )

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "version": __version__}

    app.include_router(router)
    return app
