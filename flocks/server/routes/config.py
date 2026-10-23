"""
Configuration management routes

Routes for getting and updating configuration.

Flocks TUI expects Config format:
{
    "$schema": string,
    "theme": string,
    "keybinds": KeybindsConfig,
    "model": string,
    "provider": { [providerID]: ProviderConfig },
    "agent": { [agentName]: AgentConfig },
    "mcp": { [name]: McpConfig },
    ...
}
"""

import asyncio
import copy
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, NamedTuple, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from flocks.config.config import Config, GlobalConfig, ConfigInfo as ConfigInfoModel, UIConfig
from flocks.config.config_writer import ConfigWriter
from flocks.provider.provider import Provider
from flocks.retention import (
    CONFIG_SECTION as DATA_RETENTION_SECTION,
    MAX_RETENTION_DAYS,
    DataRetentionScheduler,
    RetentionPolicy,
    apply_log_retention,
    bump_policy_version,
    collect_targets,
    load_policy,
    policy_from_config,
)
from flocks.config.references import (
    ReferencePlaces,
    contains_reference,
    drop_unchanged,
    leftover_masks,
    member_view,
    quote_bare_references,
    restore_hidden,
)
from flocks.server.auth import get_optional_user, require_admin
from flocks.server.config_mutation import serialized_config_mutation
from flocks.utils.log import Log


router = APIRouter()
log = Log.create(service="routes.config")


def _channel_allow_from_deletion_ids(config_data: Dict[str, Any]) -> set[str]:
    """Return channel IDs whose PATCH explicitly removes allowFrom."""
    channels = config_data.get("channels")
    if not isinstance(channels, dict):
        return set()

    return {
        channel_id
        for channel_id, channel_cfg in channels.items()
        if isinstance(channel_cfg, dict)
        and "allowFrom" in channel_cfg
        and channel_cfg.get("allowFrom") is None
    }


def _normalize_slack_dm_policy(config_data: Dict[str, Any]) -> None:
    """Keep Slack allowFrom and dmPolicy aligned for DM access control."""
    channels = config_data.get("channels")
    if not isinstance(channels, dict):
        return
    slack = channels.get("slack")
    if not isinstance(slack, dict) or "allowFrom" not in slack:
        return
    allow_from = slack.get("allowFrom")
    if isinstance(allow_from, list) and len(allow_from) > 0:
        slack["dmPolicy"] = "allowlist"
    else:
        slack["dmPolicy"] = "open"


