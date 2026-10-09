"""Stateless projection of RAGFlow file, dataset and retrieval calls."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

from .errors import KBError, UpstreamError
from .schemas import DatasetCreate, DatasetUpdate, Documents, FileUpdate, FolderCreate, LinkFiles, Retrieval, resource_id

_FILE_SCAN_PAGE_SIZE = 100
_FILE_SCAN_MAX_PAGES = 100
_FILE_SCAN_TIMEOUT_SECONDS = 25
# v0.27.2 api.db constants, not inferred resource IDs or paths. The skills root
# and reused legacy knowledgebase roots do not always have a source marker.
_RESERVED_ROOT_NAMES = frozenset({".knowledgebase", "skills"})
_MAX_ANCESTORS = 128


def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _optional_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _created_at(value: Any) -> str | None:
    # RAGFlow v0.27.2 create_time is Unix milliseconds on the resource itself.
    if type(value) is not int or value < 0:
        return None
    try:
        created = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)
        return created.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (OverflowError, ValueError):
        return None


def file_view(item: dict) -> dict:
    if not isinstance(item.get("id"), str) or not item["id"] or not isinstance(item.get("name"), str):
        raise UpstreamError("upstream_invalid_response")
    return {
        "id": item["id"],
        "name": item["name"],
        "size": _optional_int(item.get("size")),
        "created_at": _created_at(item.get("create_time")),
        "updated_at": _created_at(item.get("update_time")),
        "parent_id": item.get("parent_id") if isinstance(item.get("parent_id"), str) else None,
    }


def _resource_metadata(item: dict) -> None:
    if not isinstance(item, dict):
        raise UpstreamError("upstream_invalid_response")
    try:
        resource_id(item.get("id"))
        resource_id(item.get("parent_id"))
    except ValueError:
        raise UpstreamError("upstream_invalid_response") from None
    if not isinstance(item.get("name"), str) or not item["name"] or not isinstance(item.get("type"), str) or not item["type"]:
        raise UpstreamError("upstream_invalid_response")


def _writable_chain(chain: list[dict]) -> bool:
    # Missing source_type is intentionally not treated as the upstream LOCAL="".
    root_id = chain[-1]["id"]
    return all(
        item.get("source_type") == ""
        and not (item["id"] != root_id and item["parent_id"] == root_id and item["name"].casefold() in _RESERVED_ROOT_NAMES)
        for item in chain
    )


def _folder_view(chain: list[dict]) -> dict:
    item = chain[0]
    return {"id": item["id"], "name": item["name"], "parent_id": item["parent_id"], "can_write": _writable_chain(chain)}


def _directory_entry(item: dict, parent_chain: list[dict]) -> dict:
    _resource_metadata(item)
    return {
        **file_view(item),
        "kind": "folder" if item["type"] == "folder" else "file",
        "can_manage": item["id"] != item["parent_id"] and _writable_chain([item, *parent_chain]),
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
        # Document listings do not expose File2Document.file_id in v0.27.2.
        "source_file_id": None,
        "created_at": _created_at(item.get("create_time")),
        "updated_at": _created_at(item.get("update_time")),
        "size": _optional_int(item.get("size")),
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
        # Keep the lightweight single-page contract used by connection probes.
        raw = await self.ragflow.list_files(page=page, page_size=page_size, keywords=keywords)
        files = [item for item in raw.get("files", []) if isinstance(item, dict)]
        hidden = sum(1 for item in files if item.get("type") == "folder")
        items = [file_view(item) for item in files if item.get("type") != "folder"]
        total = raw.get("total", len(items))
        if type(total) is int:
            total = max(0, total - hidden)
        return _page(items, total, page, keywords)

    async def list_source_files(self, *, page: int, page_size: int, keywords: str | None) -> dict:
        try:
            return await asyncio.wait_for(
                self._list_source_files(page=page, page_size=page_size, keywords=keywords),
                timeout=_FILE_SCAN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            raise UpstreamError("upstream_timeout") from None

    async def _list_source_files(self, *, page: int, page_size: int, keywords: str | None) -> dict:
        rows, _ = await self._scan_files()
        items = [file_view(item) for item in rows if item.get("type") != "folder"]
        result = _page(items, len(items), page, keywords)
        start = (page - 1) * page_size
        result["items"] = result["items"][start:start + page_size]
        return result

    async def _scan_files(self, parent_id: str | None = None) -> tuple[list[dict], dict | None]:
        # v0.27.2 keywords traverses subfolders. Never pass it here, and never
        # recursively visit folders. This is one bounded, complete directory.
        total = None
        parent = None
        seen_ids: set[str] = set()
        items = []
        for source_page in range(1, _FILE_SCAN_MAX_PAGES + 1):
            raw = await self.ragflow.list_files(parent_id, page=source_page, page_size=_FILE_SCAN_PAGE_SIZE)
            if total is None:
                total = raw["total"]
                parent = raw["parent_folder"]
                if total > _FILE_SCAN_PAGE_SIZE * _FILE_SCAN_MAX_PAGES:
                    raise UpstreamError("upstream_listing_limit")
            if raw["total"] != total or raw["parent_folder"] != parent:
                raise UpstreamError("upstream_invalid_response")
            batch = raw["files"]
            if len(batch) != min(_FILE_SCAN_PAGE_SIZE, total - len(seen_ids)):
                raise UpstreamError("upstream_invalid_response")
            for item in batch:
                ident = item.get("id")
                if not isinstance(ident, str) or not ident or ident in seen_ids:
                    raise UpstreamError("upstream_invalid_response")
                seen_ids.add(ident)
                items.append(item)
            if len(seen_ids) == total:
                return items, parent
        raise UpstreamError("upstream_listing_limit")

    async def _ancestors(self, file_id: str) -> list[dict]:
        chain = await self.ragflow.file_ancestors(file_id)
        if not chain or len(chain) > _MAX_ANCESTORS:
            raise UpstreamError("upstream_invalid_response")
        seen = set()
        for index, item in enumerate(chain):
            _resource_metadata(item)
            if item["id"] in seen or (index > 0 and item["type"] != "folder"):
                raise UpstreamError("upstream_invalid_response")
            seen.add(item["id"])
            expected_parent = chain[index + 1].get("id") if index + 1 < len(chain) else item["id"]
            if item["parent_id"] != expected_parent:
                raise UpstreamError("upstream_invalid_response")
        if chain[0]["id"] != file_id or chain[-1]["type"] != "folder":
            raise UpstreamError("upstream_invalid_response")
        return chain

    @staticmethod
    def _require_folder(chain: list[dict]) -> None:
        if chain[0]["type"] != "folder":
            raise KBError(400, "not_a_folder", "The destination must be a folder.")

    @staticmethod
    def _require_writable(chain: list[dict], *, resource: bool = False) -> None:
        if not _writable_chain(chain) or (resource and chain[0]["id"] == chain[0]["parent_id"]):
            raise KBError(403, "resource_read_only", "This resource is read-only.")

    @staticmethod
    def _check_name(name: str, parent_chain: list[dict], rows: list[dict], *, exclude_id: str | None = None) -> None:
        parent = parent_chain[0]
        # Be conservative across upstream database collations: MySQL commonly
        # considers differently cased names equal, unlike Python string equality.
        folded_name = name.casefold()
        if parent["id"] == parent["parent_id"] and folded_name in _RESERVED_ROOT_NAMES:
            raise KBError(400, "reserved_name", "This name is reserved in the root folder.")
        if any(item["name"].casefold() == folded_name and item["id"] != exclude_id for item in rows):
            raise KBError(409, "name_conflict", "A resource with this name already exists in the folder.")

    async def _collect_directory(self, parent_id: str | None) -> tuple[list[dict], list[dict]]:
        chain = await self._ancestors(parent_id) if parent_id is not None else None
        if chain is not None:
            self._require_folder(chain)
        rows, parent = await self._scan_files(parent_id)
        _resource_metadata(parent)
        if chain is None:
            # Omitted parent_id is the only root lookup. Never synthesize its ID.
            if parent["type"] != "folder" or parent["id"] != parent["parent_id"]:
                raise UpstreamError("upstream_invalid_response")
            chain = [parent]
        expected_parent = chain[1] if len(chain) > 1 else chain[0]
        if any(parent.get(key) != expected_parent.get(key) for key in ("id", "parent_id", "name", "type", "source_type")):
            raise UpstreamError("upstream_invalid_response")
        for item in rows:
            _resource_metadata(item)
            if item["parent_id"] != chain[0]["id"] or item["id"] == chain[0]["id"]:
                raise UpstreamError("upstream_invalid_response")
        return chain, rows

    async def _directory_entries(self, parent_id: str | None) -> tuple[list[dict], list[dict]]:
        try:
            return await asyncio.wait_for(self._collect_directory(parent_id), timeout=_FILE_SCAN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise UpstreamError("upstream_timeout") from None

    async def list_directory(self, parent_id: str | None, *, page: int, page_size: int, keywords: str | None) -> dict:
        chain, rows = await self._directory_entries(parent_id)
        items = [_directory_entry(item, chain) for item in rows]
        file_total = sum(item["kind"] == "file" for item in items)
        # Folders stay navigable, including readonly system folders. A badge counts
        # all files in this directory, independent of search, never the whole tree.
        items.sort(key=lambda item: item["kind"] != "folder")
        result = _page(items, len(items), page, keywords)
        start = (page - 1) * page_size
        result["items"] = result["items"][start:start + page_size]
        return {
            **result,
            "file_total": file_total,
            "current_folder": _folder_view(chain),
            "breadcrumbs": [_folder_view(chain[index:]) for index in reversed(range(len(chain)))],
        }

    async def create_folder(self, payload: FolderCreate) -> dict:
        chain, rows = await self._directory_entries(payload.parent_id)
        self._require_writable(chain)
        self._check_name(payload.name, chain, rows)
        created = await self.ragflow.create_folder(payload.name, chain[0]["id"])
        _resource_metadata(created)
        if created["type"] != "folder" or created["parent_id"] != chain[0]["id"] or created["name"] != payload.name:
            raise UpstreamError("upstream_invalid_response")
        return _directory_entry(created, chain)

    async def upload(self, filename: str, content: bytes, content_type: str, parent_id: str | None = None) -> dict:
        chain, rows = await self._directory_entries(parent_id)
        self._require_writable(chain)
        self._check_name(filename, chain, rows)
        created = await self.ragflow.upload_file(filename, content, parent_id=chain[0]["id"], content_type=content_type)
        _resource_metadata(created)
        if created["type"] == "folder" or created["parent_id"] != chain[0]["id"]:
            raise UpstreamError("upstream_invalid_response")
        return _directory_entry(created, chain)

    async def update_file(self, file_id: str, payload: FileUpdate) -> None:
        chain = await self._ancestors(file_id)
        self._require_writable(chain, resource=True)
        item = chain[0]
        moving = payload.parent_id is not None
        if moving and (payload.parent_id == item["parent_id"] or payload.parent_id == file_id):
            raise KBError(400, "invalid_move", "Choose a different destination folder.")
        if moving and item["type"] == "folder":
            # v0.27.2 recursively recreates/merges moved folders and changes IDs.
            raise KBError(400, "folder_move_unsupported", "Moving folders is not supported.")
        name = payload.name if payload.name is not None else item["name"]
        renamed = name != item["name"]
        if not moving and not renamed:
            raise KBError(400, "no_changes", "The resource has not changed.")
        if renamed and item["type"] == "folder" and name.casefold() == item["name"].casefold():
            raise KBError(400, "case_only_folder_rename_unsupported", "Changing only the case of a folder name is not supported.")
        if renamed and item["type"] != "folder" and PurePosixPath(name.lower()).suffix != PurePosixPath(item["name"].lower()).suffix:
            raise KBError(400, "file_extension_change", "The file extension cannot be changed.")
        target_id = payload.parent_id if moving else item["parent_id"]
        target_chain, rows = await self._directory_entries(target_id)
        self._require_writable(target_chain)
        self._check_name(name, target_chain, rows, exclude_id=file_id)
        if not moving and not any(
            row["id"] == file_id and all(row.get(key) == item.get(key) for key in ("name", "parent_id", "type", "source_type"))
            for row in rows
        ):
            raise KBError(409, "resource_changed", "The resource changed. Refresh the folder and try again.")
        await self.ragflow.move_file(file_id, name=name if renamed else None, parent_id=target_id if moving else None)

    async def download(self, file_id: str) -> tuple[bytes, str]:
        return await self.ragflow.download_file(file_id)

    async def delete_file(self, file_id: str) -> None:
        chain = await self._ancestors(file_id)
        self._require_writable(chain, resource=True)
        if chain[0]["type"] == "folder":
            # A preflight cannot protect children added before recursive DELETE.
            # Refuse even empty folders until atomic empty-only deletion exists.
            raise KBError(
                400,
                "folder_delete_unsupported",
                "Folder deletion is disabled because the knowledge engine cannot "
                "guarantee atomic empty-only deletion.",
            )
        result = await self.ragflow.delete_files([file_id])
        if type(result.get("success_count")) is not int or result["success_count"] != 1:
            raise UpstreamError("upstream_invalid_response")

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

    async def update_dataset(self, dataset_id: str, payload: DatasetUpdate) -> None:
        await self.ragflow.update_dataset(
            dataset_id, payload.model_dump(include={"name", "description"}, exclude_unset=True)
        )

    async def delete_dataset(self, dataset_id: str) -> None:
        await self.ragflow.delete_dataset(dataset_id)

    async def list_documents(self, dataset_id: str, *, page: int, page_size: int, keywords: str | None) -> dict:
        raw = await self.ragflow.list_documents(dataset_id, page=page, page_size=page_size)
        items = [document_view(dataset_id, item) for item in raw.get("docs", [])]
        return _page(items, raw.get("total", len(items)), page, keywords)

    async def download_document(self, dataset_id: str, document_id: str) -> tuple[bytes, str]:
        return await self.ragflow.download_document(dataset_id, document_id)

    async def link_files(self, dataset_id: str, payload: LinkFiles) -> dict:
        async def check_files():
            for file_id in payload.file_ids:
                chain = await self._ancestors(file_id)
                if chain[0]["type"] == "folder":
                    # Upstream silently expands folders recursively; the picker
                    # contract is explicit file IDs only, including direct callers.
                    raise KBError(400, "folder_link_unsupported", "Select files, not folders, to add to a dataset.")

        try:
            await asyncio.wait_for(check_files(), timeout=_FILE_SCAN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise UpstreamError("upstream_timeout") from None
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
