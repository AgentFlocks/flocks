"""
AI Gateway Provider SDK

Provides a unified gateway for routing requests to multiple AI providers.
This is useful for load balancing, failover, and unified API access.
"""

import os
from typing import Optional, Dict, Any, List, AsyncIterator

from flocks.provider.provider import (
    BaseProvider,
    ModelInfo,
    ModelCapabilities,
    ChatMessage,
    ChatResponse,
    StreamChunk,
)
from flocks.utils.log import Log

log = Log.create(service="provider.gateway")


class GatewayProvider(BaseProvider):
    """
    AI Gateway Provider
    
    A unified gateway that can route requests to multiple AI providers.
    Supports various gateway implementations like LiteLLM, Portkey, etc.
    """
    
    # Default models - these should be configured based on the gateway setup
    DEFAULT_MODELS_CONFIG = [
        {
            "id": "gpt-4",
            "name": "GPT-4 (via Gateway)",
            "context_window": 128000,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
        },
        {
            "id": "gpt-3.5-turbo",
            "name": "GPT-3.5 Turbo (via Gateway)",
            "context_window": 16385,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": False,
            "supports_streaming": True,
        },
        {
            "id": "claude-3-opus",
            "name": "Claude 3 Opus (via Gateway)",
            "context_window": 200000,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
        },
        {
            "id": "claude-3-sonnet",
            "name": "Claude 3 Sonnet (via Gateway)",
            "context_window": 200000,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
        },
        {
            "id": "gemini-pro",
            "name": "Gemini Pro (via Gateway)",
            "context_window": 32000,
            "max_tokens": 8192,
            "supports_tools": True,
            "supports_vision": False,
            "supports_streaming": True,
        },
    ]
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize Gateway provider
        
        Args:
            api_key: Gateway API key (or from GATEWAY_API_KEY env)
            base_url: Gateway base URL (or from GATEWAY_BASE_URL env)
            **kwargs: Additional configuration
        """
        super().__init__(provider_id="gateway", name="AI Gateway")
        
        # Get configuration from environment if not provided
        self.api_key = api_key or os.environ.get("GATEWAY_API_KEY", "")
        self.base_url = base_url or os.environ.get(
            "GATEWAY_BASE_URL",
            "http://localhost:4000"  # Default LiteLLM port
        )
        
        # Build models from config
        self._models_config = self.DEFAULT_MODELS_CONFIG.copy()
        self._client = None
        
        log.info("gateway.initialized", {"base_url": self.base_url})
    
    def _get_client(self):
        """Get or create OpenAI client for gateway"""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                
                self._client = AsyncOpenAI(
                    api_key=self.api_key or "not-needed",
                    base_url=self.base_url,
                )
            except ImportError:
                raise ImportError("openai package not installed. Install with: pip install openai")
        return self._client
    
    def get_models(self) -> List[ModelInfo]:
        """Get available models through the gateway"""
        models = []
        for config in self._models_config:
            models.append(ModelInfo(
                id=config["id"],
                name=config["name"],
                provider_id=self.id,
                capabilities=ModelCapabilities(
                    supports_streaming=config.get("supports_streaming", True),
                    supports_tools=config.get("supports_tools", True),
                    supports_vision=config.get("supports_vision", False),
                    max_tokens=config.get("max_tokens", 4096),
                    context_window=config.get("context_window", 8192),
                ),
            ))
        return models
    
    def configure_models(self, models: List[Dict[str, Any]]) -> None:
        """
        Configure available models
        
        Args:
            models: List of model configurations
        """
        self._models_config = models
        log.info("gateway.models_configured", {"count": len(models)})
    
    async def chat(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> ChatResponse:
        """Send chat completion request through gateway"""
        client = self._get_client()
        
        # Convert messages to OpenAI format
        formatted_messages = [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]
        
        # Extract parameters
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens")
        tools = kwargs.get("tools")
        
        # Make request
        request_params = {
            "model": model_id,
            "messages": formatted_messages,
            "temperature": temperature,
        }
        
        if max_tokens:
            request_params["max_tokens"] = max_tokens
        if tools:
            request_params["tools"] = tools
        
        response = await client.chat.completions.create(**request_params)
        
        # Format response
        choice = response.choices[0]
        return ChatResponse(
            id=response.id,
            model=response.model,
            content=choice.message.content or "",
            finish_reason=choice.finish_reason or "stop",
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            }
        )
    
    async def chat_stream(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> AsyncIterator[StreamChunk]:
        """Send streaming chat completion request through gateway"""
        client = self._get_client()
        
        # Convert messages to OpenAI format
        formatted_messages = [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]
        
        # Extract parameters
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens")
        
        # Make streaming request
        request_params = {
            "model": model_id,
            "messages": formatted_messages,
            "temperature": temperature,
            "stream": True,
        }
        
        if max_tokens:
            request_params["max_tokens"] = max_tokens
        
        stream = await client.chat.completions.create(**request_params)
        
        async for chunk in stream:
            if chunk.choices:
                choice = chunk.choices[0]
                if choice.delta.content:
                    yield StreamChunk(
                        delta=choice.delta.content,
                        finish_reason=choice.finish_reason,
                    )
    
    async def list_available_models(self) -> List[Dict[str, Any]]:
        """Query the gateway for available models"""
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                
                response = await client.get(
                    f"{self.base_url}/models",
                    headers=headers,
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    models = []
                    
                    for model in data.get("data", []):
                        models.append({
                            "id": model.get("id"),
                            "name": model.get("id"),
                            "context_window": model.get("context_length", 4096),
                            "max_tokens": model.get("max_output", 4096),
                            "supports_tools": True,
                            "supports_streaming": True,
                        })
                    
                    if models:
                        self._models_config = models
                        log.info("gateway.models_discovered", {"count": len(models)})
                    
                    return models
                    
        except Exception as e:
            log.warn("gateway.models_discovery_failed", {"error": str(e)})
        
        return self._models_config
    
    async def health_check(self) -> Dict[str, Any]:
        """Check gateway health"""
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                
                response = await client.get(
                    f"{self.base_url}/health",
                    headers=headers,
                    timeout=10.0
                )
                
                return {
                    "healthy": response.status_code == 200,
                    "status_code": response.status_code,
                    "base_url": self.base_url,
                }
                
        except Exception as e:
            return {
                "healthy": False,
                "error": str(e),
                "base_url": self.base_url,
            }


# Provider factory function
def create_provider(**kwargs) -> GatewayProvider:
    """Create a Gateway provider instance"""
    return GatewayProvider(**kwargs)