async def _validate_plugin_group_updates(config_data: Dict[str, Any]) -> None:
    """Check native definition locks before any config or secret side effects."""
    try:
        for section in ("mcp", "api_services"):
            entries = config_data.get(section)
            if not isinstance(entries, dict):
                continue
            groups = [entry["group"] for entry in entries.values() if isinstance(entry, dict) and "group" in entry]
            if not groups:
                continue
            if section == "mcp":
                from flocks.mcp.types import normalize_mcp_group as normalize_group
            else:
                from flocks.tool.schema.api_service_schema import normalize_api_service_group as normalize_group
            for group in groups:
                normalize_group(group)

        for section in ("agent", "mode"):
            entries = config_data.get(section)
            if not isinstance(entries, dict):
                continue
            updates = {
                name: entry for name, entry in entries.items()
                if isinstance(entry, dict) and "group" in entry
            }
            if not updates:
                continue
            from flocks.agent.agent import normalize_agent_group
            from flocks.agent.registry import Agent

            for entry in updates.values():
                normalize_agent_group(entry["group"])
            try:
                await Agent.validate_group_settings(updates)
            except ValueError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc

        settings = config_data.get("tool_settings")
        if isinstance(settings, dict):
            updates = {
                name: entry for name, entry in settings.items()
                if isinstance(entry, dict) and "group" in entry
            }
            if updates:
                from flocks.tool.registry import ToolRegistry, normalize_tool_group

                for entry in updates.values():
                    normalize_tool_group(entry["group"])
                try:
                    ToolRegistry.validate_group_settings(updates)
                except ValueError as exc:
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _build_model_from_config(
    provider_id: str,
    model_id: str,
    model_cfg: Any,
    existing: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if hasattr(model_cfg, "model_dump"):
        data = model_cfg.model_dump(exclude_none=True, by_alias=True)
    elif isinstance(model_cfg, dict):
        data = {k: v for k, v in model_cfg.items() if v is not None}
    else:
        data = {}

    if data.get("disabled") is True:
        return None

    existing = existing or {}
    existing_limit = existing.get("limit", {}) if isinstance(existing.get("limit", {}), dict) else {}
    limit = data.get("limit") if isinstance(data.get("limit"), dict) else {}

    context = limit.get("context") or existing_limit.get("context") or 128000
    output = limit.get("output") or existing_limit.get("output") or 4096

    tool_call = data.get("tool_call")
    if tool_call is None:
        tool_call = data.get("toolCall")
    if tool_call is None:
        tool_call = existing.get("tool_call", True)

    temperature = data.get("temperature")
    if temperature is None:
        temperature = existing.get("temperature", True)

    attachment = data.get("attachment")
    if attachment is None:
        attachment = existing.get("attachment", False)

    reasoning = data.get("reasoning")
    if reasoning is None:
        reasoning = existing.get("reasoning", False)

    name = data.get("name") or existing.get("name") or model_id

    model_info = {
        "id": model_id,
        "name": name,
        "providerID": provider_id,
        "attachment": attachment,
        "reasoning": reasoning,
        "temperature": temperature,
        "tool_call": tool_call,
        "limit": {
            "context": context,
            "output": output,
        },
        "options": data.get("options") or existing.get("options") or {},
    }

    if "family" in data or "family" in existing:
        model_info["family"] = data.get("family") or existing.get("family")
    if "api" in data or "api" in existing:
        model_info["api"] = data.get("api") or existing.get("api")

    return model_info


def _merge_config_models(
    models_dict: Dict[str, Dict[str, Any]],
    provider_id: str,
    config: Any,
) -> Dict[str, Dict[str, Any]]:
    provider_cfg = (getattr(config, "provider", None) or {}).get(provider_id)
    if not provider_cfg or not getattr(provider_cfg, "models", None):
        return models_dict

    for model_id, model_cfg in provider_cfg.models.items():
        existing = models_dict.get(model_id)
        merged = _build_model_from_config(provider_id, model_id, model_cfg, existing)
        if merged:
            models_dict[model_id] = merged
        else:
            # disabled (soft-deleted) in flocks.json: hide it even if the runtime
            # still lists it
            models_dict.pop(model_id, None)

    return models_dict


class ProviderDefaultsResponse(BaseModel):
    """Provider defaults response"""
    providers: list[Dict[str, Any]]
    default: Dict[str, str]


class UIDisplayResponse(BaseModel):
    """Public WebUI display-name response."""

    display_name: str = Field(alias="displayName")
    configured_display_name: Optional[str] = Field(None, alias="configuredDisplayName")
    favicon_url: Optional[str] = Field(None, alias="faviconUrl")


class UIConfigUpdateRequest(BaseModel):
    """Update request for visible WebUI display preferences."""

    model_config = {"populate_by_name": True}

    display_name: Optional[str] = Field(None, alias="displayName")


class ToolFailurePreference(BaseModel):
    """Repeated tool-failure preference exposed to the WebUI."""

    model_config = {"populate_by_name": True}

    disable_on_repeated_failure: bool = Field(
        ...,
        alias="disableOnRepeatedFailure",
    )


class DataRetentionUpdateRequest(BaseModel):
    """Retention days to save; 0 keeps that kind of data forever."""

    model_config = {"populate_by_name": True}

    log_days: int = Field(..., alias="logDays", ge=0, le=MAX_RETENTION_DAYS)
    message_days: int = Field(..., alias="messageDays", ge=0, le=MAX_RETENTION_DAYS)
    session_days: int = Field(..., alias="sessionDays", ge=0, le=MAX_RETENTION_DAYS)

    def to_policy(self) -> RetentionPolicy:
        return RetentionPolicy(
            logDays=self.log_days,
            messageDays=self.message_days,
            sessionDays=self.session_days,
        )


class DataRetentionResponse(BaseModel):
    """Effective retention policy plus the state of the cleanup job."""

    model_config = {"populate_by_name": True}

    log_days: int = Field(..., alias="logDays")
    message_days: int = Field(..., alias="messageDays")
    session_days: int = Field(..., alias="sessionDays")
    running: bool = False
    last_run: Optional[Dict[str, Any]] = Field(None, alias="lastRun")


class DataRetentionPreviewResponse(BaseModel):
    """What a policy would delete right now."""

    model_config = {"populate_by_name": True}

    sessions: int
    messages: int
    log_paths: int = Field(..., alias="logPaths")
    log_bytes: int = Field(..., alias="logBytes")
    record_files: int = Field(..., alias="recordFiles")
    record_bytes: int = Field(..., alias="recordBytes")
    skipped_busy: int = Field(..., alias="skippedBusy")


DEFAULT_UI_DISPLAY_NAME = "Flocks"
FAVICON_MAX_BYTES = 512 * 1024
FAVICON_RELATIVE_DIR = "assets"
FAVICON_BASENAME = "favicon"
FAVICON_MEDIA_TYPES = {
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
SVG_ALLOWED_TAGS = {
    "circle",
    "clippath",
    "defs",
    "desc",
    "ellipse",
    "g",
    "line",
    "lineargradient",
    "mask",
    "path",
    "pattern",
    "polygon",
    "polyline",
    "radialgradient",
    "rect",
    "stop",
    "svg",
    "symbol",
    "text",
    "textpath",
    "title",
    "tspan",
    "use",
}
SVG_ALLOWED_ATTRIBUTES = {
    "aria-label",
    "baseprofile",
    "class",
    "clip-path",
    "clip-rule",
    "color",
    "cx",
    "cy",
    "d",
    "direction",
    "display",
    "dominant-baseline",
    "dx",
    "dy",
    "fill",
    "fill-opacity",
    "fill-rule",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "fr",
    "fx",
    "fy",
    "gradienttransform",
    "gradientunits",
    "height",
    "href",
    "id",
    "letter-spacing",
    "mask",
    "offset",
    "opacity",
    "points",
    "preserveaspectratio",
    "r",
    "role",
    "rotate",
    "rx",
    "ry",
    "spreadmethod",
    "stop-color",
    "stop-opacity",
    "stroke",
    "stroke-dasharray",
    "stroke-dashoffset",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-opacity",
    "stroke-width",
    "text-anchor",
    "transform",
    "version",
    "viewbox",
    "visibility",
    "width",
    "x",
    "x1",
    "x2",
    "y",
    "y1",
    "y2",
}
SVG_UNSAFE_VALUE_PATTERN = re.compile(
    r"(?:javascript|vbscript|data|file|https?):|//|@import|expression\s*\(",
    re.IGNORECASE,
)
SVG_URL_PATTERN = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)


def _default_display_name() -> str:
    """The product name when the deployment has not configured one.

    Comes from whatever branding provider is registered, so an installation
    that ships its own name supplies it through the extension point rather than
    this module knowing which packages exist and what they are called.
    """

    from flocks.server.branding import default_display_name

    return default_display_name()


def _effective_display_name(
    config: ConfigInfoModel, places: Optional[ReferencePlaces] = None
) -> tuple[str, Optional[str]]:
    configured = config.ui.display_name if config.ui else None
    if configured and places is not None and (places.covers(("ui", "displayName")) or places.reveals(configured)):
        # The display name is public (login page). One that a config file stores as
        # a {secret:…}-style reference, or that holds what a reference expands to
        # (another spelling of the key), is never shown.
        configured = None
    return configured or _default_display_name(), configured


def _ui_assets_dir() -> Path:
    return Config.get_config_path() / FAVICON_RELATIVE_DIR


def _favicon_relative_path(ext: str) -> str:
    return f"{FAVICON_RELATIVE_DIR}/{FAVICON_BASENAME}{ext}"


def _safe_config_relative_path(relative_path: str) -> Optional[Path]:
    try:
        config_dir = Config.get_config_path().resolve()
        target = (config_dir / relative_path).resolve()
        if target == config_dir or config_dir not in target.parents:
            return None
        return target
    except Exception:
        return None


def _configured_favicon_path(config: ConfigInfoModel) -> Optional[Path]:
    relative_path = config.ui.favicon_path if config.ui else None
    if not relative_path:
        return None
    target = _safe_config_relative_path(relative_path)
    if not target or not target.is_file():
        return None
    return target


def _favicon_media_type(path: Path) -> str:
    return FAVICON_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _favicon_url(config: ConfigInfoModel) -> Optional[str]:
    path = _configured_favicon_path(config)
    if not path:
        return None
    try:
        version = int(path.stat().st_mtime)
    except OSError:
        version = 0
    return f"/api/config/ui-favicon?v={version}"


def _xml_local_name(name: str) -> str:
    if name.startswith("{") and "}" in name:
        return name.rsplit("}", 1)[1]
    if ":" in name:
        return name.rsplit(":", 1)[1]
    return name


def _xml_namespace(name: str) -> Optional[str]:
    if name.startswith("{") and "}" in name:
        return name[1:].split("}", 1)[0]
    return None


def _validate_svg_value(attribute: str, value: str) -> None:
    stripped = value.strip()
    if SVG_UNSAFE_VALUE_PATTERN.search(stripped):
        raise HTTPException(status_code=400, detail=f"Unsafe SVG attribute value: {attribute}")

    if attribute == "href" and stripped and not stripped.startswith("#"):
        raise HTTPException(status_code=400, detail="SVG href values must reference local fragments only")

    for match in SVG_URL_PATTERN.finditer(stripped):
        target = match.group(2).strip()
        if not target.startswith("#"):
            raise HTTPException(status_code=400, detail="SVG url() values must reference local fragments only")


def _validate_svg_favicon(content: bytes) -> bytes:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail="SVG favicon must be UTF-8 encoded") from e

    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered or "<?xml-stylesheet" in lowered:
        raise HTTPException(status_code=400, detail="SVG favicon contains unsupported XML declarations")

    try:
        root = DefusedET.fromstring(text)
    except (DefusedXmlException, ET.ParseError) as e:
        raise HTTPException(status_code=400, detail="Invalid SVG favicon") from e

    if _xml_namespace(root.tag) not in (None, SVG_NAMESPACE) or _xml_local_name(root.tag).lower() != "svg":
        raise HTTPException(status_code=400, detail="Favicon SVG root element must be <svg>")

    for element in root.iter():
        tag_name = _xml_local_name(element.tag).lower()
        if _xml_namespace(element.tag) not in (None, SVG_NAMESPACE):
            raise HTTPException(status_code=400, detail=f"Unsupported SVG namespace for <{tag_name}>")
        if tag_name not in SVG_ALLOWED_TAGS:
            raise HTTPException(status_code=400, detail=f"Unsupported SVG element: <{tag_name}>")

        for raw_attribute, value in element.attrib.items():
            attribute_namespace = _xml_namespace(raw_attribute)
            attribute_name = _xml_local_name(raw_attribute).lower()
            if attribute_namespace not in (None, XLINK_NAMESPACE):
                raise HTTPException(status_code=400, detail=f"Unsupported SVG attribute: {attribute_name}")
            if attribute_namespace == XLINK_NAMESPACE and attribute_name != "href":
                raise HTTPException(status_code=400, detail=f"Unsupported SVG attribute: {attribute_name}")
            if attribute_name.startswith("on") or attribute_name in {"style", "src"}:
                raise HTTPException(status_code=400, detail=f"Unsupported SVG attribute: {attribute_name}")
            if attribute_name not in SVG_ALLOWED_ATTRIBUTES:
                raise HTTPException(status_code=400, detail=f"Unsupported SVG attribute: {attribute_name}")
            _validate_svg_value(attribute_name, value)

    return content


