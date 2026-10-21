"""Administrator model discovery and additive inventory import routes."""

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
        await Bus.publish(ProviderUpdated, {"provider_id": provider_id})
    return {"added": added, "skipped": skipped, "failed": []}
