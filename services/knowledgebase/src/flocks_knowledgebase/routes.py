from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response

from .auth import require_service
from .errors import KBError
from .schemas import DatasetCreate, Documents, LinkFiles, Retrieval, resource_id

router = APIRouter(prefix="/v1", dependencies=[Depends(require_service)])


def path_id(value: str) -> str:
    try:
        return resource_id(value)
    except ValueError:
        raise KBError(422, "invalid_request", "A resource identifier is required.") from None


def _api(request: Request):
    return request.app.state.service


def _filename(name: str) -> str:
    cleaned = "".join(char if 32 <= ord(char) < 127 and char not in {'"', "\\"} else "_" for char in name)
    return cleaned[:180] or "download"


@router.get("/files")
async def files(
    request: Request,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    return {"data": await _api(request).list_files(page=page, page_size=page_size, keywords=q or None)}


@router.post("/files", status_code=201)
async def upload_file(request: Request, file: UploadFile = File(...)):
    content = await file.read()
    limit = request.app.state.settings.max_upload_bytes
    if len(content) > limit:
        raise KBError(413, "request_too_large", "The request exceeds the configured body limit.")
    name = file.filename or "upload"
    if "/" in name or "\\" in name or name in {".", ".."} or len(name) > 255:
        raise KBError(400, "invalid_request", "A file name is required.")
    created = await _api(request).upload(name, content, file.content_type or "application/octet-stream")
    return {"data": created}


@router.get("/files/{file_id}/content")
async def content(file_id: str, request: Request):
    ident = path_id(file_id)
    body, media_type = await _api(request).download(ident)
    return Response(
        content=body,
        media_type=media_type.split(";", 1)[0].strip() or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{_filename(ident)}"'},
    )


@router.delete("/files/{file_id}", status_code=204)
async def delete_file(file_id: str, request: Request):
    await _api(request).delete_file(path_id(file_id))
    return Response(status_code=204)


@router.get("/datasets")
async def datasets(
    request: Request,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    return {"data": await _api(request).list_datasets(page=page, page_size=page_size, keywords=q or None)}


@router.post("/datasets", status_code=201)
async def create_dataset(payload: DatasetCreate, request: Request):
    return {"data": await _api(request).create_dataset(payload)}


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, request: Request):
    return {"data": await _api(request).get_dataset(path_id(dataset_id))}


@router.delete("/datasets/{dataset_id}", status_code=204)
async def delete_dataset(dataset_id: str, request: Request):
    await _api(request).delete_dataset(path_id(dataset_id))
    return Response(status_code=204)


@router.get("/datasets/{dataset_id}/documents")
async def documents(
    dataset_id: str,
    request: Request,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    return {
        "data": await _api(request).list_documents(
            path_id(dataset_id), page=page, page_size=page_size, keywords=q or None
        )
    }


@router.post("/datasets/{dataset_id}/files")
async def link_files(dataset_id: str, payload: LinkFiles, request: Request):
    return {"data": await _api(request).link_files(path_id(dataset_id), payload)}


@router.delete("/datasets/{dataset_id}/documents")
async def remove_documents(dataset_id: str, payload: Documents, request: Request):
    return {"data": await _api(request).remove_documents(path_id(dataset_id), payload)}


@router.post("/datasets/{dataset_id}/parse")
async def parse_documents(dataset_id: str, payload: Documents, request: Request):
    return {"data": await _api(request).parse(path_id(dataset_id), payload)}


@router.post("/retrieval")
async def retrieval(payload: Retrieval, request: Request):
    return {"data": await _api(request).retrieve(payload)}
