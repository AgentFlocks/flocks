"""
AI Provider management

Manages different AI model providers (Anthropic, OpenAI, Google, etc.)
"""

from typing import Dict, List, Optional, Any, AsyncIterator, Union
from pydantic import BaseModel, Field, PrivateAttr
from enum import Enum
import os

from flocks.utils.log import Log
from flocks.config.config import Config


log = Log.create(service="provider")


class ProviderType(str, Enum):
    """Provider types"""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    AZURE = "azure"
    COHERE = "cohere"
    MISTRAL = "mistral"
    GROQ = "groq"
    TOGETHER = "together"
    LOCAL = "local"
    # Added in Batch 3
    XAI = "xai"
    DEEPINFRA = "deepinfra"
    CEREBRAS = "cerebras"
    PERPLEXITY = "perplexity"
    OPENROUTER = "openrouter"
    BEDROCK = "amazon-bedrock"
    VERTEX = "google-vertex"
    # Added in Batch 5
    GATEWAY = "gateway"
    GITLAB = "gitlab"
    # Added in Batch 6 - Missing providers from Flocks comparison
    GITHUB_COPILOT = "github-copilot"
    GITHUB_COPILOT_ENTERPRISE = "github-copilot-enterprise"
    VERCEL = "vercel"
    OPENCODE = "opencode"
    SAP_AI_CORE = "sap-ai-core"
    CLOUDFLARE_GATEWAY = "cloudflare-ai-gateway"
    # Added in Batch 7 - Final providers
    VERTEX_ANTHROPIC = "google-vertex-anthropic"
    AZURE_COGNITIVE = "azure-cognitive-services"
    ZENMUX = "zenmux"


class ModelCapabilities(BaseModel):
    """Model capabilities"""
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False
    max_tokens: Optional[int] = None
    context_window: Optional[int] = None


class ModelInfo(BaseModel):
    """Model information"""
    id: str = Field(..., description="Model ID (e.g., claude-3-5-sonnet-20241022)")
    name: str = Field(..., description="Human-readable name")
    provider_id: str = Field(..., description="Provider ID")
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    pricing: Optional[Dict[str, Any]] = Field(None, description="Pricing info")
    _explicit_keys: set = PrivateAttr(default_factory=set)
    """Field names explicitly present in flocks.json (not defaults)."""


class ProviderConfig(BaseModel):
    """Provider configuration"""
    provider_id: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    enabled: bool = True
    custom_settings: Dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    """Chat message"""
    role: str = Field(..., description="Message role (user, assistant, system, tool)")
    content: Union[str, List[Dict[str, Any]]] = Field("", description="Message content")
    # Reasoning/Thinking content (optional)
    reasoning: Optional[str] = Field(None, description="Reasoning or thinking content")
    # OpenAI function-calling fields (optional)
    tool_calls: Optional[List[Dict[str, Any]]] = Field(None, description="Tool calls for assistant messages")
    tool_call_id: Optional[str] = Field(None, description="Tool call ID for tool-result messages")
    name: Optional[str] = Field(None, description="Tool name for tool-result messages")
    custom_settings: Dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    """Chat completion request"""
    model: str = Field(..., description="Model ID")
    messages: List[ChatMessage] = Field(..., description="Conversation messages")
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = None
    stream: bool = False
    tools: Optional[List[Dict[str, Any]]] = None


class ChatResponse(BaseModel):
    """Chat completion response"""
    id: str
    model: str
    content: str
    finish_reason: str
    usage: Dict[str, int]
    tool_calls: Optional[List[Dict[str, Any]]] = None
    reasoning: Optional[str] = None


class ToolCallInfo(BaseModel):
    """Tool call information from LLM response"""
    id: str
    type: str = "function"
    function: Dict[str, Any]  # {"name": str, "arguments": str}


class StreamChunk(BaseModel):
    """
    Streaming response chunk
    
    Extended to support Flocks-style event types for richer streaming.
    """
    # Basic fields (backward compatible)
    delta: str = ""
    finish_reason: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    
    # Extended fields for Flocks-style events
    event_type: Optional[str] = None  # "text", "reasoning", "tool-input", "tool-call", etc.
    reasoning: Optional[str] = None  # Reasoning/thinking content
    tool_input: Optional[Dict[str, Any]] = None  # Incremental tool input
    metadata: Optional[Dict[str, Any]] = None  # Provider-specific metadata
    usage: Optional[Dict[str, int]] = None  # Token usage from provider (prompt_tokens, completion_tokens, total_tokens)