def _get_or_create_ui_section(data: Dict[str, Any]) -> Dict[str, Any]:
    ui_section = data.get("ui")
    if not isinstance(ui_section, dict):
        ui_section = {}
    return ui_section


def _persist_ui_section(data: Dict[str, Any], ui_section: Dict[str, Any]) -> None:
    if ui_section:
        data["ui"] = ui_section
    else:
        data.pop("ui", None)
    ConfigWriter._write_raw(data)


def _effective_tool_failure_preference(config: ConfigInfoModel) -> bool:
    if config.tool_failure is None:
        return True
    return config.tool_failure.disable_on_repeated_failure


@router.get("/ui-display", response_model=UIDisplayResponse, summary="Get public UI display name")
async def get_ui_display() -> UIDisplayResponse:
    """Return only the effective WebUI display name for public screens."""
    try:
        complete_config, places = await Config.get_with_reference_places()
        display_name, configured_display_name = await asyncio.to_thread(
            _effective_display_name, complete_config, places
        )
        return UIDisplayResponse(
            displayName=display_name,
            configuredDisplayName=configured_display_name,
            faviconUrl=_favicon_url(complete_config),
        )
    except Exception as e:
        log.error("config.ui_display.get.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/ui", response_model=UIDisplayResponse, summary="Update UI display preferences")
