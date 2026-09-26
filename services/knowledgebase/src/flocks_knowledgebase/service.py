"""Stateless projection of RAGFlow file, dataset and retrieval calls."""

from __future__ import annotations

from typing import Any

from .errors import UpstreamError
from .schemas import DatasetCreate, Documents, LinkFiles, Retrieval

def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _optional_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def file_view(item: dict) -> dict:
    if not isinstance(item.get("id"), str) or not item["id"] or not isinstance(item.get("name"), str):
        raise UpstreamError("upstream_invalid_response")
    return {
        "id": item["id"],
        "name": item["name"],
        "size": _optional_int(item.get("size")),
        "parent_id": item.get("parent_id") if isinstance(item.get("parent_id"), str) else None,
    }


def dataset_view(item: dict) -> dict:
    if not isinstance(item.get("id"), str) or not item["id"] or not isinstance(item.get("name"), str):
        raise UpstreamError("upstream_invalid_response")
    return {
        "id": item["id"],
        "name": item["name"],
        "description": _text(item.get("description")),
        "document_count": _optional_int(item.get("document_count")),
        "chunk_count": _optional_int(item.get("chunk_count")),
    }


def document_view(dataset_id: str, item: dict) -> dict:
    if not isinstance(item.get("id"), str) or not item["id"] or not isinstance(item.get("name"), str):
        raise UpstreamError("upstream_invalid_response")
    progress = item.get("progress")
    return {
        "id": item["id"],
        "dataset_id": dataset_id,
        "name": item["name"],
        "status": _text(item.get("run") or item.get("status"), "unknown"),
        "progress": progress if isinstance(progress, (int, float)) and 0 <= progress <= 1 else None,
        "chunk_count": _optional_int(item.get("chunk_count")),
    }


def chunk_view(item: dict) -> dict | None:
    content = item.get("content")
    if not isinstance(content, str):
        return None
    return {
        "id": _text(item.get("id")),
        "content": content,
        "dataset_id": _text(item.get("dataset_id")),
        "document_id": _text(item.get("document_id")),
        "document_name": _text(item.get("document_keyword") or item.get("docnm_kwd") or item.get("document_name")),
        "similarity": item.get("similarity") if isinstance(item.get("similarity"), (int, float)) else None,
    }


def _page(items: list, total: Any, page: int, keywords: str | None) -> dict:
    if keywords:
        needle = keywords.casefold()
        items = [item for item in items if needle in item["name"].casefold()]
        total = len(items)
    elif type(total) is not int or total < 0:
        total = len(items)
    return {"items": items, "total": total, "page": page}


class KnowledgeAPI:
    def __init__(self, ragflow: Any):
        self.ragflow = ragflow

    async def list_files(self, *, page: int, page_size: int, keywords: str | None) -> dict:
        raw = await self.ragflow.list_files(page=page, page_size=page_size, keywords=keywords)
        files = [item for item in raw.get("files", []) if isinstance(item, dict)]
        hidden = sum(1 for item in files if item.get("type") == "folder")
        items = [file_view(item) for item in files if item.get("type") != "folder"]
        total = raw.get("total", len(items))
        if type(total) is int:
            total = max(0, total - hidden)
        return _page(items, total, page, keywords)

    async def upload(self, filename: str, content: bytes, content_type: str) -> dict:
        created = await self.ragflow.upload_file(filename, content, content_type=content_type)
        return file_view(created)

    async def download(self, file_id: str) -> tuple[bytes, str]:
        return await self.ragflow.download_file(file_id)

    async def delete_file(self, file_id: str) -> None:
        await self.ragflow.delete_files([file_id])

    async def list_datasets(self, *, page: int, page_size: int, keywords: str | None) -> dict:
        raw = await self.ragflow.list_datasets(page=page, page_size=page_size)
        items = [dataset_view(item) for item in raw["datasets"]]
        return _page(items, raw["total"], page, keywords)

    async def get_dataset(self, dataset_id: str) -> dict:
        return dataset_view(await self.ragflow.get_dataset(dataset_id))

    async def create_dataset(self, payload: DatasetCreate) -> dict:
        created = await self.ragflow.create_dataset(
            {"name": payload.name, "description": payload.description}
        )
        return dataset_view(created)

    async def delete_dataset(self, dataset_id: str) -> None:
        await self.ragflow.delete_dataset(dataset_id)

    async def list_documents(self, dataset_id: str, *, page: int, page_size: int, keywords: str | None) -> dict:
        raw = await self.ragflow.list_documents(dataset_id, page=page, page_size=page_size)
        items = [document_view(dataset_id, item) for item in raw.get("docs", [])]
        return _page(items, raw.get("total", len(items)), page, keywords)

    async def link_files(self, dataset_id: str, payload: LinkFiles) -> dict:
        await self.ragflow.link_files(payload.file_ids, [dataset_id])
        return {"dataset_id": dataset_id, "file_ids": payload.file_ids}

    async def remove_documents(self, dataset_id: str, payload: Documents) -> dict:
        await self.ragflow.remove_documents(dataset_id, payload.document_ids)
        return {"dataset_id": dataset_id, "document_ids": payload.document_ids}

    async def parse(self, dataset_id: str, payload: Documents) -> dict:
        await self.ragflow.parse_documents(dataset_id, payload.document_ids)
        return {"dataset_id": dataset_id, "document_ids": payload.document_ids}

    async def retrieve(self, payload: Retrieval) -> dict:
        result = await self.ragflow.retrieve(
            {
                "dataset_ids": payload.dataset_ids,
                "question": payload.keywords,
                "page": 1,
                "page_size": payload.top_k,
                "similarity_threshold": payload.similarity_threshold,
                "vector_similarity_weight": payload.vector_similarity_weight,
            }
        )
        chunks = []
        for item in result.get("chunks", []):
            if not isinstance(item, dict):
                continue
            view = chunk_view(item)
            if view is not None and view["dataset_id"] in payload.dataset_ids:
                chunks.append(view)
        return {"chunks": chunks[: payload.top_k], "total": len(chunks)}
