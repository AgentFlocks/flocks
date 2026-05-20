"""
Anthropic (Claude) provider implementation.

Supports tool/function calling for agent capabilities.
"""

from typing import List, AsyncIterator, Optional, Dict, Any
import os
import json

from flocks.provider.provider import (
    BaseProvider,
    ModelInfo,
    ChatMessage,
    ChatResponse,
    StreamChunk,
)
from flocks.provider.sdk.openai_base import build_reasoning_metadata
from flocks.utils.log import Log

log = Log.create(service="provider.anthropic")


class AnthropicProvider(BaseProvider):
    """Anthropic (Claude) provider with tool support."""

    CATALOG_ID = "anthropic"
    _INTERLEAVED_THINKING_BETA = "interleaved-thinking-2025-05-14"
    _FINE_GRAINED_TOOL_STREAMING_BETA = "fine-grained-tool-streaming-2025-05-14"

    def __init__(self):
        super().__init__(provider_id="anthropic", name="Anthropic")
        self._api_key = os.getenv("ANTHROPIC_API_KEY")
        self._client = None
    
    def is_configured(self) -> bool:
        """Check if provider is configured."""
        api_key = self._config.api_key if self._config else self._api_key
        return bool(api_key)

    def get_meta(self):
        from flocks.provider.model_catalog import get_provider_meta
        return get_provider_meta("anthropic") or super().get_meta()

    def _get_client(self):
        """Get or create Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
                api_key = self._config.api_key if self._config else self._api_key
                if not api_key:
                    raise ValueError("Anthropic API key not configured")
                
                # Support custom base URL from config
                base_url = self._config.base_url if self._config else None
                
                if base_url:
                    log.info("anthropic.client.init", {
                        "base_url": base_url,
                        "has_api_key": bool(api_key),
                    })
                    self._client = AsyncAnthropic(api_key=api_key, base_url=base_url)
                else:
                    self._client = AsyncAnthropic(api_key=api_key)
            except ImportError:
                raise ImportError("anthropic package not installed. Install with: pip install anthropic")
        return self._client
    
    def get_models(self) -> List[ModelInfo]:
        """Return models from flocks.json (_config_models) only.

        catalog.json is not consulted at runtime; it is only used when
        credentials are first saved to pre-populate flocks.json.
        """
        return list(getattr(self, "_config_models", []))
    
    def _convert_tools(self, tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        """Convert OpenAI-style tools to Anthropic format."""
        if not tools:
            return None
        
        anthropic_tools = []
        for tool in tools:
            if tool.get("type") == "function":
                func = tool.get("function", {})
                anthropic_tool = {
                    "name": func.get("name"),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                }
                anthropic_tools.append(anthropic_tool)
        
        return anthropic_tools if anthropic_tools else None
    
    @staticmethod
    def _format_user_content(content: Any) -> Any:
        if not isinstance(content, list):
            return content

        blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text" and isinstance(block.get("text"), str):
                blocks.append({"type": "text", "text": block["text"]})
            elif block_type == "image" and block.get("data") and block.get("mimeType"):
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block["mimeType"],
                        "data": block["data"],
                    },
                })
        return blocks

    @staticmethod
    def _format_messages_anthropic(messages: List[ChatMessage]) -> list:
        """Convert ChatMessage list to Anthropic API format.

        Anthropic requires:
        - No "system" role in messages (handled separately)
        - Assistant tool calls as ``tool_use`` content blocks
        - Tool results as ``tool_result`` content blocks inside user messages
        - Alternating user/assistant turns
        """
        formatted: list = []
        for msg in messages:
            if msg.role == "system":
                continue

            if msg.role == "assistant":
                content_blocks: list = []
                anthropic_thinking_blocks = None
                if isinstance(msg.custom_settings, dict):
                    anthropic_thinking_blocks = msg.custom_settings.get("anthropic_thinking_blocks")
                if isinstance(anthropic_thinking_blocks, list):
                    for block in anthropic_thinking_blocks:
                        if isinstance(block, dict) and block.get("type") in {"thinking", "redacted_thinking"}:
                            content_blocks.append(block)
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        fn = tc.get("function", {})
                        args_raw = fn.get("arguments", "{}")
                        try:
                            input_obj = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                        except (json.JSONDecodeError, TypeError):
                            input_obj = {"raw": args_raw}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": fn.get("name", ""),
                            "input": input_obj,
                        })
                formatted.append({
                    "role": "assistant",
                    "content": content_blocks if content_blocks else msg.content,
                })

            elif msg.role == "tool":
                # Anthropic: tool results are user messages with tool_result blocks.
                # Merge consecutive tool results into one user message.
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "",
                    "content": msg.content,
                }
                if formatted and formatted[-1]["role"] == "user" and isinstance(formatted[-1]["content"], list):
                    formatted[-1]["content"].append(tool_result_block)
                else:
                    formatted.append({
                        "role": "user",
                        "content": [tool_result_block],
                    })

            else:
                # user messages
                formatted.append({
                    "role": msg.role,
                    "content": AnthropicProvider._format_user_content(msg.content),
                })
        return formatted

    @classmethod
    def _beta_flags_for_request(cls, *, thinking_enabled: bool, has_tools: bool) -> Optional[List[str]]:
        """Return beta feature flags needed for interleaved thinking."""
        if not thinking_enabled:
            return None
        betas = [cls._INTERLEAVED_THINKING_BETA]
        if has_tools:
            betas.append(cls._FINE_GRAINED_TOOL_STREAMING_BETA)
        return betas

    @staticmethod
    def _reasoning_metadata(
        *,
        provider_id: str,
        model_id: str,
        reasoning_source: str,
        reasoning_field: str = "thinking",
        reasoning_content: Optional[str] = None,
        thinking_signature: Optional[str] = None,
        redacted_thinking_data: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build Anthropic reasoning metadata for thinking/redacted blocks."""
        metadata = build_reasoning_metadata(
            provider_id=provider_id,
            model_id=model_id,
            reasoning_content=reasoning_content,
            reasoning_source=reasoning_source,
            reasoning_field=reasoning_field,
        ) or {
            "providerID": provider_id,
            "modelID": model_id,
            "reasoningField": reasoning_field,
            "reasoningSource": reasoning_source,
        }
        if thinking_signature:
            metadata["thinkingSignature"] = thinking_signature
        if redacted_thinking_data:
            metadata["redactedThinkingData"] = redacted_thinking_data
        return metadata

    async def chat(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> ChatResponse:
        """Send chat completion request to Anthropic."""
        client = self._get_client()
        
        # Convert messages to Anthropic format
        anthropic_messages = self._format_messages_anthropic(messages)
        
        # Check if we have any non-system messages
        if not anthropic_messages:
            log.error("anthropic.no_messages", {"total_messages": len(messages)})
            raise ValueError("No non-system messages provided to Anthropic API")
        
        # Extract system message
        system_message = next(
            (msg.content for msg in messages if msg.role == "system"),
            None
        )
        
        # Convert tools if provided
        tools = self._convert_tools(kwargs.get("tools"))
        
        # Build request params
        request_params = {
            "model": model_id,
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "cache_control": {"type": "ephemeral"},
        }
        
        # Add thinking mode support
        # https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking
        # When thinking is enabled, temperature MUST NOT be set
        if kwargs.get("thinking"):
            request_params["thinking"] = kwargs["thinking"]
            if "max_tokens" in kwargs:
                request_params["max_tokens"] = kwargs["max_tokens"]
        else:
            request_params["temperature"] = kwargs.get("temperature", 0.7)
        
        if system_message:
            request_params["system"] = system_message
        if tools:
            request_params["tools"] = tools

        betas = self._beta_flags_for_request(
            thinking_enabled=bool(kwargs.get("thinking")),
            has_tools=bool(tools),
        )
        if betas and hasattr(client, "beta") and hasattr(client.beta, "messages"):
            response = await client.beta.messages.create(**request_params, betas=betas)
        else:
            response = await client.messages.create(**request_params)
        
        # Parse response content
        content_parts = []
        tool_calls = []
        
        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input),
                    }
                })
        
        return ChatResponse(
            id=response.id,
            model=response.model,
            content="\n".join(content_parts),
            finish_reason="tool_calls" if tool_calls else (response.stop_reason or "stop"),
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
                "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            }
        )
    
    async def chat_stream(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs
    ) -> AsyncIterator[StreamChunk]:
        """
        Send streaming chat completion request to Anthropic.
        
        Handles both text and tool_use content blocks.
        """
        client = self._get_client()
        
        # Convert messages
        anthropic_messages = self._format_messages_anthropic(messages)
        
        # Check if we have any non-system messages
        if not anthropic_messages:
            log.error("anthropic.no_messages", {"total_messages": len(messages)})
            raise ValueError("No non-system messages provided to Anthropic API")
        
        system_message = next(
            (msg.content for msg in messages if msg.role == "system"),
            None
        )
        
        # Convert tools
        tools = self._convert_tools(kwargs.get("tools"))
        
        # Build request params
        request_params = {
            "model": model_id,
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "cache_control": {"type": "ephemeral"},
        }
        
        # Add thinking mode support for streaming
        # https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking
        # When thinking is enabled:
        #   - temperature MUST NOT be set (API rejects it)
        #   - max_tokens must be > budget_tokens
        if kwargs.get("thinking"):
            request_params["thinking"] = kwargs["thinking"]
            # Override max_tokens if provided in kwargs
            if "max_tokens" in kwargs:
                request_params["max_tokens"] = kwargs["max_tokens"]
            # Do NOT set temperature when thinking is enabled
        else:
            # Only set temperature when thinking is NOT enabled
            request_params["temperature"] = kwargs.get("temperature", 0.7)
        
        if system_message:
            request_params["system"] = system_message
        if tools:
            request_params["tools"] = tools

        betas = self._beta_flags_for_request(
            thinking_enabled=bool(kwargs.get("thinking")),
            has_tools=bool(tools),
        )

        # Track tool calls during streaming
        current_tool_id: Optional[str] = None
        current_tool_name: Optional[str] = None
        current_tool_input: str = ""
        current_reasoning_open = False
        current_reasoning_signature: Optional[str] = None
        current_redacted_thinking_data: Optional[str] = None
        # Track token usage from streaming events
        input_tokens: int = 0
        output_tokens: int = 0
        cache_read_tokens: int = 0
        cache_write_tokens: int = 0
        
        try:
            stream_target = client.messages
            stream_kwargs = dict(request_params)
            if betas and hasattr(client, "beta") and hasattr(client.beta, "messages"):
                stream_target = client.beta.messages
                stream_kwargs["betas"] = betas

            async with stream_target.stream(**stream_kwargs) as stream:
                async for event in stream:
                    # Handle different event types
                    if event.type == "message_start":
                        # Capture initial token counts (input tokens + cache)
                        msg = getattr(event, 'message', None)
                        if msg:
                            usage = getattr(msg, 'usage', None)
                            if usage:
                                input_tokens = getattr(usage, 'input_tokens', 0) or 0
                                output_tokens = getattr(usage, 'output_tokens', 0) or 0
                                cache_read_tokens = getattr(usage, 'cache_read_input_tokens', 0) or 0
                                cache_write_tokens = getattr(usage, 'cache_creation_input_tokens', 0) or 0

                    elif event.type == "content_block_start":
                        block = event.content_block
                        if hasattr(block, 'type'):
                            if block.type == "tool_use":
                                current_tool_id = block.id
                                current_tool_name = block.name
                                current_tool_input = ""
                            elif block.type == "thinking":
                                current_reasoning_open = True
                                current_reasoning_signature = None
                                current_redacted_thinking_data = None
                                yield StreamChunk(
                                    event_type="reasoning-start",
                                    metadata=self._reasoning_metadata(
                                        provider_id=self.id,
                                        model_id=model_id,
                                        reasoning_source="anthropic_thinking",
                                    ),
                                )
                            elif block.type == "redacted_thinking":
                                current_reasoning_open = True
                                current_reasoning_signature = None
                                current_redacted_thinking_data = getattr(block, "data", None)
                                yield StreamChunk(
                                    event_type="reasoning-start",
                                    metadata=self._reasoning_metadata(
                                        provider_id=self.id,
                                        model_id=model_id,
                                        reasoning_source="anthropic_redacted_thinking",
                                        redacted_thinking_data=current_redacted_thinking_data,
                                    ),
                                )
                    
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if hasattr(delta, 'type'):
                            if delta.type == "text_delta":
                                yield StreamChunk(delta=delta.text, finish_reason=None)
                            elif delta.type == "thinking_delta":
                                # Stream thinking content as reasoning
                                yield StreamChunk(
                                    event_type="reasoning",
                                    reasoning=delta.thinking,
                                    finish_reason=None,
                                    metadata=self._reasoning_metadata(
                                        provider_id=self.id,
                                        model_id=model_id,
                                        reasoning_content=delta.thinking,
                                        reasoning_source="anthropic_thinking",
                                    ),
                                )
                            elif delta.type == "signature_delta":
                                current_reasoning_signature = getattr(delta, "signature", None)
                            elif delta.type == "input_json_delta":
                                current_tool_input += delta.partial_json
                    
                    elif event.type == "content_block_stop":
                        # Finalize tool call if we were building one
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
                            yield StreamChunk(
                                event_type="reasoning-end",
                                metadata=self._reasoning_metadata(
                                    provider_id=self.id,
                                    model_id=model_id,
                                    reasoning_source=(
                                        "anthropic_redacted_thinking"
                                        if current_redacted_thinking_data
                                        else "anthropic_thinking"
                                    ),
                                    thinking_signature=current_reasoning_signature,
                                    redacted_thinking_data=current_redacted_thinking_data,
                                ),
                            )
                            current_reasoning_open = False
                            current_reasoning_signature = None
                            current_redacted_thinking_data = None

                    elif event.type == "message_delta":
                        # Capture output token count (accumulates during streaming)
                        usage = getattr(event, 'usage', None)
                        if usage:
                            output_tokens = getattr(usage, 'output_tokens', output_tokens) or output_tokens
                    
                    elif event.type == "message_stop":
                        # Build usage metadata from captured token counts.
                        # Use OpenAI-compatible key names (prompt_tokens / completion_tokens)
                        # so that runner.py can read them without provider-specific branching.
                        usage_meta: Dict[str, Any] = {}
                        if input_tokens or output_tokens or cache_read_tokens:
                            usage_meta = {
                                "prompt_tokens": input_tokens,
                                "completion_tokens": output_tokens,
                                "total_tokens": input_tokens + output_tokens,
                                # Keep Anthropic-specific cache fields for future use
                                "cache_read_input_tokens": cache_read_tokens,
                                "cache_creation_input_tokens": cache_write_tokens,
                            }
                            log.info("anthropic.stream.usage", {
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "cache_read": cache_read_tokens,
                                "cache_write": cache_write_tokens,
                            })
                        yield StreamChunk(
                            delta="",
                            finish_reason="stop",
                            usage=usage_meta if usage_meta else None,
                        )
        
        except Exception as e:
            # Catch and log stream errors, but don't propagate harmless connection close errors
            error_msg = str(e).lower()
            if "peer closed" in error_msg or "incomplete chunked read" in error_msg:
                # This is a known Anthropic SDK issue when stream ends after tool calls
                # The stream has actually completed successfully, so we can safely ignore this
                log.debug("anthropic.stream.harmless_close", {"error": str(e)})
            elif "list index out of range" in error_msg:
                # Fallback to non-streaming request if streaming fails unexpectedly
                log.warn("anthropic.stream.fallback_to_chat", {"error": str(e)})
                try:
                    response = await client.messages.create(**request_params)
                    content_parts = []
                    tool_calls = []
                    
                    for block in response.content:
                        if block.type == "text":
                            content_parts.append(block.text)
                        elif block.type == "thinking":
                            thinking_text = getattr(block, "thinking", None) or ""
                            yield StreamChunk(
                                event_type="reasoning-start",
                                metadata=self._reasoning_metadata(
                                    provider_id=self.id,
                                    model_id=model_id,
                                    reasoning_source="anthropic_thinking",
                                ),
                            )
                            if thinking_text:
                                yield StreamChunk(
                                    event_type="reasoning",
                                    reasoning=thinking_text,
                                    metadata=self._reasoning_metadata(
                                        provider_id=self.id,
                                        model_id=model_id,
                                        reasoning_content=thinking_text,
                                        reasoning_source="anthropic_thinking",
                                    ),
                                )
                            yield StreamChunk(
                                event_type="reasoning-end",
                                metadata=self._reasoning_metadata(
                                    provider_id=self.id,
                                    model_id=model_id,
                                    reasoning_source="anthropic_thinking",
                                    thinking_signature=getattr(block, "signature", None),
                                ),
                            )
                        elif block.type == "redacted_thinking":
                            redacted_data = getattr(block, "data", None)
                            yield StreamChunk(
                                event_type="reasoning-start",
                                metadata=self._reasoning_metadata(
                                    provider_id=self.id,
                                    model_id=model_id,
                                    reasoning_source="anthropic_redacted_thinking",
                                    redacted_thinking_data=redacted_data,
                                ),
                            )
                            yield StreamChunk(
                                event_type="reasoning-end",
                                metadata=self._reasoning_metadata(
                                    provider_id=self.id,
                                    model_id=model_id,
                                    reasoning_source="anthropic_redacted_thinking",
                                    redacted_thinking_data=redacted_data,
                                ),
                            )
                        elif block.type == "tool_use":
                            tool_calls.append({
                                "id": block.id,
                                "type": "function",
                                "function": {
                                    "name": block.name,
                                    "arguments": json.dumps(block.input),
                                }
                            })
                    
                    content = "\n".join(content_parts)
                    if tool_calls:
                        yield StreamChunk(
                            delta=content,
                            finish_reason="tool_calls",
                            tool_calls=tool_calls,
                        )
                    else:
                        yield StreamChunk(delta=content, finish_reason="stop")
                except Exception as fallback_e:
                    log.error("anthropic.stream.fallback_failed", {"error": str(fallback_e)})
                    raise
            else:
                # This is a real error, propagate it
                log.error("anthropic.stream.error", {"error": str(e)})
                raise
