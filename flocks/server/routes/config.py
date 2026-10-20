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
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, Optional
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
from flocks.server.auth import require_admin
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


def _effective_display_name(config: ConfigInfoModel) -> tuple[str, Optional[str]]:
    configured = config.ui.display_name if config.ui else None
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
        complete_config = await Config.get()
        display_name, configured_display_name = await asyncio.to_thread(_effective_display_name, complete_config)
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
# network.proxy — outbound proxy policy (mode / URLs / direct-connection whitelist)
# ---------------------------------------------------------------------------

_PROXY_PASSWORD_SECRET_ID = "network_proxy_password"


class NetworkProxyView(BaseModel):
    """What the WebUI sees: credentials redacted, effective policy resolved."""

    model_config = {"populate_by_name": True}

    mode: str
    http: Optional[str] = None
    https: Optional[str] = None
    all: Optional[str] = None
    username: Optional[str] = None
    has_password: bool = Field(False, alias="hasPassword")
    bypass: list[str] = Field(default_factory=list)
    bypass_private: bool = Field(True, alias="bypassPrivate")
    source: str = "default"
    effective: Dict[str, Any] = Field(default_factory=dict)
    process_env: Dict[str, str] = Field(default_factory=dict, alias="processEnv")


class NetworkProxyUpdate(BaseModel):
    """PATCH body; omitted fields keep their current value, empty strings clear them."""

    model_config = {"populate_by_name": True, "extra": "forbid"}

    mode: Optional[str] = Field(None, pattern="^(off|system|manual)$")
    http: Optional[str] = None
    https: Optional[str] = None
    all: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = Field(None, description="New password; empty string removes it; omit to keep")
    bypass: Optional[list[str]] = None
    bypass_private: Optional[bool] = Field(None, alias="bypassPrivate")


class NetworkProxyTestRequest(BaseModel):
    model_config = {"populate_by_name": True, "extra": "forbid"}

    url: str = Field("https://www.google.com/generate_204", description="http(s) URL to fetch through the current policy")
    timeout: float = Field(10.0, ge=1, le=60)


class NetworkProxyTestResult(BaseModel):
    model_config = {"populate_by_name": True}

    ok: bool
    url: str
    via: Optional[str] = Field(None, description="Proxy used (credentials redacted) or null for direct")
    status: Optional[int] = None
    elapsed_ms: Optional[int] = Field(None, alias="elapsedMs")
    error: Optional[str] = None


