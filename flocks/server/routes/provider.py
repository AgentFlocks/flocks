"""
Provider management routes

Flocks TUI expects models as Dict[modelID, Model], not List[Model].
Model objects must include: id, name, providerID, attachment, reasoning, 
temperature, tool_call, limit, etc.
"""

import time
import json
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Body, HTTPException, Query, status
from pydantic import BaseModel, Field, ConfigDict

from flocks.utils.log import Log
from flocks.provider.provider import Provider, ModelInfo as ProviderModelInfo
from flocks.security.secrets import SecretManager
from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter
from flocks.storage.storage import Storage


router = APIRouter()
log = Log.create(service="provider-routes")


_EMAIL_KEY_PAIR_PATTERN = re.compile(
    r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\s*:\s*([^\s]+)"
)


def _split_compound_service_credentials(raw_value: str) -> Optional[tuple[str, str]]:
    """Split a combined credential string into (api_key, secret).

    Supports:
    - legacy format: ``api_key|secret``
    - FOFA-friendly format: ``email:key`` (or text containing that pair)
    """
    if not isinstance(raw_value, str):
        return None

    text = raw_value.strip()
    if not text:
        return None

    if "|" in text:
        left, right = text.split("|", 1)
        api_key = left.strip()
        secret = right.strip()
        if api_key and secret:
            return api_key, secret
        return None

    match = _EMAIL_KEY_PAIR_PATTERN.search(text)
    if not match:
        return None

    email = match.group(1).strip()
    api_key = match.group(2).strip()
    if not email or not api_key:
        return None
    return api_key, email


def _get_compound_secret_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    compound_secret = metadata.get("compound_secret")
    if isinstance(compound_secret, dict):
        return compound_secret
    return {}


def _should_persist_secondary_secret(metadata: Optional[Dict[str, Any]]) -> bool:
    compound_secret = _get_compound_secret_metadata(metadata)
    if compound_secret.get("persist_secondary_secret") is False:
        return False
    return True


def _get_inline_provider_api_key(provider_id: str) -> Optional[str]:
    """Return an inline apiKey from flocks.json when present.

    WebUI credential flows primarily store API keys in .secret.json, but some
    users still configure custom providers directly in flocks.json. The test
    and credential endpoints should treat those inline keys as usable
    credentials instead of reporting a false "No credentials" state.
    """
    raw_provider = ConfigWriter.get_provider_raw(provider_id)
    if not raw_provider:
        return None

    options = raw_provider.get("options", {})
    api_key = options.get("apiKey") or options.get("api_key")
    if not isinstance(api_key, str):
        return None

    api_key = api_key.strip()
    if not api_key:
        return None

    if api_key.startswith("{secret:") or api_key.startswith("{env:"):
        return None

    return api_key


def _get_provider_custom_settings(provider: Any) -> Dict[str, Any]:
    """Return the currently applied provider custom settings, if any."""
    provider_config = getattr(provider, "_config", None)
    custom_settings = getattr(provider_config, "custom_settings", None) or {}
    return dict(custom_settings) if isinstance(custom_settings, dict) else {}


def _model_to_api_format(model: ProviderModelInfo) -> Dict[str, Any]:
    """Convert internal ModelInfo to Flocks-compatible dict format.

    Centralises the serialisation logic that was previously duplicated
    in list_providers, get_provider, and list_models.
    """
    return {
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

def _get_npm_package(provider_id: str) -> str:
    """Get the NPM SDK package for a provider (from catalog.json)."""
    from flocks.provider.model_catalog import get_provider_npm
    return get_provider_npm(provider_id)


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


# Response Models - Flocks compatible format
class ModelLimit(BaseModel):
    """Model token limits"""
    context: int = Field(..., description="Context window size")
    output: int = Field(..., description="Max output tokens")
    input: Optional[int] = Field(None, description="Max input tokens")


class ModelCost(BaseModel):
    """Model cost information"""
    input: float = Field(0.0, description="Cost per input token")
    output: float = Field(0.0, description="Cost per output token")


class ModelApi(BaseModel):
    """Model API configuration - Flocks compatible"""
    id: str = Field(..., description="API model ID")
    url: Optional[str] = Field(None, description="API endpoint URL")
    npm: str = Field(..., description="NPM package name")


class ModelInfo(BaseModel):
    """AI Model information - Flocks compatible format"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(..., description="Model ID")
    name: str = Field(..., description="Model name")
    providerID: str = Field(..., description="Provider ID")
    family: Optional[str] = Field(None, description="Model family")
    api: Optional[ModelApi] = Field(None, description="API configuration")
    attachment: bool = Field(False, description="Supports attachments/vision")
    reasoning: bool = Field(False, description="Has reasoning capabilities")
    temperature: bool = Field(True, description="Supports temperature parameter")
    tool_call: bool = Field(True, description="Supports tool calling")
    limit: ModelLimit = Field(..., description="Token limits")
    cost: Optional[ModelCost] = Field(None, description="Cost information")
    options: Optional[Dict[str, Any]] = Field(None, description="Additional options")


class ProviderInfo(BaseModel):
    """Provider information - Flocks compatible format"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(..., description="Provider ID")
    name: str = Field(..., description="Provider name")
    source: str = Field(default="config", description="Provider source: env, config, custom, api")
    env: List[str] = Field(default_factory=list, description="Environment variable names")
    key: Optional[str] = Field(None, description="API key (if configured)")
    options: Dict[str, Any] = Field(default_factory=dict, description="Provider options")
    # Flocks expects models as Dict[modelID, Model], not List[Model]
    models: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Available models")


class ProviderListResponse(BaseModel):
    """Response for provider list"""
    all: List[ProviderInfo] = Field(..., description="All available providers")
    default: Dict[str, str] = Field(..., description="Default model for each provider")
    connected: List[str] = Field(..., description="Connected provider IDs")


# Provider Routes
@router.get(
    "",
    response_model=ProviderListResponse,
    summary="List providers",
    description="List all available AI providers"
)
async def list_providers() -> ProviderListResponse:
    """
    List all available providers
    
    Returns:
        List of providers with their models
    """
    try:
        # Ensure providers are initialized
        Provider._ensure_initialized()
        
        # Get config for enabled/disabled providers
        try:
            config = await Config.get()
            await Provider.apply_config(config)
            disabled = set(config.disabled_providers or [])
            enabled_set = set(config.enabled_providers) if config.enabled_providers else None
        except Exception:
            config = None
            disabled = set()
            enabled_set = None
        
        # Only show providers the user has explicitly connected in flocks.json.
        # Built-in provider types remain discoverable via /api/provider/catalog.
        provider_ids = ConfigWriter.list_provider_ids()
        all_providers: List[ProviderInfo] = []
        connected_ids: List[str] = []
        default_models: Dict[str, str] = {}
        
        for provider_id in provider_ids:
            # Filter by enabled/disabled
            if enabled_set and provider_id not in enabled_set:
                continue
            if provider_id in disabled:
                continue
            
            provider = Provider.get(provider_id)
            if not provider:
                continue
            
            # Credentials are now resolved at config load time via
            # {secret:xxx} or {env:xxx} in flocks.json. Provider.apply_config()
            # above already configures providers from resolved config values.
            
            # Get models for this provider
            try:
                provider_models = Provider.list_models(provider_id)
            except (NotImplementedError, Exception) as model_err:
                # Skip providers that fail to load models
                log.warning("provider.list_models.failed", {
                    "provider_id": provider_id,
                    "error": str(model_err)
                })
                continue
            
            # Convert to response format - Flocks expects Dict[modelID, Model]
            models_dict: Dict[str, Dict[str, Any]] = {}
            first_model_id = None
            
            for model in provider_models:
                if first_model_id is None:
                    first_model_id = model.id
                models_dict[model.id] = _model_to_api_format(model)
            
            # Merge config-defined models
            if config:
                _merge_config_models(models_dict, provider_id, config)

            connected_ids.append(provider_id)
            
            provider_info = ProviderInfo(
                id=provider_id,
                name=provider.name,
                source="config",  # Provider source: env, config, custom, api
                env=[],  # Environment variable names (can be enhanced later)
                key=None,  # API key not exposed in list
                options={},
                models=models_dict,
            )
            
            all_providers.append(provider_info)
            
            # Set default model (first model in list)
            if not first_model_id and models_dict:
                first_model_id = next(iter(models_dict))
            if first_model_id:
                default_models[provider_id] = first_model_id
        
        return ProviderListResponse(
            all=all_providers,
            default=default_models,
            connected=connected_ids,
        )
    except Exception as e:
        import traceback
        error_msg = str(e) or repr(e)
        tb = traceback.format_exc()
        log.error("provider.list.error", {"error": error_msg, "traceback": tb})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=error_msg or "Unknown error in list_providers"
        )


# Catalog endpoint - must be before /{provider_id} to avoid path conflict
@router.get(
    "/catalog",
    summary="Get provider catalog",
    description="Get all available provider types with metadata, credential schemas, and models",
)
async def get_provider_catalog():
    """Return all available provider types from the model catalog.

    Used by the frontend Add Provider dialog to populate the provider dropdown,
    auto-fill credential fields, and preview available models.

    All data comes from catalog.json — no hardcoded values.
    """
    from flocks.provider.model_catalog import get_raw_catalog, get_catalog

    raw_catalog = get_raw_catalog()
    parsed_catalog = get_catalog()

    providers = []
    for provider_id, raw in raw_catalog.items():
        entry = parsed_catalog.get(provider_id)
        if not entry:
            continue
        meta = entry["meta"]
        models = entry["models"]

        provider_data: Dict[str, Any] = {
            "id": provider_id,
            "name": raw["name"],
            "description": raw.get("description"),
            "credential_schemas": [s.model_dump() for s in meta.credential_schemas],
            "env_vars": raw.get("env_vars", []),
            "default_base_url": raw.get("default_base_url"),
            "model_count": len(models),
            "models": [
                {
                    "id": m.id,
                    "name": m.name,
                    "family": m.family,
                    "model_type": m.model_type.value,
                    "status": m.status.value,
                    "capabilities": {
                        "supports_tools": m.capabilities.supports_tools,
                        "supports_vision": m.capabilities.supports_vision,
                        "supports_reasoning": m.capabilities.supports_reasoning,
                        "supports_streaming": m.capabilities.supports_streaming,
                    },
                    "limits": {
                        "context_window": m.limits.context_window,
                        "max_output_tokens": m.limits.max_output_tokens,
                    } if m.limits else None,
                    "pricing": {
                        "input": m.pricing.input,
                        "output": m.pricing.output,
                        "currency": m.pricing.currency,
                    } if m.pricing else None,
                }
                for m in models
            ],
        }
        # Propagate allow_multiple flag (only openai-compatible has it)
        if raw.get("allow_multiple"):
            provider_data["allow_multiple"] = True

        providers.append(provider_data)

    return {"providers": providers}


