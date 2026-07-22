"""
Model Configuration Tools — manage providers and models through conversation.

Provides tools for:
- list_providers: List all configured providers and their models
- add_provider: Add a custom OpenAI-compatible provider
- add_model: Add a model to an existing provider
"""

from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from flocks.tool.registry import (
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolResult,
    ParameterType,
    ToolRegistry,
)
from flocks.utils.log import Log

log = Log.create(service="tool.model_config")


# ==================== list_providers ====================

LIST_PROVIDERS_DESC = """List all configured AI model providers and their models.

Returns provider names, IDs, connection status, and available models.

IMPORTANT: Always call this tool FIRST before using add_provider or add_model,
so you know which providers already exist and their correct IDs.

Use this when:
- The user asks about available models or providers
- Before adding a new provider (to check if it already exists)
- Before adding a model (to find the correct provider_id)"""


@ToolRegistry.register_function(
    name="list_providers",
    description=LIST_PROVIDERS_DESC,
    category=ToolCategory.SYSTEM,
    native=False,
    parameters=[
        ToolParameter(
            name="provider_id",
            type=ParameterType.STRING,
            description="Optional: filter by specific provider ID",
            required=False,
        ),
    ],
)
async def list_providers_tool(
    ctx: ToolContext,
    provider_id: Optional[str] = None,
) -> ToolResult:
    try:
        from flocks.provider.provider import Provider

        Provider._ensure_initialized()

        if provider_id:
            provider = Provider.get(provider_id)
            if not provider:
                return ToolResult(
                    success=False,
                    error=f"Provider '{provider_id}' not found",
                )
            providers_list = [provider]
        else:
            providers_list = [
                Provider.get(pid) for pid in Provider.list_providers()
            ]

        lines: List[str] = []
        for p in providers_list:
            if p is None:
                continue
            configured = p.is_configured()
            models = p.get_models()
            config_models = getattr(p, "_config_models", [])
            all_models = models + [m for m in config_models if m not in models]

            status = "✓ configured" if configured else "✗ not configured"
            lines.append(f"## {p.name} [provider_id={p.id}] — {status}")

            if all_models:
                for m in all_models:
                    caps = []
                    if getattr(m.capabilities, "supports_tools", False):
                        caps.append("tools")
                    if getattr(m.capabilities, "supports_vision", False):
                        caps.append("vision")
                    if getattr(m.capabilities, "supports_streaming", False):
                        caps.append("streaming")
                    ctx_win = getattr(m.capabilities, "context_window", 0)
                    ctx_str = f" ctx={ctx_win // 1000}k" if ctx_win else ""
                    caps_str = f" [{', '.join(caps)}]" if caps else ""
                    lines.append(f"  - {m.id} ({m.name}){ctx_str}{caps_str}")
            else:
                lines.append("  (no models)")
            lines.append("")

        output = "\n".join(lines) if lines else "No providers configured."
        return ToolResult(success=True, output=output)

    except Exception as e:
        log.error("list_providers.error", {"error": str(e)})
        return ToolResult(success=False, error=str(e))


# ==================== add_provider ====================

ADD_PROVIDER_DESC = """Add a NEW custom AI model provider (OpenAI-compatible API endpoint).

IMPORTANT: Only use this to create a BRAND NEW provider that does NOT exist yet.
If the user wants to add a model to an EXISTING provider, use add_model instead.
Always call list_providers first to check if the provider already exists.

The provider will be registered as an OpenAI-compatible endpoint. After adding,
use add_model to add specific models to it.

Example: add a provider named "MyLLM" with base_url "https://api.myllm.com/v1"
"""


