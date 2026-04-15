"""
OpenAI (GPT) provider implementation.

Supports tool/function calling for agent capabilities.
"""

from typing import List, AsyncIterator, Optional, Dict, Any
import os
import json

from flocks.provider.provider import (
    BaseProvider,
    ModelInfo,
    ModelCapabilities,
    ChatMessage,
    ChatResponse,
    StreamChunk,
)
from flocks.provider.sdk.openai_base import (
    _coerce_bool,
    extract_reasoning_content,
    resolve_verify_ssl,
)
from flocks.utils.log import Log

log = Log.create(service="provider.openai")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class OpenAIProvider(BaseProvider):
    """OpenAI (GPT) provider with tool support."""

    CATALOG_ID = "openai"

    def __init__(self):
        super().__init__(provider_id="openai", name="OpenAI")
        self._api_key = os.getenv("OPENAI_API_KEY")
        self._client = None
    
    def is_configured(self) -> bool:
        """Check if provider is configured via env var or config."""
        if self._config and self._config.api_key:
            return True
        return os.getenv("OPENAI_API_KEY") is not None or os.getenv("LLM_API_KEY") is not None

    def get_meta(self):
        from flocks.provider.model_catalog import get_provider_meta
        return get_provider_meta("openai") or super().get_meta()

    def _get_client(self):
        """Get or create OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                import httpx
                
                api_key = self._config.api_key if self._config else self._api_key
                if not api_key:
                    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
                if not api_key:
                    raise ValueError("OpenAI API key not configured")
                
                base_url = None
                if self._config and self._config.base_url:
                    base_url = self._config.base_url
                else:
                    base_url = os.getenv("OPENAI_API_BASE") or os.getenv("LLM_API_BASE")

                trust_env = _env_bool("FLOCKS_HTTP_TRUST_ENV", True)
                cfg_settings = getattr(self._config, "custom_settings", None) or {}
                if isinstance(cfg_settings, dict) and "trust_env" in cfg_settings:
                    trust_env = _coerce_bool(cfg_settings.get("trust_env"), trust_env)
                verify_ssl = resolve_verify_ssl(cfg_settings, default=True)
                http_client = httpx.AsyncClient(
                    trust_env=trust_env,
                    verify=verify_ssl,
                    timeout=120.0,
                )

                if base_url:
                    self._client = AsyncOpenAI(
                        api_key=api_key,
                        base_url=base_url,
                        http_client=http_client,
                    )
                    self.log.info(
                        "openai.client.created",
                        {
                            "base_url": base_url,
                            "trust_env": trust_env,
                            "verify_ssl": verify_ssl,
                        },
                    )
                else:
                    self._client = AsyncOpenAI(api_key=api_key, http_client=http_client)
                    self.log.info(
                        "openai.client.created",
                        {"trust_env": trust_env, "verify_ssl": verify_ssl},
                    )
                    
            except ImportError:
                raise ImportError("openai package not installed. Install with: pip install openai")
        return self._client
    
    def get_models(self) -> List[ModelInfo]:
        """Return models from flocks.json (_config_models) only.

        catalog.json is not consulted at runtime; it is only used when
        credentials are first saved to pre-populate flocks.json.
        """
        return list(getattr(self, "_config_models", []))
    
    @staticmethod
    def _format_content(content: Any) -> Any:
        if not isinstance(content, list):
            return content

        formatted: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text" and isinstance(block.get("text"), str):
                formatted.append({"type": "text", "text": block["text"]})
            elif block_type == "image" and block.get("data") and block.get("mimeType"):
                formatted.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{block['mimeType']};base64,{block['data']}",
                    },
                })
        return formatted

    @staticmethod
    def _format_messages(messages: List[ChatMessage]) -> list:
        """Convert ChatMessage list to OpenAI API dicts, preserving tool_calls / tool results."""
        formatted = []
        for msg in messages:
            m: dict = {"role": msg.role, "content": OpenAIProvider._format_content(msg.content)}
            if msg.tool_calls:
                m["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                m["tool_call_id"] = msg.tool_call_id
            if msg.name:
                m["name"] = msg.name
            formatted.append(m)
        return formatted

    async def chat(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> ChatResponse:
        """Send chat completion request to OpenAI."""
        client = self._get_client()
        
        openai_messages = self._format_messages(messages)
        
        # Build request params
        request_params = {
            "model": model_id,
            "messages": openai_messages,
            "temperature": kwargs.get("temperature", 0.7),
        }
        
        if kwargs.get("max_tokens"):
            request_params["max_tokens"] = kwargs["max_tokens"]
        
        if kwargs.get("tools"):
            request_params["tools"] = kwargs["tools"]
        
        # Add reasoning effort for o1/o3/gpt-5 models
        if kwargs.get("reasoningEffort"):
            request_params["reasoning_effort"] = kwargs["reasoningEffort"]
        
        response = await client.chat.completions.create(**request_params)
        choice = response.choices[0]
        assistant_message = getattr(choice, "message", None)
        text_content = (
            (getattr(assistant_message, "content", None) or "")
            if assistant_message is not None
            else ""
        )

        return ChatResponse(
            id=response.id,
            model=response.model,
            content=text_content,
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
        """
        Send streaming chat completion request to OpenAI.
        
        Handles both text content and tool calls.
        """
        client = self._get_client()
        
        openai_messages = self._format_messages(messages)
        
        # Build request params
        request_params = {
            "model": model_id,
            "messages": openai_messages,
            "temperature": kwargs.get("temperature", 0.7),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        
        if kwargs.get("max_tokens"):
            request_params["max_tokens"] = kwargs["max_tokens"]
        
        if kwargs.get("tools"):
            request_params["tools"] = kwargs["tools"]
        
        # Add reasoning effort for o1/o3/gpt-5 models
        if kwargs.get("reasoningEffort"):
            request_params["reasoning_effort"] = kwargs["reasoningEffort"]
        
        # Track tool calls during streaming
        tool_calls: Dict[int, Dict[str, Any]] = {}
        
        stream = await client.chat.completions.create(**request_params)
        
        # Track usage from final chunk (when stream_options.include_usage is set)
        stream_usage: Optional[Dict[str, int]] = None
        
        async for chunk in stream:
            # Capture usage from the final chunk (OpenAI returns it in a chunk with no choices)
            if hasattr(chunk, 'usage') and chunk.usage:
                stream_usage = {
                    "prompt_tokens": getattr(chunk.usage, 'prompt_tokens', 0) or 0,
                    "completion_tokens": getattr(chunk.usage, 'completion_tokens', 0) or 0,
                    "total_tokens": getattr(chunk.usage, 'total_tokens', 0) or 0,
                }
            
            if not chunk.choices:
                continue
            
            choice = chunk.choices[0]
            delta = choice.delta

            if delta is None:
                if choice.finish_reason:
                    if tool_calls:
                        sorted_calls = [tool_calls[i] for i in sorted(tool_calls.keys())]
                        yield StreamChunk(
                            delta="",
                            finish_reason="tool_calls",
                            tool_calls=sorted_calls,
                            usage=stream_usage,
                        )
                    else:
                        yield StreamChunk(
                            delta="",
                            finish_reason=choice.finish_reason,
                            usage=stream_usage,
                        )
                continue

            # Handle reasoning/thinking content (for o1/o3/gpt-5 models)
            reasoning_content = extract_reasoning_content(delta)
            if reasoning_content:
                yield StreamChunk(
                    event_type="reasoning",
                    reasoning=reasoning_content,
                    finish_reason=None,
                )

            # Handle text content
            delta_text = getattr(delta, "content", None)
            if delta_text:
                yield StreamChunk(delta=delta_text, finish_reason=None)

            # Handle tool calls
            delta_tcs = getattr(delta, "tool_calls", None)
            if delta_tcs:
                for tc_delta in delta_tcs:
                    idx = tc_delta.index
                    
                    if idx not in tool_calls:
                        tool_calls[idx] = {
                            "id": tc_delta.id or "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""}
                        }
                    
                    if tc_delta.id:
                        tool_calls[idx]["id"] = tc_delta.id
                    
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls[idx]["function"]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls[idx]["function"]["arguments"] += tc_delta.function.arguments
            
            # Handle finish
            if choice.finish_reason:
                if tool_calls:
                    # Sort by index and convert to list
                    sorted_calls = [tool_calls[i] for i in sorted(tool_calls.keys())]
                    yield StreamChunk(
                        delta="",
                        finish_reason="tool_calls",
                        tool_calls=sorted_calls,
                        usage=stream_usage,
                    )
                else:
                    yield StreamChunk(
                        delta="",
                        finish_reason=choice.finish_reason,
                        usage=stream_usage,
                    )
    
    # Embeddings support (added for memory system)
    async def embed(
        self,
        text: str,
        model: Optional[str] = None,
        **kwargs
    ) -> List[float]:
        """Generate embedding using OpenAI API"""
        client = self._get_client()
        model = model or "text-embedding-3-small"
        
        try:
            response = await client.embeddings.create(
                input=text,
                model=model,
                **kwargs
            )
            return response.data[0].embedding
        except Exception as e:
            self.log.error("openai.embed.failed", {"error": str(e), "model": model})
            raise
    
    async def embed_batch(
        self,
        texts: List[str],
        model: Optional[str] = None,
        batch_size: Optional[int] = 100,
        **kwargs
    ) -> List[List[float]]:
        """Batch embeddings with OpenAI API"""
        client = self._get_client()
        model = model or "text-embedding-3-small"
        
        all_embeddings = []
        
        try:
            # Process in batches
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                response = await client.embeddings.create(
                    input=batch,
                    model=model,
                    **kwargs
                )
                all_embeddings.extend([item.embedding for item in response.data])
            
            return all_embeddings
        except Exception as e:
            self.log.error("openai.embed_batch.failed", {
                "error": str(e),
                "model": model,
                "batch_count": len(texts)
            })
            raise
    
    def get_embedding_models(self) -> List[str]:
        """Get OpenAI embedding models"""
        return [
            "text-embedding-3-small",
            "text-embedding-3-large",
            "text-embedding-ada-002",
        ]
