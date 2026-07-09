"""Flocks Hub routes."""

from __future__ import annotations

import asyncio
from typing import Optional, Union

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from flocks.hub.catalog import (
    category_counts,
    clear_catalog_caches,
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


def _build_hub_catalog_facets(items: list[HubCatalogEntry]) -> HubCatalogFacets:
    facets = HubCatalogFacets()
    for item in items:
        facets.type[item.type] = facets.type.get(item.type, 0) + 1
        facets.category[item.category] = facets.category.get(item.category, 0) + 1
        facets.state[item.state] = facets.state.get(item.state, 0) + 1
        facets.trust[item.trust] = facets.trust.get(item.trust, 0) + 1
        facets.riskLevel[item.riskLevel] = facets.riskLevel.get(item.riskLevel, 0) + 1
        for tag in item.tags:
            facets.tags[tag] = facets.tags.get(tag, 0) + 1
        for use_case in item.useCases:
            facets.useCases[use_case] = facets.useCases.get(use_case, 0) + 1
    return facets


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
    entries = await asyncio.to_thread(
        list_catalog,
        plugin_type=type,
        category=_split_csv(category),
        tags=_split_csv(tags),
        use_cases=_split_csv(useCases),
        state=_split_csv(state),
        trust=_split_csv(trust),
        risk=_split_csv(risk),
        q=q,
    )
    if limit is None and offset == 0:
        return entries

    page_limit = limit or 25
    total = len(entries)
    return HubCatalogPageResponse(
        items=entries[offset:offset + page_limit],
        total=total,
        offset=offset,
        limit=page_limit,
        facets=_build_hub_catalog_facets(entries),
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
async def hub_install_plugin(plugin_type: PluginType, plugin_id: str, req: HubInstallRequest = HubInstallRequest()):
    _guard_legacy_removed_plugin(plugin_type, plugin_id)
    try:
        return await install_plugin(plugin_type, plugin_id, scope=req.scope)
    except Exception as exc:
        log.error("hub.install.failed", {"type": plugin_type, "id": plugin_id, "error": str(exc)})
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/hub/plugins/{plugin_type}/{plugin_id}/install/stream")
async def hub_install_plugin_stream(plugin_type: PluginType, plugin_id: str, req: HubInstallRequest = HubInstallRequest()):
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
async def hub_update_plugin(plugin_type: PluginType, plugin_id: str, req: HubInstallRequest = HubInstallRequest()):
    _guard_legacy_removed_plugin(plugin_type, plugin_id)
    try:
        return await update_plugin(plugin_type, plugin_id, scope=req.scope)
    except Exception as exc:
        log.error("hub.update.failed", {"type": plugin_type, "id": plugin_id, "error": str(exc)})
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/hub/plugins/{plugin_type}/{plugin_id}")
async def hub_uninstall_plugin(plugin_type: PluginType, plugin_id: str):
    _guard_legacy_removed_plugin(plugin_type, plugin_id)
    try:
        removed = await uninstall_plugin(plugin_type, plugin_id)
        return {"removed": removed}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/hub/refresh")
async def hub_refresh():
    _clear_hub_runtime_caches()
    return {"count": len(await asyncio.to_thread(list_catalog))}
