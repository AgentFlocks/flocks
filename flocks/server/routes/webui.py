"""WebUI page plugin API routes."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field

from flocks.server.auth import require_admin, require_user
from flocks.contracts.webui.builder import WebUIPageBuilder
from flocks.contracts.webui.api_runtime import WebUIPageApiRuntime
from flocks.contracts.webui.models import (
    WebUIPageBuildMeta,
    WebUIPageDetail,
    WebUIPageListItem,
    WebUIPageManifest,
    WebUIWorkspaceListItem,
)
from flocks.contracts.webui.store import WebUIPagesStore, webui_contract_page_route
from flocks.hub.catalog import load_manifest, scene_suite_workspace_owners
from flocks.license import is_pro_edition_status, license_status
from flocks.hub.suite_license import (
    SuiteOwnershipUnknown,
    clear_page_suite_cache,
    edition_status,
    is_open_edition,
    is_pro_suite,
    pro_suite_for_page,
    runtime_suite_license,
    shipped_page_owners,
    suite_license,
)
from flocks.server.routes.event import publish_event
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="webui-pages-routes")

MAX_IMPORT_ARCHIVE_BYTES = 10_000_000
MAX_IMPORT_FILES = 500
MAX_IMPORT_FILE_BYTES = 5_000_000
MAX_IMPORT_TOTAL_BYTES = 50_000_000
_IMPORT_SOURCE_SUFFIXES = {".tsx", ".ts", ".jsx", ".js", ".css", ".json"}
_IMPORT_API_SUFFIXES = {".py", ".yaml", ".yml"}
_IMPORT_DIST_FILES = {"dist/page.js", "dist/meta.json", "dist/api-meta.json"}

_store = WebUIPagesStore()
_builder = WebUIPageBuilder(_store)
_api_runtime = WebUIPageApiRuntime(_store)


def _page_id_or_400(page_id: str) -> str:
    """Validate a page id from the URL; a malformed id is a client error, not a 500."""
    try:
        return _store.validate_page_id(page_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


class WebUIPageCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., description="Page identifier")
    title: str = Field(..., description="Navigation title")
    icon: str = Field("LayoutDashboard", description="Lucide icon name")
    order: int = Field(100, description="Navigation sort order")


class WebUIPageSaveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    manifest: Optional[dict[str, Any]] = Field(None, description="Manifest fields to merge")
    sourcePath: Optional[str] = Field(None, description="Relative source path to write")
    sourceContent: Optional[str] = Field(None, description="Source file content")


class WebUIPageSaveResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, by_alias=True)

    manifest: WebUIPageManifest
    build: WebUIPageBuildMeta


async def _read_limited_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_IMPORT_ARCHIVE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="archive is too large",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _normalize_archive_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or ".." in normalized.split("/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid archive path")
    parts = [part for part in normalized.split("/") if part]
    if any(part.startswith(".") for part in parts):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="hidden archive paths are not allowed")
    return "/".join(parts)


def _validate_import_relative_path(relative_path: str) -> None:
    if not relative_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid archive structure")
    if relative_path == "manifest.json":
        return
    if relative_path.startswith("src/") and Path(relative_path).suffix in _IMPORT_SOURCE_SUFFIXES:
        return
    if relative_path.startswith("api/") and Path(relative_path).suffix in _IMPORT_API_SUFFIXES:
        return
    if relative_path.startswith("assets/"):
        return
    if relative_path in _IMPORT_DIST_FILES:
        return
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unsupported archive file: {relative_path}")


def _validate_manifest_entry(entry: str) -> str:
    normalized = (entry or "").replace("\\", "/").lstrip("/")
    parts = [part for part in normalized.split("/") if part]
    if not parts or ".." in parts or any(part.startswith(".") for part in parts):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid manifest entry")
    if not normalized.startswith("src/") or Path(normalized).suffix not in _IMPORT_SOURCE_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid manifest entry")
    return normalized


def _normalize_import_manifest(extracted_root: Path, page_id: str) -> None:
    manifest_path = extracted_root / "manifest.json"
    if not manifest_path.is_file():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="manifest.json is required")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = WebUIPageManifest.model_validate(raw)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid manifest.json: {exc}") from exc

    entry = _validate_manifest_entry(manifest.entry)
    normalized = manifest.model_copy(
        update={
            "id": page_id,
            "route": webui_contract_page_route(page_id),
            "entry": entry,
            "updatedAt": int(time.time() * 1000),
        }
    )
    manifest_path.write_text(
        json.dumps(normalized.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def _require_suite_license(page_id: str) -> None:
    """Serve a pro-edition suite's page only while that suite's own licence
    is in force. The built-in checker enforces nothing here; see
    flocks.hub.suite_license."""
    try:
        suite_id = await asyncio.to_thread(pro_suite_for_page, page_id, _store)
    except SuiteOwnershipUnknown as exc:
        if is_open_edition(await edition_status()):
            return
        # Undecided is not "no licence needed".
        log.warning("webui.page.suite_ownership_unknown", {"pageId": page_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="暂时无法确认这个页面属于哪个场景套件，请稍后重试。",
        ) from exc
    if suite_id is None:
        return
    verdict = await runtime_suite_license(suite_id)
    if verdict is None or verdict.licensed:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"该场景套件（{suite_id}）未授权或授权已失效（{verdict.state or 'unlicensed'}），"
            "请在系统设置的「场景套件授权」中导入授权文件。"
        ),
    )


def _maybe_pro_suite(suite_id: Optional[str]) -> bool:
    """For listings: a suite whose edition cannot be read counts as one that
    needs its own licence, so it stays hidden unless the checker says it is
    licensed. (Page access itself is decided by _require_suite_license.)"""
    try:
        return is_pro_suite(suite_id)
    except SuiteOwnershipUnknown:
        return bool(suite_id)


async def _pro_suite_verdicts(suite_ids: set[str]) -> dict[str, bool]:
    """Licence verdicts for the given pro-edition suites; empty when nothing is enforced."""
    if not suite_ids or is_open_edition(await edition_status()):
        return {}
    ordered = sorted(suite_ids)
    verdicts = await asyncio.gather(*(suite_license(suite_id) for suite_id in ordered))
    return {suite_id: verdict.licensed for suite_id, verdict in zip(ordered, verdicts)}


@router.get("/contracts/webui/pages", response_model=list[WebUIPageListItem])
async def list_webui_pages(enabled_only: bool = Query(False, alias="enabledOnly")):
    def load() -> tuple[list[WebUIPageListItem], dict[str, str]]:
        pages = _store.list_pages(enabled_only=enabled_only)
        workspace_owners = {
            workspace_id: suite_id
            for workspace_id, suite_id in scene_suite_workspace_owners().items()
            if _maybe_pro_suite(suite_id)
        }
        try:
            # By page id first, as for serving: a suite page copied or
            # imported outside its workspace is still the suite's.
            by_id = shipped_page_owners(_store)
        except SuiteOwnershipUnknown as exc:
            log.warning("webui.pages.suite_ownership_unknown", {"error": str(exc)})
            by_id = {}
        owners = {}
        for page in pages:
            suite_id = by_id.get(page.id) or workspace_owners.get(page.workspaceId or "")
            if suite_id:
                owners[page.id] = suite_id
        return pages, owners

    pages, owners = await asyncio.to_thread(load)
    licensed = await _pro_suite_verdicts(set(owners.values()))
    # A suite that is not licensed has no pages to offer anywhere.
    return [page for page in pages if licensed.get(owners.get(page.id, ""), True)]


@router.get("/contracts/webui/workspaces", response_model=list[WebUIWorkspaceListItem])
async def list_webui_workspaces(enabled_only: bool = Query(False, alias="enabledOnly")):
    try:
        pro_active = is_pro_edition_status(await license_status())
    except Exception as exc:
        log.warning("webui.workspaces.license_status_failed", {"error": str(exc)})
        pro_active = False

    # Directory scans plus the hub catalog signature: keep them off the event loop.
    def load() -> tuple[list[WebUIWorkspaceListItem], set[str]]:
        workspaces = _attach_scene_suites(_store.list_workspaces(enabled_only=enabled_only), pro_active=pro_active)
        if not pro_active:
            return workspaces, set()
        return workspaces, {w.suiteId for w in workspaces if w.suiteId and _maybe_pro_suite(w.suiteId)}

    workspaces, pro_suites = await asyncio.to_thread(load)
    # Under Pro each pro-edition suite needs its own licence as well; one
    # without it is locked like any Pro scene without Pro.
    licensed = await _pro_suite_verdicts(pro_suites)
    result = []
    for workspace in workspaces:
        verdict = licensed.get(workspace.suiteId or "")
        if verdict is not None:
            workspace = workspace.model_copy(update={"licensed": verdict, "locked": workspace.locked or not verdict})
        # The navigation (enabledOnly) leaves locked scenes out; the scene
        # manager lists them, locked.
        if enabled_only and workspace.locked:
            continue
        result.append(workspace)
    return result


def _suite_edition(suite_id: str) -> str:
    try:
        return getattr(load_manifest("component", suite_id), "edition", "oss")
    except Exception:
        return "oss"


def _attach_scene_suites(
    workspaces: list[WebUIWorkspaceListItem],
    *,
    pro_active: bool = False,
) -> list[WebUIWorkspaceListItem]:
    """Tag each workspace with the scene suite that ships it and drop
    user-root scene workspaces no suite knows about. A workspace whose suite
    needs Pro is marked locked while no Pro license is active.

    Scenes reach the user root (~/.flocks/plugins/contracts/webui) only through
    the suite manager, so a ``sceneWorkspace`` directory there without a suite
    in the catalog is a leftover (typically from a build of another branch)
    whose backend is not here; the navigation must not open it. Scenes checked
    into the project root are development work and stay, as do workbench pages,
    which are not scenes.
    """
    try:
        owners = scene_suite_workspace_owners()
    except Exception as exc:
        # Without the catalog nothing can be verified; keep everything reachable.
        log.warning("webui.workspaces.suite_lookup_failed", {"error": str(exc)})
        return workspaces
    result: list[WebUIWorkspaceListItem] = []
    for workspace in workspaces:
        suite_id = owners.get(workspace.id)
        if suite_id is None and workspace.placement == "sceneWorkspace" and not workspace.native:
            # The list is fetched on every navigation refresh; say it once.
            if workspace.id not in _hidden_scene_workspaces_logged:
                _hidden_scene_workspaces_logged.add(workspace.id)
                log.info("webui.workspaces.unmanaged_scene_hidden", {"workspaceId": workspace.id})
            continue
        locked = bool(suite_id) and not pro_active and _suite_edition(suite_id) == "pro"
        result.append(workspace.model_copy(update={"suiteId": suite_id, "locked": locked}))
    return result


_hidden_scene_workspaces_logged: set[str] = set()


class WebUIWorkspaceEnabledRequest(BaseModel):
    enabled: bool = Field(..., description="Whether the workspace appears in navigation")


@router.patch("/contracts/webui/workspaces/{workspace_id}", response_model=WebUIWorkspaceListItem)
async def set_webui_workspace_enabled(
    workspace_id: str,
    req: WebUIWorkspaceEnabledRequest,
    _admin: object = Depends(require_admin),
):
    """Turn a scene workspace off or back on without uninstalling its suite."""
    try:
        workspace = _store.set_workspace_enabled(workspace_id, req.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await publish_event("contracts.webui.pages.nav_changed", {"workspaceId": workspace_id, "enabled": req.enabled})
    return workspace


@router.post("/contracts/webui/pages", response_model=WebUIPageDetail, status_code=status.HTTP_201_CREATED)
async def create_webui_page(req: WebUIPageCreateRequest, _admin: object = Depends(require_admin)):
    try:
        detail = _store.create_page(
            page_id=req.id,
            title=req.title,
            icon=req.icon,
            order=req.order,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        build = _builder.build(detail.manifest.id)
        if build.status == "ready":
            await publish_event("contracts.webui.pages.updated", {"id": detail.manifest.id, "hash": build.hash})
        elif build.status == "failed":
            await publish_event(
                "contracts.webui.pages.build_failed",
                {"id": detail.manifest.id, "error": build.error or "build failed"},
            )
    except Exception as exc:
        log.warning("webui_pages.create.build_failed", {"pageId": detail.manifest.id, "error": str(exc)})

    await publish_event("contracts.webui.pages.nav_changed", {"id": detail.manifest.id})
    return _store.get_page(detail.manifest.id)


@router.get("/contracts/webui/pages/{page_id}", response_model=WebUIPageDetail)
async def get_webui_page(page_id: str):
    page_id = _page_id_or_400(page_id)
    await _require_suite_license(page_id)
    try:
        return _store.get_page(page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/contracts/webui/pages/{page_id}", response_model=WebUIPageSaveResponse)
async def save_webui_page(page_id: str, req: WebUIPageSaveRequest, _admin: object = Depends(require_admin)):
    page_id = _page_id_or_400(page_id)
    await _require_suite_license(page_id)
    nav_changed = False
    try:
        if req.manifest is not None:
            _store.save_manifest(page_id, req.manifest)
            nav_changed = True
        if req.sourcePath is not None:
            if req.sourceContent is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="sourceContent is required when sourcePath is provided",
                )
            _store.save_source_file(page_id, req.sourcePath, req.sourceContent)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    build = WebUIPageBuildMeta(status="idle")
    if req.sourcePath is not None:
        rel = req.sourcePath.replace("\\", "/").lstrip("/")
        if rel.startswith("api/"):
            try:
                routes = await _api_runtime.reload_page(page_id)
                await publish_event("contracts.webui.pages.api_changed", {"id": page_id, "routes": routes})
            except HTTPException as exc:
                await publish_event(
                    "contracts.webui.pages.api_failed",
                    {"id": page_id, "error": str(exc.detail)},
                )
                raise
            except Exception as exc:
                await publish_event(
                    "contracts.webui.pages.api_failed",
                    {"id": page_id, "error": str(exc)},
                )
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
        else:
            build = _builder.build(page_id)
            if build.status == "ready":
                await publish_event("contracts.webui.pages.updated", {"id": page_id, "hash": build.hash})
                nav_changed = True
            else:
                await publish_event(
                    "contracts.webui.pages.build_failed",
                    {"id": page_id, "error": build.error or "build failed"},
                )
    elif nav_changed:
        await publish_event("contracts.webui.pages.nav_changed", {"id": page_id})

    manifest = _store.get_page(page_id).manifest
    return WebUIPageSaveResponse(manifest=manifest, build=build)


@router.post("/contracts/webui/pages/{page_id}/build", response_model=WebUIPageBuildMeta)
async def build_webui_page(page_id: str, _admin: object = Depends(require_admin)):
    page_id = _page_id_or_400(page_id)
    await _require_suite_license(page_id)
    try:
        build = _builder.build(page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    if build.status == "ready":
        await publish_event("contracts.webui.pages.updated", {"id": page_id, "hash": build.hash})
        await publish_event("contracts.webui.pages.nav_changed", {"id": page_id})
    else:
        await publish_event(
            "contracts.webui.pages.build_failed",
            {"id": page_id, "error": build.error or "build failed"},
        )
    return build


@router.get("/contracts/webui/pages/{page_id}/bundle.js")
async def get_webui_page_bundle(page_id: str, v: Optional[str] = Query(None)):
    await _require_suite_license(_page_id_or_400(page_id))
    try:
        bundle_path = _store.bundle_path(page_id)
        if not bundle_path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bundle not found")
        headers = {"Cache-Control": "no-cache"} if v else None
        return FileResponse(
            path=bundle_path,
            media_type="application/javascript",
            headers=headers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/contracts/webui/pages/{page_id}/assets/{asset_path:path}")
async def get_webui_page_asset(page_id: str, asset_path: str):
    await _require_suite_license(_page_id_or_400(page_id))
    try:
        path = _store.asset_path(page_id, asset_path)
        if not path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
        return FileResponse(path=path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/contracts/webui/pages/{page_id}/api")
async def list_webui_page_api_routes(page_id: str):
    page_id = _page_id_or_400(page_id)
    await _require_suite_license(page_id)
    return await _api_runtime.list_routes(page_id)


@router.post("/contracts/webui/pages/{page_id}/api/reload")
async def reload_webui_page_api(page_id: str, _admin: object = Depends(require_admin)):
    page_id = _page_id_or_400(page_id)
    await _require_suite_license(page_id)
    routes = await _api_runtime.reload_page(page_id)
    await publish_event("contracts.webui.pages.api_changed", {"id": page_id, "routes": routes})
    return {"routes": routes}


@router.api_route(
    "/contracts/webui/pages/{page_id}/api/{api_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def dispatch_webui_page_api(page_id: str, api_path: str, request: Request):
    user = require_user(request)
    page_id = _page_id_or_400(page_id)
    await _require_suite_license(page_id)
    return await _api_runtime.dispatch(page_id, api_path, request, user)


@router.get("/contracts/webui/pages/{page_id}/export")
async def export_webui_page(page_id: str, _admin: object = Depends(require_admin)):
    page_id = _page_id_or_400(page_id)
    await _require_suite_license(page_id)
    page_path = _store.page_dir(page_id)
    if not page_path.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"page not found: {page_id}")

    fd, archive_path = tempfile.mkstemp(prefix=f"webui-page-{page_id}-", suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in page_path.rglob("*"):
                if not file_path.is_file():
                    continue
                rel_parts = file_path.relative_to(page_path).parts
                # Bytecode caches are produced when the page API is loaded and
                # are not importable page content; shipping them would make the
                # archive fail the import allow-list on the way back in.
                if "__pycache__" in rel_parts or file_path.suffix == ".pyc" or any(part.startswith(".") for part in rel_parts):
                    continue
                arc_name = "/".join(rel_parts)
                zf.write(file_path, arcname=f"{page_id}/{arc_name}")
    except Exception:
        if os.path.exists(archive_path):
            os.unlink(archive_path)
        raise
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"{page_id}.zip",
        background=BackgroundTask(lambda: os.path.exists(archive_path) and os.unlink(archive_path)),
    )


@router.post("/contracts/webui/pages/import")
async def import_webui_page(
    file: UploadFile = File(...),
    overwrite: bool = Query(False),
    _admin: object = Depends(require_admin),
):
    data = await _read_limited_upload(file)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty archive")
    try:
        with tempfile.TemporaryDirectory(prefix="webui-import-") as tmpdir:
            temp_root = Path(tmpdir) / "extract"
            temp_root.mkdir(parents=True, exist_ok=True)
            archive_path = Path(tmpdir) / "archive.zip"
            archive_path.write_bytes(data)
            with zipfile.ZipFile(archive_path) as zf:
                members = [member for member in zf.infolist() if not member.is_dir()]
                if not members:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="archive has no files")
                if len(members) > MAX_IMPORT_FILES:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="archive has too many files")
                names = [_normalize_archive_member_name(member.filename) for member in members]
                root_parts = {name.split("/", 1)[0] for name in names}
                if len(root_parts) != 1:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="archive must contain a single page root directory")
                page_id = _page_id_or_400(next(iter(root_parts)))
                extracted_root = temp_root / page_id
                total_uncompressed = 0
                for member, member_name in zip(members, names):
                    if "/" not in member_name:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid archive structure")
                    rel_part = member_name.split("/", 1)[1]
                    _validate_import_relative_path(rel_part)
                    if member.file_size > MAX_IMPORT_FILE_BYTES:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="archive file is too large")
                    total_uncompressed += member.file_size
                    if total_uncompressed > MAX_IMPORT_TOTAL_BYTES:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="archive contents are too large")
                    target = (extracted_root / rel_part).resolve()
                    try:
                        target.relative_to(extracted_root.resolve())
                    except ValueError:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid archive path")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(member))
            _normalize_import_manifest(extracted_root, page_id)
            if _store.page_exists(page_id) and not overwrite:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"page already exists: {page_id}")
            # A suite's page imported again under its id is still that suite's
            # page, and replacing it needs the same licence as serving it.
            await _require_suite_license(page_id)
            target = _store.root_page_dir(page_id)
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str((temp_root / page_id).resolve()), str(target))
        clear_page_suite_cache()
        try:
            build = _builder.build(page_id)
            if build.status == "ready":
                await publish_event("contracts.webui.pages.updated", {"id": page_id, "hash": build.hash})
            else:
                await publish_event(
                    "contracts.webui.pages.build_failed",
                    {"id": page_id, "error": build.error or "build failed"},
                )
        except Exception as exc:
            log.warning("webui_pages.import.build_failed", {"pageId": page_id, "error": str(exc)})
            await publish_event("contracts.webui.pages.build_failed", {"id": page_id, "error": str(exc)})
        if _store.routes_path(page_id).is_file():
            try:
                routes = await _api_runtime.reload_page(page_id)
                await publish_event("contracts.webui.pages.api_changed", {"id": page_id, "routes": routes})
            except Exception as exc:
                log.warning("webui_pages.import.api_reload_failed", {"pageId": page_id, "error": str(exc)})
                await publish_event("contracts.webui.pages.api_failed", {"id": page_id, "error": str(exc)})
        await publish_event("contracts.webui.pages.nav_changed", {"id": page_id})
        return _store.get_page(page_id)
    except HTTPException:
        raise
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid zip archive") from exc


@router.get("/user-defined-pages", response_model=list[WebUIPageListItem])
async def list_legacy_user_defined_pages(enabled_only: bool = Query(False, alias="enabledOnly")):
    """Compatibility alias for pre-contract WebUI page clients."""
    return await list_webui_pages(enabled_only=enabled_only)


@router.post("/user-defined-pages", response_model=WebUIPageDetail, status_code=status.HTTP_201_CREATED)
async def create_legacy_user_defined_page(req: WebUIPageCreateRequest, _admin: object = Depends(require_admin)):
    """Compatibility alias for pre-contract WebUI page clients."""
    return await create_webui_page(req, _admin)


@router.get("/user-defined-pages/{page_id}", response_model=WebUIPageDetail)
async def get_legacy_user_defined_page(page_id: str):
    """Compatibility alias for pre-contract WebUI page clients."""
    return await get_webui_page(page_id)


@router.put("/user-defined-pages/{page_id}", response_model=WebUIPageSaveResponse)
async def save_legacy_user_defined_page(
    page_id: str,
    req: WebUIPageSaveRequest,
    _admin: object = Depends(require_admin),
):
    """Compatibility alias for pre-contract WebUI page clients."""
    return await save_webui_page(page_id, req, _admin)


@router.post("/user-defined-pages/{page_id}/build", response_model=WebUIPageBuildMeta)
async def build_legacy_user_defined_page(page_id: str, _admin: object = Depends(require_admin)):
    """Compatibility alias for pre-contract WebUI page clients."""
    return await build_webui_page(page_id, _admin)


@router.get("/user-defined-pages/{page_id}/bundle.js")
async def get_legacy_user_defined_page_bundle(page_id: str, v: Optional[str] = Query(None)):
    """Compatibility alias for pre-contract WebUI page clients."""
    return await get_webui_page_bundle(page_id, v)


@router.get("/user-defined-pages/{page_id}/assets/{asset_path:path}")
async def get_legacy_user_defined_page_asset(page_id: str, asset_path: str):
    """Compatibility alias for pre-contract WebUI page clients."""
    return await get_webui_page_asset(page_id, asset_path)


@router.get("/user-defined-pages/{page_id}/api")
async def list_legacy_user_defined_page_api_routes(page_id: str):
    """Compatibility alias for pre-contract WebUI page clients."""
    return await list_webui_page_api_routes(page_id)


@router.post("/user-defined-pages/{page_id}/api/reload")
async def reload_legacy_user_defined_page_api(page_id: str, _admin: object = Depends(require_admin)):
    """Compatibility alias for pre-contract WebUI page clients."""
    return await reload_webui_page_api(page_id, _admin)


@router.api_route(
    "/user-defined-pages/{page_id}/api/{api_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def dispatch_legacy_user_defined_page_api(page_id: str, api_path: str, request: Request):
    """Compatibility alias for pre-contract WebUI page clients."""
    return await dispatch_webui_page_api(page_id, api_path, request)


@router.get("/user-defined-pages/{page_id}/export")
async def export_legacy_user_defined_page(page_id: str, _admin: object = Depends(require_admin)):
    """Compatibility alias for pre-contract WebUI page clients."""
    return await export_webui_page(page_id, _admin)


@router.post("/user-defined-pages/import")
async def import_legacy_user_defined_page(
    file: UploadFile = File(...),
    overwrite: bool = Query(False),
    _admin: object = Depends(require_admin),
):
    """Compatibility alias for pre-contract WebUI page clients."""
    return await import_webui_page(file, overwrite, _admin)


def reset_route_dependencies(
    *,
    store: Optional[WebUIPagesStore] = None,
    builder: Optional[WebUIPageBuilder] = None,
    api_runtime: Optional[WebUIPageApiRuntime] = None,
) -> None:
    """Test helper to inject isolated store/builder instances."""
    global _store, _builder, _api_runtime
    _store = store or WebUIPagesStore()
    _builder = builder or WebUIPageBuilder(_store)
    _api_runtime = api_runtime or WebUIPageApiRuntime(_store)
