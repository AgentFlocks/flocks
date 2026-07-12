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

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from flocks.config.config import Config, GlobalConfig, ConfigInfo as ConfigInfoModel, UIConfig
from flocks.config.config_writer import ConfigWriter
from flocks.provider.provider import Provider
from flocks.security.action_gateway import (
    ActionDecisionError,
    SecurityAction,
    enforce_action_decision,
    run_before_action,
)
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="routes.config")


def _config_action_input(config_data: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize an update for action hooks without exposing config values."""
    encoded = json.dumps(
        config_data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "sections": [
            {"name": section_name, "type": type(config_data[section_name]).__name__}
            for section_name in sorted(config_data)
        ],
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


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


DEFAULT_UI_DISPLAY_NAME = "Flocks"
DEFAULT_UI_PRO_DISPLAY_NAME = "Flocks Pro"
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


def _is_flockspro_enabled() -> bool:
    try:
        from flocks.server.routes.console_upgrade import _get_pro_capability_status

        status_data = _get_pro_capability_status()
    except Exception:
        return False
    return any(status_data.get(key) is True for key in ("pro_enabled", "active", "activated"))


def _default_display_name() -> str:
    return DEFAULT_UI_PRO_DISPLAY_NAME if _is_flockspro_enabled() else DEFAULT_UI_DISPLAY_NAME


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


@router.get("/ui-display", response_model=UIDisplayResponse, summary="Get public UI display name")
async def get_ui_display() -> UIDisplayResponse:
    """Return only the effective WebUI display name for public screens."""
    try:
        complete_config = await Config.get()
        display_name, configured_display_name = _effective_display_name(complete_config)
        return UIDisplayResponse(
            displayName=display_name,
            configuredDisplayName=configured_display_name,
            faviconUrl=_favicon_url(complete_config),
        )
    except Exception as e:
        log.error("config.ui_display.get.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/ui", response_model=UIDisplayResponse, summary="Update UI display preferences")
async def update_ui_config(request: UIConfigUpdateRequest) -> UIDisplayResponse:
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
async def upload_ui_favicon(file: UploadFile = File(...)) -> UIDisplayResponse:
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
async def reset_ui_favicon() -> UIDisplayResponse:
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


@router.get("", summary="Get configuration")
async def get_config() -> Dict[str, Any]:
    """
    Get configuration
    
    Retrieve the current Flocks configuration settings and preferences.
    Flocks TUI expects the merged Config object directly.
    """
    try:
        # Get complete configuration (includes global + project + env)
        complete_config = await Config.get()
        
        # Return merged config in Flocks format
        return complete_config.model_dump(by_alias=True, exclude_none=True)
    except Exception as e:
        log.error("config.get.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/", summary="Update configuration")
async def update_config(config_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update configuration
    
    Update Flocks configuration settings and preferences.
    Returns the updated Config in Flocks format.

    Sensitive channel fields (botToken, appSecret, secret, clientSecret, …)
    are automatically extracted to .secret.json and replaced with
    {secret:channel_<id>_<field>} references before the config is written to
    flocks.json, so that plaintext secrets never land in that file.
    """
    try:
        decision = await run_before_action(
            SecurityAction(
                action="configure",
                resource={"type": "control_plane_config", "id": "flocks"},
                canonical_input=_config_action_input(config_data),
                execution_domain="control_plane",
                metadata={"entry": "api"},
            )
        )
        enforce_action_decision(decision)

        # Extract channel sensitive fields into .secret.json before persisting
        if "channels" in config_data and isinstance(config_data.get("channels"), dict):
            from flocks.security.channel_secrets import extract_channel_secrets
            config_data = {**config_data, "channels": extract_channel_secrets(config_data["channels"])}

        # Parse and validate configuration
        config = ConfigInfoModel.model_validate(config_data)
        
        # Update project config
        await Config.update(config)
        
        # Clear cache to reload
        Config.clear_cache()
        
        log.info("config.updated")
        
        return await get_config()
    except ActionDecisionError:
        raise
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
