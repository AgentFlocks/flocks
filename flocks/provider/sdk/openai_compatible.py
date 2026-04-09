"""
OpenAI Compatible API provider implementation

Supports any API that implements OpenAI's chat completions API, including:
- Ollama
- LM Studio
- vLLM
- text-generation-webui
- And many others
"""

import asyncio
from typing import Dict, List, AsyncIterator, Optional, Any
import os

from flocks.provider.provider import (
    BaseProvider,
    ModelInfo,
    ModelCapabilities,
    ChatMessage,
    ChatResponse,
    StreamChunk,
)
from flocks.provider.sdk.openai_base import (
    ThinkTagExtractor,
    _normalize_stream_usage,
    _supports_include_usage_fallback,
    extract_reasoning_content,
)
from flocks.utils.log import Log

log = Log.create(service="provider.openai_compatible")


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI Compatible API provider"""

    _MINIMAX_EMPTY_RESPONSE_TARGETS = {
        "minimax-m2.5",
        "minimax-m2.7",
    }
    _MINIMAX_EMPTY_RESPONSE_RETRY_DELAY_SECONDS = 3
    
    def __init__(self):
        super().__init__(provider_id="openai-compatible", name="OpenAI Compatible")
        self._api_key = os.getenv("OPENAI_COMPATIBLE_API_KEY", "not-needed")
        self._base_url = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
        self._client = None
    
    def _get_client(self):
        """Get or create OpenAI Compatible client"""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                
                # Get API key (many local services don't need one)
                api_key = self._config.api_key if self._config else self._api_key
                if not api_key:
                    api_key = os.getenv("OPENAI_COMPATIBLE_API_KEY", "not-needed")
                
                # Get base URL (required for compatible APIs)
                base_url = None
                if self._config and self._config.base_url:
                    base_url = self._config.base_url
                else:
                    base_url = self._base_url or os.getenv("OPENAI_COMPATIBLE_BASE_URL")
                
                if not base_url:
                    raise ValueError("OpenAI Compatible base URL not configured. Set OPENAI_COMPATIBLE_BASE_URL environment variable.")
                
                # Create client
                self._client = AsyncOpenAI(
                    api_key=api_key,
                    base_url=base_url,
                )
                self.log.info("openai_compatible.client.created", {"base_url": base_url})
                    
            except ImportError:
                raise ImportError("openai package not installed. Install with: pip install openai")
        return self._client
    
    def get_models(self) -> List[ModelInfo]:
        """Return models from flocks.json (_config_models) only.

        OpenAI-compatible providers have no predefined model list; users add
        their own models via the model management UI.
        """
        return list(getattr(self, "_config_models", []))

    @classmethod
    def _normalize_model_id(cls, model_id: str) -> str:
        return model_id.strip().lower().replace("_", "-")

    @classmethod
    def _is_minimax_empty_response_target(cls, model_id: str) -> bool:
        normalized = cls._normalize_model_id(model_id)
        return any(target in normalized for target in cls._MINIMAX_EMPTY_RESPONSE_TARGETS)

    @staticmethod
    def _has_non_empty_text_content(content: Any) -> bool:
        return isinstance(content, str) and bool(content.strip())

    async def _sleep_before_minimax_empty_retry(self, model_id: str, stage: str, has_tools: bool) -> None:
        if not self._is_minimax_empty_response_target(model_id):
            return
        delay_seconds = self._MINIMAX_EMPTY_RESPONSE_RETRY_DELAY_SECONDS
        self.log.warn("openai_compatible.minimax_empty_response_retry_wait", {
            "model": model_id,
            "has_tools": has_tools,
            "fallback_stage": stage,
            "attempt_type": "provider_fallback",
            "wait_before_retry_seconds": delay_seconds,
        })
        await asyncio.sleep(delay_seconds)
    
    @staticmethod
    def _format_content(content: Any) -> Any:
        if not isinstance(content, list):
            return content

        formatted: list[dict] = []
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
            m: dict = {
                "role": msg.role,
                "content": OpenAICompatibleProvider._format_content(msg.content),
            }
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
        """Send chat completion request to compatible API"""
        client = self._get_client()
        
        # Convert messages to OpenAI format
        formatted_messages = self._format_messages(messages)
        
        # Extract parameters
        max_tokens = kwargs.get("max_tokens")
        tools = kwargs.get("tools")
        thinking = kwargs.get("thinking")
        
        # Make request
        request_params = {
            "model": model_id,
            "messages": formatted_messages,
        }

        if thinking:
            request_params["extra_body"] = {"thinking": thinking}
        else:
            temperature = kwargs.get("temperature")
            if temperature is not None:
                request_params["temperature"] = temperature
        
        if max_tokens:
            request_params["max_tokens"] = max_tokens
        if tools:
            # Some compatible APIs don't support tools
            try:
                request_params["tools"] = tools
            except Exception:
                self.log.warn("openai_compatible.tools.not_supported", {"model": model_id})
        
        response = await client.chat.completions.create(**request_params)

        # Format response
        choice = response.choices[0]
        msg = getattr(choice, "message", None)
        if msg is None:
            raise ValueError(
                f"OpenAI-compatible API returned choice with null message. model={model_id}"
            )
        return ChatResponse(
            id=response.id,
            model=response.model,
            content=msg.content or "",
            finish_reason=choice.finish_reason,
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
        """Send streaming chat completion request to compatible API"""
        client = self._get_client()
        
        # Convert messages to OpenAI format
        formatted_messages = self._format_messages(messages)
        
        # Extract parameters
        max_tokens = kwargs.get("max_tokens")
        tools = kwargs.get("tools")
        thinking = kwargs.get("thinking")
        
        # Make streaming request
        request_params = {
            "model": model_id,
            "messages": formatted_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if thinking:
            request_params["extra_body"] = {"thinking": thinking}
        else:
            temperature = kwargs.get("temperature")
            if temperature is not None:
                request_params["temperature"] = temperature
        
        if max_tokens:
            request_params["max_tokens"] = max_tokens
        if tools:
            # Some compatible APIs don't support tools
            try:
                request_params["tools"] = tools
            except Exception:
                self.log.warn("openai_compatible.tools.not_supported", {"model": model_id})
        
        self.log.info("openai_compatible.stream.request", {
            "model": model_id,
            "thinking_enabled": bool(thinking),
            "has_tools": bool(tools),
            "max_tokens": max_tokens,
            "include_usage": True,
        })

        try:
            stream = await client.chat.completions.create(**request_params)
        except Exception as exc:
            if not _supports_include_usage_fallback(exc):
                raise
            self.log.warn("openai_compatible.stream.include_usage_unsupported", {
                "model": model_id,
                "error": str(exc),
            })
            request_params = dict(request_params)
            request_params.pop("stream_options", None)
            stream = await client.chat.completions.create(**request_params)

        # Stateful extractor to separate <think>...</think> from content.
        think_extractor = ThinkTagExtractor()
        _first_delta_logged = False
        emitted_substantive_chunk = False
        stream_usage: Optional[Dict[str, int]] = None

        async for chunk in stream:
            normalized_usage = _normalize_stream_usage(getattr(chunk, "usage", None))
            if normalized_usage:
                stream_usage = normalized_usage
            if chunk.choices:
                choice = chunk.choices[0]
                delta = choice.delta
                yielded_finish_for_this_chunk = False

                if delta is not None:
                    if not _first_delta_logged:
                        _first_delta_logged = True
                        try:
                            delta_attrs = {
                                k: type(v).__name__
                                for k, v in vars(delta).items()
                                if v is not None and k != "__pydantic_fields_set__"
                            }
                        except TypeError:
                            delta_attrs = {}
                        extra = getattr(delta, "model_extra", None)
                        self.log.info("openai_compatible.stream.first_delta", {
                            "delta_attrs": delta_attrs,
                            "model_extra_keys": list(extra.keys()) if extra else [],
                            "has_content": bool(getattr(delta, "content", None)),
                            "has_tool_calls": bool(getattr(delta, "tool_calls", None)),
                        })

                    # Handle reasoning/thinking content (DeepSeek R1, GLM, Claude proxies, etc.)
                    reasoning = extract_reasoning_content(delta)
                    if reasoning:
                        emitted_substantive_chunk = True
                        yield StreamChunk(
                            event_type="reasoning",
                            reasoning=reasoning,
                            finish_reason=None,
                        )

                    # Handle text content – extract inline <think> tags if present
                    delta_text = getattr(delta, "content", None)
                    if delta_text:
                        segments = think_extractor.process(delta_text)
                        for seg_type, seg_text in segments:
                            if seg_type == "reasoning":
                                if seg_text:
                                    emitted_substantive_chunk = True
                                yield StreamChunk(
                                    event_type="reasoning",
                                    reasoning=seg_text,
                                    finish_reason=None,
                                )
                            else:
                                if seg_text:
                                    emitted_substantive_chunk = True
                                yield StreamChunk(
                                    delta=seg_text,
                                    finish_reason=None,
                                )

                # Flush think extractor on finish (before tool-call chunks; matches prior behavior)
                if choice.finish_reason:
                    for seg_type, seg_text in think_extractor.flush():
                        if seg_type == "reasoning":
                            if seg_text:
                                emitted_substantive_chunk = True
                            yield StreamChunk(
                                event_type="reasoning",
                                reasoning=seg_text,
                                finish_reason=None,
                            )
                        else:
                            if seg_text:
                                emitted_substantive_chunk = True
                            yield StreamChunk(
                                delta=seg_text,
                                finish_reason=None,
                            )

                if delta is not None:
                    delta_tcs = getattr(delta, "tool_calls", None)
                    if delta_tcs:
                        emitted_substantive_chunk = True
                        # Convert OpenAI tool_calls format to our format
                        # Note: OpenAI streaming sends tool_calls incrementally:
                        # - First chunk: id and name (and maybe empty arguments)
                        # - Subsequent chunks: arguments in pieces (with index but no id/name)
                        # We need to send ALL chunks, even those without id
                        tool_calls = []
                        for tool_call in delta_tcs:
                            # Include the tool call even if it only has index (for argument increments)
                            # The runner will use the index to accumulate arguments
                            tc_dict = {
                                "index": tool_call.index if hasattr(tool_call, "index") else 0,
                                "id": tool_call.id if tool_call.id else None,
                                "type": "function",
                                "function": {
                                    "name": tool_call.function.name if (tool_call.function and tool_call.function.name) else None,
                                    "arguments": tool_call.function.arguments if (tool_call.function and tool_call.function.arguments) else None,
                                }
                            }
                            tool_calls.append(tc_dict)

                        if tool_calls:
                            yield StreamChunk(
                                delta="",  # Empty string, not None
                                finish_reason=choice.finish_reason,
                                tool_calls=tool_calls,
                                usage=stream_usage,
                            )
                            yielded_finish_for_this_chunk = True

                if choice.finish_reason and not yielded_finish_for_this_chunk:
                    yield StreamChunk(
                        delta="",
                        finish_reason=choice.finish_reason,
                        usage=stream_usage,
                    )

        if not emitted_substantive_chunk:
            self.log.warn("openai_compatible.stream.empty_response", {
                "model": model_id,
                "has_tools": bool(tools),
            })
            minimax_empty_response_target = self._is_minimax_empty_response_target(model_id)
            self.log.debug("openai_compatible.minimax_empty_response_target_check", {
                "model": model_id,
                "normalized_model": self._normalize_model_id(model_id),
                "is_target": minimax_empty_response_target,
                "has_tools": bool(tools),
            })
            if minimax_empty_response_target:
                self.log.warn("openai_compatible.minimax_empty_response_fallback_started", {
                    "model": model_id,
                    "has_tools": bool(tools),
                    "attempt_type": "provider_fallback",
                })
            fallback_error: Optional[Exception] = None
            try:
                await self._sleep_before_minimax_empty_retry(
                    model_id,
                    stage="chat_fallback",
                    has_tools=bool(tools),
                )
                fallback = await self.chat(model_id, messages, **kwargs)
                fallback_content = fallback.content or ""
                self.log.debug("openai_compatible.minimax_empty_response_fallback_result", {
                    "model": model_id,
                    "fallback_stage": "chat_fallback",
                    "has_tools": bool(tools),
                    "content_length": len(fallback_content),
                    "finish_reason": fallback.finish_reason,
                    "is_target": minimax_empty_response_target,
                })
                if (
                    self._has_non_empty_text_content(fallback_content)
                    if minimax_empty_response_target
                    else bool(fallback_content)
                ):
                    if minimax_empty_response_target:
                        self.log.info("openai_compatible.minimax_empty_response_recovered", {
                            "model": model_id,
                            "has_tools": bool(tools),
                            "fallback_stage": "chat_fallback",
                            "attempt_type": "provider_fallback",
                        })
                    yield StreamChunk(
                        delta=fallback_content,
                        finish_reason=fallback.finish_reason or "stop",
                        usage=fallback.usage or None,
                    )
                    return
                if minimax_empty_response_target:
                    self.log.warn("openai_compatible.minimax_empty_response_blank_fallback", {
                        "model": model_id,
                        "has_tools": bool(tools),
                        "fallback_stage": "chat_fallback",
                        "attempt_type": "provider_fallback",
                        "content_length": len(fallback_content),
                    })
            except Exception as exc:
                fallback_error = exc
                if minimax_empty_response_target:
                    self.log.warn("openai_compatible.minimax_empty_response_fallback_error", {
                        "model": model_id,
                        "has_tools": bool(tools),
                        "fallback_stage": "chat_fallback",
                        "attempt_type": "provider_fallback",
                        "error": str(exc),
                    })

            if tools:
                self.log.warn("openai_compatible.stream.retry_without_tools", {
                    "model": model_id,
                    "reason": str(fallback_error) if fallback_error else "empty_fallback_content",
                })
                self.log.debug("openai_compatible.minimax_empty_response_retry_without_tools_start", {
                    "model": model_id,
                    "has_tools": True,
                    "is_target": minimax_empty_response_target,
                })
                await self._sleep_before_minimax_empty_retry(
                    model_id,
                    stage="no_tools_fallback",
                    has_tools=bool(tools),
                )
                no_tool_kwargs = dict(kwargs)
                no_tool_kwargs.pop("tools", None)
                fallback = await self.chat(model_id, messages, **no_tool_kwargs)
                fallback_content = fallback.content or ""
                self.log.debug("openai_compatible.minimax_empty_response_fallback_result", {
                    "model": model_id,
                    "fallback_stage": "no_tools_fallback",
                    "has_tools": False,
                    "content_length": len(fallback_content),
                    "finish_reason": fallback.finish_reason,
                    "is_target": minimax_empty_response_target,
                })
                if (
                    self._has_non_empty_text_content(fallback_content)
                    if minimax_empty_response_target
                    else bool(fallback_content)
                ):
                    if minimax_empty_response_target:
                        self.log.info("openai_compatible.minimax_empty_response_recovered", {
                            "model": model_id,
                            "has_tools": bool(tools),
                            "fallback_stage": "no_tools_fallback",
                            "attempt_type": "provider_fallback",
                        })
                    yield StreamChunk(
                        delta=fallback_content,
                        finish_reason=fallback.finish_reason or "stop",
                        usage=fallback.usage or None,
                    )
                    return
                if minimax_empty_response_target:
                    self.log.warn("openai_compatible.minimax_empty_response_blank_fallback", {
                        "model": model_id,
                        "has_tools": bool(tools),
                        "fallback_stage": "no_tools_fallback",
                        "attempt_type": "provider_fallback",
                        "content_length": len(fallback_content),
                    })

            if fallback_error:
                if minimax_empty_response_target:
                    self.log.error("openai_compatible.minimax_empty_response_fallback_exhausted", {
                        "model": model_id,
                        "has_tools": bool(tools),
                        "attempt_type": "provider_fallback",
                        "final_action": "raise_original_fallback_error",
                        "error": str(fallback_error),
                    })
                raise fallback_error
            if minimax_empty_response_target:
                self.log.error("openai_compatible.minimax_empty_response_fallback_exhausted", {
                    "model": model_id,
                    "has_tools": bool(tools),
                    "attempt_type": "provider_fallback",
                    "final_action": "raise_empty_output_error",
                })
                raise ValueError(
                    f"MiniMax target model returned empty output after provider fallback chain. "
                    f"model={model_id}"
                )
            raise ValueError(
                f"OpenAI-compatible API returned an empty streaming response and empty fallback response. "
                f"model={model_id}"
            )
