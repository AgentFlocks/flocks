"""
Default model management API routes

Provides endpoints to get/set default models per model type.
"""

from typing import Dict, List

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from flocks.config.config import Config, FallbackProviderConfig
from flocks.config.config_writer import ConfigWriter
from flocks.provider.model_manager import get_model_manager
from flocks.provider.provider import Provider
from flocks.provider.types import DefaultModelConfig, ModelType
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="routes.default_model")


# ==================== Request / Response Models ====================


class SetDefaultModelRequest(BaseModel):
    """Set default model request"""
    provider_id: str = Field(..., description="Provider ID")
    model_id: str = Field(..., description="Model ID")


class DefaultModelListResponse(BaseModel):
    """List of all default model configs"""
    defaults: List[DefaultModelConfig]


class FallbackProvidersConfig(BaseModel):
    """Ordered runtime fallback model configuration."""

    fallback_providers: List[FallbackProviderConfig] = Field(default_factory=list)


# ==================== Routes ====================


@router.get(
    "",
    response_model=DefaultModelListResponse,
    summary="Get all default models",
    description="Get default model configuration for all model types",
)
async def get_all_defaults() -> DefaultModelListResponse:
    """Get all configured default models."""
    manager = get_model_manager()
    defaults = manager.get_all_defaults()
    return DefaultModelListResponse(defaults=defaults)


@router.get(
    "/resolved",
    summary="Get resolved default LLM model",
    description=(
        "Return the effective default LLM, checking both structured default_models.llm "
        "and the legacy top-level 'model' string in flocks.json."
    ),
)
async def get_resolved_default_model():
    """Return the resolved default LLM model (provider_id + model_id)."""
    result = await Config.resolve_default_llm()
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No default LLM model configured",
        )
    return {"provider_id": result["provider_id"], "model_id": result["model_id"]}


@router.get(
    "/fallbacks",
    response_model=FallbackProvidersConfig,
    summary="Get runtime fallback models",
    description="Get the ordered fallback model configuration for WebUI Auto mode",
)
async def get_fallback_providers() -> FallbackProvidersConfig:
    """Return the ordered, structurally valid fallback model list."""
    config = await Config.get()
    return FallbackProvidersConfig(
        fallback_providers=config.fallback_providers or []
    )


@router.put(
    "/fallbacks",
    response_model=FallbackProvidersConfig,
    summary="Replace runtime fallback models",
    description="Atomically replace the ordered fallback model configuration",
)
async def set_fallback_providers(
    body: FallbackProvidersConfig,
) -> FallbackProvidersConfig:
    """Validate and atomically replace the runtime fallback model list."""
    override_source = ConfigWriter.get_fallback_override_source()
    if override_source:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Fallback models are overridden by "
                f"{override_source} and cannot be changed from WebUI"
            ),
        )

    config = await Config.get()
    await Provider.apply_config(config)
    manager = get_model_manager()
    primary = await Config.resolve_default_llm()
    primary_identity = None
    if primary:
        primary_identity = (
            primary["provider_id"].strip(),
            primary["model_id"].strip(),
        )
    disabled_providers = set(
        getattr(config, "disabled_providers", None) or []
    )
    enabled_providers = getattr(config, "enabled_providers", None)

    normalized: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, fallback in enumerate(body.fallback_providers):
        provider_id = fallback.provider_id.strip()
        model_id = fallback.model_id.strip()
        if not provider_id or not model_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Fallback at index {index} must include provider_id and model_id",
            )

        identity = (provider_id, model_id)
        if identity in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Duplicate fallback model '{provider_id}/{model_id}' "
                    f"at index {index}"
                ),
            )
        if identity == primary_identity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Fallback model '{provider_id}/{model_id}' is the current "
                    "default LLM"
                ),
            )

        if provider_id in disabled_providers or (
            enabled_providers and provider_id not in enabled_providers
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Fallback provider '{provider_id}' is disabled",
            )

        definition = manager.get_model(provider_id, model_id)
        if definition is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown fallback model '{provider_id}/{model_id}'",
            )
        if definition.model_type != ModelType.LLM:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Fallback model '{provider_id}/{model_id}' is not an LLM",
            )

        setting = manager.get_setting(provider_id, model_id)
        if setting is not None and not setting.enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Fallback model '{provider_id}/{model_id}' is disabled",
            )

        seen.add(identity)
        normalized.append({
            "provider_id": provider_id,
            "model_id": model_id,
        })

    try:
        ConfigWriter.set_fallback_providers(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return FallbackProvidersConfig(fallback_providers=normalized)


@router.get(
    "/{model_type}",
    response_model=DefaultModelConfig,
    summary="Get default model for type",
)
async def get_default_model(model_type: ModelType) -> DefaultModelConfig:
    """Get default model for a specific model type."""
    manager = get_model_manager()
    default = manager.get_default_model(model_type)
    if not default:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No default model configured for type '{model_type.value}'",
        )
    return default


@router.put(
    "/{model_type}",
    response_model=DefaultModelConfig,
    summary="Set default model for type",
)
async def set_default_model(
    model_type: ModelType, body: SetDefaultModelRequest
) -> DefaultModelConfig:
    """Set the default model for a specific model type."""
    manager = get_model_manager()
    result = manager.set_default_model(
        model_type=model_type,
        provider_id=body.provider_id,
        model_id=body.model_id,
    )
    return result


@router.delete(
    "/{model_type}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete default model for type",
)
async def delete_default_model(model_type: ModelType):
    """Remove default model setting for a model type."""
    manager = get_model_manager()
    deleted = manager.delete_default_model(model_type)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No default model configured for type '{model_type.value}'",
        )
