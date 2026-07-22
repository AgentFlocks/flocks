"""
Google Vertex Anthropic Provider SDK

Provides integration with Anthropic models through Google Cloud Vertex AI.
This allows using Claude models with Google Cloud authentication and billing.

Ported from original @ai-sdk/google-vertex/anthropic implementation.
"""

import json
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
from flocks.provider.sdk.anthropic import AnthropicProvider
from flocks.provider.sdk.openai_base import build_reasoning_metadata
from flocks.utils.log import Log

log = Log.create(service="provider.vertex-anthropic")


class VertexAnthropicProvider(BaseProvider):
    """
    Google Vertex Anthropic Provider
    
    Integrates with Anthropic's Claude models through Google Cloud Vertex AI:
    - Uses Google Cloud authentication (Application Default Credentials)
    - Billing through Google Cloud
    - Access to Claude models in Google's infrastructure
    
    Environment Variables:
        GOOGLE_CLOUD_PROJECT: GCP project ID (also GCP_PROJECT, GCLOUD_PROJECT)
        GOOGLE_CLOUD_LOCATION: Region (default: global)
        GOOGLE_APPLICATION_CREDENTIALS: Path to service account JSON
    """
    
    # Claude models available through Vertex AI
    DEFAULT_MODELS = [
        {
            "id": "claude-3-5-sonnet@20241022",
            "name": "Claude 3.5 Sonnet (Vertex)",
            "context_window": 200000,
            "max_tokens": 8192,
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
        },
        {
            "id": "claude-3-5-haiku@20241022",
            "name": "Claude 3.5 Haiku (Vertex)",
            "context_window": 200000,
            "max_tokens": 8192,
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
        },
        {
            "id": "claude-3-opus@20240229",
            "name": "Claude 3 Opus (Vertex)",
            "context_window": 200000,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
        },
        {
            "id": "claude-3-sonnet@20240229",
            "name": "Claude 3 Sonnet (Vertex)",
            "context_window": 200000,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
        },
        {
            "id": "claude-3-haiku@20240307",
            "name": "Claude 3 Haiku (Vertex)",
            "context_window": 200000,
            "max_tokens": 4096,
            "supports_tools": True,
            "supports_vision": True,
            "supports_streaming": True,
        },
    ]
    
    def __init__(
        self,
        project: Optional[str] = None,
        location: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize Google Vertex Anthropic provider.
        
        Args:
            project: GCP project ID (or from GOOGLE_CLOUD_PROJECT env)
            location: GCP region (default: global)
            **kwargs: Additional configuration
        """
        super().__init__(provider_id="google-vertex-anthropic", name="Google Vertex Anthropic")
        
        # Get project from environment if not provided
        self.project = (
            project or
            os.environ.get("GOOGLE_CLOUD_PROJECT") or
            os.environ.get("GCP_PROJECT") or
            os.environ.get("GCLOUD_PROJECT") or
            ""
        )
        
        # Get location (Vertex Anthropic typically uses 'global')
        self.location = (
            location or
            os.environ.get("GOOGLE_CLOUD_LOCATION") or
            os.environ.get("VERTEX_LOCATION") or
            "global"
        )
        
        self._client = None
        self._models_config = self.DEFAULT_MODELS.copy()
        
        log.info("vertex_anthropic.initialized", {
            "project": self.project,
            "location": self.location,
        })
    
    async def _get_access_token(self) -> str:
        """
        Get access token using Google Cloud Application Default Credentials.
        
        Returns:
            Access token string
        """
        try:
            # Try to use google-auth library for ADC
            import google.auth
            import google.auth.transport.requests
            
            credentials, project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            
            # Refresh credentials if needed
            request = google.auth.transport.requests.Request()
            credentials.refresh(request)
            
            return credentials.token
            
        except ImportError:
            # Fallback: try to get token from gcloud CLI
            import subprocess
            result = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return result.stdout.strip()
            raise ValueError(
                "Could not get Google Cloud credentials. "
                "Install google-auth: pip install google-auth "
                "or authenticate with: gcloud auth application-default login"
            )
    
    def _get_api_url(self, model_id: str) -> str:
        """
        Build the Vertex AI Anthropic API URL.
        
        Args:
            model_id: Model identifier
            
        Returns:
            API endpoint URL
        """
        # Vertex AI Anthropic uses a specific endpoint format
        return (
            f"https://{self.location}-aiplatform.googleapis.com/v1/"
            f"projects/{self.project}/locations/{self.location}/"
            f"publishers/anthropic/models/{model_id}"
        )
    
    def get_models(self) -> List[ModelInfo]:
        """Get available Vertex Anthropic models."""
        models = []
        for config in self._models_config:
            models.append(ModelInfo(
                id=config["id"],
                name=config["name"],
                provider_id=self.id,
                capabilities=ModelCapabilities(
                    supports_streaming=config.get("supports_streaming", True),
                    supports_tools=config.get("supports_tools", True),
                    supports_vision=config.get("supports_vision", True),
                    supports_reasoning=True,
                    interleaved={"field": "thinking", "echo": "tool_calls"},
                    max_tokens=config.get("max_tokens", 4096),
                    context_window=config.get("context_window", 200000),
                ),
            ))
        return models

    def _convert_tools(self, tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        if not tools:
            return None

        anthropic_tools = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            func = tool.get("function", {})
            anthropic_tools.append({
                "name": func.get("name"),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            })
        return anthropic_tools or None
    
    async def chat(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> ChatResponse:
        """
        Create a chat completion using Vertex Anthropic.
        
        Args:
            model_id: Model to use (e.g., claude-3-5-sonnet@20241022)
            messages: List of conversation messages
            **kwargs: Additional parameters (temperature, max_tokens, etc.)
            
        Returns:
            Chat completion response
        """
        if not self.project:
            raise ValueError(
                "Google Cloud project not configured. "
                "Set GOOGLE_CLOUD_PROJECT environment variable."
            )
        
        token = await self._get_access_token()
        
        system_message = next(
            (msg.content for msg in messages if msg.role == "system"),
            None
        )
        anthropic_messages = AnthropicProvider._format_messages_anthropic(messages)
        tools = self._convert_tools(kwargs.get("tools"))
        
        # Build request payload (Anthropic format)
        payload = {
            "anthropic_version": "vertex-2023-10-16",
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
        }
        
        if system_message:
            payload["system"] = system_message
        if tools:
            payload["tools"] = tools
        if kwargs.get("thinking"):
            payload["thinking"] = kwargs["thinking"]
        elif "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        
        try:
            import httpx
            
            url = f"{self._get_api_url(model_id)}:rawPredict"
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=120.0,
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Extract content from Anthropic response format
                    content = ""
                    reasoning = ""
                    tool_calls = []
                    if "content" in data and data["content"]:
                        for block in data["content"]:
                            if block.get("type") == "text":
                                content += block.get("text", "")
                            elif block.get("type") == "thinking":
                                reasoning += block.get("thinking", "")
                            elif block.get("type") == "tool_use":
                                tool_calls.append({
                                    "id": block.get("id", ""),
                                    "type": "function",
                                    "function": {
                                        "name": block.get("name", ""),
                                        "arguments": json.dumps(block.get("input", {})),
                                    },
                                })
                    
                    return ChatResponse(
                        id=data.get("id", "vertex-anthropic-response"),
                        model=model_id,
                        content=content,
                        finish_reason="tool_calls" if tool_calls else data.get("stop_reason", "end_turn"),
                        usage={
                            "prompt_tokens": data.get("usage", {}).get("input_tokens", 0),
                            "completion_tokens": data.get("usage", {}).get("output_tokens", 0),
                            "total_tokens": (
                                data.get("usage", {}).get("input_tokens", 0) +
                                data.get("usage", {}).get("output_tokens", 0)
                            ),
                        },
                        tool_calls=tool_calls or None,
                        reasoning=reasoning or None,
                    )
                else:
                    log.error("vertex_anthropic.chat.error", {
                        "status": response.status_code,
                        "body": response.text[:500],
                    })
                    raise Exception(f"Vertex Anthropic API error: {response.status_code}")
                    
        except Exception as e:
            log.error("vertex_anthropic.chat.error", {"error": str(e), "model": model_id})
            raise
    
    async def chat_stream(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> AsyncIterator[StreamChunk]:
        """
        Create a streaming chat completion using Vertex Anthropic.
        
        Args:
            model_id: Model to use
            messages: List of conversation messages
            **kwargs: Additional parameters
            
        Yields:
            Streaming response chunks
        """
        if not self.project:
            raise ValueError(
                "Google Cloud project not configured. "
                "Set GOOGLE_CLOUD_PROJECT environment variable."
            )
        
        token = await self._get_access_token()
        
        system_message = next(
            (msg.content for msg in messages if msg.role == "system"),
            None
        )
        anthropic_messages = AnthropicProvider._format_messages_anthropic(messages)
        tools = self._convert_tools(kwargs.get("tools"))
        
        # Build request payload with streaming
        payload = {
            "anthropic_version": "vertex-2023-10-16",
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "stream": True,
        }
        
        if system_message:
            payload["system"] = system_message
        if tools:
            payload["tools"] = tools
        if kwargs.get("thinking"):
            payload["thinking"] = kwargs["thinking"]
        elif "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        
        try:
            import httpx
            
            url = f"{self._get_api_url(model_id)}:streamRawPredict"
            current_tool_id: Optional[str] = None
            current_tool_name: Optional[str] = None
            current_tool_input = ""
            current_reasoning_signature: Optional[str] = None
            current_redacted_thinking_data: Optional[str] = None
            current_reasoning_open = False
            
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=120.0,
                ) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            
                            try:
                                event = json.loads(data)
                                event_type = event.get("type", "")
                                
                                if event_type == "content_block_start":
                                    block = event.get("content_block", {})
                                    block_type = block.get("type")
                                    if block_type == "tool_use":
                                        current_tool_id = block.get("id")
                                        current_tool_name = block.get("name")
                                        current_tool_input = ""
                                    elif block_type == "thinking":
                                        current_reasoning_open = True
                                        current_reasoning_signature = None
                                        current_redacted_thinking_data = None
                                        yield StreamChunk(
                                            event_type="reasoning-start",
                                            metadata=build_reasoning_metadata(
                                                provider_id=self.id,
                                                model_id=model_id,
                                                reasoning_source="vertex_anthropic_thinking",
                                                reasoning_field="thinking",
                                            ),
                                        )
                                    elif block_type == "redacted_thinking":
                                        current_reasoning_open = True
                                        current_reasoning_signature = None
                                        current_redacted_thinking_data = block.get("data")
                                        metadata = build_reasoning_metadata(
                                            provider_id=self.id,
                                            model_id=model_id,
                                            reasoning_source="vertex_anthropic_redacted_thinking",
                                            reasoning_field="thinking",
                                        ) or {}
                                        metadata["redactedThinkingData"] = current_redacted_thinking_data
                                        yield StreamChunk(
                                            event_type="reasoning-start",
                                            metadata=metadata,
                                        )

                                elif event_type == "content_block_delta":
                                    delta = event.get("delta", {})
                                    delta_type = delta.get("type")
                                    if delta_type == "text_delta":
                                        yield StreamChunk(
                                            delta=delta.get("text", ""),
                                            finish_reason=None,
                                        )
                                    elif delta_type == "thinking_delta":
                                        thinking_text = delta.get("thinking", "")
                                        metadata = build_reasoning_metadata(
                                            provider_id=self.id,
                                            model_id=model_id,
                                            reasoning_content=thinking_text,
                                            reasoning_source="vertex_anthropic_thinking",
                                            reasoning_field="thinking",
                                        )
                                        yield StreamChunk(
                                            event_type="reasoning",
                                            reasoning=thinking_text,
                                            finish_reason=None,
                                            metadata=metadata,
                                        )
                                    elif delta_type == "signature_delta":
                                        current_reasoning_signature = delta.get("signature")
                                    elif delta_type == "input_json_delta":
                                        current_tool_input += delta.get("partial_json", "")

                                elif event_type == "content_block_stop":
                                    if current_tool_id and current_tool_name:
                                        yield StreamChunk(
                                            delta="",
                                            finish_reason=None,
                                            tool_calls=[{
                                                "id": current_tool_id,
                                                "type": "function",
                                                "function": {
                                                    "name": current_tool_name,
                                                    "arguments": current_tool_input or "{}",
                                                },
                                            }],
                                        )
                                        current_tool_id = None
                                        current_tool_name = None
                                        current_tool_input = ""
                                    elif current_reasoning_open:
                                        metadata = build_reasoning_metadata(
                                            provider_id=self.id,
                                            model_id=model_id,
                                            reasoning_source=(
                                                "vertex_anthropic_redacted_thinking"
                                                if current_redacted_thinking_data
                                                else "vertex_anthropic_thinking"
                                            ),
                                            reasoning_field="thinking",
                                        ) or {}
                                        if current_reasoning_signature:
                                            metadata["thinkingSignature"] = current_reasoning_signature
                                        if current_redacted_thinking_data:
                                            metadata["redactedThinkingData"] = current_redacted_thinking_data
                                        yield StreamChunk(
                                            event_type="reasoning-end",
                                            metadata=metadata,
                                        )
                                        current_reasoning_open = False
                                        current_reasoning_signature = None
                                        current_redacted_thinking_data = None
                                
                                elif event_type == "message_stop":
                                    yield StreamChunk(
                                        delta="",
                                        finish_reason="end_turn",
                                    )
                                    
                            except json.JSONDecodeError:
                                continue
                    
        except Exception as e:
            log.error("vertex_anthropic.stream.error", {"error": str(e), "model": model_id})
            raise
    
    async def health_check(self) -> Dict[str, Any]:
        """Check Vertex Anthropic service health."""
        if not self.project:
            return {
                "healthy": False,
                "provider": self.id,
                "error": "Project not configured",
            }
        
        try:
            # Try to get access token as health check
            token = await self._get_access_token()
            
            return {
                "healthy": True,
                "provider": self.id,
                "project": self.project,
                "location": self.location,
                "has_token": bool(token),
            }
            
        except Exception as e:
            return {
                "healthy": False,
                "provider": self.id,
                "error": str(e),
            }


# Provider factory function
def create_provider(**kwargs) -> VertexAnthropicProvider:
    """Create a Google Vertex Anthropic provider instance."""
    return VertexAnthropicProvider(**kwargs)