class Provider:
    """
    Provider management namespace
    
    Similar to Flocks's Provider namespace
    """
    
    # Registry of providers
    _providers: Dict[str, "BaseProvider"] = {}
    _models: Dict[str, ModelInfo] = {}
    _initialized = False
    
    @classmethod
    async def init(cls) -> None:
        """Initialize provider system"""
        if cls._initialized:
            return

        # Delegate to _ensure_initialized() so that the full provider set
        # (all built-ins + dynamic providers from flocks.json) is registered.
        # Previously this only loaded 3 providers, which caused dynamic providers
        # like minimax/siliconflow (defined in flocks.json) to be silently missing
        # in CLI mode because the shared _initialized flag blocked lazy init later.
        cls._ensure_initialized()

        log.info("provider.initialized", {
            "provider_count": len(cls._providers),
            "model_count": len(cls._models),
        })
    
    @classmethod
    def register(cls, provider: "BaseProvider") -> None:
        """Register a provider"""
        cls._providers[provider.id] = provider
        
        # Register models
        for model in provider.get_models():
            cls._models[model.id] = model
        
        log.debug("provider.registered", {
            "provider_id": provider.id,
            "model_count": len(provider.get_models()),
        })
    
    @classmethod
    def _ensure_initialized(cls):
        """Ensure providers are initialized"""
        if not cls._initialized:
            cls._initialized = True
            
            # Auto-register built-in providers (Batch 1+2)
            providers_to_register = [
                ("openai", "flocks.provider.sdk.openai", "OpenAIProvider"),
                ("anthropic", "flocks.provider.sdk.anthropic", "AnthropicProvider"),
                ("google", "flocks.provider.sdk.google", "GoogleProvider"),
                ("azure", "flocks.provider.sdk.azure", "AzureProvider"),
                ("openai-compatible", "flocks.provider.sdk.openai_compatible", "OpenAICompatibleProvider"),
                ("mistral", "flocks.provider.sdk.mistral", "MistralProvider"),
                ("groq", "flocks.provider.sdk.groq", "GroqProvider"),
                ("cohere", "flocks.provider.sdk.cohere", "CohereProvider"),
                ("together", "flocks.provider.sdk.together", "TogetherProvider"),
                # Added in Batch 3
                ("xai", "flocks.provider.sdk.xai", "XAIProvider"),
                ("deepinfra", "flocks.provider.sdk.deepinfra", "DeepInfraProvider"),
                ("cerebras", "flocks.provider.sdk.cerebras", "CerebrasProvider"),
                ("perplexity", "flocks.provider.sdk.perplexity", "PerplexityProvider"),
                ("openrouter", "flocks.provider.sdk.openrouter", "OpenRouterProvider"),
                ("amazon-bedrock", "flocks.provider.sdk.bedrock", "BedrockProvider"),
                ("google-vertex", "flocks.provider.sdk.vertex", "VertexProvider"),
                ("local", "flocks.provider.sdk.local", "LocalProvider"),
                # Added in Batch 5
                ("gateway", "flocks.provider.sdk.gateway", "GatewayProvider"),
                ("gitlab", "flocks.provider.sdk.gitlab", "GitLabProvider"),
                # Added in Batch 6 - Missing providers from Flocks comparison
                ("github-copilot", "flocks.provider.sdk.github_copilot", "GitHubCopilotProvider"),
                ("github-copilot-enterprise", "flocks.provider.sdk.github_copilot", "GitHubCopilotEnterpriseProvider"),
                ("vercel", "flocks.provider.sdk.vercel", "VercelProvider"),
                ("opencode", "flocks.provider.sdk.opencode", "FlocksCompatProvider"),
                ("sap-ai-core", "flocks.provider.sdk.sap_ai_core", "SAPAICoreProvider"),
                ("cloudflare-ai-gateway", "flocks.provider.sdk.cloudflare_gateway", "CloudflareGatewayProvider"),
                # Added in Batch 7 - Final providers
                ("google-vertex-anthropic", "flocks.provider.sdk.vertex_anthropic", "VertexAnthropicProvider"),
                ("azure-cognitive-services", "flocks.provider.sdk.azure_cognitive", "AzureCognitiveServicesProvider"),
                ("zenmux", "flocks.provider.sdk.zenmux", "ZenMuxProvider"),
                # Chinese providers
                ("deepseek", "flocks.provider.sdk.deepseek", "DeepSeekProvider"),
                ("volcengine", "flocks.provider.sdk.volcengine", "VolcengineProvider"),
                ("alibaba", "flocks.provider.sdk.alibaba", "AlibabaProvider"),
                ("tencent", "flocks.provider.sdk.tencent", "TencentProvider"),
                ("siliconflow", "flocks.provider.sdk.siliconflow", "SiliconFlowProvider"),
                ("threatbook-cn-llm", "flocks.provider.sdk.threatbook", "ThreatBookCnLLMProvider"),
                ("threatbook-io-llm", "flocks.provider.sdk.threatbook", "ThreatBookIoLLMProvider"),
                ("ollama", "flocks.provider.sdk.ollama", "OllamaProvider"),
                # Client-side tool calling (for backends without --enable-auto-tool-choice)
                ("cherry", "flocks.provider.sdk.cherry", "CherryProvider"),
            ]
            
            for provider_id, module_name, class_name in providers_to_register:
                try:
                    module = __import__(module_name, fromlist=[class_name])
                    provider_class = getattr(module, class_name)
                    cls.register(provider_class())
                    log.debug("provider.auto_registered", {"provider": provider_id})
                except Exception as e:
                    log.warning("provider.register.failed", {"provider": provider_id, "error": str(e)})
            
            # Load dynamic providers from flocks.json
            cls._load_dynamic_providers()
    
    @classmethod
    def _load_dynamic_providers(cls):
        """
        Load dynamic providers from flocks.json.

        Supports providers configured with:
          - npm: "@ai-sdk/openai-compatible"  – generic OpenAI-compatible endpoint
          - npm: "@ai-sdk/azure"              – Azure OpenAI endpoint

        Can be called multiple times safely; already-registered providers are
        skipped so newly-created providers are picked up without restarting.
        """
        try:
            from flocks.config.config_writer import ConfigWriter
            
            # Get all provider configs from flocks.json
            providers_config = ConfigWriter.get_all_providers()
            if not providers_config:
                return
            
            for provider_id, config in providers_config.items():
                # Skip if already registered
                if provider_id in cls._providers:
                    continue
                
                npm_package = config.get("npm", "")
                options = config.get("options", {})
                base_url = options.get("baseURL") or options.get("base_url")

                if npm_package == "@ai-sdk/openai-compatible":
                    # Create dynamic OpenAI-compatible provider instance
                    try:
                        from flocks.provider.sdk.openai_base import OpenAIBaseProvider
                        
                        # Capture loop variables for the class body
                        _pid = provider_id
                        _cfg = config
                        _base_url = base_url

                        class DynamicOpenAIProvider(OpenAIBaseProvider):
                            """Dynamically created OpenAI-compatible provider."""
                            
                            DEFAULT_BASE_URL = _base_url or ""
                            ENV_API_KEY = [f"{_pid.upper().replace('-', '_')}_API_KEY"]
                            ENV_BASE_URL = f"{_pid.upper().replace('-', '_')}_BASE_URL"
                            CATALOG_ID = ""
                            
                            def __init__(self):
                                super().__init__(
                                    provider_id=_pid,
                                    name=_cfg.get("name", _pid)
                                )
                                if not self._api_key:
                                    try:
                                        from flocks.provider.credential import get_api_key
                                        secret_key = get_api_key(_pid)
                                        if secret_key:
                                            self._api_key = secret_key
                                    except Exception:
                                        pass
                        
                        provider_instance = DynamicOpenAIProvider()
                        cls.register(provider_instance)
                        log.info("provider.dynamic_loaded", {
                            "provider_id": provider_id,
                            "base_url": base_url,
                            "configured": provider_instance.is_configured()
                        })
                        
                    except Exception as e:
                        log.warning("provider.dynamic_load_failed", {
                            "provider_id": provider_id,
                            "error": str(e)
                        })

                elif npm_package == "@ai-sdk/cherry":
                    try:
                        from flocks.provider.sdk.cherry import CherryProvider

                        _pid = provider_id
                        _cfg = config
                        _base_url = base_url

                        class DynamicCherryProvider(CherryProvider):
                            """Dynamically created Cherry provider (client-side tool calling)."""

                            def __init__(self):
                                super().__init__()
                                self.id = _pid
                                self.name = _cfg.get("name", _pid)
                                if _base_url:
                                    self._base_url = _base_url
                                if not self._api_key:
                                    try:
                                        from flocks.provider.credential import get_api_key
                                        secret_key = get_api_key(_pid)
                                        if secret_key:
                                            self._api_key = secret_key
                                    except Exception:
                                        pass

                        provider_instance = DynamicCherryProvider()
                        cls.register(provider_instance)
                        log.info("provider.dynamic_cherry_loaded", {
                            "provider_id": provider_id,
                            "base_url": base_url,
                            "configured": provider_instance.is_configured()
                        })

                    except Exception as e:
                        log.warning("provider.dynamic_cherry_load_failed", {
                            "provider_id": provider_id,
                            "error": str(e)
                        })

                elif npm_package == "@ai-sdk/azure":
                    # Create dynamic Azure OpenAI provider instance.
                    # Uses AzureProvider's chat/stream logic but with the
                    # provider_id from flocks.json (e.g. "azure-openai").
                    try:
                        from flocks.provider.sdk.azure import AzureProvider

                        _pid = provider_id
                        _cfg = config
                        _base_url = base_url

                        class DynamicAzureProvider(AzureProvider):
                            """Dynamically created Azure OpenAI provider."""

                            def __init__(self):
                                # Bypass AzureProvider.__init__ which hard-codes
                                # provider_id="azure"; call BaseProvider directly.
                                import os
                                BaseProvider.__init__(self, provider_id=_pid, name=_cfg.get("name", _pid))
                                self._endpoint = _base_url or os.getenv("AZURE_OPENAI_ENDPOINT")
                                self._client = None
                                # Prefer secret manager, fall back to env var
                                self._api_key = None
                                try:
                                    from flocks.provider.credential import get_api_key
                                    self._api_key = get_api_key(_pid)
                                except Exception:
                                    pass
                                if not self._api_key:
                                    self._api_key = os.getenv("AZURE_OPENAI_API_KEY")

                        provider_instance = DynamicAzureProvider()
                        cls.register(provider_instance)
                        log.info("provider.dynamic_azure_loaded", {
                            "provider_id": provider_id,
                            "base_url": base_url,
                            "configured": provider_instance.is_configured()
                        })

                    except Exception as e:
                        log.warning("provider.dynamic_azure_load_failed", {
                            "provider_id": provider_id,
                            "error": str(e)
                        })
                    
        except Exception as e:
            log.debug("provider.dynamic_load_skipped", {"error": str(e)})
    
    @classmethod
    def get(cls, provider_id: str) -> Optional["BaseProvider"]:
        """Get a provider by ID"""
        cls._ensure_initialized()
        return cls._providers.get(provider_id)

    @classmethod
    def remove_model_from_runtime(cls, provider_id: str, model_id: str) -> None:
        """Remove a model from runtime caches (both global registry and provider instance)."""
        cls._models.pop(model_id, None)
        p = cls.get(provider_id)
        if p:
            if hasattr(p, "_custom_models"):
                p._custom_models = [m for m in p._custom_models if m.id != model_id]
            if hasattr(p, "_config_models"):
                p._config_models = [m for m in p._config_models if m.id != model_id]
    
    @classmethod
    def get_model(cls, model_id: str) -> Optional[ModelInfo]:
        """Get model info by ID"""
        return cls._models.get(model_id)
    
    @classmethod
    def resolve_model_info(cls, provider_id: str, model_id: str) -> tuple:
        """
        Resolve context_window, max_output_tokens, and max_input_tokens for a
        provider/model pair.
        
        Lookup order:
          1. provider._config_models (models loaded from flocks.json)
          2. Provider._models global registry (via Provider.get_model)
        
        Returns:
            (context_window, max_output_tokens, max_input_tokens) tuple.
            max_input_tokens may be None when the API does not advertise an
            explicit input limit; callers should fall back to
            ``context_window - max_output_tokens``.
        """
        context_window = 0
        max_output = 0
        max_input = None
        
        try:
            model_info = None
            
            # 1. Check provider._config_models first (flocks.json models)
            provider = cls.get(provider_id)
            if provider:
                for m in getattr(provider, "_config_models", []):
                    if m.id == model_id:
                        model_info = m
                        break
            
            # 2. Fallback to global model registry
            if model_info is None:
                model_info = cls.get_model(model_id)
            
            if model_info and hasattr(model_info, 'capabilities') and model_info.capabilities:
                context_window = getattr(model_info.capabilities, 'context_window', 0) or 0
                max_output = getattr(model_info.capabilities, 'max_tokens', 0) or 0
            
            # Check for explicit max_input_tokens (from ModelLimits / ModelDefinition)
            limits = getattr(model_info, 'limits', None) if model_info else None
            if limits is not None:
                max_input = getattr(limits, 'max_input_tokens', None)
                if not context_window:
                    context_window = getattr(limits, 'context_window', 0) or 0
                if not max_output:
                    max_output = getattr(limits, 'max_output_tokens', 0) or 0
            
            if context_window > 0:
                log.debug("provider.model_info_resolved", {
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "context_window": context_window,
                    "max_output_tokens": max_output,
                    "max_input_tokens": max_input,
                })
        except Exception as e:
            log.warn("provider.model_info_resolve_error", {
                "provider_id": provider_id,
                "model_id": model_id,
                "error": str(e),
            })
        
        # Default to 128K context / 4096 output if not resolved
        if context_window <= 0:
            context_window = 128_000
            max_output = max_output or 4096
            log.info("provider.model_info_default", {
                "provider_id": provider_id,
                "model_id": model_id,
                "context_window": context_window,
                "max_output_tokens": max_output,
            })
        
        return context_window, max_output, max_input
    
    @classmethod
    def list_providers(cls) -> List[str]:
        """List all registered providers"""
        cls._ensure_initialized()
        return list(cls._providers.keys())
    
    @classmethod
    def list_models(cls, provider_id: Optional[str] = None) -> List[ModelInfo]:
        """List all models, optionally filtered by provider"""
        cls._ensure_initialized()
        if provider_id:
            # Get models directly from provider to avoid _models dict conflicts
            provider = cls._providers.get(provider_id)
            if provider:
                return provider.get_models()
            return []
        # Return all models from all providers
        all_models = []
        for provider in cls._providers.values():
            all_models.extend(provider.get_models())
        return all_models

    @classmethod
    async def apply_config(cls, config: Optional[Any] = None, provider_id: Optional[str] = None) -> None:
        """
        Apply provider configuration from Config to registered providers.

        This configures provider instances with api_key/base_url so TUI/Server
        reads take effect without requiring env vars.
        """
        cls._ensure_initialized()

        if config is None:
            config = await Config.get()

        provider_configs = getattr(config, "provider", None) or {}

        for pid, pconfig in provider_configs.items():
            if provider_id and pid != provider_id:
                continue

            provider = cls.get(pid)
            if not provider:
                continue

            options = getattr(pconfig, "options", None)
            if not options:
                continue

            if hasattr(options, "model_dump"):
                options_data = options.model_dump(exclude_none=True, by_alias=False)
            elif isinstance(options, dict):
                options_data = {k: v for k, v in options.items() if v is not None}
            else:
                continue

            # Handle both Python-style (api_key, base_url) and JS-style (apiKey, baseURL)
            api_key = (
                options_data.pop("api_key", None)
                or options_data.pop("apiKey", None)
            )
            base_url = (
                options_data.pop("base_url", None)
                or options_data.pop("baseURL", None)
            )

            # Treat empty strings as None (e.g. unresolved {secret:xxx})
            if isinstance(api_key, str) and not api_key.strip():
                api_key = None
            if isinstance(base_url, str) and not base_url.strip():
                base_url = None

            # Also filter out remaining options that resolved to empty strings
            options_data = {
                k: v for k, v in options_data.items()
                if not (isinstance(v, str) and not v.strip())
            }

            if api_key is None and base_url is None and not options_data:
                continue

            provider.configure(ProviderConfig(
                provider_id=pid,
                api_key=api_key,
                base_url=base_url,
                custom_settings=options_data,
            ))

            # Update provider display name from flocks.json only for providers
            # that support custom naming (openai-compatible instances and custom-* providers).
            # Standard catalog providers (anthropic, openai, etc.) always keep their SDK name.
            if pid == "openai-compatible" or pid.startswith("custom-"):
                config_name = getattr(pconfig, "name", None)
                if config_name and isinstance(config_name, str):
                    provider.name = config_name
            
            # Load models from config
            models_config = getattr(pconfig, "models", None)
            if models_config:
                provider._config_models = []
                if isinstance(models_config, dict):
                    for model_id, model_data in models_config.items():
                        try:
                            # Handle both dict and object formats
                            if hasattr(model_data, "model_dump"):
                                model_dict = model_data.model_dump()
                            elif isinstance(model_data, dict):
                                model_dict = model_data
                            else:
                                continue

                            # Track which fields the user explicitly set in flocks.json
                            _explicit_keys = set(model_dict.keys())

                            # Create ModelInfo from config
                            _input_price = model_dict.get("input_price")
                            _output_price = model_dict.get("output_price")
                            _pricing = None
                            if _input_price is not None or _output_price is not None:
                                _pricing = {
                                    "input": float(_input_price or 0.0),
                                    "output": float(_output_price or 0.0),
                                    "currency": model_dict.get("currency", "USD"),
                                }
                            model_info = ModelInfo(
                                id=model_id,
                                name=model_dict.get("name", model_id),
                                provider_id=pid,
                                capabilities=ModelCapabilities(
                                    supports_streaming=model_dict.get("supports_streaming", True),
                                    supports_tools=model_dict.get("supports_tools", True),
                                    supports_vision=model_dict.get("supports_vision", False),
                                    supports_reasoning=model_dict.get("supports_reasoning", False),
                                    max_tokens=model_dict.get("max_output_tokens") or model_dict.get("max_tokens"),
                                    context_window=model_dict.get("context_window"),
                                ),
                                pricing=_pricing,
                            )
                            model_info._explicit_keys = _explicit_keys
                            provider._config_models.append(model_info)
                        except Exception as e:
                            log.warning("provider.config_model.parse_failed", {
                                "provider_id": pid,
                                "model_id": model_id,
                                "error": str(e)
                            })
                
                if provider._config_models:
                    log.info("provider.config_models.loaded", {
                        "provider_id": pid,
                        "count": len(provider._config_models)
                    })
    
    @classmethod
    async def chat(
        cls,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> ChatResponse:
        """
        Send a chat completion request.

        Args:
            model_id: Model ID
            messages: Conversation messages
            **kwargs: Additional parameters

        Returns:
            Chat response
        """
        model = cls.get_model(model_id)
        if not model:
            raise ValueError(f"Model {model_id} not found")

        provider = cls.get(model.provider_id)
        if not provider:
            raise ValueError(f"Provider {model.provider_id} not found")

        return await provider.chat(model_id, messages, **kwargs)

    @classmethod
    async def chat_stream(
        cls,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> AsyncIterator[StreamChunk]:
        """
        Send a streaming chat completion request.

        Args:
            model_id: Model ID
            messages: Conversation messages
            **kwargs: Additional parameters

        Yields:
            Stream chunks
        """
        model = cls.get_model(model_id)
        if not model:
            raise ValueError(f"Model {model_id} not found")

        provider = cls.get(model.provider_id)
        if not provider:
            raise ValueError(f"Provider {model.provider_id} not found")

        async for chunk in provider.chat_stream(model_id, messages, **kwargs):
            yield chunk
    
    @classmethod
    async def embed(
        cls,
        text: str,
        provider_id: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> List[float]:
        """
        Generate embedding using specified provider
        
        Args:
            text: Input text
            provider_id: Provider ID (optional, will use first available)
            model: Model ID (optional)
            **kwargs: Provider-specific parameters
            
        Returns:
            Embedding vector
        """
        cls._ensure_initialized()
        
        # If no provider specified, try OpenAI first, then Google
        if provider_id is None:
            for pid in ["openai", "google"]:
                provider = cls.get(pid)
                if provider and provider.supports_embeddings():
                    provider_id = pid
                    break
        
        if provider_id is None:
            raise ValueError("No embedding provider available")
        
        provider = cls.get(provider_id)
        if provider is None:
            raise ValueError(f"Provider {provider_id} not found")
        
        if not provider.supports_embeddings():
            raise ValueError(f"Provider {provider_id} does not support embeddings")
        
        return await provider.embed(text, model=model, **kwargs)
    
    @classmethod
    async def embed_batch(
        cls,
        texts: List[str],
        provider_id: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> List[List[float]]:
        """
        Batch embeddings using specified provider
        
        Args:
            texts: List of input texts
            provider_id: Provider ID (optional)
            model: Model ID (optional)
            **kwargs: Provider-specific parameters
            
        Returns:
            List of embedding vectors
        """
        cls._ensure_initialized()
        
        # If no provider specified, try OpenAI first, then Google
        if provider_id is None:
            for pid in ["openai", "google"]:
                provider = cls.get(pid)
                if provider and provider.supports_embeddings():
                    provider_id = pid
                    break
        
        if provider_id is None:
            raise ValueError("No embedding provider available")
        
        provider = cls.get(provider_id)
        if provider is None:
            raise ValueError(f"Provider {provider_id} not found")
        
        if not provider.supports_embeddings():
            raise ValueError(f"Provider {provider_id} does not support embeddings")
        
        return await provider.embed_batch(texts, model=model, **kwargs)


class BaseProvider:
    """Base class for AI providers"""

    # Subclasses set this to their catalog.json ID to enable metadata enrichment
    # in get_model_definitions(). Leave empty for providers without a catalog entry.
    CATALOG_ID: str = ""

    def __init__(self, provider_id: str, name: str):
        self.id = provider_id
        self.name = name
        self._config: Optional[ProviderConfig] = None
        self._config_models: List[ModelInfo] = []  # Models from config
        self.log = Log.create(service=f"provider-{provider_id}")
    
    def configure(self, config: ProviderConfig) -> None:
        """Configure the provider"""
        self._config = config
        self.log.info("provider.configured", {
            "provider_id": self.id,
            "has_api_key": config.api_key is not None,
        })
    
    def is_configured(self) -> bool:
        """Check if provider is configured.

        Credentials are resolved at config load time via {secret:xxx} or
        {env:VAR} references in flocks.json, then applied via configure().
        """
        return self._config is not None and self._config.api_key is not None
    
    def get_models(self) -> List[ModelInfo]:
        """Get list of available models"""
        raise NotImplementedError
    
    async def chat(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> ChatResponse:
        """Send chat completion request"""
        raise NotImplementedError
    
    async def chat_stream(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> AsyncIterator[StreamChunk]:
        """Send streaming chat completion request"""
        raise NotImplementedError
        yield  # Make this a generator
    
    # ==================== Embeddings Support (for memory system) ====================
    
    async def embed(
        self,
        text: str,
        model: Optional[str] = None,
        **kwargs
    ) -> List[float]:
        """
        Generate embedding for a single text
        
        Args:
            text: Input text
            model: Model ID (optional, use default if not specified)
            **kwargs: Provider-specific parameters
            
        Returns:
            List of floats (embedding vector)
            
        Raises:
            NotImplementedError: If provider doesn't support embeddings
        """
        raise NotImplementedError(
            f"Provider {self.id} does not support embeddings"
        )
    
    async def embed_batch(
        self,
        texts: List[str],
        model: Optional[str] = None,
        batch_size: Optional[int] = None,
        **kwargs
    ) -> List[List[float]]:
        """
        Generate embeddings for multiple texts (batch)
        
        Args:
            texts: List of input texts
            model: Model ID (optional)
            batch_size: Batch size for processing (optional)
            **kwargs: Provider-specific parameters
            
        Returns:
            List of embedding vectors
        """
        # Default implementation: call embed() sequentially
        embeddings = []
        for text in texts:
            embedding = await self.embed(text, model=model, **kwargs)
            embeddings.append(embedding)
        return embeddings
    
    def supports_embeddings(self) -> bool:
        """Check if provider supports embeddings"""
        try:
            # Check if embed method is overridden
            import inspect
            method = getattr(self.__class__, 'embed', None)
            if method is None:
                return False
            # Check if it's the base implementation
            return method != BaseProvider.embed
        except Exception:
            return False
    
    def get_embedding_models(self) -> List[str]:
        """Get list of available embedding models"""
        return []

    # ==================== Model Management Extensions ====================

    def get_meta(self) -> "ProviderMeta":
        """
        Return provider metadata.

        Subclasses should override to provide accurate metadata including
        supported auth methods, model types, and credential schemas.
        Default implementation returns basic metadata.
        """
        from flocks.provider.types import (
            AuthMethod,
            ConfigurateMethod,
            CredentialFieldSchema,
            CredentialSchema,
            ModelType,
            ProviderMeta,
        )
        return ProviderMeta(
            id=self.id,
            name=self.name,
            supported_auth_methods=[AuthMethod.API_KEY],
            supported_model_types=[ModelType.LLM],
            configurate_methods=[ConfigurateMethod.PREDEFINED_MODEL],
            credential_schemas=[
                CredentialSchema(
                    auth_method=AuthMethod.API_KEY,
                    fields=[
                        CredentialFieldSchema(
                            name="api_key",
                            label="API Key",
                            type="secret",
                            required=True,
                            placeholder="sk-...",
                        ),
                        CredentialFieldSchema(
                            name="base_url",
                            label="Base URL",
                            type="text",
                            required=False,
                            placeholder="https://api.example.com/v1",
                        ),
                    ],
                ),
            ],
        )

    def _build_model_definition(self, model: "ModelInfo") -> "ModelDefinition":
        """Build a ModelDefinition from a ModelInfo (config-only fallback)."""
        from flocks.provider.types import (
            FetchFrom,
            ModelCapabilitiesV2,
            ModelDefinition,
            ModelLimits,
            ParameterRule,
            ParameterType,
            PriceConfig,
        )
        pricing = None
        if model.pricing:
            pricing = PriceConfig(
                input=model.pricing.get("input", 0.0),
                output=model.pricing.get("output", 0.0),
                currency=model.pricing.get("currency", "USD"),
            )
        max_output = model.capabilities.max_tokens or 4096
        return ModelDefinition(
            id=model.id,
            name=model.name,
            provider_id=model.provider_id,
            fetch_from=FetchFrom.CUSTOMIZABLE,
            capabilities=ModelCapabilitiesV2(
                supports_streaming=model.capabilities.supports_streaming,
                supports_tools=model.capabilities.supports_tools,
                supports_vision=model.capabilities.supports_vision,
                supports_reasoning=getattr(model.capabilities, "supports_reasoning", False),
            ),
            limits=ModelLimits(
                context_window=model.capabilities.context_window or 128000,
                max_output_tokens=max_output,
            ),
            pricing=pricing,
            parameter_rules=[
                ParameterRule(
                    name="temperature", label="Temperature", type=ParameterType.FLOAT,
                    default=1.0, min=0.0, max=2.0, precision=2,
                    help_text="Controls randomness. Lower = more deterministic.",
                ),
                ParameterRule(
                    name="top_p", label="Top P", type=ParameterType.FLOAT,
                    default=1.0, min=0.0, max=1.0, precision=2,
                    help_text="Nucleus sampling threshold.",
                ),
                ParameterRule(
                    name="max_tokens", label="Max Tokens", type=ParameterType.INT,
                    default=min(4096, max_output), min=1, max=max_output,
                    help_text="Maximum number of output tokens.",
                ),
            ],
        )

    def _apply_config_overrides(self, catalog_def: "ModelDefinition", model: "ModelInfo") -> "ModelDefinition":
        """Apply user overrides from flocks.json (_config_models) on top of catalog definition.

        Only fields explicitly present in flocks.json are overridden; fields the user
        never touched keep their richer catalog values.  This avoids e.g. a catalog
        ``supports_reasoning=True`` being silently reset to ``False`` by a default.
        """
        from flocks.provider.types import PriceConfig

        overridden = catalog_def.model_copy(deep=True)
        keys = getattr(model, "_explicit_keys", set())

        # Display name — always override (every flocks.json entry has "name")
        if "name" in keys:
            overridden.name = model.name

        # Capabilities — only override fields explicitly stored in flocks.json
        if "supports_streaming" in keys:
            overridden.capabilities.supports_streaming = model.capabilities.supports_streaming
        if "supports_tools" in keys:
            overridden.capabilities.supports_tools = model.capabilities.supports_tools
        if "supports_vision" in keys:
            overridden.capabilities.supports_vision = model.capabilities.supports_vision
        if "supports_reasoning" in keys:
            overridden.capabilities.supports_reasoning = getattr(
                model.capabilities, "supports_reasoning", False
            )

        # Limits — only override when explicitly stored
        if "context_window" in keys and model.capabilities.context_window is not None:
            overridden.limits.context_window = model.capabilities.context_window
        if ("max_output_tokens" in keys or "max_tokens" in keys) and model.capabilities.max_tokens is not None:
            overridden.limits.max_output_tokens = model.capabilities.max_tokens

        # Pricing — only override when pricing fields are present
        if model.pricing:
            overridden.pricing = PriceConfig(
                input=model.pricing.get("input", 0.0),
                output=model.pricing.get("output", 0.0),
                currency=model.pricing.get("currency", "USD"),
            )

        return overridden

    def get_model_definitions(self) -> List["ModelDefinition"]:
        """Return model definitions for models in flocks.json (_config_models).

        If CATALOG_ID is set, catalog.json is used as a metadata source: models
        whose ID appears in the catalog get the richer catalog entry (parameter_rules,
        release_date, etc.); all others fall back to the config data in flocks.json.
        flocks.json is the single source of truth for *which* models are listed.
        User-edited values in flocks.json always override catalog defaults.
        """
        catalog_by_id: dict = {}
        if self.CATALOG_ID:
            try:
                from flocks.provider.model_catalog import get_provider_model_definitions
                defs = get_provider_model_definitions(self.CATALOG_ID)
                if defs:
                    catalog_by_id = {d.id: d for d in defs}
            except Exception:
                pass

        result = []
        for model in getattr(self, "_config_models", []):
            if model.id in catalog_by_id:
                # Catalog provides rich metadata (parameter_rules, release_date, …)
                # but user edits stored in flocks.json always take precedence.
                result.append(self._apply_config_overrides(catalog_by_id[model.id], model))
            else:
                result.append(self._build_model_definition(model))
        return result

    async def validate_credential(
        self, config: "CredentialConfig"
    ) -> "CredentialValidateResult":
        """
        Validate credential configuration by making a test API call.

        Subclasses should override to implement provider-specific validation.
        Default implementation tries a simple model listing or minimal API call
        using the provided api_key.

        Args:
            config: Decrypted credential config to validate.

        Returns:
            Validation result with success/error and latency.
        """
        import time
        from flocks.provider.types import CredentialValidateResult

        start = time.monotonic()
        try:
            # Default: configure with the provided key and try to list models
            test_config = ProviderConfig(
                provider_id=self.id,
                api_key=config.api_key,
                base_url=config.base_url,
            )
            # Save and restore original config
            original_config = self._config
            try:
                self.configure(test_config)
                models = self.get_models()
                latency = int((time.monotonic() - start) * 1000)
                return CredentialValidateResult(
                    valid=True,
                    latency_ms=latency,
                    model_count=len(models),
                )
            finally:
                self._config = original_config
        except Exception as e:
            latency = int((time.monotonic() - start) * 1000)
            return CredentialValidateResult(
                valid=False,
                latency_ms=latency,
                error=str(e),
            )

    def configure_from_credential(self, config: "CredentialConfig") -> None:
        """
        Configure the provider from a decrypted CredentialConfig.

        This applies credential fields (api_key, base_url, etc.) to the
        provider. Subclasses may override for special auth methods
        (e.g. subscription tokens, OAuth).

        Args:
            config: Decrypted credential configuration.
        """
        provider_config = ProviderConfig(
            provider_id=self.id,
            api_key=config.api_key,
            base_url=config.base_url,
            custom_settings=config.extra,
        )
        self.configure(provider_config)