# Auth endpoint - must be before /{provider_id} to avoid path conflict
@router.get(
    "/auth",
    summary="Get provider auth methods",
    description="Retrieve available authentication methods for all AI providers",
)
async def get_all_provider_auth() -> Dict[str, List[Any]]:
    """
    Get provider authentication methods
    
    Returns a dict mapping provider IDs to available auth methods.
    Flocks compatible: GET /provider/auth
    """
    # TODO: Implement actual auth methods retrieval
    # Flocks format: Record<string, ProviderAuth.Method[]>
    return {}


# OAuth authorize endpoint - Flocks compatible
@router.post(
    "/{provider_id}/oauth/authorize",
    summary="OAuth authorize",
    description="Initiate OAuth authorization for a specific AI provider to get an authorization URL",
)
async def oauth_authorize(
    provider_id: str,
    method: int = 0
) -> Optional[Dict[str, Any]]:
    """
    Initiate OAuth authorization
    
    Flocks compatible: POST /provider/:providerID/oauth/authorize
    Request body: { method: number }
    Returns: Authorization URL and method or null
    """
    # TODO: Implement OAuth authorization
    # Return format: { url: string, method: 'browser' | 'redirect' } | null
    return None


# OAuth callback endpoint - Flocks compatible
@router.post(
    "/{provider_id}/oauth/callback",
    summary="OAuth callback",
    description="Handle the OAuth callback from a provider after user authorization",
)
async def oauth_callback(
    provider_id: str,
    method: int,
    code: Optional[str] = None
) -> bool:
    """
    Handle OAuth callback
    
    Flocks compatible: POST /provider/:providerID/oauth/callback
    Request body: { method: number, code?: string }
    Returns: boolean (success status)
    """
    # TODO: Implement OAuth callback handling
    return True


@router.get(
    "/api-services",
    summary="List API services",
    description="List all API services with enabled state, metadata, and cached status."
)
async def list_api_services_route():
    return await list_api_services()


@router.patch(
    "/api-services/{provider_id}",
    summary="Update API service",
    description="Enable or disable an API service and all tools it exposes."
)
async def update_api_service_route(
    provider_id: str,
    request: Dict[str, Any] = Body(...),
):
    return await update_api_service(provider_id, APIServiceUpdateRequest.model_validate(request))


@router.delete(
    "/api-services/{provider_id}",
    response_model=Dict[str, bool],
    summary="Delete API service",
    description="Delete an API service configuration and its stored credential."
)
async def delete_api_service_route(provider_id: str):
    return await delete_api_service(provider_id)


@router.get(
    "/{provider_id}",
    response_model=ProviderInfo,
    summary="Get provider",
    description="Get provider by ID"
)
async def get_provider(provider_id: str) -> ProviderInfo:
    """
    Get provider by ID
    
    Args:
        provider_id: Provider ID
        
    Returns:
        Provider information
    """
    try:
        Provider._ensure_initialized()
        provider = Provider.get(provider_id)
        
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider {provider_id} not found"
            )
        
        config = await Config.get()
        await Provider.apply_config(config, provider_id=provider_id)
        
        # Credentials are resolved at config load time via {secret:xxx} in flocks.json.
        # Provider.apply_config() above already configures from resolved config.

        # Get models - Convert to Dict[modelID, Model] format for Flocks
        provider_models = Provider.list_models(provider_id)
        models_dict: Dict[str, Dict[str, Any]] = {}
        
        for model in provider_models:
            models_dict[model.id] = _model_to_api_format(model)
        
        # Merge config-defined models
        _merge_config_models(models_dict, provider_id, config)

        return ProviderInfo(
            id=provider_id,
            name=provider.name,
            source="config",  # Provider source
            env=[],  # Environment variable names
            key=None,  # API key not exposed
            options={},
            models=models_dict,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error("provider.get.error", {"error": str(e), "provider_id": provider_id})
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get(
    "/{provider_id}/models",
    summary="List models",
    description="List models for a provider"
)
async def list_models(provider_id: str) -> List[Dict[str, Any]]:
    """
    List models for a provider
    
    Args:
        provider_id: Provider ID
        
    Returns:
        List of models in Flocks-compatible format
    """
    try:
        Provider._ensure_initialized()
        if provider_id not in ConfigWriter.list_provider_ids():
            return []
        config = await Config.get()
        await Provider.apply_config(config, provider_id=provider_id)
        provider_models = Provider.list_models(provider_id)

        models_dict: Dict[str, Dict[str, Any]] = {}
        for model in provider_models:
            models_dict[model.id] = _model_to_api_format(model)

        _merge_config_models(models_dict, provider_id, config)

        return list(models_dict.values())
    except Exception as e:
        log.error("provider.models.error", {"error": str(e), "provider_id": provider_id})
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# API Key Configuration
class ProviderConfigRequest(BaseModel):
    """Provider configuration request"""
    api_key: Optional[str] = Field(None, description="API key")
    base_url: Optional[str] = Field(None, description="Custom base URL")
    custom_settings: Dict[str, Any] = Field(default_factory=dict, description="Custom settings")


@router.post(
    "/{provider_id}/configure",
    response_model=ProviderInfo,
    summary="Configure provider",
    description="Configure provider with API key and settings"
)
async def configure_provider(provider_id: str, config: ProviderConfigRequest) -> ProviderInfo:
    """
    Configure provider
    
    Args:
        provider_id: Provider ID
        config: Configuration data
        
    Returns:
        Updated provider information
    """
    try:
        Provider._ensure_initialized()
        provider = Provider.get(provider_id)
        
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider {provider_id} not found"
            )
        
        # Create provider config
        from flocks.provider.provider import ProviderConfig
        provider_config = ProviderConfig(
            provider_id=provider_id,
            api_key=config.api_key,
            base_url=config.base_url,
            custom_settings=config.custom_settings,
        )
        
        # Configure provider
        provider.configure(provider_config)
        
        # TODO: Save configuration to storage/config
        
        log.info("provider.configured", {"provider_id": provider_id})
        
        # Return updated provider info
        return await get_provider(provider_id)
    except HTTPException:
        raise
    except Exception as e:
        log.error("provider.configure.error", {"error": str(e), "provider_id": provider_id})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/{provider_id}/test",
    summary="Test provider",
    description="Test provider connection"
)
async def test_provider(provider_id: str) -> Dict[str, Any]:
    """
    Test provider connection
    
    Args:
        provider_id: Provider ID
        
    Returns:
        Test result
    """
    try:
        Provider._ensure_initialized()
        provider = Provider.get(provider_id)
        
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider {provider_id} not found"
            )
        
        if not provider.is_configured():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Provider {provider_id} is not configured"
            )
        
        # TODO: Implement actual connection test
        # For now, just check if configured
        
        return {
            "success": True,
            "message": f"Provider {provider_id} is configured",
            "provider_id": provider_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error("provider.test.error", {"error": str(e), "provider_id": provider_id})
        return {
            "success": False,
            "message": str(e),
            "provider_id": provider_id,
        }


# =============================================================================
# WebUI Enhancement Routes
# =============================================================================

@router.put(
    "/{provider_id}",
    response_model=ProviderInfo,
    summary="Update provider",
    description="Update provider configuration"
)
async def update_provider(provider_id: str, config: ProviderConfigRequest) -> ProviderInfo:
    """
    Update provider configuration
    
    Updates the provider's API key, base URL, and custom settings.
    """
    try:
        Provider._ensure_initialized()
        provider = Provider.get(provider_id)
        
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider {provider_id} not found"
            )
        
        # Create provider config
        from flocks.provider.provider import ProviderConfig
        provider_config = ProviderConfig(
            provider_id=provider_id,
            api_key=config.api_key,
            base_url=config.base_url,
            custom_settings=config.custom_settings,
        )
        
        # Update configuration
        provider.configure(provider_config)
        
        # Save to storage
        config_key = f"provider_config/{provider_id}"
        await Storage.write(config_key, {
            "provider_id": provider_id,
            "api_key": config.api_key,
            "base_url": config.base_url,
            "custom_settings": config.custom_settings,
        })
        
        log.info("provider.updated", {"provider_id": provider_id})
        
        # Return updated provider info
        return await get_provider(provider_id)
    except HTTPException:
        raise
    except Exception as e:
        log.error("provider.update.error", {"error": str(e), "provider_id": provider_id})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# =============================================================================
# API Service Metadata
# =============================================================================

class APIServiceMetadata(BaseModel):
    """API service metadata"""
    name: str
    version: Optional[str] = None
    description: Optional[str] = None
    description_cn: Optional[str] = None
    author: Optional[str] = None
    category: Optional[str] = None
    authentication: Optional[Dict[str, Any]] = None
    dependencies: Optional[List[str]] = None
    apis: Optional[List[Dict[str, Any]]] = None
    rate_limits: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    base_url: Optional[str] = None
    docs_url: Optional[str] = None
    credential_schema: Optional[List[Dict[str, Any]]] = None