@serialized_config_mutation
async def update_ui_config(
    request: UIConfigUpdateRequest,
    _admin: object = Depends(require_admin),
) -> UIDisplayResponse:
    """Update visible WebUI display preferences."""
    try:
        if request.display_name and contains_reference(request.display_name):
            # It would be expanded into the secret and shown on the public login page.
            raise ValueError("显示名称不能包含 {env:…}、{secret:…}、{file:…} 这类引用")
        ui_config = UIConfig.model_validate({"displayName": request.display_name})
        data = ConfigWriter._read_raw()
        ui_section = _get_or_create_ui_section(data)

        if ui_config.display_name:
            ui_section["displayName"] = ui_config.display_name
        else:
            ui_section.pop("displayName", None)

        _persist_ui_section(data, ui_section)
        return await get_ui_display()
    except Exception as e:
        log.error("config.ui.update.error", {"error": str(e)})
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/ui-favicon", summary="Get custom UI favicon")
async def get_ui_favicon() -> FileResponse:
    """Return the uploaded favicon, if one is configured."""
    try:
        complete_config = await Config.get()
        path = _configured_favicon_path(complete_config)
        if not path:
            raise HTTPException(status_code=404, detail="custom favicon is not configured")
        return FileResponse(path, media_type=_favicon_media_type(path))
    except HTTPException:
        raise
    except Exception as e:
        log.error("config.ui_favicon.get.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ui/favicon", response_model=UIDisplayResponse, summary="Upload UI favicon")
@serialized_config_mutation
async def upload_ui_favicon(
    file: UploadFile = File(...),
    _admin: object = Depends(require_admin),
) -> UIDisplayResponse:
    """Upload a custom favicon for visible WebUI branding."""
    filename = Path(file.filename or "").name
    ext = Path(filename).suffix.lower()
    if ext not in FAVICON_MEDIA_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported favicon type. Use .ico, .png, .svg, .jpg, or .webp")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > FAVICON_MAX_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Favicon file is too large. Maximum size is 512 KB.",
            )
        chunks.append(chunk)

    if total == 0:
        raise HTTPException(status_code=400, detail="Favicon file is empty")

    content = b"".join(chunks)
    if ext == ".svg":
        content = _validate_svg_favicon(content)

    assets_dir = _ui_assets_dir()
    assets_dir.mkdir(parents=True, exist_ok=True)
    for old in assets_dir.glob(f"{FAVICON_BASENAME}.*"):
        if old.is_file():
            try:
                old.unlink()
            except OSError:
                pass

    target = assets_dir / f"{FAVICON_BASENAME}{ext}"
    target.write_bytes(content)

    data = ConfigWriter._read_raw()
    ui_section = _get_or_create_ui_section(data)
    ui_section["faviconPath"] = _favicon_relative_path(ext)
    _persist_ui_section(data, ui_section)

    log.info("config.ui_favicon.uploaded", {"path": ui_section["faviconPath"], "size": total})
    return await get_ui_display()


