"""
Custom provider & model management — flocks.json backed.

Custom provider = OpenAI-compatible endpoint stored in flocks.json with npm="@ai-sdk/openai-compatible".
Custom model    = model entry in flocks.json under its provider's "models" dict.

All custom providers use the "custom-" ID prefix to distinguish from built-in providers.
Model unique name format: "{provider_id}/{model_id}"
"""

from datetime import UTC, datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from flocks.config.config_writer import ConfigWriter
from flocks.provider.provider import (
    BaseProvider,
    ModelCapabilities,
    ModelInfo,
    Provider,
    ProviderConfig,
)
from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider
from flocks.utils.log import Log


class CustomProvider(OpenAICompatibleProvider):
    """A user-created custom provider backed by flocks.json.

    Properly subclasses OpenAICompatibleProvider instead of monkey-patching
    the get_models method at runtime.
    """

    def __init__(self, provider_id: str, name: str):
        super().__init__()
        self.id = provider_id
        self.name = name
        self._custom_models: List[ModelInfo] = []

    def get_models(self) -> List[ModelInfo]:
        return list(self._custom_models)

router = APIRouter()
log = Log.create(service="routes.custom")


# ==================== Request / Response ====================


class CreateProviderReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., min_length=1)
    api_key: Optional[str] = Field(None)
    description: Optional[str] = Field(None)


class ProviderResp(BaseModel):
    id: str
    name: str
    base_url: str
    description: Optional[str] = None
    created_at: str


class CreateModelReq(BaseModel):
    model_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    context_window: int = Field(128000, ge=1024)
    max_output_tokens: int = Field(4096, ge=1)
    supports_vision: bool = False
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_reasoning: bool = False
    input_price: float = Field(0.0, ge=0)
    output_price: float = Field(0.0, ge=0)
    currency: str = "USD"


class ModelResp(BaseModel):
    id: str
    provider_id: str
    model_id: str
    unique_name: str  # "provider_id/model_id"
    name: str
    context_window: int
    max_output_tokens: int
    input_price: float
    output_price: float
    currency: str
    created_at: str


# ==================== Provider CRUD ====================


@router.get("/providers", response_model=List[ProviderResp])
async def list_providers():
    """List all custom providers from flocks.json."""
    results: List[ProviderResp] = []
    for pid in ConfigWriter.list_provider_ids():
        if not pid.startswith("custom-"):
            continue
        raw = ConfigWriter.get_provider_raw(pid)
        if raw is None:
            continue
        options = raw.get("options", {})
        results.append(ProviderResp(
            id=pid,
            name=raw.get("name", pid),
            base_url=options.get("baseURL", ""),
            description=raw.get("description"),
            created_at=raw.get("created_at", ""),
        ))
    return results


@router.post("/providers", response_model=ProviderResp, status_code=201)
async def create_provider(body: CreateProviderReq):
    """Create a custom OpenAI-compatible provider in flocks.json."""
    pid = "custom-" + body.name.lower().replace(" ", "-").replace("_", "-")

    # Check if already exists
    if ConfigWriter.get_provider_raw(pid) is not None:
        raise HTTPException(409, f"Provider '{pid}' already exists")

    now = datetime.now(UTC).isoformat()

    # Build provider config for flocks.json
    config_dict = ConfigWriter.build_provider_config(
        pid,
        npm="@ai-sdk/openai-compatible",
        base_url=body.base_url,
    )
    config_dict["name"] = body.name
    if body.description:
        config_dict["description"] = body.description
    config_dict["created_at"] = now

    # Write to flocks.json
    ConfigWriter.add_provider(pid, config_dict)

    # Save API key to .secret.json using the LLM provider convention (_llm_key)
    # so it matches the {secret:<pid>_llm_key} reference written by build_provider_config.
    try:
        from flocks.security import get_secret_manager
        secrets = get_secret_manager()
        secrets.set(f"{pid}_llm_key", body.api_key or "not-needed")
    except Exception as e:
        log.warning("custom_provider.secret_save_failed", {"id": pid, "error": str(e)})

    # Register in runtime
    _register_provider(pid, body.name, body.base_url, body.api_key)

    log.info("custom_provider.created", {"id": pid})
    return ProviderResp(
        id=pid, name=body.name, base_url=body.base_url,
        description=body.description, created_at=now,
    )


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: str):
    """Delete a custom provider from flocks.json."""
    raw = ConfigWriter.get_provider_raw(provider_id)
    if raw is None:
        raise HTTPException(404, "Not found")

    ConfigWriter.clear_default_models_for_provider(provider_id)

    # Remove from flocks.json
    ConfigWriter.remove_provider(provider_id)

    # Remove API key from .secret.json (both naming conventions for compatibility)
    try:
        from flocks.security import get_secret_manager
        secrets = get_secret_manager()
        secrets.delete(f"{provider_id}_llm_key")
        secrets.delete(f"{provider_id}_api_key")  # legacy / backward compat
        secrets.delete(f"{provider_id}_base_url")  # legacy
    except Exception:
        pass

    # Remove from runtime
    Provider._providers.pop(provider_id, None)

    log.info("custom_provider.deleted", {"id": provider_id})