class APIServiceSummary(BaseModel):
    """API service summary for the Tool API page."""
    id: str
    name: str
    enabled: bool = True
    status: str = "unknown"
    message: Optional[str] = None
    latency_ms: Optional[int] = None
    checked_at: Optional[int] = None
    tool_count: int = 0
    description: Optional[str] = None
    description_cn: Optional[str] = None
    builtin: bool = False
    verify_ssl: bool = False


class APIServiceUpdateRequest(BaseModel):
    """API service update request."""
    enabled: bool = Field(..., description="Enable or disable the API service")
    verify_ssl: Optional[bool] = Field(None, description="SSL verification for HTTP requests (default: False)")


class APIServiceCredentialField(BaseModel):
    """Credential field definition exposed to the WebUI."""

    key: str
    label: str
    description: Optional[str] = None
    storage: str = "config"
    sensitive: bool = False
    required: bool = False
    input_type: str = "text"
    config_key: str
    secret_id: Optional[str] = None
    default_value: Optional[str] = None


def _default_api_service_field_label(field_key: str) -> str:
    labels = {
        "api_key": "API Key",
        "base_url": "Base URL",
        "secret": "Secret",
        "username": "Username",
        "password": "Password",
    }
    return labels.get(field_key, field_key.replace("_", " ").title())


def _extract_secret_id(secret_ref: Any) -> Optional[str]:
    if isinstance(secret_ref, str) and secret_ref.startswith("{secret:") and secret_ref.endswith("}"):
        return secret_ref[len("{secret:"):-1]
    return None


def _load_api_service_metadata_data(provider_id: str) -> Optional[Dict[str, Any]]:
    """Load raw API service metadata from config, metadata JSON, or YAML provider."""
    merged: Dict[str, Any] = {}

    config_data = ConfigWriter.get_api_service_raw(provider_id)
    if isinstance(config_data, dict):
        merged.update(config_data)

    metadata_dirs = [
        Path(__file__).parent.parent.parent / "tool" / "security" / "metadata",
    ]
    for md in metadata_dirs:
        meta_file = md / f"{provider_id}.json"
        if meta_file.is_file():
            with open(meta_file, "r", encoding="utf-8") as f:
                metadata_data = json.load(f)
            if isinstance(metadata_data, dict):
                merged = {**metadata_data, **merged}
            break

    yaml_data = _load_provider_yaml_metadata(provider_id)
    if isinstance(yaml_data, dict):
        merged = {**yaml_data, **merged}

    return merged or None


