"""
SAP AI Core Provider SDK

Provides integration with SAP AI Core platform for enterprise AI capabilities.
Uses SAP-specific authentication with service keys.

Ported from original sap-ai-core implementation.
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

log = Log.create(service="provider.sap-ai-core")


class SAPAICoreProvider(BaseProvider):
    """
    SAP AI Core Provider
    
    Integrates with SAP AI Core platform for enterprise AI capabilities:
    - Foundation models (GPT-4, Claude, etc.)
    - Custom trained models
    - Enterprise-grade security and compliance
    
    Authentication:
    - Uses SAP AI Core service key (JSON)
    - Supports deployment ID and resource group configuration
    
    Environment Variables:
        AICORE_SERVICE_KEY: JSON service key for authentication
        AICORE_DEPLOYMENT_ID: Deployment ID for model access
        AICORE_RESOURCE_GROUP: Resource group name
    """
    
    # Default models available through SAP AI Core
    DEFAULT_MODELS = [
        {
            "id": "gpt-4",
            "name": "GPT-4 (SAP AI Core)",
            "context_window": 8192,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": False,
            "supports_streaming": True,
        },
        {
            "id": "gpt-4-32k",
            "name": "GPT-4 32K (SAP AI Core)",
            "context_window": 32768,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": False,
            "supports_streaming": True,
        },
        {
            "id": "gpt-3.5-turbo",
            "name": "GPT-3.5 Turbo (SAP AI Core)",
            "context_window": 16385,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": False,
            "supports_streaming": True,
        },
        {
            "id": "claude-3-sonnet",
            "name": "Claude 3 Sonnet (SAP AI Core)",
            "context_window": 200000,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
        },
    ]
    
    def __init__(
        self,
        service_key: Optional[str] = None,
        deployment_id: Optional[str] = None,
        resource_group: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize SAP AI Core provider.
        
        Args:
            service_key: JSON service key string (or from AICORE_SERVICE_KEY env)
            deployment_id: Deployment ID (or from AICORE_DEPLOYMENT_ID env)
            resource_group: Resource group (or from AICORE_RESOURCE_GROUP env)
            **kwargs: Additional configuration
        """
        super().__init__(provider_id="sap-ai-core", name="SAP AI Core")
        
        # Get service key from environment if not provided
        service_key_str = service_key or os.environ.get("AICORE_SERVICE_KEY", "")
        
        # Parse service key JSON
        self._service_key = None
        self._auth_url = None
        self._api_url = None
        self._client_id = None
        self._client_secret = None
        
        if service_key_str:
            try:
                self._service_key = json.loads(service_key_str)
                self._auth_url = self._service_key.get("url")
                self._api_url = self._service_key.get("serviceurls", {}).get("AI_API_URL")
                self._client_id = self._service_key.get("clientid")
                self._client_secret = self._service_key.get("clientsecret")
            except json.JSONDecodeError:
                log.warn("sap_ai_core.invalid_service_key", {
                    "error": "Service key is not valid JSON"
                })
        
        # Get deployment and resource group settings
        self.deployment_id = deployment_id or os.environ.get("AICORE_DEPLOYMENT_ID", "")
        self.resource_group = resource_group or os.environ.get("AICORE_RESOURCE_GROUP", "default")
        
        self._access_token = None
        self._token_expiry = 0
        self._models_config = self.DEFAULT_MODELS.copy()
        
        log.info("sap_ai_core.initialized", {
            "has_service_key": bool(self._service_key),
            "deployment_id": self.deployment_id,
            "resource_group": self.resource_group,
        })
    
    async def _get_access_token(self) -> str:
        """
        Get OAuth access token for SAP AI Core.
        
        Returns:
            Access token string
        """
        import time
        
        # Check if token is still valid
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token
        
        if not self._auth_url or not self._client_id or not self._client_secret:
            raise ValueError(
                "SAP AI Core service key not configured. "
                "Set AICORE_SERVICE_KEY environment variable with valid JSON."
            )
        
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self._auth_url}/oauth/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=30.0,
                )
                
                if response.status_code == 200:
                    data = response.json()
                    self._access_token = data["access_token"]
                    self._token_expiry = time.time() + data.get("expires_in", 3600)
                    return self._access_token
                else:
                    raise Exception(f"OAuth token request failed: {response.status_code}")
                    
        except Exception as e:
            log.error("sap_ai_core.auth.error", {"error": str(e)})
            raise
    
    def _get_headers(self, token: str) -> Dict[str, str]:
        """Get request headers with authentication."""
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "AI-Resource-Group": self.resource_group,
        }
    
    def get_models(self) -> List[ModelInfo]:
        """Get available SAP AI Core models."""
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
    
    async def chat(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> ChatResponse:
        """
        Create a chat completion using SAP AI Core.
        
        Args:
            model_id: Model to use
            messages: List of conversation messages
            **kwargs: Additional parameters (temperature, max_tokens, etc.)
            
        Returns:
            Chat completion response
        """
        if not self._api_url:
            raise ValueError("SAP AI Core API URL not configured")
        
        token = await self._get_access_token()
        
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
        
        try:
            import httpx
            
            # Build URL with deployment ID if specified
            url = f"{self._api_url}/v2/inference/deployments/{self.deployment_id}/chat/completions"
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers=self._get_headers(token),
                    json=payload,
                    timeout=120.0,
                )
                
                if response.status_code == 200:
                    data = response.json()
                    choice = data["choices"][0]
                    
                    return ChatResponse(
                        id=data.get("id", "sap-response"),
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
                    log.error("sap_ai_core.chat.error", {
                        "status": response.status_code,
                        "body": response.text[:500],
                    })
                    raise Exception(f"SAP AI Core API error: {response.status_code}")
                    
        except Exception as e:
            log.error("sap_ai_core.chat.error", {"error": str(e), "model": model_id})
            raise
    
    async def chat_stream(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> AsyncIterator[StreamChunk]:
        """
        Create a streaming chat completion using SAP AI Core.
        
        Args:
            model_id: Model to use
            messages: List of conversation messages
            **kwargs: Additional parameters
            
        Yields:
            Streaming response chunks
        """
        if not self._api_url:
            raise ValueError("SAP AI Core API URL not configured")
        
        token = await self._get_access_token()
        
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
        
        try:
            import httpx
            
            # Build URL with deployment ID if specified
            url = f"{self._api_url}/v2/inference/deployments/{self.deployment_id}/chat/completions"
            
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=self._get_headers(token),
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
            log.error("sap_ai_core.stream.error", {"error": str(e), "model": model_id})
            raise
    
    async def health_check(self) -> Dict[str, Any]:
        """Check SAP AI Core service health."""
        try:
            # Try to get access token as health check
            token = await self._get_access_token()
            
            return {
                "healthy": True,
                "provider": self.id,
                "has_token": bool(token),
                "deployment_id": self.deployment_id,
                "resource_group": self.resource_group,
            }
            
        except Exception as e:
            return {
                "healthy": False,
                "provider": self.id,
                "error": str(e),
            }


# Provider factory function
def create_provider(**kwargs) -> SAPAICoreProvider:
    """Create a SAP AI Core provider instance."""
    return SAPAICoreProvider(**kwargs)