# ==================== Model CRUD ====================


@router.get("/models/{provider_id}", response_model=List[ModelResp])
async def list_models(provider_id: str):
    """List models for a custom provider from flocks.json."""
    raw = ConfigWriter.get_provider_raw(provider_id)
    if raw is None:
        raise HTTPException(404, f"Provider '{provider_id}' not found")

    models = raw.get("models", {})
    now_str = raw.get("created_at", "")
    results: List[ModelResp] = []
    for model_id, mcfg in models.items():
        results.append(ModelResp(
            id=model_id,
            provider_id=provider_id,
            model_id=model_id,
            unique_name=f"{provider_id}/{model_id}",
            name=mcfg.get("name", model_id),
            context_window=mcfg.get("context_window", 128000),
            max_output_tokens=mcfg.get("max_output_tokens", 4096),
            input_price=mcfg.get("input_price", 0.0),
            output_price=mcfg.get("output_price", 0.0),
            currency=mcfg.get("currency", "USD"),
            created_at=mcfg.get("created_at", now_str),
        ))
    return results


@router.post("/models/{provider_id}", response_model=ModelResp, status_code=201)
async def create_model(provider_id: str, body: CreateModelReq):
    """Add a model to a provider in flocks.json."""
    raw = ConfigWriter.get_provider_raw(provider_id)
    if raw is None:
        raise HTTPException(404, f"Provider '{provider_id}' not found")

    models = raw.get("models", {})
    existing_model = models.get(body.model_id)

    now = datetime.now(UTC).isoformat()
    model_config = {
        "name": body.name,
        "context_window": body.context_window,
        "max_output_tokens": body.max_output_tokens,
        "supports_vision": body.supports_vision,
        "supports_tools": body.supports_tools,
        "supports_streaming": body.supports_streaming,
        "supports_reasoning": body.supports_reasoning,
        "input_price": body.input_price,
        "output_price": body.output_price,
        "currency": body.currency,
        "created_at": existing_model.get("created_at", now) if existing_model else now,
    }

    # Write to flocks.json (upsert — create or update)
    ConfigWriter.add_model(provider_id, body.model_id, model_config)

    # Add/update runtime
    _add_model_to_runtime(provider_id, body)

    action = "updated" if existing_model else "created"
    log.info(f"custom_model.{action}", {"unique": f"{provider_id}/{body.model_id}"})
    return ModelResp(
        id=body.model_id, provider_id=provider_id, model_id=body.model_id,
        unique_name=f"{provider_id}/{body.model_id}", name=body.name,
        context_window=body.context_window, max_output_tokens=body.max_output_tokens,
        input_price=body.input_price, output_price=body.output_price,
        currency=body.currency, created_at=now,
    )


@router.delete("/models/{provider_id}/{model_id:path}", status_code=204)
async def delete_model(provider_id: str, model_id: str):
    """Remove a model from a provider in flocks.json."""
    removed = ConfigWriter.remove_model(provider_id, model_id)
    if not removed:
        raw = ConfigWriter.get_provider_raw(provider_id)
        if raw is None:
            raise HTTPException(404, f"Provider '{provider_id}' not found")
        raise HTTPException(404, f"Model '{model_id}' not found for provider '{provider_id}'")

    # Remove from runtime
    Provider.remove_model_from_runtime(provider_id, model_id)

    ConfigWriter.clear_default_models_for_model(provider_id, model_id)

    log.info("custom_model.deleted", {"unique": f"{provider_id}/{model_id}"})


# ==================== Runtime helpers ====================


def _register_provider(
    pid: str, name: str, base_url: str, api_key: Optional[str] = None
):
    """Register a custom provider in the runtime Provider registry."""
    if Provider.get(pid):
        return
    p = CustomProvider(provider_id=pid, name=name)
    p._base_url = base_url
    p._api_key = api_key or "not-needed"
    p.configure(ProviderConfig(
        provider_id=pid,
        api_key=api_key or "not-needed",
        base_url=base_url,
    ))
    Provider.register(p)


