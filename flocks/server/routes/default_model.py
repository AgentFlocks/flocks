"""
Default model management API routes

Provides endpoints to get/set default models per model type.
"""

from typing import List

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from flocks.provider.model_manager import get_model_manager
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
    from flocks.config.config import Config

    result = await Config.resolve_default_llm()
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No default LLM model configured",
        )
    return {"provider_id": result["provider_id"], "model_id": result["model_id"]}


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
