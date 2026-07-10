"""Flocks Hub routes."""

from __future__ import annotations

import asyncio
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from flocks.hub.catalog import (
    category_counts,
    clear_catalog_caches,
    filter_catalog_entries,
    legacy_removed_plugin_message,
    list_catalog,
    load_manifest,
    load_taxonomy,
)
from flocks.hub.files import file_tree, read_file_content
from flocks.hub.installer import install_plugin, uninstall_plugin, update_plugin
from flocks.hub.models import (
    HubCatalogEntry,
    HubFileContent,
    HubFileNode,
    HubInstallProgressEvent,
    HubPluginManifest,
    InstalledPluginRecord,
    PluginType,
)
from flocks.server.auth import require_admin
from flocks.utils.log import Log


router = APIRouter()
log = Log.create(service="hub-routes")


class HubInstallRequest(BaseModel):
    scope: str = Field(default="global", description="'global' only")


class HubCatalogFacets(BaseModel):
    type: dict[str, int] = Field(default_factory=dict)
    category: dict[str, int] = Field(default_factory=dict)
    tags: dict[str, int] = Field(default_factory=dict)
    useCases: dict[str, int] = Field(default_factory=dict)
    state: dict[str, int] = Field(default_factory=dict)
    trust: dict[str, int] = Field(default_factory=dict)
    riskLevel: dict[str, int] = Field(default_factory=dict)


class HubCatalogPageResponse(BaseModel):
    items: list[HubCatalogEntry] = Field(default_factory=list)
    total: int = Field(0)
    offset: int = Field(0)
    limit: int = Field(25)
    facets: HubCatalogFacets = Field(default_factory=HubCatalogFacets)


def _split_csv(value: Optional[str | list[str]]) -> Optional[list[str]]:
    if value is None:
        return None
    if isinstance(value, list):
        parts = value
    else:
        parts = value.split(",")
    result = [part.strip() for part in parts if part and part.strip()]
    return result or None


def _guard_legacy_removed_plugin(plugin_type: PluginType, plugin_id: str) -> None:
    detail = legacy_removed_plugin_message(plugin_type, plugin_id)
    if detail:
        raise HTTPException(status_code=410, detail=detail)


def _clear_hub_runtime_caches() -> None:
    clear_catalog_caches()
    try:
        from flocks.tool.device.plugin_index import clear_device_template_cache

        clear_device_template_cache()
    except Exception:
        pass


