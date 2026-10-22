"""Administrator model discovery, credential checks, and inventory management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from flocks.bus import Bus, BusEvent
from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter
from flocks.provider.model_catalog import get_raw_catalog
from flocks.provider.model_discovery import discover_models
from flocks.provider.provider import Provider
from flocks.server.auth import require_admin
from flocks.server.config_mutation import serialized_config_mutation
from flocks.server.routes.provider import _ensure_provider_initialized, _load_dynamic_providers

router = APIRouter()

_MODEL_LIST_KEY_CHECK_PROVIDERS = {"threatbook-cn-llm", "threatbook-io-llm"}


class DiscoverModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str | None = Field(None, max_length=16384)
    base_url: str | None = Field(None, max_length=4096)


ModelIdentifier = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class ImportModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: ModelIdentifier
    name: str = Field(..., min_length=1, max_length=512)
    context_window: int | None = Field(None, gt=0, le=100_000_000)
    max_output_tokens: int | None = Field(None, gt=0, le=100_000_000)
    supports_vision: bool | None = None
    supports_tools: bool | None = None
    supports_reasoning: bool | None = None
    supports_streaming: bool | None = None

    @field_validator("id", "name")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Model IDs and names must contain visible text without control characters")
        return value


class ImportModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: list[ImportModelInput] = Field(..., max_length=1000)


class ProviderUpdatedProperties(BaseModel):
    provider_id: str


ProviderUpdated = BusEvent.define("provider.updated", ProviderUpdatedProperties)


async def publish_provider_updated(provider_id: str) -> None:
    """Notify internal subscribers and authenticated browser SSE clients."""
    from flocks.server.routes.event import publish_event

    properties = {"provider_id": provider_id}
    await Bus.publish(ProviderUpdated, properties)
    await publish_event("provider.updated", properties)


@router.post("/{provider_id}/discover-models")
async def discover_provider_models(
    provider_id: str,
    body: DiscoverModelsRequest | None = None,
    _admin: object = Depends(require_admin),
):
    await _ensure_provider_initialized()
    if (
        provider_id not in get_raw_catalog()
        and ConfigWriter.get_provider_raw(provider_id) is None
        and provider_id not in Provider._providers
    ):
        raise HTTPException(404, "Provider not found")
    body = body or DiscoverModelsRequest()
    return await discover_models(provider_id, api_key=body.api_key, base_url=body.base_url)


@router.post("/{provider_id}/import-models")
@serialized_config_mutation
async def import_provider_models(
    provider_id: str,
    body: ImportModelsRequest,
    _admin: object = Depends(require_admin),
):
    # Re-read after acquiring the shared mutation lock. Persist the models and
    # inventory ownership in one write, without touching routing or settings.
    data = ConfigWriter._read_raw()
    raw_provider = (data.get("provider") or {}).get(provider_id)
    if not isinstance(raw_provider, dict):
        raise HTTPException(404, "Save the provider before importing models")
    current = raw_provider.get("models")
    models = dict(current) if isinstance(current, dict) else {}
    added: list[str] = []
    skipped: list[str] = []
    for candidate in body.models:
        if candidate.id in models:
            skipped.append(candidate.id)
            continue
        model = candidate.model_dump(exclude_none=True)
        model.pop("id")
        models[candidate.id] = model
        added.append(candidate.id)
    if added or raw_provider.get("model_sync") != "manual":
        raw_provider["models"] = models
        raw_provider["model_sync"] = "manual"
        ConfigWriter._write_raw(data)
        Config.clear_cache()
        await _ensure_provider_initialized()
        await _load_dynamic_providers()
        config = await Config.get()
        await Provider.apply_config(config, provider_id=provider_id)
        provider = Provider._providers.get(provider_id)
        if provider is not None and hasattr(provider, "_custom_models"):
            provider._custom_models = list(provider._config_models)
        await publish_provider_updated(provider_id)
    return {"added": added, "skipped": skipped, "failed": []}


class InventoryModelInput(ImportModelInput):
    enabled: bool = True
    edited: bool = False


class SaveInventoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: list[InventoryModelInput] = Field(default_factory=list, max_length=1000)
    deleted: list[ModelIdentifier] = Field(default_factory=list, max_length=1000)


@router.get("/{provider_id}/model-inventory")
async def get_provider_model_inventory(provider_id: str, _admin: object = Depends(require_admin)):
    raw = ConfigWriter.get_provider_raw(provider_id)
    if raw is None:
        raise HTTPException(404, "Provider not found")
    await _ensure_provider_initialized()
    settings = ConfigWriter.get_all_model_settings()
    fields = ("name", "context_window", "max_output_tokens", "supports_tools", "supports_vision", "supports_reasoning", "supports_streaming")
    provider = Provider._providers.get(provider_id)
    effective = {model.id: model.capabilities for model in provider.get_model_definitions()} if provider else {}
    models = []
    for mid, value in (raw.get("models") or {}).items():
        model = {"id": mid, **{key: value[key] for key in fields if key in value},
                 "name": value.get("name", mid), "enabled": settings.get(f"{provider_id}/{mid}", {}).get("enabled", True)}
        # Capability switches reflect the effective configuration. Limits stay
        # raw so an unset override is still displayed as Auto.
        for field in ("supports_tools", "supports_vision", "supports_reasoning", "supports_streaming"):
            if field not in model:
                model[field] = getattr(effective.get(mid), field, field != "supports_vision")
        models.append(model)
    return {"models": models}


@router.post("/{provider_id}/model-inventory")
@serialized_config_mutation
async def save_provider_model_inventory(provider_id: str, body: SaveInventoryRequest, _admin: object = Depends(require_admin)):
    data = ConfigWriter._read_raw()
    raw = (data.get("provider") or {}).get(provider_id)
    if not isinstance(raw, dict):
        raise HTTPException(404, "Provider not found")
    ids = [model.id for model in body.models]
    if len(ids) != len(set(ids)) or set(ids) & set(body.deleted):
        raise HTTPException(422, "Duplicate or conflicting model IDs")
    models = raw.setdefault("models", {})
    settings = data.setdefault("model_settings", {})
    added, updated, deleted = [], [], []
    for mid in body.deleted:
        if mid not in models:
            continue
        del models[mid]
        deleted.append(mid)
        identity = f"{provider_id}/{mid}"
        settings.pop(identity, None)
        def matches(value):
            return isinstance(value, dict) and value.get("provider_id") == provider_id and value.get("model_id") == mid
        defaults = data.get("default_models")
        if isinstance(defaults, dict):
            for kind, value in list(defaults.items()):
                if matches(value):
                    del defaults[kind]
        for field in ("model", "small_model", "smallModel"):
            if data.get(field) == identity:
                del data[field]
        if isinstance(data.get("fallback_providers"), list):
            data["fallback_providers"] = [value for value in data["fallback_providers"] if not matches(value)]
    for item in body.models:
        exists = item.id in models
        if not exists and not item.enabled:
            continue
        if not exists or item.edited:
            values = item.model_dump(exclude_unset=True, exclude={"id", "enabled", "edited"})
            target = dict(models.get(item.id) or {})
            for key, value in values.items():
                if value is None:
                    target.pop(key, None)
                else:
                    target[key] = value
            target.setdefault("name", item.id)
            models[item.id] = target
        (updated if exists else added).append(item.id)
        key = f"{provider_id}/{item.id}"
        settings[key] = {**settings.get(key, {}), "enabled": item.enabled}
    raw["model_sync"] = "manual"
    ConfigWriter._write_raw(data)
    Config.clear_cache()
    await _ensure_provider_initialized()
    await _load_dynamic_providers()
    provider = Provider._providers.get(provider_id)
    if provider is not None:
        from flocks.config.config import ProviderConfig as ProviderSettings
        Provider.apply_provider_config(provider, ProviderSettings.model_validate({"models": models, "model_sync": "manual"}))
    if provider is not None and hasattr(provider, "_custom_models"):
        provider._custom_models = list(provider._config_models)
    if provider is not None:
        desired = {model.id: model for model in provider.get_models()}
        for cached_id, cached in list(Provider._models.items()):
            if cached.provider_id == provider_id and cached_id not in desired:
                del Provider._models[cached_id]
        for mid, model in desired.items():
            if mid not in Provider._models or Provider._models[mid].provider_id == provider_id:
                Provider._models[mid] = model
    await publish_provider_updated(provider_id)
    return {"added": added, "updated": updated, "deleted": deleted, "skipped": [], "failed": []}


@router.post("/{provider_id}/check-key")
async def check_provider_key(provider_id: str, body: DiscoverModelsRequest, _admin: object = Depends(require_admin)):
    await _ensure_provider_initialized()
    if provider_id not in get_raw_catalog() and ConfigWriter.get_provider_raw(provider_id) is None and provider_id not in Provider._providers:
        raise HTTPException(404, "Provider not found")
    result = await discover_models(provider_id, api_key=body.api_key, base_url=body.base_url)
    if result["source"] != "api" or result["status"] != "success":
        return {"status": "invalid" if result["code"] in {"authentication_failed", "missing_credentials"} else "unverified", "code": result["code"]}
    # ThreatBook uses a successful live model-list response as its validation
    # contract. Catalog fallback was excluded above and cannot verify a key.
    if provider_id in _MODEL_LIST_KEY_CHECK_PROVIDERS:
        return {"status": "valid", "code": "model_list_verified"}
    # A public models endpoint does not prove that a key is valid. Require
    # rejection of an unrelated credential before reporting authentication.
    import secrets
    control = await discover_models(provider_id, api_key="flocks-probe-" + secrets.token_urlsafe(24), base_url=body.base_url)
    verified = control["code"] in {"authentication_failed", "permission_denied"}
    return {"status": "valid" if verified else "unverified", "code": "key_valid" if verified else "key_check_unsupported"}