@ToolRegistry.register_function(
    name="add_provider",
    description=ADD_PROVIDER_DESC,
    category=ToolCategory.SYSTEM,
    requires_confirmation=True,
    native=False,
    parameters=[
        ToolParameter(
            name="name",
            type=ParameterType.STRING,
            description="Provider display name (e.g. 'My Corp LLM')",
            required=True,
        ),
        ToolParameter(
            name="base_url",
            type=ParameterType.STRING,
            description="API base URL (e.g. 'https://api.example.com/v1')",
            required=True,
        ),
        ToolParameter(
            name="api_key",
            type=ParameterType.STRING,
            description="API key for authentication",
            required=True,
        ),
        ToolParameter(
            name="description",
            type=ParameterType.STRING,
            description="Optional description of the provider",
            required=False,
        ),
    ],
)
async def add_provider_tool(
    ctx: ToolContext,
    name: str,
    base_url: str,
    api_key: str,
    description: Optional[str] = None,
) -> ToolResult:
    try:
        from flocks.config.config_writer import ConfigWriter
        from flocks.provider.provider import Provider, ProviderConfig, ModelInfo
        from flocks.security import get_secret_manager

        pid = "custom-" + name.lower().replace(" ", "-").replace("_", "-")

        if ConfigWriter.get_provider_raw(pid) is not None:
            return ToolResult(
                success=False,
                error=f"Provider '{pid}' already exists. Use a different name.",
            )

        now = datetime.now(UTC).isoformat()

        config_dict = ConfigWriter.build_provider_config(
            pid,
            npm="@ai-sdk/openai-compatible",
            base_url=base_url,
        )
        config_dict["name"] = name
        if description:
            config_dict["description"] = description
        config_dict["created_at"] = now

        ConfigWriter.add_provider(pid, config_dict)

        secrets = get_secret_manager()
        secrets.set(f"{pid}_api_key", api_key)

        from flocks.server.routes.custom_provider import _register_provider
        _register_provider(pid, name, base_url, api_key)

        _publish_provider_event(pid, "added")

        log.info("tool.add_provider.success", {"provider_id": pid})
        return ToolResult(
            success=True,
            output=(
                f"已成功添加供应商 {name}（ID: {pid}）。\n"
                f"Base URL: {base_url}\n"
                f"API Key: {api_key[:4]}***{api_key[-4:]}\n"
                f"接下来请使用 add_model 工具为该供应商添加模型。"
            ),
        )

    except Exception as e:
        log.error("tool.add_provider.error", {"error": str(e)})
        return ToolResult(success=False, error=str(e))


# ==================== add_model ====================

ADD_MODEL_DESC = """Add a model to an EXISTING provider.

Use this when the user wants to add a specific model to a provider that is
already configured.

CRITICAL RULES:

1. provider_id: Pass the EXACT name the user used (e.g. "三方模型").
   The tool resolves it to the correct internal ID automatically.

2. model_id: Pass the COMPLETE model identifier EXACTLY as the user typed it,
   including any prefix before a colon. Do NOT split or remove parts.
   For example, "apigptopen:gpt-4.1" is ONE model_id — do NOT split it
   into "apigptopen" and "gpt-4.1".

Only the provider_id and model_id are required. Other parameters are optional.

Examples:
  - User: "在三方模型下加 apigptopen:gpt-4.1"
    → provider_id="三方模型", model_id="apigptopen:gpt-4.1"
  - User: "在三方模型下加 gpt-4o"
    → provider_id="三方模型", model_id="gpt-4o"
  - User: "给 Anthropic 加 claude-4"
    → provider_id="Anthropic", model_id="claude-4"
"""


