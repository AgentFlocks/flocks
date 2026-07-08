"""Flocks Hub routes."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from flocks.hub.catalog import category_counts, legacy_removed_plugin_message, list_catalog, load_manifest
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


@router.get("/hub/catalog", response_model=list[HubCatalogEntry])
async def hub_catalog(
    type: Optional[PluginType] = Query(default=None),  # noqa: A002 - API field name
    category: Optional[str] = None,
    tags: Optional[str] = None,
    useCases: Optional[str] = None,
    state: Optional[str] = None,
    trust: Optional[str] = None,
    risk: Optional[str] = None,
    q: Optional[str] = None,
):
    return list_catalog(
        plugin_type=type,
        category=_split_csv(category),
        tags=_split_csv(tags),
        use_cases=_split_csv(useCases),
        state=_split_csv(state),
        trust=_split_csv(trust),
        risk=_split_csv(risk),
        q=q,
    )


@router.get("/hub/categories")
async def hub_categories():
    return category_counts()


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
    # The bundled catalog is filesystem-backed, so refresh just returns the current count.
    return {"count": len(list_catalog())}