@router.delete("/ui/favicon", response_model=UIDisplayResponse, summary="Reset UI favicon")
@serialized_config_mutation
async def reset_ui_favicon(_admin: object = Depends(require_admin)) -> UIDisplayResponse:
    """Remove the uploaded favicon and fall back to the default bundled favicon."""
    assets_dir = _ui_assets_dir()
    if assets_dir.exists():
        for old in assets_dir.glob(f"{FAVICON_BASENAME}.*"):
            if old.is_file():
                try:
                    old.unlink()
                except OSError:
                    pass

    data = ConfigWriter._read_raw()
    ui_section = _get_or_create_ui_section(data)
    ui_section.pop("faviconPath", None)
    _persist_ui_section(data, ui_section)
    return await get_ui_display()


@router.get(
    "/tool-failure",
    response_model=ToolFailurePreference,
    summary="Get repeated tool-failure preference",
)
async def get_tool_failure_preference() -> ToolFailurePreference:
    """Return whether repeated identical failures automatically disable tools."""
    try:
        config = await Config.get()
        return ToolFailurePreference(
            disableOnRepeatedFailure=_effective_tool_failure_preference(config)
        )
    except Exception as e:
        log.error("config.tool_failure.get.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.patch(
    "/tool-failure",
    response_model=ToolFailurePreference,
    summary="Update repeated tool-failure preference",
)
@serialized_config_mutation
async def update_tool_failure_preference(
    request: ToolFailurePreference,
    _admin: object = Depends(require_admin),
) -> ToolFailurePreference:
    """Update only the repeated-failure switch in flocks.json."""
    try:
        data = ConfigWriter._read_raw()
        existing = data.get("toolFailure", data.get("tool_failure", {}))
        section = dict(existing) if isinstance(existing, dict) else {}
        section.pop("disable_on_repeated_failure", None)
        section["disableOnRepeatedFailure"] = request.disable_on_repeated_failure
        data.pop("tool_failure", None)
        data["toolFailure"] = section
        ConfigWriter._write_raw(data)
        return await get_tool_failure_preference()
    except Exception as e:
        log.error("config.tool_failure.update.error", {"error": str(e)})
        raise HTTPException(status_code=400, detail=str(e))



# ---------------------------------------------------------------------------
# dataRetention — how long logs, messages and sessions are kept
# ---------------------------------------------------------------------------


async def _data_retention_status() -> DataRetentionResponse:
    policy = await load_policy()
    return DataRetentionResponse(
        logDays=policy.log_days,
        messageDays=policy.message_days,
        sessionDays=policy.session_days,
        running=DataRetentionScheduler.is_running(),
        lastRun=await DataRetentionScheduler.last_run(),
    )


@router.get(
    "/data-retention",
    response_model=DataRetentionResponse,
    summary="Get data retention policy",
)
async def get_data_retention(request: Request) -> DataRetentionResponse:
    """Return how long logs, messages and sessions are kept, and the last cleanup."""
    require_admin(request)
    return await _data_retention_status()


@router.post(
    "/data-retention/preview",
    response_model=DataRetentionPreviewResponse,
    summary="Preview what a data retention policy would delete",
)
async def preview_data_retention(
    request: Request,
    body: DataRetentionUpdateRequest,
) -> DataRetentionPreviewResponse:
    """Count what saving ``body`` would delete now; nothing is deleted."""
    require_admin(request)
    targets = await collect_targets(body.to_policy())
    return DataRetentionPreviewResponse(**targets.summary())


@router.patch(
    "/data-retention",
    response_model=DataRetentionResponse,
    summary="Update data retention policy",
)
@serialized_config_mutation
async def update_data_retention(
    request: Request,
    body: DataRetentionUpdateRequest,
) -> DataRetentionResponse:
    """Save the policy in flocks.json and start a cleanup pass in the background."""
    require_admin(request)
    policy = body.to_policy()
    try:
        data = ConfigWriter._read_raw()
        existing = data.get(DATA_RETENTION_SECTION)
        section = dict(existing) if isinstance(existing, dict) else {}
        section.update(policy.to_config())
        data[DATA_RETENTION_SECTION] = section
        ConfigWriter._write_raw(data)
    except Exception as e:
        log.error("config.data_retention.update.error", {"error": str(e)})
        raise HTTPException(status_code=400, detail=str(e))
    # Waits out deletes still running under the old policy in any worker.
    await bump_policy_version()
    apply_log_retention(policy)
    DataRetentionScheduler.request_run()
    log.info("config.data_retention.updated", policy.to_config())
    return await _data_retention_status()


def _is_admin(request: Request) -> bool:
    user = get_optional_user(request)
    return bool(user and user.role == "admin")