def _add_model_to_runtime(provider_id: str, body: CreateModelReq):
    """Add (or upsert) a model in the runtime Provider registry.

    Works for both CustomProvider (_custom_models) and DynamicOpenAIProvider
    / OpenAICompatibleProvider (_config_models).
    """
    _pricing = None
    if body.input_price is not None or body.output_price is not None:
        _pricing = {
            "input": float(body.input_price or 0.0),
            "output": float(body.output_price or 0.0),
            "currency": body.currency,
        }
    mi = ModelInfo(
        id=body.model_id,
        name=body.name,
        provider_id=provider_id,
        capabilities=ModelCapabilities(
            supports_streaming=body.supports_streaming,
            supports_tools=body.supports_tools,
            supports_vision=body.supports_vision,
            supports_reasoning=body.supports_reasoning,
            max_tokens=body.max_output_tokens,
            context_window=body.context_window,
        ),
        pricing=_pricing,
    )
    mi._explicit_keys = {
        "name", "context_window", "max_output_tokens",
        "supports_streaming", "supports_tools", "supports_vision",
        "supports_reasoning", "input_price", "output_price", "currency",
    }
    Provider._models[body.model_id] = mi
    p = Provider.get(provider_id)
    if p:
        # CustomProvider (custom-* providers) uses _custom_models
        if hasattr(p, "_custom_models"):
            p._custom_models = [m for m in p._custom_models if m.id != body.model_id]
            p._custom_models.append(mi)
        # DynamicOpenAIProvider (siliconflow, minimax, openai-compatible, etc.) uses _config_models
        if hasattr(p, "_config_models"):
            p._config_models = [m for m in p._config_models if m.id != body.model_id]
            p._config_models.append(mi)


async def load_custom_providers_on_startup():
    """Load custom providers from flocks.json into runtime.

    Reads all providers with npm="@ai-sdk/openai-compatible" and id starting
    with "custom-" from flocks.json, registers them in the Provider runtime.
    """
    from flocks.security import get_secret_manager

    secrets = get_secret_manager()
    provider_ids = ConfigWriter.list_provider_ids()
    loaded_providers = 0
    loaded_models = 0

    for pid in provider_ids:
        raw = ConfigWriter.get_provider_raw(pid)
        if raw is None:
            continue

        # Only process custom providers here; built-in providers are
        # handled by apply_config via the standard Config path.
        if not pid.startswith("custom-"):
            continue

        npm = raw.get("npm", "")
        if npm != "@ai-sdk/openai-compatible":
            continue

        options = raw.get("options", {})
        base_url = options.get("baseURL", "")
        name = raw.get("name", pid)

        # Get API key: try current convention (_llm_key) then legacy (_api_key)
        api_key = secrets.get(f"{pid}_llm_key") or secrets.get(f"{pid}_api_key") or "not-needed"

        _register_provider(pid, name, base_url, api_key)
        loaded_providers += 1

        # Load models from flocks.json
        models = raw.get("models", {})
        for model_id, mcfg in models.items():
            # Skip models marked as disabled (soft-deleted)
            if mcfg.get("disabled"):
                continue
            _input_price = mcfg.get("input_price")
            _output_price = mcfg.get("output_price")
            _pricing = None
            if _input_price is not None or _output_price is not None:
                _pricing = {
                    "input": float(_input_price or 0.0),
                    "output": float(_output_price or 0.0),
                    "currency": mcfg.get("currency", "USD"),
                }
            mi = ModelInfo(
                id=model_id,
                name=mcfg.get("name", model_id),
                provider_id=pid,
                capabilities=ModelCapabilities(
                    supports_streaming=mcfg.get("supports_streaming", True),
                    supports_tools=mcfg.get("supports_tools", True),
                    supports_vision=mcfg.get("supports_vision", False),
                    supports_reasoning=mcfg.get("supports_reasoning", False),
                    max_tokens=mcfg.get("max_output_tokens", 4096),
                    context_window=mcfg.get("context_window", 128000),
                ),
                pricing=_pricing,
            )
            mi._explicit_keys = set(mcfg.keys())
            Provider._models[model_id] = mi
            p = Provider.get(pid)
            if p and hasattr(p, "_custom_models"):
                p._custom_models.append(mi)
            loaded_models += 1

    log.info("custom.loaded", {"providers": loaded_providers, "models": loaded_models})
