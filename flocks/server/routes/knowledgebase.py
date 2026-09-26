"""BFF for the knowledgebase service. Flocks authenticates the user; the service token stays here."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from flocks.knowledgebase.errors import KnowledgebaseError
from flocks.knowledgebase.retrieval import retrieve_for_session
from flocks.knowledgebase.runtime import get_client
from flocks.knowledgebase.session_datasets import detach_dataset, get_selection, set_selection
from flocks.server.auth import require_user


class DatasetBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)


class FileIds(BaseModel):
    file_ids: list[str] = Field(min_length=1, max_length=100)


class DocumentIds(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=100)


class SessionDatasetsBody(BaseModel):
    dataset_ids: list[str] = Field(default_factory=list, max_length=50)


class RetrievalBody(BaseModel):
    keywords: str = Field(min_length=1, max_length=8000)
    dataset: list[str] | None = None
    top_k: int = Field(default=5, ge=1, le=100)
    similarity_threshold: float = Field(default=0.2, ge=0, le=1)
    vector_similarity_weight: float = Field(default=0.3, ge=0, le=1)


# Same inline types as the workspace preview. Anything else is always a download.
_PREVIEW_MEDIA_TYPES = frozenset({
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
})
_SVG_PREVIEW_CSP = (
    "sandbox; default-src 'none'; script-src 'none'; "
    "object-src 'none'; base-uri 'none'; img-src data: blob:; "
    "style-src 'unsafe-inline'"
)


def _media_type(value: str) -> str:
    media = value.split(";", 1)[0].strip().lower()
    if not media or "/" not in media or any(ord(char) < 33 or ord(char) == 127 for char in media):
        return "application/octet-stream"
    return media


def file_content_response(body: bytes, media: str, *, inline: bool) -> Response:
    """Serve bytes without letting the browser treat an upload as a Flocks page."""
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "frame-ancestors 'none'",
    }
    media_type = _media_type(media)
    if inline and media_type in _PREVIEW_MEDIA_TYPES:
        headers["Content-Disposition"] = "inline"
        if media_type == "image/svg+xml":
            headers["Content-Security-Policy"] = _SVG_PREVIEW_CSP
        return Response(content=body, media_type=media_type, headers=headers)
    headers["Content-Disposition"] = "attachment"
    return Response(content=body, media_type="application/octet-stream", headers=headers)


def create_router() -> APIRouter:
    router = APIRouter(prefix="/knowledgebase", tags=["knowledgebase"])

    def client():
        current = get_client()
        if current is None:
            raise KnowledgebaseError(503, "knowledgebase_not_configured", "Knowledgebase is not configured.")
        return current

    def _error(exc: KnowledgebaseError) -> JSONResponse:
        return JSONResponse(exc.public(), status_code=exc.status)

    class UploadLimitRoute(APIRoute):
        def get_route_handler(self):
            handler = super().get_route_handler()

            async def limited_upload(request: Request):
                try:
                    limit = client().connection.max_upload_bytes
                    declared = request.headers.get("content-length")
                    if declared is not None:
                        try:
                            if int(declared) > limit:
                                raise KnowledgebaseError(413, "request_too_large", "The file exceeds the upload limit.")
                        except ValueError:
                            raise KnowledgebaseError(400, "invalid_request", "The upload length is invalid.") from None
                except KnowledgebaseError as exc:
                    return _error(exc)

                received = 0
                overflow = False

                async def bounded_receive():
                    nonlocal received, overflow
                    message = await request.receive()
                    if message["type"] == "http.request":
                        received += len(message.get("body", b""))
                        if received > limit + 512 * 1024:
                            overflow = True
                            # This exception makes the multipart parser close partial files.
                            raise MultiPartException("The file exceeds the upload limit.")
                    return message

                try:
                    return await handler(Request(request.scope, receive=bounded_receive))
                except StarletteHTTPException:
                    if not overflow:
                        raise
                    return _error(KnowledgebaseError(413, "request_too_large", "The file exceeds the upload limit."))

            return limited_upload

    @router.get("/status")
    async def status(_user=Depends(require_user)):
        configured = get_client() is not None
        return {"data": {"configured": configured, "ready": configured}}

    @router.get("/files")
    async def files(
        page: int = Query(default=1, ge=1, le=10000),
        page_size: int = Query(default=20, ge=1, le=100),
        q: str | None = Query(default=None, max_length=200),
        _user=Depends(require_user),
    ):
        try:
            return {"data": await client().files(page=page, page_size=page_size, q=q)}
        except KnowledgebaseError as exc:
            return _error(exc)

    async def upload(request: Request, file: UploadFile = File(...), _user=Depends(require_user)):
        try:
            current = client()
            limit = current.connection.max_upload_bytes
            chunks: list[bytes] = []
            size = 0
            while True:
                block = await file.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > limit:
                    raise KnowledgebaseError(413, "request_too_large", "The file exceeds the upload limit.")
                chunks.append(block)
            created = await current.upload(
                file.filename or "upload",
                b"".join(chunks),
                file.content_type or "application/octet-stream",
            )
            return {"data": created}
        except KnowledgebaseError as exc:
            return _error(exc)

    router.add_api_route("/files", upload, methods=["POST"], status_code=201, route_class_override=UploadLimitRoute)

    @router.get("/files/{file_id}/content")
    async def download(
        file_id: str,
        inline: bool = Query(default=False),
        _user=Depends(require_user),
    ):
        try:
            body, media = await client().content(file_id)
        except KnowledgebaseError as exc:
            return _error(exc)
        return file_content_response(body, media, inline=inline)

    @router.delete("/files/{file_id}", status_code=204)
    async def delete_file(file_id: str, _user=Depends(require_user)):
        try:
            await client().delete_file(file_id)
        except KnowledgebaseError as exc:
            return _error(exc)
        return Response(status_code=204)

    @router.get("/datasets")
    async def datasets(
        page: int = Query(default=1, ge=1, le=10000),
        page_size: int = Query(default=20, ge=1, le=100),
        q: str | None = Query(default=None, max_length=200),
        _user=Depends(require_user),
    ):
        try:
            return {"data": await client().datasets(page=page, page_size=page_size, q=q)}
        except KnowledgebaseError as exc:
            return _error(exc)

    @router.post("/datasets", status_code=201)
    async def create_dataset(body: DatasetBody, _user=Depends(require_user)):
        try:
            return {"data": await client().create_dataset(body.model_dump())}
        except KnowledgebaseError as exc:
            return _error(exc)

    @router.get("/datasets/{dataset_id}")
    async def dataset(dataset_id: str, _user=Depends(require_user)):
        try:
            return {"data": await client().dataset(dataset_id)}
        except KnowledgebaseError as exc:
            return _error(exc)

    @router.delete("/datasets/{dataset_id}", status_code=204)
    async def delete_dataset(dataset_id: str, _user=Depends(require_user)):
        try:
            await client().delete_dataset(dataset_id)
            await detach_dataset(dataset_id)
        except KnowledgebaseError as exc:
            return _error(exc)
        return Response(status_code=204)

    @router.get("/datasets/{dataset_id}/documents")
    async def documents(
        dataset_id: str,
        page: int = Query(default=1, ge=1, le=10000),
        page_size: int = Query(default=20, ge=1, le=100),
        q: str | None = Query(default=None, max_length=200),
        _user=Depends(require_user),
    ):
        try:
            return {"data": await client().documents(dataset_id, page=page, page_size=page_size, q=q)}
        except KnowledgebaseError as exc:
            return _error(exc)

    @router.post("/datasets/{dataset_id}/files")
    async def link_files(dataset_id: str, body: FileIds, _user=Depends(require_user)):
        try:
            return {"data": await client().link_files(dataset_id, body.file_ids)}
        except KnowledgebaseError as exc:
            return _error(exc)

    @router.delete("/datasets/{dataset_id}/documents")
    async def remove_documents(dataset_id: str, body: DocumentIds, _user=Depends(require_user)):
        try:
            return {"data": await client().remove_documents(dataset_id, body.document_ids)}
        except KnowledgebaseError as exc:
            return _error(exc)

    @router.post("/datasets/{dataset_id}/parse")
    async def parse(dataset_id: str, body: DocumentIds, _user=Depends(require_user)):
        try:
            return {"data": await client().parse(dataset_id, body.document_ids)}
        except KnowledgebaseError as exc:
            return _error(exc)

    @router.get("/sessions/{session_id}/datasets")
    async def session_datasets(session_id: str, request: Request):
        try:
            return {"data": await get_selection(session_id, require_user(request))}
        except KnowledgebaseError as exc:
            return _error(exc)

    @router.put("/sessions/{session_id}/datasets")
    async def set_session_datasets(session_id: str, body: SessionDatasetsBody, request: Request):
        try:
            return {"data": await set_selection(session_id, require_user(request), body.dataset_ids)}
        except KnowledgebaseError as exc:
            return _error(exc)

    @router.post("/sessions/{session_id}/retrieval")
    async def session_retrieval(session_id: str, body: RetrievalBody, request: Request):
        try:
            result = await retrieve_for_session(
                session_id,
                require_user(request),
                body.keywords,
                client=client(),
                dataset=body.dataset,
                top_k=body.top_k,
                similarity_threshold=body.similarity_threshold,
                vector_similarity_weight=body.vector_similarity_weight,
            )
            return {"data": result}
        except KnowledgebaseError as exc:
            return _error(exc)

    return router