class _AdminConfigView(NamedTuple):
    """What an admin reads of the config, and the config in force it stands for."""

    view: Dict[str, Any]
    loaded: Dict[str, Any]
    # Masked view only: each masked place and the value it hides (``member_view``).
    hidden: Optional[Dict[Any, Any]] = None


def _restore_unchanged(sent: Any, shown: Any, loaded: Any) -> Any:
    """``sent`` with every part the admin sent back exactly as shown set to the value in force.

    Compared part by part through objects; a list counts only as a whole, since
    layers merge some lists (``plugin``, ``instructions``) in no fixed order and
    the shown list need not line up with the loaded one. A changed list is kept
    as sent, references written out.
    """
    if sent == shown:
        return copy.deepcopy(loaded)
    if isinstance(sent, dict) and isinstance(shown, dict) and isinstance(loaded, dict):
        return {
            key: _restore_unchanged(value, shown[key], loaded[key]) if key in shown and key in loaded else value
            for key, value in sent.items()
        }
    return sent


def _raise_if_config_file_unreadable() -> None:
    """Raise ValueError when flocks.json is not JSON(C), templates outside strings allowed."""
    path = ConfigWriter._get_config_path()
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if text.strip():
        Config.parse_jsonc(quote_bare_references(text), path)


async def _admin_config_view() -> _AdminConfigView:
    """The config as admins read it: references written out, never their values.

    A client that sends it back unchanged keeps the references
    (``_restore_admin_payload``). When the files cannot be read unexpanded (a
    typed field holding a reference), the loaded config is shown with every
    referenced and credential-looking value masked, as members see it.
    """
    complete_config, places = await Config.get_with_reference_places()
    loaded = complete_config.model_dump(by_alias=True, exclude_none=True)
    try:
        unresolved = (await Config.get_unresolved()).model_dump(by_alias=True, exclude_none=True)
    except ValueError as exc:
        # An unreadable file must surface as an error (never serve the
        # stale cached config for a corrupt flocks.json) ...
        _raise_if_config_file_unreadable()
        # ... but a typed field holding an unresolved reference is fine.
        log.warning("config.get.unresolved_failed", {"error": str(exc)})
        view, hidden = member_view(loaded, places)
        return _AdminConfigView(view, loaded, hidden)
    return _AdminConfigView(unresolved, loaded)


