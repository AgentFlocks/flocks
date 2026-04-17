"""
Base class for OpenAI-compatible providers.

Provides standard chat/chat_stream implementation that works with any
OpenAI-compatible API (DeepSeek, Volcengine, Alibaba, Tencent, SiliconFlow, etc.).

Subclasses only need to define class attributes and get_models().
"""

import os
from typing import Any, AsyncIterator, Dict, List, Optional

from flocks.provider.provider import (
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ModelCapabilities,
    ModelInfo,
    StreamChunk,
)
from flocks.utils.log import Log

log = Log.create(service="provider.openai_base")


def _coerce_bool(value: Any, default: bool) -> bool:
    """Coerce loosely-typed config values to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def resolve_verify_ssl(custom_settings: Any, default: bool = True) -> bool:
    """Resolve the SSL verification flag from provider custom settings."""
    if not isinstance(custom_settings, dict):
        return default

    if "verify_ssl" in custom_settings:
        return _coerce_bool(custom_settings.get("verify_ssl"), default)
    if "ssl_verify" in custom_settings:
        return _coerce_bool(custom_settings.get("ssl_verify"), default)
    return default


def _normalize_stream_usage(raw_usage: Any) -> Optional[Dict[str, int]]:
    """Normalize provider usage objects to a shared stream usage schema."""
    if not raw_usage:
        return None

    _pt = getattr(raw_usage, "prompt_tokens", None)
    prompt_tokens = (_pt if _pt is not None else getattr(raw_usage, "input_tokens", 0)) or 0
    _ct = getattr(raw_usage, "completion_tokens", None)
    completion_tokens = (_ct if _ct is not None else getattr(raw_usage, "output_tokens", 0)) or 0
    reasoning_tokens = getattr(raw_usage, "reasoning_tokens", 0) or 0
    total_tokens = getattr(raw_usage, "total_tokens", 0) or (
        prompt_tokens + completion_tokens + reasoning_tokens
    )
    cache_read_tokens = getattr(raw_usage, "cache_read_input_tokens", 0) or 0
    cache_write_tokens = getattr(raw_usage, "cache_creation_input_tokens", 0) or 0

    if not any(
        [
            prompt_tokens,
            completion_tokens,
            reasoning_tokens,
            total_tokens,
            cache_read_tokens,
            cache_write_tokens,
        ]
    ):
        return None

    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    if reasoning_tokens:
        usage["reasoning_tokens"] = reasoning_tokens
    if cache_read_tokens:
        usage["cache_read_input_tokens"] = cache_read_tokens
    if cache_write_tokens:
        usage["cache_creation_input_tokens"] = cache_write_tokens
    return usage


def _supports_include_usage_fallback(exc: Exception) -> bool:
    """Return True when the provider rejects OpenAI stream usage options."""
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "stream_options",
            "include_usage",
            "unknown parameter",
            "unsupported parameter",
            "extra inputs are not permitted",
            "extra fields not permitted",
        )
    )


class ThinkTagExtractor:
    """Extract reasoning content from streaming LLM output.

    Handles two patterns that small/mid-size models use to emit
    chain-of-thought (CoT) content within the ``content`` field:

    1. **Explicit ``<think>...</think>`` tags** – used by GLM, DeepSeek-R1-
       distill and similar models.
    2. **Leaked CoT markers** – some models (notably GLM-4-7B) output their
       thinking *outside* ``<think>`` tags using patterns like
       ``Tool Call: …\\nInput: …\\nOutput: …``.  Once a leaked-CoT marker is
       detected, all subsequent content is classified as reasoning until the
       next ``<think>`` tag appears or the stream ends.

    Edge-case handling:
    * Tags / markers may be split across arbitrary chunk boundaries.
    * A stray ``</think>`` appearing before any ``<think>`` treats everything
      before it as reasoning (the model started thinking implicitly).
    * Multiple ``<think>...</think>`` blocks are supported.
    """

    OPEN_TAG = "<think>"
    CLOSE_TAG = "</think>"

    # Patterns that indicate leaked chain-of-thought output.  When one of
    # these appears at the start of text content or right after a newline,
    # everything from that point is treated as reasoning.
    _LEAKED_COT_MARKERS = (
        "Tool Call:",
        "Thinking:",
        "Tool Call: ",
        "Thinking: ",
    )

    def __init__(self) -> None:
        self._in_think = False
        self._in_leaked_cot = False  # implicit reasoning via leaked markers
        self._buffer = ""
        self._seen_open = False

    # ------------------------------------------------------------------

    def process(self, text: str) -> list:
        """Feed a new text chunk and return ``[(type, content), ...]``.

        *type* is ``"reasoning"`` or ``"text"``.
        """
        results: list = []
        self._buffer += text

        while self._buffer:
            # --- Inside an explicit <think> block ---
            if self._in_think:
                close_idx = self._buffer.find(self.CLOSE_TAG)
                if close_idx != -1:
                    reasoning = self._buffer[:close_idx]
                    if reasoning:
                        results.append(("reasoning", reasoning))
                    self._buffer = self._buffer[close_idx + len(self.CLOSE_TAG) :]
                    self._in_think = False
                else:
                    partial = self._partial_suffix(self._buffer, self.CLOSE_TAG)
                    if partial > 0:
                        safe = self._buffer[: -partial]
                        if safe:
                            results.append(("reasoning", safe))
                        self._buffer = self._buffer[-partial:]
                    else:
                        results.append(("reasoning", self._buffer))
                        self._buffer = ""
                    break

            # --- Inside a leaked-CoT block (implicit reasoning) ---
            elif self._in_leaked_cot:
                # Exit leaked-CoT mode when an explicit <think> appears
                open_idx = self._buffer.find(self.OPEN_TAG)
                if open_idx != -1:
                    before = self._buffer[:open_idx]
                    if before:
                        results.append(("reasoning", before))
                    self._buffer = self._buffer[open_idx + len(self.OPEN_TAG) :]
                    self._in_leaked_cot = False
                    self._in_think = True
                    self._seen_open = True
                else:
                    partial = self._partial_suffix(self._buffer, self.OPEN_TAG)
                    if partial > 0:
                        safe = self._buffer[: -partial]
                        if safe:
                            results.append(("reasoning", safe))
                        self._buffer = self._buffer[-partial:]
                    else:
                        results.append(("reasoning", self._buffer))
                        self._buffer = ""
                    break

            # --- Outside any reasoning block (text mode) ---
            else:
                open_idx = self._buffer.find(self.OPEN_TAG)
                close_idx = self._buffer.find(self.CLOSE_TAG)

                # Stray </think>: treat preceding content as reasoning.
                if close_idx != -1 and (open_idx == -1 or close_idx < open_idx):
                    before = self._buffer[:close_idx]
                    if before:
                        results.append(("reasoning", before))
                    self._buffer = self._buffer[close_idx + len(self.CLOSE_TAG) :]
                    continue

                # Check for leaked-CoT markers (e.g. "Tool Call:")
                cot_idx = self._find_leaked_cot(self._buffer)

                # Determine earliest marker among <think> and leaked-CoT
                first_marker = None
                first_pos = len(self._buffer)
                if open_idx != -1 and open_idx < first_pos:
                    first_marker = "think"
                    first_pos = open_idx
                if cot_idx != -1 and cot_idx < first_pos:
                    first_marker = "cot"
                    first_pos = cot_idx

                if first_marker == "think":
                    before = self._buffer[:open_idx]
                    if before:
                        results.append(("text", before))
                    self._buffer = self._buffer[open_idx + len(self.OPEN_TAG) :]
                    self._in_think = True
                    self._seen_open = True
                elif first_marker == "cot":
                    before = self._buffer[:cot_idx]
                    if before:
                        results.append(("text", before))
                    # Keep the marker text in the buffer as reasoning content
                    self._buffer = self._buffer[cot_idx:]
                    self._in_leaked_cot = True
                else:
                    # No marker found – check for partial prefixes at the
                    # end of the buffer (tag or leaked-CoT marker).
                    partial = self._max_partial(self._buffer)
                    if partial > 0:
                        safe = self._buffer[: -partial]
                        if safe:
                            results.append(("text", safe))
                        self._buffer = self._buffer[-partial:]
                    else:
                        results.append(("text", self._buffer))
                        self._buffer = ""
                    break

        return results

    def flush(self) -> list:
        """Flush remaining buffer at end of stream."""
        results: list = []
        if self._buffer:
            kind = "reasoning" if (self._in_think or self._in_leaked_cot) else "text"
            results.append((kind, self._buffer))
            self._buffer = ""
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_leaked_cot(self, buf: str) -> int:
        """Return index of the earliest leaked-CoT marker, or -1.

        Markers are recognised at the start of the buffer or after a newline.
        """
        best = -1
        for marker in self._LEAKED_COT_MARKERS:
            # At start of buffer
            if buf.startswith(marker):
                return 0
            # After a newline
            needle = "\n" + marker
            idx = buf.find(needle)
            if idx != -1:
                # Point to the marker itself (skip the newline)
                pos = idx + 1
                if best == -1 or pos < best:
                    best = pos
        return best

    def _max_partial(self, buf: str) -> int:
        """Return the longest suffix of *buf* matching a prefix of any marker."""
        best = 0
        # Check think tags
        best = max(best, self._partial_suffix(buf, self.OPEN_TAG))
        best = max(best, self._partial_suffix(buf, self.CLOSE_TAG))
        # Check leaked-CoT markers (with leading newline, since that's how
        # they appear mid-stream)
        for marker in self._LEAKED_COT_MARKERS:
            best = max(best, self._partial_suffix(buf, "\n" + marker))
        return best

    @staticmethod
    def _partial_suffix(text: str, tag: str) -> int:
        """Return length of longest suffix of *text* that is a prefix of *tag*."""
        max_check = min(len(tag) - 1, len(text))
        for i in range(max_check, 0, -1):
            if text.endswith(tag[:i]):
                return i
        return 0


_REASONING_FIELDS = (
    "reasoning_content",
    "thinking_content",
    "thinking",
    "reasoning",
)


def extract_reasoning_content(delta) -> Optional[str]:
    """Extract reasoning/thinking content from a streaming delta object.

    Supports multiple provider/proxy formats:
    - Direct attribute: OpenAI o-series, DeepSeek R1 (reasoning_content)
    - Anthropic-compatible proxies (thinking, thinking_content)
    - model_extra dict: GLM, other OpenAI-compatible APIs

    This is a shared utility used by OpenAIBaseProvider, OpenAIProvider,
    and OpenAICompatibleProvider.
    """
    if delta is None:
        return None
    for field in _REASONING_FIELDS:
        value = getattr(delta, field, None)
        if value is not None:
            return value
    extra = getattr(delta, "model_extra", None)
    if extra and isinstance(extra, dict):
        for field in _REASONING_FIELDS:
            value = extra.get(field)
            if value is not None:
                return value
    return None


class OpenAIBaseProvider(BaseProvider):
    """Base class for providers using OpenAI-compatible API.

    Subclasses should set:
        DEFAULT_BASE_URL: Default API endpoint
        ENV_API_KEY: List of env var names for API key lookup
        ENV_BASE_URL: Env var name for base URL override
        CATALOG_ID: ID in catalog.json (for get_meta / get_model_definitions)
    """

    DEFAULT_BASE_URL: str = ""
    ENV_API_KEY: List[str] = []
    ENV_BASE_URL: str = ""
    CATALOG_ID: str = ""

    def __init__(self, provider_id: str, name: str):
        super().__init__(provider_id=provider_id, name=name)
        self._api_key: Optional[str] = self._resolve_env_key()
        self._base_url: str = (
            os.getenv(self.ENV_BASE_URL, self.DEFAULT_BASE_URL)
            if self.ENV_BASE_URL
            else self.DEFAULT_BASE_URL
        )
        self._client = None

    # ==================== Configuration ====================

    def _resolve_env_key(self) -> Optional[str]:
        """Resolve API key from environment variables."""
        for env_var in self.ENV_API_KEY:
            val = os.getenv(env_var)
            if val:
                return val
        return None

    def is_configured(self) -> bool:
        """Check if provider has a valid API key."""
        api_key = self._config.api_key if self._config else self._api_key
        return bool(api_key)

    def _get_client(self):
        """Get or create AsyncOpenAI client."""
        if self._client is None:
            from openai import AsyncOpenAI
            import httpx

            api_key = self._config.api_key if self._config else self._api_key
            if not api_key:
                env_hint = self.ENV_API_KEY[0] if self.ENV_API_KEY else "API_KEY"
                raise ValueError(
                    f"{self.name} API key not configured. Set {env_hint}."
                )

            base_url = (
                self._config.base_url
                if self._config and self._config.base_url
                else self._base_url
            )

            custom_settings = getattr(self._config, "custom_settings", None) or {}
            verify_ssl = resolve_verify_ssl(custom_settings, default=True)
            http_client = httpx.AsyncClient(verify=verify_ssl, timeout=120.0)

            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                http_client=http_client,
            )
        return self._client

    async def _close_client_safely(self) -> None:
        """Close the cached AsyncOpenAI client on the active event loop."""
        client = self._client
        if client is None:
            return
        self._client = None
        try:
            await client.close()
        except Exception:
            pass

    # ==================== Catalog Integration ====================

    def get_meta(self):
        if self.CATALOG_ID:
            from flocks.provider.model_catalog import get_provider_meta

            meta = get_provider_meta(self.CATALOG_ID)
            if meta:
                return meta
        return super().get_meta()

    def get_models(self) -> List[ModelInfo]:
        """Return models from flocks.json (_config_models) only.

        catalog.json is not consulted at runtime; it is only used when
        credentials are first saved to pre-populate flocks.json.
        """
        return list(getattr(self, "_config_models", []))

    # ==================== Chat ====================

    @staticmethod
    def _format_messages(messages: List[ChatMessage]) -> list:
        """Convert ChatMessage list to OpenAI API dicts, preserving tool_calls / tool results."""
        formatted = []
        for m in messages:
            d: Dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                d["tool_calls"] = m.tool_calls
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            if m.name:
                d["name"] = m.name
            formatted.append(d)
        return formatted

    async def chat(
        self, model_id: str, messages: List[ChatMessage], **kwargs
    ) -> ChatResponse:
        """Send chat completion request via OpenAI-compatible API."""
        client = self._get_client()
        openai_messages = self._format_messages(messages)

        thinking = kwargs.get("thinking")

        params: Dict[str, Any] = {
            "model": model_id,
            "messages": openai_messages,
        }

        extra_body = dict(kwargs.get("extra_body") or {})
        if thinking:
            extra_body["thinking"] = thinking
        else:
            temperature = kwargs.get("temperature")
            if temperature is not None:
                params["temperature"] = temperature
        if extra_body:
            params["extra_body"] = extra_body

        if kwargs.get("max_tokens"):
            params["max_tokens"] = kwargs["max_tokens"]
        if kwargs.get("tools"):
            params["tools"] = kwargs["tools"]

        response = await client.chat.completions.create(**params)
        if not response.choices:
            extra = getattr(response, "model_extra", {}) or {}
            err_detail = extra.get("error") or extra.get("message") or str(extra) or "no choices returned"
            raise ValueError(
                f"{self.name} API returned empty choices. "
                f"model={model_id}, detail={err_detail}"
            )
        choice = response.choices[0]
        msg = getattr(choice, "message", None)
        if msg is None:
            extra = getattr(response, "model_extra", {}) or {}
            err_detail = extra.get("error") or extra.get("message") or str(extra) or "message is None"
            raise ValueError(
                f"{self.name} API returned choice with null message. "
                f"model={model_id}, detail={err_detail}"
            )
        return ChatResponse(
            id=response.id,
            model=response.model,
            content=msg.content or "",
            finish_reason=choice.finish_reason or "stop",
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": (
                    response.usage.completion_tokens if response.usage else 0
                ),
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
        )

    async def chat_stream(
        self, model_id: str, messages: List[ChatMessage], **kwargs
    ) -> AsyncIterator[StreamChunk]:
        """Send streaming chat completion request with tool-call and reasoning support."""
        client = self._get_client()
        openai_messages = self._format_messages(messages)

        thinking = kwargs.get("thinking")

        params: Dict[str, Any] = {
            "model": model_id,
            "messages": openai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        extra_body = dict(kwargs.get("extra_body") or {})
        if thinking:
            extra_body["thinking"] = thinking
        else:
            temperature = kwargs.get("temperature")
            if temperature is not None:
                params["temperature"] = temperature
        if extra_body:
            params["extra_body"] = extra_body

        if kwargs.get("max_tokens"):
            params["max_tokens"] = kwargs["max_tokens"]
        if kwargs.get("tools"):
            params["tools"] = kwargs["tools"]

        log.info("openai_base.stream.request", {
            "model": model_id,
            "thinking_enabled": bool(thinking),
            "has_extra_body": "extra_body" in params,
            "has_tools": bool(kwargs.get("tools")),
            "max_tokens": kwargs.get("max_tokens"),
            "has_temperature": "temperature" in params,
            "include_usage": True,
        })

        try:
            stream = await client.chat.completions.create(**params)
        except Exception as exc:
            if not _supports_include_usage_fallback(exc):
                raise
            log.warn("openai_base.stream.include_usage_unsupported", {
                "model": model_id,
                "error": str(exc),
            })
            params_without_usage = dict(params)
            params_without_usage.pop("stream_options", None)
            stream = await client.chat.completions.create(**params_without_usage)
        tool_calls: Dict[int, Dict[str, Any]] = {}
        emitted_substantive_chunk = False
        stream_usage: Optional[Dict[str, int]] = None
        usage_emitted = False

        # Stateful extractor to separate <think>...</think> from content.
        think_extractor = ThinkTagExtractor()
        _first_delta_logged = False

        async for chunk in stream:
            normalized_usage = _normalize_stream_usage(getattr(chunk, "usage", None))
            if normalized_usage:
                stream_usage = normalized_usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

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
                    log.info("openai_base.stream.first_delta", {
                        "delta_attrs": delta_attrs,
                        "model_extra_keys": list(extra.keys()) if extra else [],
                        "has_content": bool(getattr(delta, "content", None)),
                        "has_tool_calls": bool(getattr(delta, "tool_calls", None)),
                    })

                # 1) Native reasoning_content field (OpenAI o-series, DeepSeek R1, etc.)
                reasoning = extract_reasoning_content(delta)
                if reasoning:
                    emitted_substantive_chunk = True
                    yield StreamChunk(
                        event_type="reasoning",
                        reasoning=reasoning,
                        finish_reason=None,
                    )

                # 2) Regular content – extract inline <think> tags if present
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
                            yield StreamChunk(delta=seg_text, finish_reason=None)

                delta_tcs = getattr(delta, "tool_calls", None)
                if delta_tcs:
                    emitted_substantive_chunk = True
                    for tc in delta_tcs:
                        idx = tc.index
                        if idx not in tool_calls:
                            tool_calls[idx] = {
                                "id": tc.id or "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        if tc.id:
                            tool_calls[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls[idx]["function"]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls[idx]["function"]["arguments"] += (
                                    tc.function.arguments
                                )

            if choice.finish_reason:
                # Flush any remaining buffered content from the think-tag extractor
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
                        yield StreamChunk(delta=seg_text, finish_reason=None)

                if tool_calls:
                    sorted_calls = [tool_calls[i] for i in sorted(tool_calls.keys())]
                    tool_calls.clear()
                    # Preserve real finish_reason (e.g. "length" when max_tokens
                    # hit) so the runner can detect truncated tool arguments.
                    terminal_chunk = StreamChunk(
                        delta="",
                        finish_reason=choice.finish_reason,
                        tool_calls=sorted_calls,
                        usage=stream_usage,
                    )
                    yield terminal_chunk
                    usage_emitted = usage_emitted or terminal_chunk.usage is not None
                else:
                    terminal_chunk = StreamChunk(
                        delta="",
                        finish_reason=choice.finish_reason,
                        usage=stream_usage,
                    )
                    yield terminal_chunk
                    usage_emitted = usage_emitted or terminal_chunk.usage is not None

        # OpenAI-compatible APIs may send the usage-only chunk after the
        # terminal finish chunk. Surface that trailing usage so the runner can
        # persist it into message metadata and usage_records.
        if emitted_substantive_chunk and stream_usage and not usage_emitted:
            yield StreamChunk(delta="", finish_reason=None, usage=stream_usage)

        if not emitted_substantive_chunk:
            log.warn("openai_base.stream.empty_response", {
                "model": model_id,
                "has_tools": bool(kwargs.get("tools")),
            })
            fallback_error: Optional[Exception] = None
            try:
                fallback = await self.chat(model_id, messages, **kwargs)
                fallback_content = fallback.content or ""
                if fallback_content:
                    yield StreamChunk(
                        delta=fallback_content,
                        finish_reason=fallback.finish_reason or "stop",
                        usage=fallback.usage or None,
                    )
                    return
            except Exception as exc:
                fallback_error = exc

            if kwargs.get("tools"):
                log.warn("openai_base.stream.retry_without_tools", {
                    "model": model_id,
                    "reason": str(fallback_error) if fallback_error else "empty_fallback_content",
                })
                no_tool_kwargs = dict(kwargs)
                no_tool_kwargs.pop("tools", None)
                try:
                    fallback = await self.chat(model_id, messages, **no_tool_kwargs)
                    fallback_content = fallback.content or ""
                    if fallback_content:
                        yield StreamChunk(
                            delta=fallback_content,
                            finish_reason=fallback.finish_reason or "stop",
                            usage=fallback.usage or None,
                        )
                        return
                except Exception as exc:
                    if fallback_error is None:
                        fallback_error = exc

            if fallback_error:
                raise fallback_error
            raise ValueError(
                f"{self.name} API returned an empty streaming response and empty fallback response. "
                f"model={model_id}"
            )