def _proxy_view() -> NetworkProxyView:
    from flocks.net import get_proxy_settings

    settings = get_proxy_settings()  # re-reads flocks.json when its mtime changed
    public = settings.public_dict()
    env = {name: os.environ[name] for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY") if name in os.environ}
    from flocks.net.proxy import _redact  # credentials never leave the process

    return NetworkProxyView(
        mode=public["mode"],
        http=public["http"],
        https=public["https"],
        all=public["all"],
        username=public["username"],
        hasPassword=public["hasPassword"],
        bypass=public["bypass"],
        bypassPrivate=public["bypassPrivate"],
        source=public["source"],
        effective=public["effective"],
        processEnv={k: (_redact(v) or v) for k, v in env.items()},
    )


@router.get("/network/proxy", response_model=NetworkProxyView, summary="Get outbound proxy policy")
async def get_network_proxy(_admin: object = Depends(require_admin)) -> NetworkProxyView:
    """Proxy mode, URLs (credentials redacted), whitelist and the policy actually in effect."""
    return _proxy_view()


@router.patch("/network/proxy", response_model=NetworkProxyView, summary="Update outbound proxy policy")
@serialized_config_mutation
async def update_network_proxy(
    request: NetworkProxyUpdate,
    _admin: object = Depends(require_admin),
) -> NetworkProxyView:
    """Write ``network.proxy`` in flocks.json, keep the password in .secret.json, apply immediately."""
    from flocks.net import apply_process_environment, invalidate_proxy_settings
    from flocks.net.proxy import ProxySettings, _normalise_proxy_url, _redact, validate_bypass_rules
    from flocks.security import get_secret_manager

    def _redacted_forms(stored: str) -> set:
        forms = {_redact(stored)}
        try:
            forms.add(_redact(_normalise_proxy_url(stored)))  # what GET actually returned
        except ValueError:
            pass
        return {form for form in forms if form}

    data = ConfigWriter._read_raw()
    network = data.get("network")
    network = dict(network) if isinstance(network, dict) else {}
    section = network.get("proxy")
    section = dict(section) if isinstance(section, dict) else {}

    payload = request.model_dump(exclude_unset=True, by_alias=False)
    if "bypass" in payload and payload["bypass"] is not None:
        cleaned = [str(rule).strip() for rule in payload["bypass"] if str(rule).strip()]
        bad = validate_bypass_rules(cleaned)
        if bad:
            raise HTTPException(status_code=400, detail=f"无效的白名单条目: {', '.join(bad)}")
        section["bypass"] = cleaned
    for key in ("mode", "http", "https", "all", "username"):
        if key in payload:
            value = payload[key]
            if isinstance(value, str) and not value.strip():
                section.pop(key, None)
            elif value is not None:
                value = value.strip() if isinstance(value, str) else value
                stored = section.get(key)
                if key in ("http", "https", "all") and isinstance(stored, str) and value != stored and value in _redacted_forms(stored):
                    # GET hands out the redacted (and normalised) form of a URL with inline
                    # credentials ("http://user:***@host:80"); a client echoing it back has not changed it
                    continue
                section[key] = value
    if "bypass_private" in payload and payload["bypass_private"] is not None:
        section["bypassPrivate"] = bool(payload["bypass_private"])
        section.pop("bypass_private", None)
    if "password" in payload:
        if payload["password"]:
            section["password"] = "{secret:" + _PROXY_PASSWORD_SECRET_ID + "}"
        else:
            section.pop("password", None)

    # Validate before writing anything: bad URLs / schemes are rejected with a 400,
    # and neither flocks.json nor .secret.json is touched.
    try:
        ProxySettings.build(
            mode=section.get("mode", "system"),
            http=section.get("http"),
            https=section.get("https"),
            all=section.get("all"),
            username=section.get("username"),
            bypass=section.get("bypass"),
            bypass_private=section.get("bypassPrivate", True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"代理地址无效: {exc}") from exc

    if "password" in payload:
        secrets = get_secret_manager()
        if payload["password"]:
            secrets.set(_PROXY_PASSWORD_SECRET_ID, payload["password"])
        else:
            secrets.delete(_PROXY_PASSWORD_SECRET_ID)

    network["proxy"] = section
    data["network"] = network
    ConfigWriter._write_raw(data)

    settings = invalidate_proxy_settings()
    apply_process_environment(settings)
    log.info("config.network.proxy.updated", {"mode": settings.mode, "source": settings.source})
    return _proxy_view()


@router.post("/network/proxy/test", response_model=NetworkProxyTestResult, summary="Test the proxy policy against a URL")
async def test_network_proxy(
    request: NetworkProxyTestRequest,
    _admin: object = Depends(require_admin),
) -> NetworkProxyTestResult:
    """Fetch ``url`` with the policy currently saved (HEAD-like GET, no body kept) and report route / status / latency."""
    import time as _time

    import httpx

    from flocks.net import get_proxy_settings
    from flocks.net.http import async_client
    from flocks.net.proxy import _redact

    url = request.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="只支持 http:// 或 https:// 地址")
    settings = get_proxy_settings()
    via = _redact(settings.proxy_url_for(url))
    started = _time.perf_counter()
    try:
        async with async_client(settings=settings, timeout=request.timeout, follow_redirects=False) as client:
            async with client.stream("GET", url) as response:
                status_code = response.status_code
        return NetworkProxyTestResult(
            ok=True, url=url, via=via, status=status_code, elapsedMs=int((_time.perf_counter() - started) * 1000)
        )
    except httpx.HTTPError as exc:
        return NetworkProxyTestResult(
            ok=False, url=url, via=via, elapsedMs=int((_time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}"[:300],
        )
    except Exception as exc:  # socks handshake errors surface as generic exceptions
        return NetworkProxyTestResult(
            ok=False, url=url, via=via, elapsedMs=int((_time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}"[:300],
        )


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


@router.get("", summary="Get configuration")
async def get_config() -> Dict[str, Any]:
    """
    Get configuration
    
    Retrieve the current Flocks configuration settings and preferences.
    Flocks TUI expects the merged Config object directly.
    """
    try:
        # Merged configuration with {secret:}/{env:} references kept verbatim:
        # clients never receive resolved secret values, and a client that
        # PATCHes this document back keeps the references intact.
        try:
            complete_config = await Config.get_unresolved()
        except ValueError as exc:
            # An unreadable file must surface as an error (never serve the
            # stale cached config for a corrupt flocks.json) ...
            ConfigWriter._read_path_raw(ConfigWriter._get_config_path(), strict=True)
            # ... but a typed field holding an unresolved reference is fine:
            # fall back to the resolved view with stored secret values scrubbed.
            log.warning("config.get.unresolved_failed", {"error": str(exc)})
            complete_config = await Config.get()
            return _mask_secret_values(
                complete_config.model_dump(by_alias=True, exclude_none=True)
            )
        
        # Return merged config in Flocks format
        return complete_config.model_dump(by_alias=True, exclude_none=True)
    except Exception as e:
        log.error("config.get.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


def _mask_secret_values(data: Any) -> Any:
    """Replace any string equal to a stored secret value with its masked form."""
    from flocks.security import get_secret_manager
    from flocks.security.secrets import SecretManager

    secrets = get_secret_manager()
    values = {
        value
        for value in (secrets.get(secret_id) for secret_id in secrets.list())
        if isinstance(value, str) and value
    }

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, str) and node in values:
            return SecretManager.mask(node)
        return node

    return walk(data)


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


# The loader expands these references anywhere in the file text, object keys included.
_CONFIG_REFERENCE = re.compile(r"\{(?:env|secret|file):[^}]+\}")


def _has_reference_key(value: Any) -> bool:
    """Whether any object key in ``value`` holds a {env:…}, {secret:…} or {file:…} reference."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(key, str) and _CONFIG_REFERENCE.search(key):
                    return True
                stack.append(child)
        elif isinstance(item, list):
            stack.extend(item)
    return False


async def _guard_model_config_change(request: Request, config_data: Dict[str, Any]) -> None:
    """Changing model settings through this generic endpoint needs admin.

    Members still save other settings here, and the Channel page sends back the
    whole config it read, so a model setting equal to the current one is not a
    change. The payload is judged as the save will write it (same model, same
    dump), under both spellings of each key.

    A key holding a reference also needs admin: once the file is reloaded,
    "{secret:…}" can turn into "model" (or any other guarded key) and slip past
    the comparison below.
    """
    if not isinstance(config_data, dict):
        return
    if _has_reference_key(config_data):
        require_admin(request)
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
    await _guard_model_config_change(request, config_data)
    retention_changed = await _guard_data_retention_change(request, config_data)
    await _validate_plugin_group_updates(config_data)
    try:
        channel_allow_from_deletions = _channel_allow_from_deletion_ids(config_data)
        _normalize_slack_dm_policy(config_data)

        channels_payload = config_data.get("channels")
        # Extract channel sensitive fields into .secret.json before persisting
        if isinstance(channels_payload, dict):
            from flocks.security.channel_secrets import extract_channel_secrets

            config_data = {**config_data, "channels": extract_channel_secrets(config_data["channels"])}

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
        return await get_config()
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
