"""
Model management routes

Provides model-level operations (listing, filtering, etc.)
Complements the provider routes with model-specific functionality.

Includes V2 endpoints that return full ModelDefinition with capabilities,
limits, pricing, and parameter rules.
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from flocks.config.config_writer import ConfigWriter
from flocks.provider.provider import Provider
from flocks.provider.model_manager import get_model_manager
from flocks.provider.types import (
    ModelDefinition,
    ModelSetting,
    ModelType,
)
from flocks.utils.log import Log


router = APIRouter()
log = Log.create(service="routes.model")


def _connected_provider_ids() -> set[str]:
    """Return provider ids explicitly connected in flocks.json."""
    return set(ConfigWriter.list_provider_ids())


# ==================== Response Models ====================

class ModelCapabilities(BaseModel):
    """Model capabilities"""
    streaming: bool = Field(True, description="Supports streaming")
    tools: bool = Field(True, description="Supports tool calling")
    vision: bool = Field(False, description="Supports vision/images")
    json_mode: bool = Field(False, description="Supports JSON mode")


class ModelDetail(BaseModel):
    """Detailed model information"""
    id: str = Field(..., description="Model ID")
    name: str = Field(..., description="Human-readable name")
    provider: str = Field(..., description="Provider ID")
    max_tokens: Optional[int] = Field(None, description="Maximum tokens")
    context_window: Optional[int] = Field(None, description="Context window size")
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    pricing: Optional[Dict[str, Any]] = Field(None, description="Pricing info")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class ModelListResponse(BaseModel):
    """Response for model list"""
    models: List[ModelDetail]
    total: int
    providers: List[str]


class ModelFilterRequest(BaseModel):
    """Model filtering options"""
    provider: Optional[str] = Field(None, description="Filter by provider")
    supports_streaming: Optional[bool] = Field(None, description="Filter by streaming support")
    supports_tools: Optional[bool] = Field(None, description="Filter by tool support")
    supports_vision: Optional[bool] = Field(None, description="Filter by vision support")
    min_context_window: Optional[int] = Field(None, description="Minimum context window")


# ==================== Routes ====================

@router.get(
    "/",
    response_model=ModelListResponse,
    summary="List all models",
    description="List all available models across all providers"
)
async def list_models(
    provider: Optional[str] = Query(None, description="Filter by provider ID"),
    streaming: Optional[bool] = Query(None, description="Filter by streaming support"),
    tools: Optional[bool] = Query(None, description="Filter by tool support"),
) -> ModelListResponse:
    """
    List all models
    
    Returns a unified list of all models from all providers,
    with optional filtering.
    
    Args:
        provider: Optional provider filter
        streaming: Optional streaming filter
        tools: Optional tools filter
        
    Returns:
        List of models with metadata
    """
    try:
        connected_provider_ids = _connected_provider_ids()
        if provider:
            provider_ids = [provider] if provider in connected_provider_ids else []
        else:
            provider_ids = list(connected_provider_ids)
        
        all_models: List[ModelDetail] = []
        providers_with_models: set = set()
        
        for provider_id in provider_ids:
            # Apply provider filter
            if provider and provider_id != provider:
                continue
            
            try:
                # Get models for this provider
                models = Provider.list_models(provider_id)
                
                for model in models:
                    # Apply capability filters
                    if streaming is not None and model.capabilities.get("streaming") != streaming:
                        continue
                    if tools is not None and model.capabilities.get("tools") != tools:
                        continue
                    
                    # Build model detail
                    model_detail = ModelDetail(
                        id=model.id,
                        name=model.id.split("/")[-1],  # Simple name extraction
                        provider=provider_id,
                        max_tokens=model.capabilities.get("maxTokens"),
                        context_window=model.capabilities.get("contextWindow"),
                        capabilities=ModelCapabilities(
                            streaming=model.capabilities.get("streaming", True),
                            tools=model.capabilities.get("tools", True),
                            vision=model.capabilities.get("vision", False),
                            json_mode=model.capabilities.get("jsonMode", False),
                        ),
                        metadata=model.capabilities,
                    )
                    
                    all_models.append(model_detail)
                    providers_with_models.add(provider_id)
            
            except Exception as e:
                log.warning("Failed to list models for provider", {
                    "provider": provider_id,
                    "error": str(e)
                })
                continue
        
        return ModelListResponse(
            models=all_models,
            total=len(all_models),
            providers=sorted(list(providers_with_models)),
        )
    
    except Exception as e:
        log.error("Failed to list models", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{model_id}",
    response_model=ModelDetail,
    summary="Get model details",
    description="Get detailed information about a specific model"
)
async def get_model(model_id: str) -> ModelDetail:
    """
    Get model details
    
    Args:
        model_id: Model ID (format: provider/model or just model)
        
    Returns:
        Model details
    """
    try:
        # Parse model ID (could be "openai/gpt-4" or just "gpt-4")
        if "/" in model_id:
            provider_id, actual_model_id = model_id.split("/", 1)
        else:
            # Search all providers
            provider_id = None
            actual_model_id = model_id
        
        # If provider specified, check it directly
        if provider_id:
            try:
                model = Provider.get_model(provider_id, actual_model_id)
                if model:
                    return ModelDetail(
                        id=model.id,
                        name=model.id.split("/")[-1],
                        provider=provider_id,
                        max_tokens=model.capabilities.get("maxTokens"),
                        context_window=model.capabilities.get("contextWindow"),
                        capabilities=ModelCapabilities(
                            streaming=model.capabilities.get("streaming", True),
                            tools=model.capabilities.get("tools", True),
                            vision=model.capabilities.get("vision", False),
                            json_mode=model.capabilities.get("jsonMode", False),
                        ),
                        metadata=model.capabilities,
                    )
            except Exception:
                pass
        
        # Search all providers
        for pid in Provider.list_providers():
            try:
                model = Provider.get_model(pid, actual_model_id)
                if model:
                    return ModelDetail(
                        id=model.id,
                        name=model.id.split("/")[-1],
                        provider=pid,
                        max_tokens=model.capabilities.get("maxTokens"),
                        context_window=model.capabilities.get("contextWindow"),
                        capabilities=ModelCapabilities(
                            streaming=model.capabilities.get("streaming", True),
                            tools=model.capabilities.get("tools", True),
                            vision=model.capabilities.get("vision", False),
                            json_mode=model.capabilities.get("jsonMode", False),
                        ),
                        metadata=model.capabilities,
                    )
            except Exception:
                continue
        
        # Not found
        raise HTTPException(
            status_code=404,
            detail=f"Model {model_id} not found"
        )
    
    except HTTPException:
        raise
    except Exception as e:
        log.error("Failed to get model", {"model_id": model_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{model_id}/compatible",
    response_model=List[str],
    summary="Find compatible models",
    description="Find models with similar capabilities"
)
async def find_compatible_models(
    model_id: str,
    limit: int = Query(5, ge=1, le=20, description="Maximum number of results")
) -> List[str]:
    """
    Find compatible models
    
    Finds models with similar capabilities to the specified model.
    Useful for fallback or alternative suggestions.
    
    Args:
        model_id: Source model ID
        limit: Maximum results
        
    Returns:
        List of compatible model IDs
    """
    try:
        # Get the source model
        source = await get_model(model_id)
        
        # Get all models
        all_models_response = await list_models()
        
        # Find similar models (same provider first, then others)
        compatible: List[str] = []
        
        # Same provider models
        for model in all_models_response.models:
            if model.id == source.id:
                continue
            if model.provider == source.provider:
                if len(compatible) < limit:
                    compatible.append(model.id)
        
        # Other provider models with similar capabilities
        for model in all_models_response.models:
            if model.id == source.id:
                continue
            if model.provider == source.provider:
                continue
            
            # Check capability match
            if (model.capabilities.streaming == source.capabilities.streaming and
                model.capabilities.tools == source.capabilities.tools):
                if len(compatible) < limit:
                    compatible.append(model.id)
        
        return compatible[:limit]
    
    except HTTPException:
        raise
    except Exception as e:
        log.error("Failed to find compatible models", {"model_id": model_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/filter",
    response_model=ModelListResponse,
    summary="Filter models",
    description="Filter models by multiple criteria"
)
async def filter_models(filters: ModelFilterRequest) -> ModelListResponse:
    """
    Filter models by criteria
    
    Provides advanced filtering capabilities for models.
    
    Args:
        filters: Filter criteria
        
    Returns:
        Filtered model list
    """
    try:
        # Get all models
        all_models_response = await list_models(
            provider=filters.provider,
            streaming=filters.supports_streaming,
            tools=filters.supports_tools,
        )
        
        filtered_models = all_models_response.models
        
        # Apply additional filters
        if filters.supports_vision is not None:
            filtered_models = [
                m for m in filtered_models
                if m.capabilities.vision == filters.supports_vision
            ]
        
        if filters.min_context_window is not None:
            filtered_models = [
                m for m in filtered_models
                if m.context_window and m.context_window >= filters.min_context_window
            ]
        
        return ModelListResponse(
            models=filtered_models,
            total=len(filtered_models),
            providers=list(set(m.provider for m in filtered_models)),
        )
    
    except Exception as e:
        log.error("Failed to filter models", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/providers/summary",
    response_model=Dict[str, int],
    summary="Provider model counts",
    description="Get model count per provider"
)
async def provider_model_counts() -> Dict[str, int]:
    """
    Get model count per provider
    
    Returns a summary of how many models each provider has.
    
    Returns:
        Dict mapping provider ID to model count
    """
    try:
        all_models_response = await list_models()
        
        counts: Dict[str, int] = {}
        for model in all_models_response.models:
            counts[model.provider] = counts.get(model.provider, 0) + 1
        
        return counts
    
    except Exception as e:
        log.error("Failed to get provider counts", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


# ==================== V2 Endpoints (ModelDefinition-based) ====================


class ModelDefinitionListResponse(BaseModel):
    """Response for V2 model list"""
    models: List[ModelDefinition]
    total: int


class UpdateModelSettingRequest(BaseModel):
    """Request to update model settings"""
    enabled: Optional[bool] = Field(None, description="Enable or disable model")
    default_parameters: Optional[Dict[str, Any]] = Field(
        None, description="Default parameter overrides"
    )


@router.get(
    "/v2/definitions",
    response_model=ModelDefinitionListResponse,
    summary="List model definitions (V2)",
    description="List all models with full definitions including capabilities, limits, pricing, and parameter rules",
)
async def list_model_definitions(
    provider: Optional[str] = Query(None, description="Filter by provider ID"),
    model_type: Optional[ModelType] = Query(None, description="Filter by model type"),
    enabled_only: bool = Query(False, description="Only return enabled models"),
) -> ModelDefinitionListResponse:
    """List model definitions with full metadata."""
    try:
        from flocks.config.config import Config

        Provider._ensure_initialized()
        try:
            config = await Config.get()
            await Provider.apply_config(config, provider_id=provider)
        except Exception:
            pass

        manager = get_model_manager()
        definitions = manager.list_models(
            provider_id=provider,
            model_type=model_type,
            enabled_only=enabled_only,
        )
        connected_provider_ids = _connected_provider_ids()
        definitions = [
            definition
            for definition in definitions
            if definition.provider_id in connected_provider_ids
        ]
        return ModelDefinitionListResponse(
            models=definitions,
            total=len(definitions),
        )
    except Exception as e:
        log.error("Failed to list model definitions", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/v2/definitions/{provider_id}/{model_id:path}/parameter-rules",
    summary="Get model parameter rules",
    description="Get the parameter rules (constraints) for a model",
)
async def get_parameter_rules(provider_id: str, model_id: str):
    """Get parameter rules for a model."""
    manager = get_model_manager()
    definition = manager.get_model(provider_id, model_id)
    if not definition:
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model_id}' not found for provider '{provider_id}'",
        )
    return {"parameter_rules": definition.parameter_rules}


@router.get(
    "/v2/definitions/{provider_id}/{model_id:path}",
    response_model=ModelDefinition,
    summary="Get model definition (V2)",
    description="Get full model definition with capabilities, limits, pricing, and parameter rules",
)
async def get_model_definition(
    provider_id: str, model_id: str
) -> ModelDefinition:
    """Get a single model definition."""
    manager = get_model_manager()
    definition = manager.get_model(provider_id, model_id)
    if not definition:
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model_id}' not found for provider '{provider_id}'",
        )
    return definition


@router.get(
    "/v2/settings/{provider_id}/{model_id:path}",
    summary="Get model settings",
)
async def get_model_settings(provider_id: str, model_id: str):
    """Get user settings for a model."""
    manager = get_model_manager()
    setting = manager.get_setting(provider_id, model_id)
    if not setting:
        # Return defaults
        return ModelSetting(provider_id=provider_id, model_id=model_id)
    return setting


@router.put(
    "/v2/settings/{provider_id}/{model_id:path}",
    response_model=ModelSetting,
    summary="Update model settings",
    description="Enable/disable a model or set default parameters",
)
async def update_model_settings(
    provider_id: str, model_id: str, body: UpdateModelSettingRequest
) -> ModelSetting:
    """Update model settings (enable/disable, default parameters)."""
    manager = get_model_manager()
    setting = manager.update_setting(
        provider_id=provider_id,
        model_id=model_id,
        enabled=body.enabled,
        default_parameters=body.default_parameters,
    )
    return setting

@router.delete(
    "/v2/definitions/{provider_id}/{model_id:path}",
    status_code=204,
    summary="Delete model definition",
    description="Delete a model from a provider (removes from flocks.json and runtime)",
)
async def delete_model_definition(
    provider_id: str, model_id: str,
):
    """Delete a model from a provider.

    Removes the model entry from flocks.json and the runtime cache.
    flocks.json is the single source of truth for the model list, so a
    simple removal is sufficient — catalog models do not reappear because
    they are no longer loaded from catalog at runtime.
    """
    try:
        removed = ConfigWriter.remove_model(provider_id, model_id)
        if not removed:
            raise HTTPException(
                status_code=404,
                detail=f"Model '{model_id}' not found for provider '{provider_id}'",
            )

        Provider.remove_model_from_runtime(provider_id, model_id)

        ConfigWriter.clear_default_models_for_model(provider_id, model_id)

        log.info("model_definition.deleted", {
            "provider_id": provider_id,
            "model_id": model_id,
        })
    except HTTPException:
        raise
    except Exception as e:
        log.error("Failed to delete model definition", {
            "provider_id": provider_id,
            "model_id": model_id,
            "error": str(e)
        })
        raise HTTPException(status_code=500, detail=str(e))
