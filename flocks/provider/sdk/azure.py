"""
Azure OpenAI provider implementation
"""

from typing import Any, Dict, List, AsyncIterator
import os

from flocks.provider.provider import (
    BaseProvider,
    ModelInfo,
    ModelCapabilities,
    ChatMessage,
    ChatResponse,
    StreamChunk,
)
from flocks.utils.log import Log

log = Log.create(service="provider.azure")


class AzureProvider(BaseProvider):
    """Azure OpenAI provider"""
    
    def __init__(self):
        super().__init__(provider_id="azure", name="Azure OpenAI")
        self._api_key = os.getenv("AZURE_OPENAI_API_KEY")
        self._endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self._client = None
    
    def _get_client(self):
        """Get or create Azure OpenAI client"""
        if self._client is None:
            try:
                from openai import AsyncAzureOpenAI
                
                # Get API key
                api_key = self._config.api_key if self._config else self._api_key
                if not api_key:
                    api_key = os.getenv("AZURE_OPENAI_API_KEY")
                if not api_key:
                    raise ValueError("Azure OpenAI API key not configured")
                
                # Get endpoint
                endpoint = None
                if self._config and self._config.base_url:
                    endpoint = self._config.base_url
                else:
                    endpoint = self._endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")
                
                if not endpoint:
                    raise ValueError("Azure OpenAI endpoint not configured")
                
                # Get API version
                api_version = (
                    self._config.custom_settings.get("api_version") 
                    if self._config 
                    else None
                ) or os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
                
                # Create client
                self._client = AsyncAzureOpenAI(
                    api_key=api_key,
                    azure_endpoint=endpoint,
                    api_version=api_version,
                )
                self.log.info("azure.client.created", {"endpoint": endpoint})
                    
            except ImportError:
                raise ImportError("openai package not installed. Install with: pip install openai")
        return self._client
    
    def get_models(self) -> List[ModelInfo]:
        """Get list of Azure OpenAI models.

        Returns user-configured models from flocks.json (_config_models) when
        available (same pattern as other providers). Falls back to a small set
        of common Azure deployment names so the static ``azure`` provider still
        works without any flocks.json configuration.
        """
        config_models = list(getattr(self, "_config_models", []))
        if config_models:
            return config_models

        # Fallback: common Azure deployment names for the built-in ``azure`` provider
        return [
            ModelInfo(
                id="gpt-5.4",
                name="GPT-5.4 (Azure)",
                provider_id=self.id,
                capabilities=ModelCapabilities(
                    supports_streaming=True,
                    supports_tools=True,
                    supports_vision=True,
                    max_tokens=16384,
                    context_window=1048576,
                ),
            ),
            ModelInfo(
                id="gpt-5-mini",
                name="GPT-5 Mini (Azure)",
                provider_id=self.id,
                capabilities=ModelCapabilities(
                    supports_streaming=True,
                    supports_tools=True,
                    supports_vision=True,
                    max_tokens=16384,
                    context_window=1048576,
                ),
            ),
        ]
    
    async def chat(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> ChatResponse:
        """Send chat completion request to Azure OpenAI"""
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
            # Newer Azure models (GPT-4o, GPT-5.x) require max_completion_tokens;
            # try that first and fall back to max_tokens for older deployments.
            request_params["max_completion_tokens"] = max_tokens
        if tools:
            request_params["tools"] = tools
        
        try:
            response = await client.chat.completions.create(**request_params)
        except Exception as e:
            if "max_completion_tokens" in str(e) and max_tokens:
                request_params.pop("max_completion_tokens", None)
                request_params["max_tokens"] = max_tokens
                response = await client.chat.completions.create(**request_params)
            else:
                raise
        
        # Format response
        choice = response.choices[0]
        return ChatResponse(
            id=response.id,
            model=response.model,
            content=choice.message.content or "",
            finish_reason=choice.finish_reason,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        )
    
    async def chat_stream(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> AsyncIterator[StreamChunk]:
        """Send streaming chat completion request to Azure OpenAI"""
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
        
        # Make streaming request
        request_params = {
            "model": model_id,
            "messages": formatted_messages,
            "temperature": temperature,
            "stream": True,
        }
        
        if max_tokens:
            request_params["max_completion_tokens"] = max_tokens
        if tools:
            request_params["tools"] = tools
        
        try:
            stream = await client.chat.completions.create(**request_params)
        except Exception as e:
            if "max_completion_tokens" in str(e) and max_tokens:
                request_params.pop("max_completion_tokens", None)
                request_params["max_tokens"] = max_tokens
                stream = await client.chat.completions.create(**request_params)
            else:
                raise
        
        tool_calls: Dict[int, Dict[str, Any]] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            if delta is None:
                if choice.finish_reason:
                    if tool_calls:
                        yield StreamChunk(
                            delta="",
                            finish_reason="tool_calls",
                            tool_calls=[tool_calls[i] for i in sorted(tool_calls.keys())],
                        )
                    else:
                        yield StreamChunk(delta="", finish_reason=choice.finish_reason)
                continue

            delta_text = getattr(delta, "content", None)
            if delta_text:
                yield StreamChunk(delta=delta_text, finish_reason=None)

            delta_tool_calls = getattr(delta, "tool_calls", None)
            if delta_tool_calls:
                for tool_call_delta in delta_tool_calls:
                    index = getattr(tool_call_delta, "index", 0)
                    if index not in tool_calls:
                        tool_calls[index] = {
                            "id": getattr(tool_call_delta, "id", None) or "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }

                    tool_call_id = getattr(tool_call_delta, "id", None)
                    if tool_call_id:
                        tool_calls[index]["id"] = tool_call_id

                    function_delta = getattr(tool_call_delta, "function", None)
                    if function_delta:
                        function_name = getattr(function_delta, "name", None)
                        if function_name:
                            tool_calls[index]["function"]["name"] = function_name

                        function_arguments = getattr(function_delta, "arguments", None)
                        if function_arguments:
                            tool_calls[index]["function"]["arguments"] += function_arguments

            if choice.finish_reason:
                if tool_calls:
                    yield StreamChunk(
                        delta="",
                        finish_reason="tool_calls",
                        tool_calls=[tool_calls[i] for i in sorted(tool_calls.keys())],
                    )
                else:
                    yield StreamChunk(delta="", finish_reason=choice.finish_reason)