def _count_hub_catalog_facet(
    items: list[HubCatalogEntry],
    attribute: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        values = getattr(item, attribute)
        if isinstance(values, str):
            values = [values]
        for value in values:
            counts[value] = counts.get(value, 0) + 1
    return counts


def _build_hub_catalog_facets_for_filters(
    all_entries: list[HubCatalogEntry],
    filters: dict[str, object],
) -> HubCatalogFacets:
    """Build each facet with every active filter except that facet itself."""
    dimensions = {
        "type": ("plugin_type", "type"),
        "category": ("category", "category"),
        "tags": ("tags", "tags"),
        "useCases": ("use_cases", "useCases"),
        "state": ("state", "state"),
        "trust": ("trust", "trust"),
        "riskLevel": ("risk", "riskLevel"),
    }
    counts: dict[str, dict[str, int]] = {}
    for response_field, (query_field, item_attribute) in dimensions.items():
        facet_filters = dict(filters)
        facet_filters[query_field] = None
        counts[response_field] = _count_hub_catalog_facet(
            filter_catalog_entries(all_entries, **facet_filters),
            item_attribute,
        )
    return HubCatalogFacets(**counts)


@router.get("/hub/catalog", response_model=Union[list[HubCatalogEntry], HubCatalogPageResponse])
async def hub_catalog(
    type: Optional[PluginType] = Query(default=None),  # noqa: A002 - API field name
    category: Optional[str] = None,
    tags: Optional[str] = None,
    useCases: Optional[str] = None,
    state: Optional[str] = None,
    trust: Optional[str] = None,
    risk: Optional[str] = None,
    q: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: Optional[int] = Query(default=None, ge=1, le=200),
):
    filters: dict[str, object] = {
        "plugin_type": type,
        "category": _split_csv(category),
        "tags": _split_csv(tags),
        "use_cases": _split_csv(useCases),
        "state": _split_csv(state),
        "trust": _split_csv(trust),
        "risk": _split_csv(risk),
        "q": q,
    }
    if limit is None and offset == 0:
        return await asyncio.to_thread(list_catalog, **filters)

    def load_page() -> tuple[list[HubCatalogEntry], HubCatalogFacets]:
        all_entries = list_catalog()
        entries = filter_catalog_entries(all_entries, **filters)
        facets = _build_hub_catalog_facets_for_filters(all_entries, filters)
        return entries, facets

    entries, facets = await asyncio.to_thread(load_page)

    page_limit = limit or 25
    total = len(entries)
    return HubCatalogPageResponse(
        items=entries[offset:offset + page_limit],
        total=total,
        offset=offset,
        limit=page_limit,
        facets=facets,
    )


@router.get("/hub/categories")
async def hub_categories(include_counts: bool = Query(True)):
    if not include_counts:
        return load_taxonomy().model_dump(mode="json")
    return await asyncio.to_thread(category_counts)


@router.get("/hub/plugins/{plugin_type}/{plugin_id}", response_model=HubPluginManifest)
async def hub_plugin(plugin_type: PluginType, plugin_id: str):
    _guard_legacy_removed_plugin(plugin_type, plugin_id)
    try:
        return load_manifest(plugin_type, plugin_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/hub/plugins/{plugin_type}/{plugin_id}/files", response_model=HubFileNode)
async def hub_plugin_files(plugin_type: PluginType, plugin_id: str):
    _guard_legacy_removed_plugin(plugin_type, plugin_id)
    try:
        return file_tree(plugin_type, plugin_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/hub/plugins/{plugin_type}/{plugin_id}/files/content", response_model=HubFileContent)
async def hub_plugin_file_content(plugin_type: PluginType, plugin_id: str, path: str):
    _guard_legacy_removed_plugin(plugin_type, plugin_id)
    try:
        return read_file_content(plugin_type, plugin_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/hub/plugins/{plugin_type}/{plugin_id}/install", response_model=InstalledPluginRecord)
async def hub_install_plugin(
    plugin_type: PluginType,
    plugin_id: str,
    req: HubInstallRequest = HubInstallRequest(),
    _admin: object = Depends(require_admin),
):
    _guard_legacy_removed_plugin(plugin_type, plugin_id)
    try:
        return await install_plugin(plugin_type, plugin_id, scope=req.scope)
    except Exception as exc:
        log.error("hub.install.failed", {"type": plugin_type, "id": plugin_id, "error": str(exc)})
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/hub/plugins/{plugin_type}/{plugin_id}/install/stream")
async def hub_install_plugin_stream(
    plugin_type: PluginType,
    plugin_id: str,
    req: HubInstallRequest = HubInstallRequest(),
    _admin: object = Depends(require_admin),
):
    _guard_legacy_removed_plugin(plugin_type, plugin_id)
    if plugin_type != "component":
        raise HTTPException(status_code=400, detail="Streaming install progress is only supported for components.")
    try:
        manifest = load_manifest(plugin_type, plugin_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def generate():
        queue: asyncio.Queue[HubInstallProgressEvent | None] = asyncio.Queue()

        async def emit(event: HubInstallProgressEvent) -> None:
            await queue.put(event)

        async def run_install() -> None:
            try:
                await install_plugin(plugin_type, plugin_id, scope=req.scope, progress=emit)
            except Exception as exc:
                log.error("hub.install_stream.failed", {"type": plugin_type, "id": plugin_id, "error": str(exc)})
                await queue.put(
                    HubInstallProgressEvent(
                        event="error",
                        id=manifest.id,
                        type=manifest.type,
                        name=manifest.name,
                        nameCn=manifest.nameCn,
                        total=len(manifest.components),
                        message=str(exc),
                    )
                )
            finally:
                await queue.put(None)

        task = asyncio.create_task(run_install())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {event.model_dump_json()}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/hub/plugins/{plugin_type}/{plugin_id}/update", response_model=InstalledPluginRecord)
async def hub_update_plugin(
    plugin_type: PluginType,
    plugin_id: str,
    req: HubInstallRequest = HubInstallRequest(),
    _admin: object = Depends(require_admin),
):
    _guard_legacy_removed_plugin(plugin_type, plugin_id)
    try:
        return await update_plugin(plugin_type, plugin_id, scope=req.scope)
    except Exception as exc:
        log.error("hub.update.failed", {"type": plugin_type, "id": plugin_id, "error": str(exc)})
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/hub/plugins/{plugin_type}/{plugin_id}")
async def hub_uninstall_plugin(
    plugin_type: PluginType,
    plugin_id: str,
    _admin: object = Depends(require_admin),
):
    _guard_legacy_removed_plugin(plugin_type, plugin_id)
    try:
        removed = await uninstall_plugin(plugin_type, plugin_id)
        return {"removed": removed}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/hub/refresh")
async def hub_refresh(_admin: object = Depends(require_admin)):
    _clear_hub_runtime_caches()
    return {"count": len(await asyncio.to_thread(list_catalog))}
