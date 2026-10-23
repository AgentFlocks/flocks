"""
Cloudflare AI Gateway Provider SDK

Provides integration with Cloudflare's AI Gateway service.
Supports unified billing where Cloudflare handles upstream provider authentication.

Ported from original cloudflare-ai-gateway implementation.
"""

import os
import json
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

log = Log.create(service="provider.cloudflare-gateway")


class CloudflareGatewayProvider(BaseProvider):
    """
    Cloudflare AI Gateway Provider
    
    Integrates with Cloudflare's AI Gateway for:
    - Unified billing across multiple AI providers
    - Request caching and rate limiting
    - Analytics and logging
    - Fallback routing
    
    The gateway acts as a proxy to upstream AI providers, handling authentication
    and billing through Cloudflare's unified system.
    
    Environment Variables:
        CLOUDFLARE_ACCOUNT_ID: Cloudflare account ID
        CLOUDFLARE_GATEWAY_ID: Gateway ID
        CLOUDFLARE_API_TOKEN: API token for authenticated gateways
    """
    
    # Default models available through Cloudflare AI Gateway
    # These depend on the upstream providers configured in the gateway
    DEFAULT_MODELS = [
        {
            "id": "gpt-4-turbo",
            "name": "GPT-4 Turbo (CF Gateway)",
            "context_window": 128000,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
        },
        {
            "id": "gpt-3.5-turbo",
            "name": "GPT-3.5 Turbo (CF Gateway)",
            "context_window": 16385,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": False,
            "supports_streaming": True,
        },
        {
            "id": "claude-3-sonnet",
            "name": "Claude 3 Sonnet (CF Gateway)",
            "context_window": 200000,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
        },
        {
            "id": "claude-3-haiku",
            "name": "Claude 3 Haiku (CF Gateway)",
            "context_window": 200000,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
        },
    ]
    
    def __init__(
        self,
        account_id: Optional[str] = None,
        gateway_id: Optional[str] = None,
        api_token: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize Cloudflare AI Gateway provider.
        
        Args:
            account_id: Cloudflare account ID (or from CLOUDFLARE_ACCOUNT_ID env)
            gateway_id: Gateway ID (or from CLOUDFLARE_GATEWAY_ID env)
            api_token: API token for authenticated gateways (or from CLOUDFLARE_API_TOKEN env)
            **kwargs: Additional configuration
        """
        super().__init__(provider_id="cloudflare-ai-gateway", name="Cloudflare AI Gateway")
        
        # Get configuration from environment if not provided
        self.account_id = account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        self.gateway_id = gateway_id or os.environ.get("CLOUDFLARE_GATEWAY_ID", "")
        self.api_token = api_token or os.environ.get("CLOUDFLARE_API_TOKEN", "")
        
        # Build base URL for the gateway
        if self.account_id and self.gateway_id:
            self._base_url = (
                f"https://gateway.ai.cloudflare.com/v1/"
                f"{self.account_id}/{self.gateway_id}/compat"
            )
        else:
            self._base_url = None
        
        self._models_config = self.DEFAULT_MODELS.copy()
        
        log.info("cloudflare_gateway.initialized", {
            "account_id": self.account_id[:8] + "..." if self.account_id else None,
            "gateway_id": self.gateway_id,
            "has_api_token": bool(self.api_token),
        })
    
    def _get_headers(self) -> Dict[str, str]:
        """
        Get request headers for Cloudflare AI Gateway.
        
        Cloudflare uses cf-aig-authorization for authenticated gateways
        instead of standard Authorization header.
        """
        headers = {
            "Content-Type": "application/json",
            "HTTP-Referer": "https://flocks.ai/",
            "X-Title": "flocks",
        }
        
        # Use cf-aig-authorization for authenticated gateways
        # This enables Unified Billing where Cloudflare handles upstream provider auth
        if self.api_token:
            headers["cf-aig-authorization"] = f"Bearer {self.api_token}"
        
        return headers
    
    def _transform_request_body(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform request body for newer models.
        
        Converts max_tokens to max_completion_tokens for models that require it.
        """
        if "max_tokens" in body and "max_completion_tokens" not in body:
            body["max_completion_tokens"] = body.pop("max_tokens")
        return body
    
    def get_models(self) -> List[ModelInfo]:
        """Get available Cloudflare AI Gateway models."""
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
                    context_window=config.get("context_window", 128000),
                ),
            ))
        return models
    
    async def chat(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> ChatResponse:
        """
        Create a chat completion using Cloudflare AI Gateway.
        
        Args:
            model_id: Model to use
            messages: List of conversation messages
            **kwargs: Additional parameters (temperature, max_tokens, etc.)
            
        Returns:
            Chat completion response
        """
        if not self._base_url:
            raise ValueError(
                "Cloudflare AI Gateway not configured. "
                "Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_GATEWAY_ID environment variables."
            )
        
        # Convert messages to OpenAI format
        formatted_messages = [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]
        
        # Build request payload
        payload = {
            "model": model_id,
            "messages": formatted_messages,
            "temperature": kwargs.get("temperature", 0.7),
        }
        
        if kwargs.get("max_tokens"):
            payload["max_tokens"] = kwargs["max_tokens"]
        
        # Transform for newer models
        payload = self._transform_request_body(payload)
        
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=120.0,
                )
                
                if response.status_code == 200:
                    data = response.json()
                    choice = data["choices"][0]
                    
                    return ChatResponse(
                        id=data.get("id", "cf-gateway-response"),
                        model=data.get("model", model_id),
                        content=choice.get("message", {}).get("content", ""),
                        finish_reason=choice.get("finish_reason", "stop"),
                        usage=data.get("usage", {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0,
                        })
                    )
                else:
                    log.error("cloudflare_gateway.chat.error", {
                        "status": response.status_code,
                        "body": response.text[:500],
                    })
                    raise Exception(f"Cloudflare AI Gateway error: {response.status_code}")
                    
        except Exception as e:
            log.error("cloudflare_gateway.chat.error", {"error": str(e), "model": model_id})
            raise
    
    async def chat_stream(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> AsyncIterator[StreamChunk]:
        """
        Create a streaming chat completion using Cloudflare AI Gateway.
        
        Args:
            model_id: Model to use
            messages: List of conversation messages
            **kwargs: Additional parameters
            
        Yields:
            Streaming response chunks
        """
        if not self._base_url:
            raise ValueError(
                "Cloudflare AI Gateway not configured. "
                "Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_GATEWAY_ID environment variables."
            )
        
        # Convert messages to OpenAI format
        formatted_messages = [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]
        
        # Build request payload with streaming enabled
        payload = {
            "model": model_id,
            "messages": formatted_messages,
            "temperature": kwargs.get("temperature", 0.7),
            "stream": True,
        }
        
        if kwargs.get("max_tokens"):
            payload["max_tokens"] = kwargs["max_tokens"]
        
        # Transform for newer models
        payload = self._transform_request_body(payload)
        
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=self._get_headers(),
                    json=payload,
                    timeout=120.0,
                ) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            
                            try:
                                chunk = json.loads(data)
                                choices = chunk.get("choices", [])
                                
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content", "")
                                    finish_reason = choices[0].get("finish_reason")
                                    
                                    if content:
                                        yield StreamChunk(
                                            delta=content,
                                            finish_reason=None,
                                        )
                                    elif finish_reason:
                                        yield StreamChunk(
                                            delta="",
                                            finish_reason=finish_reason,
                                        )
                            except json.JSONDecodeError:
                                continue
                    
                    # Final chunk
                    yield StreamChunk(
                        delta="",
                        finish_reason="stop",
                    )
                    
        except Exception as e:
            log.error("cloudflare_gateway.stream.error", {"error": str(e), "model": model_id})
            raise
    
    async def health_check(self) -> Dict[str, Any]:
        """Check Cloudflare AI Gateway service health."""
        if not self._base_url:
            return {
                "healthy": False,
                "provider": self.id,
                "error": "Gateway not configured",
            }
        
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                # Simple connectivity check
                response = await client.get(
                    f"{self._base_url}/models",
                    headers=self._get_headers(),
                    timeout=10.0,
                )
                
                return {
                    "healthy": response.status_code < 500,
                    "provider": self.id,
                    "status_code": response.status_code,
                    "account_id": self.account_id[:8] + "..." if self.account_id else None,
                    "gateway_id": self.gateway_id,
                }
                
        except Exception as e:
            return {
                "healthy": False,
                "provider": self.id,
                "error": str(e),
            }


# Provider factory function
def create_provider(**kwargs) -> CloudflareGatewayProvider:
    """Create a Cloudflare AI Gateway provider instance."""
    return CloudflareGatewayProvider(**kwargs)