@router.get("", summary="Get configuration")
async def get_config(request: Request) -> Dict[str, Any]:
    """
    Get configuration
    
    Retrieve the current Flocks configuration settings and preferences.
    Flocks TUI expects the merged Config object directly.

    The loaded config holds expanded {secret:…}/{env:…}/{file:…} values, so it
    is never returned as such: admins read the references written out
    (``_admin_config_view``); for non-admins every value a config file stores
    as a reference, and every credential-looking value, is masked.
    """
    try:
        if _is_admin(request):
            return (await _admin_config_view()).view
        # Get complete configuration (includes global + project + env)
        complete_config, places = await Config.get_with_reference_places()
        
        # Return merged config in Flocks format
        data = complete_config.model_dump(by_alias=True, exclude_none=True)
        return member_view(data, places)[0]
    except Exception as e:
        log.error("config.get.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


def _policy_config_of(section: Any) -> Dict[str, int]:
    return policy_from_config(ConfigInfoModel.model_validate({"dataRetention": section})).to_config()


def _retention_section_of(raw: Dict[str, Any]) -> Any:
    """The data retention section ``raw`` carries, resolved exactly as the config loader does.

    Both spellings go through ConfigInfo, whose ``_single_data_retention_spelling``
    collapses a camelCase/snake_case mix into one value. Reading a request body
    and the on-disk file this way means both are judged as the policy that will be
    saved / is in force, never by hand-picking one key.
    """
    retention_only = {key: raw[key] for key in ("dataRetention", "data_retention") if key in raw}
    if not retention_only:
        return None
    return ConfigInfoModel.model_validate(retention_only).data_retention


async def _guard_data_retention_change(request: Request, config_data: Dict[str, Any]) -> bool:
    """Changing the global retention policy through this generic endpoint needs admin.

    Returns whether the effective policy changes (so the caller can notify the
    scheduler). The dedicated /data-retention endpoint is already admin-only;
    this closes the same escalation here, since the retention cleanup deletes
    every user's data. A payload that leaves the effective policy unchanged (or
    omits the key) is untouched, so normal member config saves and TUI (admin)
    saves still work.
    """
    if not isinstance(config_data, dict):
        return False
    # The model accepts both the alias (camelCase) and the field name
    # (snake_case), so either spelling is just as effective; consider both.
    if "dataRetention" not in config_data and "data_retention" not in config_data:
        return False
    # Both sides are resolved with ``_retention_section_of`` — the same model and
    # spelling rules the save and the loader use — never by hand-picking one key.
    # Hand-picking is how a null camelCase value next to a real snake_case one
    # used to read as "unchanged", on the request side and on the disk side.
    try:
        incoming_section = _retention_section_of(config_data)
    except Exception:
        # A retention value that fails validation fails the real save too (400);
        # don't gate on a request that cannot be written.
        return False
    if incoming_section is None:
        # ``None`` is dropped by the exclude_none config merge, so the persisted
        # policy is left unchanged — there is nothing to gate.
        return False
    # Compare against what is persisted on disk, not a possibly-stale in-process
    # cache: a worker whose cache lags an admin's change must not let a member
    # re-establish the old (deleting) policy by echoing it.
    try:
        on_disk = ConfigWriter._read_raw()
    except Exception:
        on_disk = {}
    try:
        current_section = _retention_section_of(on_disk if isinstance(on_disk, dict) else {})
    except Exception:
        # Can't tell which policy is in force: treat any concrete value as a change.
        require_admin(request)
        return True
    if _policy_config_of(incoming_section) == _policy_config_of(current_section):
        return False
    require_admin(request)
    return True


# Model settings: providers with their models and options, the default, small and
# fallback models, and which providers are enabled. The model routes keep them
# admin-only, and this generic endpoint must not be a way around that.
_MODEL_CONFIG_FIELDS = (
    "provider",
    "model",
    "small_model",
    "default_models",
    "fallback_providers",
    "disabled_providers",
    "enabled_providers",
)


async def _guard_model_config_change(request: Request, config_data: Dict[str, Any]) -> None:
    """Changing model settings through this generic endpoint needs admin.

    Members still save other settings here, and the Channel page sends back the
    whole config it read, so a model setting equal to the current one is not a
    change. The payload is judged as the save will write it (same model, same
    dump), under both spellings of each key. References from members are
    refused before this runs (``_restore_member_payload``), since a key such as
    "{secret:…}" can turn into "model" once the file is reloaded.
    """
    if not isinstance(config_data, dict):
        return
    keys = {
        spelling
        for name in _MODEL_CONFIG_FIELDS
        for spelling in (name, ConfigInfoModel.model_fields[name].alias or name)
    }
    if not keys.intersection(config_data):
        return
    try:
        incoming = ConfigInfoModel.model_validate(config_data).model_dump(by_alias=True, exclude_none=True)
    except Exception:
        # A payload that fails validation fails the real save too (400).
        return
    current = (await Config.get()).model_dump(by_alias=True, exclude_none=True)
    if any(key in incoming and incoming[key] != current.get(key) for key in keys):
        require_admin(request)


async def _restore_member_payload(request: Request, config_data: Dict[str, Any]) -> Dict[str, Any]:
    """Turn what a non-admin read back into the stored values, and refuse new references.

    Members read the config with referenced and credential values masked
    (``get_config``), and the Channel page sends the whole of it back; masked
    values returned unchanged get their real values again, so the save sees
    them as unchanged and keeps what is stored. Whatever a member writes
    is expanded when the file is reloaded, so a reference left after that would
    read a secret for them (or turn into another key): it needs admin.
    """
    if _is_admin(request) or not isinstance(config_data, dict):
        return config_data
    current_config, places = await Config.get_with_reference_places()
    current = current_config.model_dump(by_alias=True, exclude_none=True)
    view, hidden = member_view(current, places)
    restored = restore_hidden(config_data, view, hidden)
    if contains_reference(restored):
        require_admin(request)
    if leftover_masks(drop_unchanged(restored, current), view, hidden):
        # A mask moved or edited around (a list item shifted, a masked URL
        # changed) has no real value to go back to; saving it would write the
        # mask over the hidden value.
        raise HTTPException(
            status_code=400,
            detail="Masked values cannot be saved where they were not shown; enter the real value",
        )
    return restored


async def _restore_admin_payload(request: Request, config_data: Dict[str, Any]) -> Dict[str, Any]:
    """Turn what an admin read back into the loaded values, so unchanged references stay as stored.

    Admins read the config with its references written out (``get_config``),
    and the Channel page sends the whole of it back. What comes back unchanged
    gets its loaded value again, so ``Config.update`` sees it as unchanged and
    keeps what each file stores, instead of copying references from other
    config layers into flocks.json; a reference the admin changed or moved is
    written as given.
    """
    if not _is_admin(request) or not isinstance(config_data, dict):
        return config_data
    shown = await _admin_config_view()
    if shown.hidden is None:
        return _restore_unchanged(config_data, shown.view, shown.loaded)
    # The masked view comes from the loaded config itself, so masks map back by place.
    restored = restore_hidden(config_data, shown.view, shown.hidden)
    if leftover_masks(drop_unchanged(restored, shown.loaded), shown.view, shown.hidden):
        raise HTTPException(
            status_code=400,
            detail="Masked values cannot be saved where they were not shown; enter the real value",
        )
    return restored


@router.patch("/", summary="Update configuration")
@serialized_config_mutation
async def update_config(
    request: Request,
    config_data: Dict[str, Any],
    _admin: object = Depends(require_admin),
) -> Dict[str, Any]:
    """
    Update configuration

    Update Flocks configuration settings and preferences.
    Returns the updated Config in Flocks format.

    Sensitive channel fields (botToken, appSecret, secret, clientSecret, …)
    are automatically extracted to .secret.json and replaced with
    {secret:channel_<id>_<field>} references before the config is written to
    flocks.json, so that plaintext secrets never land in that file.
    """
    config_data = await _restore_admin_payload(request, config_data)
    config_data = await _restore_member_payload(request, config_data)
    await _guard_model_config_change(request, config_data)
    retention_changed = await _guard_data_retention_change(request, config_data)
    await _validate_plugin_group_updates(config_data)
    try:
        channel_allow_from_deletions = _channel_allow_from_deletion_ids(config_data)
        _normalize_slack_dm_policy(config_data)

        channels_payload = config_data.get("channels")
        # Extract channel sensitive fields into .secret.json before persisting.
        # Only fields the caller changed: the Channel page sends back the whole
        # config it read, and an unchanged credential (an {env:…} one included)
        # must stay as flocks.json stores it; ``Config.update`` leaves it alone.
        if isinstance(channels_payload, dict):
            from flocks.security.channel_secrets import extract_channel_secrets

            current_channels = (await Config.get()).model_dump(
                by_alias=True, exclude_none=True, mode="json"
            ).get("channels") or {}
            extracted = extract_channel_secrets(drop_unchanged(channels_payload, current_channels))
            config_data = {**config_data, "channels": Config.merge_deep(channels_payload, extracted)}

        # Parse and validate configuration
        config = ConfigInfoModel.model_validate(config_data)
        # ``memory`` is typed as Any on ConfigInfo; validate it against the
        # memory schema here so bad values fail the request instead of the
        # memory subsystem later.
        if isinstance(config_data.get("memory"), dict):
            from flocks.memory.config import MemoryConfig

            MemoryConfig.model_validate(config_data["memory"])

        # Update project config
        await Config.update(
            config,
            channel_allow_from_deletions=channel_allow_from_deletions,
        )
        
        # Clear cache to reload
        Config.clear_cache()
        if any(isinstance(config_data.get(section), dict) and config_data[section] for section in ("agent", "mode")):
            from flocks.agent.registry import Agent

            Agent.invalidate_cache()
        # Refresh only OSS-owned channel routing and Agent visibility config.
        from flocks.channel.inbound.dispatcher import (
            InboundDispatcher,
            invalidate_channel_config_cache,
        )

        channels = config_data.get("channels")
        if isinstance(channels, dict) and channels:
            for channel_id in channels:
                invalidate_channel_config_cache(str(channel_id))
            for channel_id in channels:
                await InboundDispatcher._get_channel_config(
                    str(channel_id),
                    force_refresh=True,
                )
        else:
            invalidate_channel_config_cache()

        if retention_changed:
            # Mirror the dedicated endpoint: fence off deletes under the old
            # policy in every worker, apply the new log period and kick a
            # cleanup (which also bumps the scheduler generation so an in-flight
            # round here stops deleting under the superseded policy).
            await bump_policy_version()
            apply_log_retention(await load_policy())
            DataRetentionScheduler.request_run()

        log.info("config.updated")
        return await get_config(request)
    except Exception as e:
        log.error("config.update.error", {"error": str(e)})
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/providers", response_model=ProviderDefaultsResponse, summary="List config providers")
async def get_providers():
    """
    List config providers
    
    Get a list of all configured AI providers and their default models.
    
    Note: Flocks TUI expects models as Dict[modelID, Model], not List[Model].
    """
    try:
        with log.time("providers"):
            config = await Config.get()
            await Provider.apply_config(config)

            # Get all provider IDs
            provider_ids = Provider.list_providers()
            
            # Get models for each provider
            providers_list = []
            default_models = {}
            
            for provider_id in provider_ids:
                try:
                    models = Provider.list_models(provider_id)
                    
                    # Flocks TUI expects models as Dict[modelID, Model]
                    models_dict = {}
                    first_model_id = None
                    
                    for model in models:
                        if first_model_id is None:
                            first_model_id = model.id
                        
                        # Build model dict in Flocks format
                        models_dict[model.id] = {
                            "id": model.id,
                            "name": model.name,
                            "providerID": model.provider_id,
                            "attachment": model.capabilities.supports_vision,
                            "reasoning": False,
                            "temperature": True,
                            "tool_call": model.capabilities.supports_tools,
                            "limit": {
                                "context": model.capabilities.context_window or 128000,
                                "output": model.capabilities.max_tokens or 4096,
                            },
                            "options": {},
                        }
                    
                    _merge_config_models(models_dict, provider_id, config)

                    provider_info = {
                        "id": provider_id,
                        "name": provider_id.capitalize(),
                        "models": models_dict,
                    }
                    
                    providers_list.append(provider_info)
                    
                    # Get default model (first model in the list)
                    if not first_model_id and models_dict:
                        first_model_id = next(iter(models_dict))
                    if first_model_id:
                        default_models[provider_id] = first_model_id
                except Exception as e:
                    log.warn("provider.models.error", {"provider": provider_id, "error": str(e)})
            
            return ProviderDefaultsResponse(
                providers=providers_list,
                default=default_models,
            )
    except Exception as e:
        log.error("providers.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))