def _normalize_api_service_credential_field(
    provider_id: str,
    raw_field: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[APIServiceCredentialField]:
    if not isinstance(raw_field, dict):
        return None

    key = raw_field.get("key")
    if not isinstance(key, str) or not key.strip():
        return None
    key = key.strip()

    storage = raw_field.get("storage")
    if storage not in {"config", "secret"}:
        storage = "secret" if key in {"api_key", "secret", "password", "token", "client_secret"} else "config"

    config_key = raw_field.get("config_key")
    if not isinstance(config_key, str) or not config_key.strip():
        config_key = "apiKey" if key == "api_key" else key

    label = raw_field.get("label")
    if not isinstance(label, str) or not label.strip():
        label = _default_api_service_field_label(key)

    input_type = raw_field.get("input_type")
    if input_type not in {"text", "password", "url"}:
        if storage == "secret":
            input_type = "password"
        elif key.endswith("url"):
            input_type = "url"
        else:
            input_type = "text"

    sensitive = raw_field.get("sensitive")
    if sensitive is None:
        sensitive = storage == "secret"
    else:
        sensitive = bool(sensitive)

    secret_id = raw_field.get("secret_id")
    if storage == "secret":
        if not isinstance(secret_id, str) or not secret_id.strip():
            secret_id = _get_api_service_default_secret_id(provider_id, field_name=key)
        else:
            secret_id = secret_id.strip()
    else:
        secret_id = None

    default_value = raw_field.get("default_value")
    if default_value is None:
        default_value = raw_field.get("default")
    if default_value is None and key == "base_url":
        defaults = (metadata or {}).get("defaults", {})
        if isinstance(defaults, dict):
            default_value = defaults.get("base_url")
        if default_value is None:
            default_value = (metadata or {}).get("base_url")
    if default_value is not None and not isinstance(default_value, str):
        default_value = str(default_value)

    description = raw_field.get("description")
    if description is not None and not isinstance(description, str):
        description = str(description)

    return APIServiceCredentialField(
        key=key,
        label=label,
        description=description,
        storage=storage,
        sensitive=sensitive,
        required=bool(raw_field.get("required", False)),
        input_type=input_type,
        config_key=config_key,
        secret_id=secret_id,
        default_value=default_value,
    )


def _build_api_service_credential_schema(
    provider_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[APIServiceCredentialField]:
    metadata = metadata or _load_api_service_metadata_data(provider_id) or {}
    raw_fields = metadata.get("credential_fields")
    normalized_fields: List[APIServiceCredentialField] = []

    if isinstance(raw_fields, list):
        for raw_field in raw_fields:
            field = _normalize_api_service_credential_field(provider_id, raw_field, metadata)
            if field:
                normalized_fields.append(field)
    else:
        auth = metadata.get("authentication") or metadata.get("auth")
        if isinstance(auth, dict):
            api_secret_id = auth.get("secret_key") or auth.get("api_key_secret") or auth.get("secret")
            if api_secret_id:
                normalized_fields.append(
                    APIServiceCredentialField(
                        key="api_key",
                        label="API Key",
                        storage="secret",
                        sensitive=True,
                        input_type="password",
                        config_key="apiKey",
                        secret_id=str(api_secret_id).strip(),
                    )
                )
            secondary_secret_id = auth.get("secret_secret")
            if secondary_secret_id and _should_persist_secondary_secret(metadata):
                normalized_fields.append(
                    APIServiceCredentialField(
                        key="secret",
                        label="Secret",
                        storage="secret",
                        sensitive=True,
                        input_type="password",
                        config_key="secret",
                        secret_id=str(secondary_secret_id).strip(),
                    )
                )

        defaults = metadata.get("defaults", {})
        base_url = None
        if isinstance(defaults, dict):
            base_url = defaults.get("base_url")
        if base_url or metadata.get("base_url"):
            normalized_fields.append(
                APIServiceCredentialField(
                    key="base_url",
                    label="Base URL",
                    storage="config",
                    sensitive=False,
                    input_type="url",
                    config_key="base_url",
                    default_value=str(base_url or metadata.get("base_url")),
                )
            )

    deduped: List[APIServiceCredentialField] = []
    seen_keys: set[str] = set()
    for field in normalized_fields:
        if field.key in seen_keys:
            continue
        deduped.append(field)
        seen_keys.add(field.key)
    return deduped


def _get_api_service_schema_field(
    provider_id: str,
    field_name: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[APIServiceCredentialField]:
    for field in _build_api_service_credential_schema(provider_id, metadata):
        if field.key == field_name:
            return field
    return None


def _get_api_service_secret_field_names(
    provider_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[str]:
    return [
        field.key
        for field in _build_api_service_credential_schema(provider_id, metadata)
        if field.storage == "secret"
    ]


def _get_api_service_default_secret_id(provider_id: str, field_name: str = "api_key") -> str:
    """Return the canonical secret id for an API service credential field."""
    metadata = _load_api_service_metadata_data(provider_id) or {}
    raw_fields = metadata.get("credential_fields")
    if isinstance(raw_fields, list):
        for raw_field in raw_fields:
            if not isinstance(raw_field, dict):
                continue
            if raw_field.get("key") != field_name:
                continue
            storage = raw_field.get("storage")
            if storage == "config":
                break
            secret_id = raw_field.get("secret_id")
            if isinstance(secret_id, str):
                secret_id = secret_id.strip()
                if secret_id:
                    return secret_id

    auth = metadata.get("authentication") or metadata.get("auth")
    if isinstance(auth, dict):
        if field_name == "api_key":
            secret_id = auth.get("secret_key") or auth.get("api_key_secret") or auth.get("secret")
        else:
            secret_id = auth.get("secret_secret") or auth.get(f"{field_name}_secret")
        if isinstance(secret_id, str):
            secret_id = secret_id.strip()
            if secret_id:
                return secret_id
    if field_name == "api_key":
        return f"{provider_id}_api_key"
    return f"{provider_id}_{field_name}"


def _get_api_service_secret_candidates(
    provider_id: str,
    raw_service: Optional[Dict[str, Any]] = None,
    field_name: str = "api_key",
) -> List[str]:
    """Return candidate secret ids for an API service, ordered by preference."""
    candidates: List[str] = []
    if raw_service is None:
        raw_service = ConfigWriter.get_api_service_raw(provider_id) or {}

    ref_key = "apiKey" if field_name == "api_key" else field_name
    secret_ref = raw_service.get(ref_key, "")
    if isinstance(secret_ref, str) and secret_ref.startswith("{secret:") and secret_ref.endswith("}"):
        candidates.append(secret_ref[len("{secret:"):-1])

    candidates.append(_get_api_service_default_secret_id(provider_id, field_name=field_name))
    candidates.append(f"{provider_id}_{field_name}")

    deduped: List[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _get_api_service_enabled(provider_id: str) -> bool:
    """Return whether an API service is enabled in config."""
    raw_service = ConfigWriter.get_api_service_raw(provider_id) or {}
    return bool(raw_service.get("enabled", False))


def _get_api_service_tool_infos(provider_id: str) -> List[Any]:
    """Return all API tools that belong to the given service."""
    from flocks.tool.registry import ToolRegistry
    from flocks.server.routes.tool import _get_tool_source

    ToolRegistry.init()
    matched_tools: List[Any] = []
    for tool_info in ToolRegistry.list_tools():
        source, source_name = _get_tool_source(tool_info)
        if source == "api" and source_name == provider_id:
            matched_tools.append(tool_info)
    return matched_tools


def _is_api_service_builtin(provider_id: str, tools: Optional[List[Any]] = None) -> bool:
    """Check if an API service is built-in (project-level or core).

    A service is built-in if it has at least one registered tool and ALL of
    its tools have native=True (project-level .flocks/plugins/ or core code).
    User-level tools (~/.flocks/plugins/) have native=False.

    Pass pre-fetched *tools* to avoid a redundant registry scan.
    """
    if tools is None:
        tools = _get_api_service_tool_infos(provider_id)
    if not tools:
        return False
    return all(getattr(t, "native", False) for t in tools)


def _set_api_service_tools_enabled(provider_id: str, enabled: bool) -> int:
    """Synchronize the enabled state of all tools under an API service."""
    matched_tools = _get_api_service_tool_infos(provider_id)
    for tool_info in matched_tools:
        tool_info.enabled = enabled
    return len(matched_tools)


async def _read_api_service_status_cache(max_age_seconds: Optional[int] = 24 * 3600) -> Dict[str, Any]:
    """Read cached API service statuses."""
    try:
        await Storage.init()
        cached = await Storage.read(_API_SERVICE_STATUS_KEY) or {}
        checked_at = cached.get("checked_at", 0)
        if max_age_seconds is not None and checked_at:
            if time.time() - checked_at > max_age_seconds:
                return {}
        return cached.get("statuses", {})
    except Exception as e:
        log.warning("api_service_status.read_error", {"error": str(e)})
        return {}


async def _write_api_service_status_cache(statuses: Dict[str, Any]) -> None:
    """Persist API service status cache."""
    await Storage.init()
    await Storage.write(_API_SERVICE_STATUS_KEY, {
        "statuses": statuses,
        "checked_at": int(time.time()),
    })


async def _save_api_service_status_if_configured(provider_id: str, response: Dict[str, Any]) -> None:
    """Persist API test status only when the provider is configured as an API service."""
    raw_service = ConfigWriter.get_api_service_raw(provider_id)
    if raw_service is None:
        return
    if raw_service.get("enabled") is False:
        return
    await _save_api_service_status(provider_id, response)


def _build_api_service_summary(
    provider_id: str,
    raw_statuses: Dict[str, Any],
) -> APIServiceSummary:
    """Build API service summary used by the Tool API page."""
    meta = _load_api_service_metadata_data(provider_id) or {}
    enabled = _get_api_service_enabled(provider_id)
    cached_status = raw_statuses.get(provider_id) or {}
    matched_tools = _get_api_service_tool_infos(provider_id)

    status = "disabled" if not enabled else cached_status.get("status", "unknown")
    raw_config = ConfigWriter.get_api_service_raw(provider_id) or {}
    # "verify_ssl" is canonical; fall back to legacy "ssl_verify" for backward compatibility
    verify_ssl_raw = raw_config.get("verify_ssl", raw_config.get("ssl_verify", meta.get("verify_ssl", False)))
    if isinstance(verify_ssl_raw, str):
        verify_ssl = verify_ssl_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        verify_ssl = bool(verify_ssl_raw)
    return APIServiceSummary(
        id=provider_id,
        name=meta.get("name", provider_id),
        enabled=enabled,
        status=status,
        message=cached_status.get("message"),
        latency_ms=cached_status.get("latency_ms"),
        checked_at=cached_status.get("checked_at"),
        tool_count=len(matched_tools),
        description=meta.get("description"),
        description_cn=meta.get("description_cn"),
        builtin=_is_api_service_builtin(provider_id, matched_tools),
        verify_ssl=verify_ssl,
    )


async def list_api_services() -> List[APIServiceSummary]:
    try:
        from flocks.tool.registry import ToolRegistry

        ToolRegistry.init()

        configured_services = set(ConfigWriter.list_api_services_raw().keys())
        discovered_services = ToolRegistry.get_api_service_ids()
        raw_statuses = await _read_api_service_status_cache()

        service_ids = configured_services | discovered_services
        summaries = [
            _build_api_service_summary(provider_id, raw_statuses)
            for provider_id in service_ids
        ]
        summaries.sort(key=lambda item: (
            0 if item.enabled else 1,
            0 if item.status == "connected" else 1,
            item.name.lower(),
        ))
        return summaries
    except Exception as e:
        log.error("api_services.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


async def update_api_service(provider_id: str, request: APIServiceUpdateRequest) -> APIServiceSummary:
    try:
        existing = ConfigWriter.get_api_service_raw(provider_id) or {}
        existing["enabled"] = request.enabled
        if request.verify_ssl is not None:
            existing["verify_ssl"] = request.verify_ssl
        ConfigWriter.set_api_service(provider_id, existing)

        matched_count = _set_api_service_tools_enabled(provider_id, request.enabled)

        statuses = await _read_api_service_status_cache()
        if request.enabled:
            status_payload = statuses.get(provider_id, {})
            if status_payload.get("status") == "disabled":
                statuses.pop(provider_id, None)
        else:
            statuses[provider_id] = {
                "status": "disabled",
                "message": "Service disabled",
                "checked_at": int(time.time()),
            }
        await _write_api_service_status_cache(statuses)

        log.info("api_service.updated", {
            "provider_id": provider_id,
            "enabled": request.enabled,
            "verify_ssl": request.verify_ssl,
            "matched_tools": matched_count,
        })
        return _build_api_service_summary(provider_id, statuses)
    except Exception as e:
        log.error("api_service.update.error", {
            "provider_id": provider_id,
            "enabled": request.enabled,
            "error": str(e),
        })
        raise HTTPException(status_code=500, detail=str(e))


async def delete_api_service(provider_id: str) -> Dict[str, Any]:
    """Delete an API service configuration and its stored credential."""
    from flocks.security import get_secret_manager

    try:
        if _is_api_service_builtin(provider_id):
            raise HTTPException(
                status_code=403,
                detail=f"Cannot delete built-in API service '{provider_id}'"
            )

        raw_service = ConfigWriter.get_api_service_raw(provider_id) or {}
        metadata = _load_api_service_metadata_data(provider_id) or {}
        secret_ids: List[str] = []
        for field_name in _get_api_service_secret_field_names(provider_id, metadata):
            secret_ids.extend(
                candidate
                for candidate in _get_api_service_secret_candidates(provider_id, raw_service, field_name=field_name)
                if candidate not in secret_ids
            )

        removed_config = ConfigWriter.remove_api_service(provider_id)

        secrets = get_secret_manager()
        deleted_secret = False
        for secret_id in secret_ids:
            deleted_secret = secrets.delete(secret_id) or deleted_secret

        statuses = await _read_api_service_status_cache()
        if provider_id in statuses:
            statuses.pop(provider_id, None)
            await _write_api_service_status_cache(statuses)

        matched_count = _set_api_service_tools_enabled(provider_id, False)

        if not removed_config and not deleted_secret:
            raise HTTPException(status_code=404, detail="API service not found")

        log.info("api_service.deleted", {
            "provider_id": provider_id,
            "removed_config": removed_config,
            "deleted_secret": deleted_secret,
            "matched_tools": matched_count,
        })
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        log.error("api_service.delete.error", {
            "provider_id": provider_id,
            "error": str(e),
        })
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{provider_id}/metadata",
    response_model=APIServiceMetadata,
    summary="Get API service metadata",
    description="Get metadata for an API service including endpoints, authentication, etc."
)
async def get_api_service_metadata(provider_id: str):
    """Get API service metadata from config, metadata JSON, or _provider.yaml."""
    try:
        data = _load_api_service_metadata_data(provider_id)

        if not data:
            return APIServiceMetadata(
                name=provider_id,
                description=f"API service: {provider_id}"
            )

        base_url = data.get("base_url")
        if not base_url:
            defaults = data.get("defaults", {})
            base_url = defaults.get("base_url")
        if not base_url and data.get("apis") and len(data["apis"]) > 0:
            first_endpoint = data["apis"][0].get("endpoint", "")
            if first_endpoint:
                from urllib.parse import urlparse
                parsed = urlparse(first_endpoint)
                base_url = f"{parsed.scheme}://{parsed.netloc}"

        auth = data.get("authentication") or data.get("auth")
        if auth and isinstance(auth, dict) and "secret" in auth and "type" not in auth:
            auth = {
                "type": "api_key",
                "secret_key": auth["secret"],
                "description": f"API key (stored as '{auth['secret']}')",
                **{k: v for k, v in auth.items() if k != "secret"},
            }

        return APIServiceMetadata(
            name=data.get("name", provider_id),
            version=data.get("version"),
            description=data.get("description"),
            description_cn=data.get("description_cn"),
            author=data.get("author"),
            category=data.get("category") or data.get("defaults", {}).get("category"),
            authentication=auth,
            dependencies=data.get("dependencies"),
            apis=data.get("apis"),
            rate_limits=data.get("rate_limits"),
            tags=data.get("tags"),
            base_url=base_url,
            docs_url=data.get("docs_url"),
            credential_schema=[field.model_dump() for field in _build_api_service_credential_schema(provider_id, data)],
        )
    except Exception as e:
        log.error("api.metadata.error", {"provider_id": provider_id, "error": str(e)})
        return APIServiceMetadata(
            name=provider_id,
            description=f"API service: {provider_id}"
        )


def _load_provider_yaml_metadata(provider_id: str) -> Optional[Dict[str, Any]]:
    """Load metadata from a _provider.yaml file for YAML-based API tools."""
    try:
        from flocks.plugin.loader import DEFAULT_PLUGIN_ROOT
        import yaml

        api_roots = [
            Path.cwd() / ".flocks" / "plugins" / "tools" / "api",
            DEFAULT_PLUGIN_ROOT / "tools" / "api",
        ]
        api_dir: Optional[Path] = None
        for api_root in api_roots:
            direct_dir = api_root / provider_id
            if direct_dir.is_dir():
                api_dir = direct_dir
                break

            if not api_root.is_dir():
                continue

            for candidate in api_root.iterdir():
                if not candidate.is_dir():
                    continue
                provider_file = candidate / "_provider.yaml"
                if not provider_file.is_file():
                    continue
                try:
                    candidate_provider = yaml.safe_load(provider_file.read_text(encoding="utf-8"))
                except Exception as e:
                    log.debug("provider.yaml_metadata.provider_read_failed", {
                        "provider_id": provider_id, "dir": str(candidate), "error": str(e),
                    })
                    continue

                if (
                    isinstance(candidate_provider, dict)
                    and candidate_provider.get("service_id") == provider_id
                ):
                    api_dir = candidate
                    break

            if api_dir is not None:
                break
        if api_dir is None:
            return None

        provider_file = api_dir / "_provider.yaml"
        if not provider_file.is_file():
            return None

        prov = yaml.safe_load(provider_file.read_text(encoding="utf-8"))
        if not isinstance(prov, dict):
            return None

        tool_apis = []
        for item in sorted(api_dir.iterdir()):
            if item.suffix in (".yaml", ".yml") and not item.name.startswith("_"):
                try:
                    tool_data = yaml.safe_load(item.read_text(encoding="utf-8"))
                    if isinstance(tool_data, dict) and tool_data.get("name"):
                        tool_apis.append({
                            "name": tool_data["name"],
                            "description": tool_data.get("description", ""),
                        })
                except Exception as e:
                    log.debug("provider.yaml_metadata.tool_read_failed", {
                        "provider_id": provider_id, "file": item.name, "error": str(e),
                    })

        return {
            "name": prov.get("name", provider_id),
            "service_id": prov.get("service_id", provider_id),
            "description": prov.get("description"),
            "description_cn": prov.get("description_cn"),
            "auth": prov.get("auth"),
            "credential_fields": prov.get("credential_fields"),
            "defaults": prov.get("defaults", {}),
            "apis": tool_apis or None,
        }
    except Exception as e:
        log.debug("provider.yaml_metadata.load_failed", {"provider_id": provider_id, "error": str(e)})
        return None


# =============================================================================
# Credentials Management (Similar to MCP)
# =============================================================================

class ProviderCredentialRequest(BaseModel):
    """Request to set provider credentials.

    secret_id:     The key used in .secret.json (e.g., "anthropic_api_key").
                   If not provided, auto-generated as "{provider_id}_api_key".
    api_key:       The secret value to store.
    base_url:      Optional base URL to store.
    provider_name: Optional display name for the provider (used by openai-compatible).
    """
    secret_id: Optional[str] = Field(None, description="Secret ID in .secret.json")
    api_key: Optional[str] = Field(None, description="API key value")
    secret: Optional[str] = Field(None, description="Secondary secret value for custom API services")
    base_url: Optional[str] = Field(None, description="Base URL for the provider")
    username: Optional[str] = Field(None, description="Optional username for API services")
    fields: Optional[Dict[str, Optional[str]]] = Field(None, description="Dynamic service credential fields")
    provider_name: Optional[str] = Field(None, description="Display name for the provider")


class ProviderCredentialResponse(BaseModel):
    """Response with credential info"""
    secret_id: Optional[str] = None
    api_key: Optional[str] = None
    api_key_masked: Optional[str] = None
    secret: Optional[str] = None
    secret_masked: Optional[str] = None
    base_url: Optional[str] = None
    username: Optional[str] = None
    fields: Optional[Dict[str, Optional[str]]] = None
    secret_ids: Optional[Dict[str, str]] = None
    has_credential: bool


@router.get(
    "/{provider_id}/credentials",
    response_model=ProviderCredentialResponse,
    summary="Get provider credentials (masked)",
    description="Get masked credential information for a registered LLM provider."
)
async def get_provider_credentials(provider_id: str):
    """Get credential info for an LLM provider (_llm_key convention).

    - api_key: from .secret.json  (_llm_key first, legacy _api_key fallback)
    - base_url: from flocks.json provider.{id}.options.baseURL (raw, unresolved)

    For API services use GET /{provider_id}/service-credentials instead.
    """
    from flocks.security import get_secret_manager

    try:
        secrets = get_secret_manager()

        # LLM provider convention: _llm_key first, fall back to legacy _api_key
        secret_id = f"{provider_id}_llm_key"
        api_key = secrets.get(secret_id)
        if not api_key:
            secret_id = f"{provider_id}_api_key"
            api_key = secrets.get(secret_id)
        if not api_key:
            api_key = _get_inline_provider_api_key(provider_id)

        # base_url from flocks.json raw (not resolved)
        base_url = None
        raw_provider = ConfigWriter.get_provider_raw(provider_id)
        if raw_provider:
            options = raw_provider.get("options", {})
            base_url = options.get("baseURL") or options.get("base_url")
        # Fallback: check legacy .secret.json entry (migration may not have run yet)
        if not base_url:
            base_url = secrets.get(f"{provider_id}_base_url")

        return ProviderCredentialResponse(
            secret_id=secret_id if api_key else None,
            api_key=api_key,
            api_key_masked=SecretManager.mask(api_key) if api_key else None,
            base_url=base_url,
            has_credential=bool(api_key),
        )
    except Exception as e:
        log.error("provider.credentials.get.error", {"provider_id": provider_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{provider_id}/credentials",
    response_model=Dict[str, Any],
    summary="Set provider credentials",
    description="Set authentication credentials for a provider or API service."
)
async def set_provider_credentials(provider_id: str, request: ProviderCredentialRequest):
    """Set credentials for a provider.

    - api_key → .secret.json
    - base_url → flocks.json provider.{id}.options.baseURL
    - Ensures provider entry exists in flocks.json
    - Configures provider runtime immediately
    """
    from flocks.security import get_secret_manager
    from flocks.provider.provider import ProviderConfig

    try:
        if not request.api_key:
            raise HTTPException(status_code=400, detail="API key required")

        secrets = get_secret_manager()

        # 1. Save API key to .secret.json using _llm_key convention for LLM providers
        secret_id = request.secret_id or f"{provider_id}_llm_key"
        secrets.set(secret_id, request.api_key)
        masked = f"{request.api_key[:4]}***{request.api_key[-4:]}" if len(request.api_key) > 8 else "***"
        log.info("provider.credentials.saving", {
            "provider_id": provider_id,
            "secret_id": secret_id,
            "api_key_masked": masked,
            "base_url": request.base_url,
        })

        # 2. Ensure provider entry exists in flocks.json and update base_url / name
        raw_provider = ConfigWriter.get_provider_raw(provider_id)
        if raw_provider:
            # Provider already exists — update base_url and name if provided
            if request.base_url is not None:
                ConfigWriter.update_provider_field(
                    provider_id, "options.baseURL", request.base_url
                )
                # Ensure apiKey reference is set
                ConfigWriter.update_provider_field(
                    provider_id, "options.apiKey", f"{{secret:{provider_id}_llm_key}}"
                )
            if request.provider_name:
                ConfigWriter.update_provider_field(
                    provider_id, "name", request.provider_name
                )
        else:
            # Provider not yet in flocks.json — create a minimal entry
            # Use model_catalog for model defaults; fall back to SDK built-ins.
            npm = _get_npm_package(provider_id)
            models: dict = {}
            try:
                from flocks.provider.model_catalog import get_provider_model_definitions
                defs = get_provider_model_definitions(provider_id)
                if defs:
                    models = {m.id: {"name": m.name} for m in defs}
            except Exception:
                pass

            if not models:
                try:
                    sdk_provider = Provider.get(provider_id)
                    if sdk_provider:
                        for m in sdk_provider.get_models():
                            models[m.id] = {"name": m.name}
                except Exception:
                    pass

            # Resolve base_url: request > catalog > SDK default
            effective_base_url = request.base_url
            if not effective_base_url:
                try:
                    from flocks.provider.model_catalog import get_raw_catalog
                    raw_catalog = get_raw_catalog()
                    catalog_entry = raw_catalog.get(provider_id, {})
                    effective_base_url = catalog_entry.get("default_base_url")
                except Exception:
                    pass
            if not effective_base_url:
                sdk_provider = Provider.get(provider_id)
                if sdk_provider and hasattr(sdk_provider, "DEFAULT_BASE_URL"):
                    effective_base_url = sdk_provider.DEFAULT_BASE_URL or None

            config_dict = ConfigWriter.build_provider_config(
                provider_id,
                npm=npm,
                base_url=effective_base_url,
                models=models,
            )
            if request.provider_name:
                config_dict["name"] = request.provider_name
            ConfigWriter.add_provider(provider_id, config_dict)

        # 3. Configure the provider runtime so is_configured() reflects the change
        Provider._ensure_initialized()
        provider = Provider.get(provider_id)
        if provider:
            # Preserve the existing base_url from flocks.json when not explicitly provided,
            # so we don't accidentally overwrite it with None in the runtime config.
            effective_base_url = request.base_url
            if effective_base_url is None:
                raw = ConfigWriter.get_provider_raw(provider_id)
                if raw:
                    effective_base_url = raw.get("options", {}).get("baseURL")

            custom_settings = _get_provider_custom_settings(provider)
            provider.configure(ProviderConfig(
                provider_id=provider_id,
                api_key=request.api_key,
                base_url=effective_base_url,
                custom_settings=custom_settings,
            ))
            # Reset client so it picks up new base_url/key
            if hasattr(provider, "_client"):
                provider._client = None
            # Update display name if provided
            if request.provider_name:
                provider.name = request.provider_name


        log.info("provider.credentials.set", {
            "provider_id": provider_id,
            "secret_id": secret_id,
        })

        return {
            "success": True,
            "message": f"Credentials saved as '{secret_id}'",
            "secret_id": secret_id,
        }

    except HTTPException:
        raise
    except Exception as e:
        log.error("provider.credentials.set.error", {"provider_id": provider_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{provider_id}/credentials",
    response_model=Dict[str, bool],
    summary="Delete provider credentials",
    description="Delete stored credentials for a provider or API service."
)
async def delete_provider_credentials(provider_id: str):
    """Delete a provider: remove from flocks.json and .secret.json."""
    from flocks.security import get_secret_manager

    try:
        secrets = get_secret_manager()

        # 1. Clear any default models that reference this provider
        affected_defaults = ConfigWriter.clear_default_models_for_provider(provider_id)

        # 2. Remove provider entry from flocks.json
        removed_config = ConfigWriter.remove_provider(provider_id)

        # 3. Remove API key from .secret.json (try both naming conventions)
        deleted_secret = secrets.delete(f"{provider_id}_llm_key")
        deleted_secret = secrets.delete(f"{provider_id}_api_key") or deleted_secret
        # Also clean up legacy base_url entries
        secrets.delete(f"{provider_id}_base_url")

        if not removed_config and not deleted_secret:
            raise HTTPException(status_code=404, detail="No credentials found for this provider")

        # 4. Clear provider runtime config
        Provider._ensure_initialized()
        provider = Provider.get(provider_id)
        if provider:
            provider._config = None
            if hasattr(provider, "_client"):
                provider._client = None

        # 5. Remove from Provider registry if it's a custom provider
        if provider_id.startswith("custom-"):
            Provider._providers.pop(provider_id, None)

        log.info("provider.credentials.deleted", {"provider_id": provider_id})
        return {"success": True, "cleared_defaults": affected_defaults}


    except HTTPException:
        raise
    except Exception as e:
        log.error("provider.credentials.delete.error", {"provider_id": provider_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# API Service Credentials  (_api_key convention)
# =============================================================================

@router.get(
    "/{provider_id}/service-credentials",
    response_model=ProviderCredentialResponse,
    summary="Get API service credentials (masked)",
    description="Get masked credential information for an API service (tool integrations)."
)
async def get_service_credentials(provider_id: str):
    """Get credential info for an API service (_api_key convention).

    Reads the secret_id from flocks.json api_services.{id}.secret first,
    then falls back to the conventional {provider_id}_api_key default.
    """
    from flocks.security import get_secret_manager

    try:
        secrets = get_secret_manager()
        raw_service = ConfigWriter.get_api_service_raw(provider_id)

        metadata = _load_api_service_metadata_data(provider_id) or {}
        schema = _build_api_service_credential_schema(provider_id, metadata)
        field_values: Dict[str, Optional[str]] = {}
        secret_ids: Dict[str, str] = {}

        if raw_service:
            for field in schema:
                if field.storage != "config":
                    continue
                raw_value = raw_service.get(field.config_key)
                if raw_value is None and field.key == "base_url":
                    raw_value = raw_service.get("baseUrl")
                if isinstance(raw_value, str):
                    field_values[field.key] = raw_value

            legacy_base_url = raw_service.get("base_url") or raw_service.get("baseUrl")
            if isinstance(legacy_base_url, str) and legacy_base_url and "base_url" not in field_values:
                field_values["base_url"] = legacy_base_url
            legacy_username = raw_service.get("username")
            if isinstance(legacy_username, str) and legacy_username and "username" not in field_values:
                field_values["username"] = legacy_username

        for field_name in _get_api_service_secret_field_names(provider_id, metadata):
            for candidate in _get_api_service_secret_candidates(provider_id, raw_service, field_name=field_name):
                value = secrets.get(candidate)
                if value:
                    field_values[field_name] = value
                    secret_ids[field_name] = candidate
                    break

        auth = metadata.get("authentication") or metadata.get("auth")
        expects_secondary_secret = (
            isinstance(auth, dict)
            and bool(auth.get("secret_secret"))
            and _should_persist_secondary_secret(metadata)
        )
        if expects_secondary_secret and not field_values.get("secret"):
            combined = field_values.get("api_key")
            if isinstance(combined, str):
                split_result = _split_compound_service_credentials(combined)
                if split_result:
                    split_api_key, split_secret = split_result
                    field_values["api_key"] = split_api_key
                    field_values["secret"] = split_secret

        return ProviderCredentialResponse(
            secret_id=secret_ids.get("api_key"),
            api_key=field_values.get("api_key"),
            api_key_masked=SecretManager.mask(field_values["api_key"]) if field_values.get("api_key") else None,
            secret=field_values.get("secret"),
            secret_masked=SecretManager.mask(field_values["secret"]) if field_values.get("secret") else None,
            base_url=field_values.get("base_url"),
            username=field_values.get("username"),
            fields=field_values or None,
            secret_ids=secret_ids or None,
            has_credential=bool(any(value for value in field_values.values())),
        )
    except Exception as e:
        log.error("service.credentials.get.error", {"provider_id": provider_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{provider_id}/service-credentials",
    response_model=Dict[str, Any],
    summary="Set API service credentials",
    description="Set authentication credentials for an API service."
)
async def set_service_credentials(provider_id: str, request: ProviderCredentialRequest):
    """Set credentials for an API service (_api_key convention).

    Saves to .secret.json AND writes the secret reference into flocks.json
    api_services.{id}.secret so the config file is the authoritative source.
    """
    from flocks.security import get_secret_manager

    try:
        field_updates: Dict[str, Optional[str]] = {}
        if isinstance(request.fields, dict):
            field_updates.update(request.fields)
        if request.api_key is not None:
            field_updates["api_key"] = request.api_key
        if request.secret is not None:
            field_updates["secret"] = request.secret
        if request.base_url is not None:
            field_updates["base_url"] = request.base_url
        if request.username is not None:
            field_updates["username"] = request.username

        if not field_updates:
            raise HTTPException(status_code=400, detail="At least one credential field is required")

        secrets = get_secret_manager()
        existing = ConfigWriter.get_api_service_raw(provider_id) or {}
        metadata = _load_api_service_metadata_data(provider_id) or {}
        schema_by_key = {
            field.key: field
            for field in _build_api_service_credential_schema(provider_id, metadata)
        }
        if "base_url" in field_updates and "base_url" not in schema_by_key:
            schema_by_key["base_url"] = APIServiceCredentialField(
                key="base_url",
                label="Base URL",
                storage="config",
                sensitive=False,
                input_type="url",
                config_key="base_url",
            )
        if "username" in field_updates and "username" not in schema_by_key:
            schema_by_key["username"] = APIServiceCredentialField(
                key="username",
                label="Username",
                storage="config",
                sensitive=False,
                input_type="text",
                config_key="username",
            )
        unknown_fields = sorted(key for key in field_updates if key not in schema_by_key)
        if unknown_fields:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported credential fields: {', '.join(unknown_fields)}",
            )

        auth = metadata.get("authentication") or metadata.get("auth")
        expects_secondary_secret = (
            isinstance(auth, dict)
            and bool(auth.get("secret_secret"))
            and _should_persist_secondary_secret(metadata)
        )
        if expects_secondary_secret and "api_key" in field_updates and not field_updates.get("secret"):
            combined = field_updates.get("api_key")
            if isinstance(combined, str):
                split_result = _split_compound_service_credentials(combined)
                if split_result:
                    split_api_key, split_secret = split_result
                    field_updates["api_key"] = split_api_key
                    field_updates["secret"] = split_secret

        previous_secret_ref = existing.get("apiKey")
        if (
            "api_key" in field_updates
            and "secret" in field_updates
            and isinstance(previous_secret_ref, str)
            and previous_secret_ref.startswith("{secret:")
            and previous_secret_ref.endswith("}")
        ):
            previous_secret_id = previous_secret_ref[len("{secret:"):-1]
            current_api_key_field = schema_by_key.get("api_key")
            current_secret_field = schema_by_key.get("secret")
            current_secret_ids = {
                request.secret_id or (current_api_key_field.secret_id if current_api_key_field else None),
                current_secret_field.secret_id if current_secret_field else None,
            }
            if previous_secret_id not in current_secret_ids:
                previous_value = secrets.get(previous_secret_id)
                if isinstance(previous_value, str) and "|" in previous_value:
                    secrets.delete(previous_secret_id)

        touched_secret_ids: Dict[str, str] = {}
        for field_name, raw_value in field_updates.items():
            field = schema_by_key[field_name]
            if field.storage == "secret":
                cleaned_value = raw_value.strip() if isinstance(raw_value, str) else None
                if not cleaned_value:
                    raise HTTPException(status_code=400, detail=f"{field.label} cannot be empty")

                secret_id = request.secret_id if field_name == "api_key" and request.secret_id else field.secret_id
                assert secret_id is not None
                secrets.set(secret_id, cleaned_value)
                touched_secret_ids[field_name] = secret_id
                existing[field.config_key] = f"{{secret:{secret_id}}}"

                legacy_secret_id = f"{provider_id}_{field_name}"
                if secret_id != legacy_secret_id:
                    secrets.delete(legacy_secret_id)
            else:
                cleaned_value = raw_value.strip() if isinstance(raw_value, str) else ""
                if cleaned_value:
                    existing[field.config_key] = cleaned_value
                else:
                    existing.pop(field.config_key, None)
                    if field.key == "base_url":
                        existing.pop("baseUrl", None)
        ConfigWriter.set_api_service(provider_id, existing)

        log.info(
            "service.credentials.set",
            {
                "provider_id": provider_id,
                "secret_id": touched_secret_ids.get("api_key"),
                "secret_fields": sorted(touched_secret_ids.keys()),
            },
        )
        return {
            "success": True,
            "message": (
                f"Credentials saved as '{touched_secret_ids.get('api_key')}'"
                if touched_secret_ids.get("api_key")
                else "Credentials saved"
            ),
            "secret_id": touched_secret_ids.get("api_key"),
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error("service.credentials.set.error", {"provider_id": provider_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


class TestCredentialRequest(BaseModel):
    """Optional request body for test-credentials"""
    model_id: Optional[str] = Field(None, description="Model to test with (uses first available if omitted)")


@router.post(
    "/{provider_id}/test-credentials",
    response_model=Dict[str, Any],
    summary="Test provider credentials",
    description="Test if the stored credentials are valid by making a real chat API call."
)
async def test_provider_credentials(provider_id: str, body: Optional[TestCredentialRequest] = None):
    """Test credentials for a provider or API service by making a real API call"""
    from flocks.security import get_secret_manager

    try:
        start = time.time()
        
        # test-credentials handles both LLM providers and API services.
        # Try _llm_key first (LLM provider), then all secret fields defined
        # in the credential schema (api_key, password, token, etc.).
        secrets = get_secret_manager()
        secret_id = f"{provider_id}_llm_key"
        api_key = secrets.get(secret_id)
        if not api_key:
            raw_service = ConfigWriter.get_api_service_raw(provider_id) or {}
            secret_id = None
            metadata = _load_api_service_metadata_data(provider_id) or {}
            secret_field_names = _get_api_service_secret_field_names(provider_id, metadata)
            if not secret_field_names:
                secret_field_names = ["api_key"]
            for field_name in secret_field_names:
                for candidate in _get_api_service_secret_candidates(
                    provider_id, raw_service, field_name=field_name
                ):
                    api_key = secrets.get(candidate)
                    if api_key:
                        secret_id = candidate
                        break
                if api_key:
                    break
        if not api_key:
            api_key = _get_inline_provider_api_key(provider_id)

        if not api_key:
            response = {
                "success": False,
                "message": "No credentials configured for this service",
                "error": "No credentials",
            }
            await _save_api_service_status_if_configured(provider_id, response)
            return response

        Provider._ensure_initialized()
        # Re-run dynamic provider loading in case this provider was created after
        # the server started (e.g. user just added azure-openai via the UI).
        # _load_dynamic_providers skips already-registered providers, so it is safe
        # to call multiple times.
        Provider._load_dynamic_providers()
        # Apply config to ensure _config_models (user-defined models) are loaded
        config = await Config.get()
        await Provider.apply_config(config, provider_id=provider_id)
        provider = Provider.get(provider_id)

        if provider:
            from flocks.provider.provider import ProviderConfig, ChatMessage as ProviderChatMessage

            # Always reconfigure with the freshest key from secret manager
            # to avoid stale keys from cached config or prior apply_config.
            effective_base_url = None
            provider_config = getattr(provider, "_config", None)
            config_base_url = getattr(provider_config, "base_url", None)
            if isinstance(config_base_url, str) and config_base_url:
                effective_base_url = config_base_url
            else:
                base_url_attr = getattr(provider, "_base_url", None)
                if isinstance(base_url_attr, str) and base_url_attr:
                    effective_base_url = base_url_attr
            custom_settings = _get_provider_custom_settings(provider)
            provider.configure(ProviderConfig(
                provider_id=provider_id,
                api_key=api_key,
                base_url=effective_base_url,
                custom_settings=custom_settings,
            ))
            if hasattr(provider, '_client'):
                provider._client = None

            models = Provider.list_models(provider_id)

            if not models:
                response = {
                    "success": False,
                    "message": "该 Provider 没有可用的模型进行测试",
                    "error": "No models available",
                    "latency_ms": int((time.time() - start) * 1000),
                }
                await _save_api_service_status_if_configured(provider_id, response)
                return response

            # Pick model: prefer user-specified, else first available
            requested_model_id = body.model_id if body else None
            test_model_id = requested_model_id or models[0].id

            # Validate model belongs to this provider
            valid_ids = {m.id for m in models}
            if test_model_id not in valid_ids:
                response = {
                    "success": False,
                    "message": f"模型 '{test_model_id}' 不属于该 Provider",
                    "error": "Invalid model",
                }
                await _save_api_service_status_if_configured(provider_id, response)
                return response

            test_question = "What is the capital of France? Answer in one word only."
            try:
                test_messages = [ProviderChatMessage(role="user", content=test_question)]
                response = await provider.chat(
                    test_model_id,
                    test_messages,
                    max_tokens=20,
                )
                answer = ""
                if response and hasattr(response, "content"):
                    answer = response.content or ""
                elif response and hasattr(response, "message"):
                    msg = response.message
                    if hasattr(msg, "content"):
                        answer = msg.content or ""

                latency = int((time.time() - start) * 1000)
                response = {
                    "success": True,
                    "message": "连接成功",
                    "latency_ms": latency,
                    "model_id": test_model_id,
                    "question": test_question,
                    "answer": answer.strip(),
                    "model_count": len(models),
                }
                await _save_api_service_status_if_configured(provider_id, response)
                return response
            except Exception as chat_err:
                latency = int((time.time() - start) * 1000)
                error_msg = str(chat_err)
                response = {
                    "success": False,
                    "message": f"API 调用失败: {error_msg}",
                    "error": error_msg,
                    "latency_ms": latency,
                    "model_id": test_model_id,
                    "question": test_question,
                }
                await _save_api_service_status_if_configured(provider_id, response)
                return response
        else:
            # This is an API service (not a registered provider)
            # Try to test connectivity by calling a simple tool
            from flocks.tool.registry import ToolRegistry, ToolCategory, ToolInfo
            from flocks.server.routes.tool import _get_tool_source
            
            ToolRegistry.init()

            _set_api_service_tools_enabled(provider_id, True)
            
            # Find tools from this service using the shared source detection
            all_tools = ToolRegistry.list_tools()
            service_tools = []
            
            log.debug("test_credentials.scanning_tools", {
                "service": provider_id,
                "total_tools": len(all_tools),
                "dynamic_modules": list(ToolRegistry.get_dynamic_tools_by_module().keys()),
            })
            
            for tool_info in all_tools:
                if not tool_info.enabled:
                    continue
                source, source_name = _get_tool_source(tool_info)
                if source == "api" and source_name == provider_id:
                    service_tools.append(tool_info)
            
            log.info("test_credentials.service_tools", {
                "service": provider_id,
                "found_tools": [t.name for t in service_tools],
            })
            
            if not service_tools:
                latency = int((time.time() - start) * 1000)
                log.warning("test_credentials.no_tools", {
                    "service": provider_id,
                    "dynamic_modules": list(ToolRegistry.get_dynamic_tools_by_module().keys()),
                    "total_tools": len(all_tools),
                })
                return {
                    "success": False,
                    "message": "无法验证凭据：未找到可用于测试连通性的工具。请检查工具是否已正确加载。",
                    "error": "No enabled tools found for testing connectivity",
                    "latency_ms": latency
                }
            
            # Prefer simpler tools for connectivity testing.
            # Rank tools by required-parameter count (fewer = simpler);
            # prefer lightweight query/scan tools and avoid file/upload handlers.
            #
            # NOTE: For services that expose a dedicated login/auth probe
            # (e.g. qingteng_login, skyeye_login), we want it tried *first* —
            # it is parameter-free and exercises the credential pipeline end
            # to end without invoking business APIs that may need extra
            # required fields beyond the JSON schema (e.g. assets.refresh
            # which the handler validates needs `resource`/`os_type`).
            def _is_login_probe(t: ToolInfo) -> bool:
                if t.requires_confirmation:
                    return False
                if any(p.required for p in t.parameters):
                    return False
                name_lower = t.name.lower()
                # Keep this list tight: each keyword must be substring-safe
                # (no false positives on common business tool names) and
                # describe a parameter-free probe by convention.
                login_kw = ("login", "whoami", "ping", "health")
                return any(kw in name_lower for kw in login_kw)

            def _tool_sort_key(t: ToolInfo) -> tuple[int, int, str]:
                required_count = sum(1 for p in t.parameters if p.required)
                name_lower = t.name.lower()
                # Highest priority: dedicated login/health probes. These are the
                # safest connectivity test because they have no required params
                # and only exercise the auth/sign-in flow.
                if _is_login_probe(t):
                    priority = -1
                # Prefer query/scan style tools first, and push upload/file tools last.
                elif "ip" in name_lower:
                    priority = 0
                elif "url" in name_lower or "scan" in name_lower or "query" in name_lower:
                    priority = 1
                elif "domain" in name_lower:
                    priority = 2
                elif "upload" in name_lower or "file" in name_lower:
                    priority = 9
                else:
                    priority = 3
                return (priority, required_count, name_lower)
            
            service_tools.sort(key=_tool_sort_key)

            def _string_candidates(param_name: str) -> list[str]:
                param_name_lower = param_name.lower()
                if "ip" in param_name_lower or param_name_lower == "resource":
                    return ["8.8.8.8"]
                if "domain" in param_name_lower:
                    return ["example.com"]
                if "hash" in param_name_lower or "sha256" in param_name_lower:
                    return ["657483b5bf67ef0cc2e2d21c68394d1f7fd35f9c0b6998f7b944dc4e5aa881f8"]
                if "md5" in param_name_lower:
                    return ["d41d8cd98f00b204e9800998ecf8427e"]
                if "sha1" in param_name_lower:
                    return ["da39a3ee5e6b4b0d3255bfef95601890afd80709"]
                if "email" in param_name_lower:
                    return ["test@example.com"]
                if "url" in param_name_lower:
                    return ["https://example.com"]
                return ["test"]

            def _enum_sort_key(value: object) -> tuple[int, int, str]:
                text = str(value).lower()
                if any(
                    keyword in text
                    for keyword in (
                        "delete", "replace", "add", "upload", "create", "update",
                        "set", "edit", "remove", "block", "unblock", "isolate",
                        "quarantine", "scan", "restore", "disable", "virus", "stop",
                    )
                ):
                    priority = 9
                elif any(
                    keyword in text
                    for keyword in (
                        # Specialized/premium endpoints that may require extra permissions;
                        # test these after basic query/lookup actions.
                        "vuln", "cve", "threat", "alert", "risk",
                    )
                ):
                    priority = 6
                elif any(
                    keyword in text
                    for keyword in (
                        "public_ip_list", "all_", "recent", "status", "info",
                        "list", "query", "search", "get",
                    )
                ):
                    priority = 0
                else:
                    priority = 3
                return (priority, len(text), text)

            def _param_candidates(param) -> list[object]:
                values: list[object] = []
                if param.default is not None:
                    values.append(param.default)
                if param.enum:
                    values.extend(sorted(param.enum, key=_enum_sort_key))

                param_type = param.type.value if hasattr(param.type, "value") else str(param.type)
                if param_type == "string":
                    values.extend(_string_candidates(param.name))
                elif param_type in ["number", "integer"]:
                    values.append(1)
                elif param_type == "boolean":
                    values.append(True)
                elif param_type == "array":
                    item_values = _string_candidates(param.name)
                    values.append(item_values[:1] or ["test"])
                elif param_type == "object":
                    values.append({})

                deduped: list[object] = []
                seen: set[str] = set()
                for value in values:
                    key = repr(value)
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(value)
                return deduped[:4]

            def _build_param_sets(tool_info) -> list[dict[str, object]]:
                # Pre-seed base params with non-required string fields that have
                # meaningful test values (e.g. resource="8.8.8.8"). This ensures
                # action-dispatch tools (like ngtip_query) are tested with the
                # supporting params their sub-actions actually need, so actions
                # like query_ip/query_dns are exercised instead of falling through
                # to specialized endpoints (vuln) that need fewer params.
                base: dict[str, object] = {}
                for param in tool_info.parameters:
                    if param.required or param.enum:
                        continue
                    type_str = param.type.value if hasattr(param.type, "value") else str(param.type)
                    if type_str == "string":
                        candidates = _string_candidates(param.name)
                        if candidates and candidates[0] != "test":
                            base[param.name] = candidates[0]

                param_sets: list[dict[str, object]] = [dict(base)]
                for param in tool_info.parameters:
                    if not param.required:
                        continue
                    candidates = _param_candidates(param) or ["test"]
                    next_sets: list[dict[str, object]] = []
                    for existing in param_sets:
                        for candidate in candidates:
                            updated = dict(existing)
                            updated[param.name] = candidate
                            next_sets.append(updated)
                            if len(next_sets) >= 6:
                                break
                        if len(next_sets) >= 6:
                            break
                    param_sets = next_sets or param_sets
                return param_sets or [{}]

            def _short_repr(value: object, limit: int = 80) -> str:
                """Compact repr for params, capped to *limit* chars to keep
                the failure summary message readable (and the cached status
                payload small)."""
                text = repr(value)
                if len(text) <= limit:
                    return text
                return text[: limit - 3] + "..."

            attempts: list[dict[str, object]] = []
            last_response: Optional[dict[str, object]] = None
            for test_tool in service_tools[:5]:
                for test_params in _build_param_sets(test_tool):
                    try:
                        log.info("testing.api.connectivity", {
                            "service": provider_id,
                            "tool": test_tool.name,
                            "params": test_params,
                        })

                        result = await ToolRegistry.execute(tool_name=test_tool.name, **test_params)
                        latency = int((time.time() - start) * 1000)

                        if result.success:
                            response = {
                                "success": True,
                                "message": f"✅ API 连通性测试成功！使用工具 '{test_tool.name}' 进行测试。",
                                "latency_ms": latency,
                                "tool_tested": test_tool.name,
                                "attempts": attempts,
                            }
                            await _save_api_service_status(provider_id, response)
                            return response

                        error_detail = result.error or "Unknown error"
                        log.warning("testing.api.connectivity.failed", {
                            "service": provider_id,
                            "tool": test_tool.name,
                            "params": test_params,
                            "error": error_detail,
                        })
                        attempts.append({
                            "tool": test_tool.name,
                            "params": test_params,
                            "error": error_detail,
                        })
                        last_response = {
                            "success": False,
                            "message": f"❌ API 调用失败: {error_detail}",
                            "error": error_detail,
                            "latency_ms": latency,
                            "tool_tested": test_tool.name,
                        }
                    except Exception as tool_error:
                        latency = int((time.time() - start) * 1000)
                        log.warning("testing.api.connectivity.failed", {
                            "service": provider_id,
                            "tool": test_tool.name,
                            "params": test_params,
                            "error": str(tool_error),
                            "exception": True,
                        })
                        attempts.append({
                            "tool": test_tool.name,
                            "params": test_params,
                            "error": str(tool_error),
                        })
                        last_response = {
                            "success": False,
                            "message": f"❌ API 连通性测试失败: {str(tool_error)}",
                            "error": str(tool_error),
                            "latency_ms": latency,
                            "tool_tested": test_tool.name,
                        }

            response = last_response or {
                "success": False,
                "message": "❌ API 连通性测试失败: 未生成可执行的测试参数",
                "error": "No executable connectivity test candidate",
                "latency_ms": int((time.time() - start) * 1000),
            }
            # Attach all attempts so the WebUI/log can show why every probe
            # failed, instead of only the message of the very last attempt.
            response["attempts"] = attempts
            if attempts:
                # Keep message single-line so any UI (toast/popover) renders
                # consistently regardless of whitespace handling. Per-attempt
                # params are truncated to avoid blowing up the cached message.
                detail = "; ".join(
                    f"{a['tool']}({_short_repr(a.get('params') or {})})→{a['error']}"
                    for a in attempts
                )
                response["message"] = (
                    f"❌ API 连通性测试失败（共尝试 {len(attempts)} 次）。"
                    f"最后一次错误: {response.get('error', 'unknown')}｜尝试明细: {detail}"
                )
            await _save_api_service_status(provider_id, response)
            return response
    except Exception as e:
        return {
            "success": False,
            "message": f"Credentials test failed: {str(e)}",
            "error": str(e)
        }


# ==================== API Service Status (Cached) ====================

_API_SERVICE_STATUS_KEY = "api_service_status"


async def _save_api_service_status(provider_id: str, test_response: dict) -> None:
    """Persist a single API service test result into the status cache."""
    try:
        await Storage.init()
        cached = await Storage.read(_API_SERVICE_STATUS_KEY) or {}
        statuses = cached.get("statuses", {})
        statuses[provider_id] = {
            "status": "connected" if test_response.get("success") else "error",
            "message": test_response.get("message", ""),
            "latency_ms": test_response.get("latency_ms"),
            "tool_tested": test_response.get("tool_tested"),
            "checked_at": int(time.time()),
        }
        await Storage.write(_API_SERVICE_STATUS_KEY, {
            "statuses": statuses,
            "checked_at": int(time.time()),
        })
    except Exception as e:
        log.warning("api_service_status.save_error", {"provider_id": provider_id, "error": str(e)})


@router.get(
    "/api-services/status",
    response_model=Dict[str, Any],
    summary="Get all API service connectivity statuses",
    description="Get cached connectivity status for all API services. Status is refreshed daily."
)
async def get_api_services_status():
    """Get cached connectivity status for all API services.

    Returns cached results if available and not expired (24 hours).
    Otherwise returns empty dict, indicating no valid cache exists.
    """
    try:
        await Storage.init()
        cached = await Storage.read(_API_SERVICE_STATUS_KEY)

        if cached:
            checked_at = cached.get("checked_at", 0)
            if time.time() - checked_at < 24 * 3600:
                return cached.get("statuses", {})

        return {}
    except Exception as e:
        log.error("api_services_status.error", {"error": str(e)})
        return {}


@router.post(
    "/api-services/refresh",
    response_model=Dict[str, Any],
    summary="Refresh API service connectivity status",
    description="Manually trigger a refresh of all API service connectivity statuses."
)
async def refresh_api_services_status():
    """Refresh all API service connectivity statuses and cache them."""
    try:
        await Storage.init()

        # Discover API services from both config and tool registry
        config = await Config.get()
        api_services: Dict[str, Any] = (config.model_extra or {}).get("api_services", {})

        # Also discover services from registered tools (covers YAML API tools)
        from flocks.tool.registry import ToolRegistry
        ToolRegistry.init()
        service_ids: set = set(api_services.keys()) | ToolRegistry.get_api_service_ids()

        refreshed_at = int(time.time())
        statuses = {}

        for provider_id in service_ids:
            try:
                result = await test_provider_credentials(provider_id)
                if result.get("success"):
                    statuses[provider_id] = {
                        "status": "connected",
                        "message": result.get("message", "Connected"),
                        "latency_ms": result.get("latency_ms"),
                        "tool_tested": result.get("tool_tested"),
                        "checked_at": refreshed_at,
                    }
                else:
                    statuses[provider_id] = {
                        "status": "error",
                        "message": result.get("message", "Connection failed"),
                        "error": result.get("error"),
                        "checked_at": refreshed_at,
                    }
            except Exception as e:
                statuses[provider_id] = {
                    "status": "error",
                    "message": str(e),
                    "checked_at": refreshed_at,
                }

        await Storage.write(_API_SERVICE_STATUS_KEY, {
            "statuses": statuses,
            "checked_at": refreshed_at,
        })

        log.info("api_services_status.refreshed", {
            "services": list(statuses.keys()),
            "connected": sum(1 for s in statuses.values() if s.get("status") == "connected"),
        })

        return {"statuses": statuses, "refreshed_at": refreshed_at}

    except Exception as e:
        log.error("api_services_status.refresh_error", {"error": str(e)})
        return {"error": str(e)}