@ToolRegistry.register_function(
    name="add_model",
    description=ADD_MODEL_DESC,
    category=ToolCategory.SYSTEM,
    requires_confirmation=True,
    native=False,
    parameters=[
        ToolParameter(
            name="provider_id",
            type=ParameterType.STRING,
            description="Provider name as stated by the user (e.g. '三方模型', 'Anthropic'). The tool resolves it to the correct internal ID automatically.",
            required=True,
        ),
        ToolParameter(
            name="model_id",
            type=ParameterType.STRING,
            description="The COMPLETE model identifier exactly as stated by the user, including any colon prefix (e.g. 'apigptopen:gpt-4.1', 'gpt-4o'). Do NOT split or modify it.",
            required=True,
        ),
        ToolParameter(
            name="name",
            type=ParameterType.STRING,
            description="Display name for the model (defaults to model_id)",
            required=False,
        ),
        ToolParameter(
            name="context_window",
            type=ParameterType.INTEGER,
            description="Context window size in tokens (default: 128000)",
            required=False,
        ),
        ToolParameter(
            name="max_output_tokens",
            type=ParameterType.INTEGER,
            description="Maximum output tokens (default: 4096)",
            required=False,
        ),
        ToolParameter(
            name="supports_tools",
            type=ParameterType.BOOLEAN,
            description="Whether the model supports tool/function calling (default: true)",
            required=False,
        ),
        ToolParameter(
            name="supports_vision",
            type=ParameterType.BOOLEAN,
            description="Whether the model supports image inputs (default: false)",
            required=False,
        ),
    ],
)
async def add_model_tool(
    ctx: ToolContext,
    provider_id: str,
    model_id: str,
    name: Optional[str] = None,
    context_window: int = 128000,
    max_output_tokens: int = 4096,
    supports_tools: bool = True,
    supports_vision: bool = False,
) -> ToolResult:
    try:
        from flocks.config.config_writer import ConfigWriter
        from flocks.provider.provider import (
            Provider,
            ModelCapabilities,
            ModelInfo,
        )

        # Always run fuzzy matching to resolve user-facing names to internal IDs
        candidates = Provider.list_providers()
        resolved = _fuzzy_match_provider(provider_id, candidates)
        if resolved:
            provider_id = resolved

        raw = ConfigWriter.get_provider_raw(provider_id)
        provider = Provider.get(provider_id)
        if raw is None and not provider:
            avail = []
            for c in candidates[:20]:
                p = Provider.get(c)
                n = p.name if p else c
                avail.append(f"  - {c} ({n})")
            return ToolResult(
                success=False,
                error=(
                    f"Provider '{provider_id}' not found.\n"
                    f"Available providers:\n" + "\n".join(avail)
                ),
            )

        existing_models = (raw or {}).get("models", {})
        if model_id in existing_models:
            return ToolResult(
                success=False,
                error=f"Model '{model_id}' already exists in provider '{provider_id}'.",
            )

        display_name = name or model_id
        now = datetime.now(UTC).isoformat()
        model_config: Dict[str, Any] = {
            "name": display_name,
            "context_window": context_window,
            "max_output_tokens": max_output_tokens,
            "supports_vision": supports_vision,
            "supports_tools": supports_tools,
            "supports_streaming": True,
            "supports_reasoning": True,
            "input_price": 0.0,
            "output_price": 0.0,
            "currency": "USD",
            "created_at": now,
        }

        ConfigWriter.add_model(provider_id, model_id, model_config)

        mi = ModelInfo(
            id=model_id,
            name=display_name,
            provider_id=provider_id,
            capabilities=ModelCapabilities(
                supports_streaming=True,
                supports_tools=supports_tools,
                supports_vision=supports_vision,
                max_tokens=max_output_tokens,
                context_window=context_window,
            ),
        )
        Provider._models[model_id] = mi
        p = Provider.get(provider_id)
        if p:
            # CustomProvider uses _custom_models; built-in providers use _config_models
            added = False
            if hasattr(p, "_custom_models"):
                p._custom_models.append(mi)
                added = True
            if hasattr(p, "_config_models"):
                if mi not in p._config_models:
                    p._config_models.append(mi)
                added = True
            if not added:
                log.warning("tool.add_model.no_model_list", {
                    "provider_id": provider_id,
                    "model_id": model_id,
                })

        _publish_provider_event(provider_id, "model_added")

        log.info("tool.add_model.success", {
            "provider_id": provider_id,
            "model_id": model_id,
        })
        provider_name = p.name if p else provider_id
        return ToolResult(
            success=True,
            output=(
                f"已成功将模型 {model_id} 添加到 {provider_name} 供应商。\n"
                f"模型详情：名称={display_name}, 上下文={context_window // 1000}k, "
                f"工具调用={'支持' if supports_tools else '不支持'}, "
                f"视觉={'支持' if supports_vision else '不支持'}。\n"
                f"模型现已可用。"
            ),
        )

    except Exception as e:
        log.error("tool.add_model.error", {"error": str(e)})
        return ToolResult(success=False, error=str(e))


# ==================== helpers ====================


def _fuzzy_match_provider(query: str, candidates: List[str]) -> Optional[str]:
    """Match a user-provided provider name/id to an actual provider_id.

    Always checks name-based matches first, even if query is already a valid ID.
    This ensures "三方模型" resolves to custom-三方模型, not custom-threatbook,
    even if the AI mistakenly passes a different valid provider_id.

    Priority:
    1. Exact display name match   ("三方模型" → provider.name == "三方模型")
    2. Exact custom-<name> prefix ("三方模型" → "custom-三方模型")
    3. Partial name match         ("三方" → provider.name contains "三方")
    4. Exact ID match             ("custom-threatbook" → itself)
    5. Partial ID match           ("threatbook" → "custom-threatbook")
    """
    from flocks.provider.provider import Provider

    q = query.strip()
    ql = q.lower()

    # Build name index
    id_to_name: Dict[str, str] = {}
    for c in candidates:
        p = Provider.get(c)
        id_to_name[c] = (p.name or "").strip() if p else ""

    # 1. Exact name match (case-sensitive)
    for c, name in id_to_name.items():
        if name == q:
            return c

    # 2. Exact name match (case-insensitive)
    for c, name in id_to_name.items():
        if name.lower() == ql:
            return c

    # 3. Exact custom-<query> ID
    if not q.startswith("custom-"):
        guess = f"custom-{q}"
        if guess in candidates:
            return guess

    # 4. Partial name match (prefer shorter = more specific)
    name_matches = []
    for c, name in id_to_name.items():
        if ql in name.lower():
            name_matches.append(c)
    if name_matches:
        name_matches.sort(key=lambda c: len(id_to_name[c]))
        return name_matches[0]

    # 5. Exact ID match (query is already a valid provider_id)
    if q in candidates:
        return q

    # 6. Partial ID match
    for c in candidates:
        if ql in c.lower():
            return c

    return None


def _publish_provider_event(provider_id: str, action: str) -> None:
    """Best-effort publish SSE event to notify frontend of provider changes."""
    try:
        import asyncio
        from flocks.server.routes.event import publish_event

        async def _emit():
            await publish_event("provider.updated", {
                "providerID": provider_id,
                "action": action,
            })

        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_emit())
        else:
            loop.run_until_complete(_emit())
    except Exception:
        pass
