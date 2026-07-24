"""
Base class for OpenAI-compatible providers.

Provides standard chat/chat_stream implementation that works with any
OpenAI-compatible API (DeepSeek, Volcengine, Alibaba, Tencent, SiliconFlow, etc.).

Subclasses only need to define class attributes and get_models().
"""

import os
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

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

# Shared HTTP timeout used by every OpenAI-style provider (OpenAIProvider,
# OpenAICompatibleProvider, OpenAIBaseProvider). Centralised here so a single
# change covers all three providers. Granular values (instead of a flat
# timeout) let small control-plane requests fail fast while multimodal
# (image) uploads get the headroom they need on slow links.
DEFAULT_HTTP_TIMEOUT = httpx.Timeout(connect=30.0, read=180.0, write=1800.0, pool=60.0)


# Canonical OpenAI-style content translation, shared by every provider that
# talks the OpenAI chat-completions wire format. Kept as a module-level
# function (instead of a method on a single class) because the three OpenAI
# implementations — ``OpenAIProvider``, ``OpenAICompatibleProvider``, and
# ``OpenAIBaseProvider`` — sit in parallel class hierarchies. Putting the
# logic here gives all of them one point of truth: change the schema once,
# every provider follows.
#
# Recognised input block schema (Flocks-internal):
#   {"type": "text",  "text": "..."}
#   {"type": "image", "mimeType": "image/png", "data": "<base64>"}
# Plus already-OpenAI-native blocks (image_url / input_audio / audio /
# refusal / file) which are passed through unchanged so callers that
# pre-format won't lose them.
_OPENAI_NATIVE_BLOCK_TYPES = frozenset({
    "image_url", "input_audio", "audio", "refusal", "file",
})

# Flocks-internal block types that the translation logic knows about. Used to
# distinguish "known type with missing/invalid fields" from "genuinely unknown
# type" when logging dropped blocks.
_FLOCKS_INTERNAL_BLOCK_TYPES = frozenset({"text", "image"})


def _summarise_block(block: Any) -> Dict[str, Any]:
    """Describe a content block for diagnostic logs without leaking base64.

    Multimodal requests can carry several MB of base64 image data. Logging the
    full payload would dwarf every other line in the journal *and* expose
    user-uploaded data, so for ``image_url`` blocks we only record the URL
    scheme and length. ``text`` blocks record character count only.
    """
    if isinstance(block, dict):
        btype = block.get("type")
        if btype == "image_url":
            img = block.get("image_url") or {}
            url = img.get("url") if isinstance(img, dict) else ""
            scheme = (
                url.split(":", 1)[0]
                if isinstance(url, str) and ":" in url
                else ""
            )
            return {
                "type": btype,
                "url_scheme": scheme,
                "url_chars": len(url) if isinstance(url, str) else 0,
            }
        if btype == "text":
            txt = block.get("text") or ""
            return {"type": btype, "text_chars": len(txt)}
        return {"type": btype}
    return {"type": type(block).__name__}


def _summarise_messages(openai_messages: List[Any]) -> Dict[str, Any]:
    """Compute a compact diagnostic summary for request logging.

    The previous ``message_shapes`` payload logged every message in the request,
    which became very large on long-running sessions because the full history
    was repeated on every model call. Keep the useful signal, but collapse it
    into aggregate counters plus a short tail of the most recent messages.
    """
    role_counts: Dict[str, int] = {}
    block_type_counts: Dict[str, int] = {}
    skipped_types: Dict[str, int] = {}
    tail: List[Dict[str, Any]] = []
    total_content_chars = 0
    max_content_chars = 0
    multimodal_messages = 0

    for m in openai_messages:
        if not isinstance(m, dict):
            skipped_type = type(m).__name__
            skipped_types[skipped_type] = skipped_types.get(skipped_type, 0) + 1
            continue

        role = str(m.get("role") or "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
        content = m.get("content")
        tail_entry: Dict[str, Any] = {"role": role}
        content_chars: Optional[int] = None

        if isinstance(content, list):
            multimodal_messages += 1
            block_summaries = [_summarise_block(block) for block in content]
            message_block_types: Dict[str, int] = {}
            text_chars = 0
            image_url_chars = 0
            for summary in block_summaries:
                block_type = str(summary.get("type") or "unknown")
                block_type_counts[block_type] = block_type_counts.get(block_type, 0) + 1
                message_block_types[block_type] = message_block_types.get(block_type, 0) + 1
                text_chars += int(summary.get("text_chars") or 0)
                image_url_chars += int(summary.get("url_chars") or 0)

            content_chars = text_chars + image_url_chars
            tail_entry.update(
                {
                    "content_kind": "blocks",
                    "block_count": len(content),
                    "block_types": message_block_types,
                }
            )
            if text_chars:
                tail_entry["text_chars"] = text_chars
            if image_url_chars:
                tail_entry["image_url_chars"] = image_url_chars
        else:
            content_chars = len(content) if isinstance(content, str) else None
            tail_entry["content_kind"] = "text"

        if content_chars is not None:
            tail_entry["content_chars"] = content_chars
            total_content_chars += content_chars
            max_content_chars = max(max_content_chars, content_chars)

        tail.append(tail_entry)
        if len(tail) > 6:
            tail.pop(0)

    summary: Dict[str, Any] = {
        "message_count": len(openai_messages),
        "role_counts": role_counts,
        "multimodal_messages": multimodal_messages,
        "total_content_chars": total_content_chars,
        "max_content_chars": max_content_chars,
        "tail": tail,
    }
    if block_type_counts:
        summary["block_type_counts"] = block_type_counts
    if skipped_types:
        summary["skipped_types"] = skipped_types
    return summary


def format_openai_content(content: Any) -> Any:
    """Translate Flocks-internal content blocks to OpenAI chat.completions schema.

    Plain string content (the common case for assistant/tool messages) is
    passed through untouched. List content is rewritten so each block is in
    the schema OpenAI's chat.completions API expects:

    * ``{"type": "image", "mimeType": ..., "data": <b64>}``
      → ``{"type": "image_url", "image_url": {"url": "data:...;base64,..."}}``
    * ``{"type": "text", "text": ...}`` is preserved.
    * Already-OpenAI-native block types are passed through unchanged.
    * Unknown block types are dropped and logged at DEBUG level to avoid
      sending malformed payloads that would otherwise trigger a 400 from
      the gateway. Callers should monitor for ``unknown_block_dropped`` log
      events when adding new block kinds.
    """
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
        elif block_type in _OPENAI_NATIVE_BLOCK_TYPES:
            formatted.append(block)
        elif block_type in _FLOCKS_INTERNAL_BLOCK_TYPES:
            # Known type but missing required fields (e.g. image without data/mimeType,
            # or text with a non-string value). Log at debug with the actual keys present
            # to make it easy to diagnose upstream encoding bugs.
            log.debug("openai_base.malformed_block_dropped", {
                "type": block_type,
                "keys": sorted(block.keys()),
            })
        else:
            log.debug("openai_base.unknown_block_dropped", {"type": block_type})
    # Return the translated list as-is (possibly empty). Callers that need to
    # omit the ``content`` field entirely (e.g. assistant messages with
    # tool_calls only) are responsible for detecting the empty-list case and
    # dropping the key themselves. Returning ``None`` here risks silencing a
    # 400 for user/system roles where ``content=null`` is not permitted.
    return formatted


def format_openai_messages(
    messages: List["ChatMessage"],
    *,
    include_reasoning: bool = False,
    reasoning_field: str = "reasoning_content",
) -> list:
    """Convert a ``ChatMessage`` list to the OpenAI chat-completions wire format.

    Shared by all three OpenAI-style providers (``OpenAIProvider``,
    ``OpenAICompatibleProvider``, ``OpenAIBaseProvider``) so the role-aware
    content-null guard lives in exactly one place.

    Content-null rules (OpenAI chat completions spec):
    * ``user`` / ``system`` / ``tool`` roles MUST have non-null content.
    * ``assistant`` messages with ``tool_calls`` MAY omit ``content`` (null is
      accepted) — we omit the key entirely for cleaner serialisation.
    * An empty translated list (all blocks were dropped/malformed) is treated
      the same as no content: safe fallback is ``""`` for non-assistant roles
      and key-omission for assistant-with-tool-calls.
    """
    formatted = []
    for m in messages:
        role = m.role if isinstance(m.role, str) else m.role.value
        content = format_openai_content(m.content)
        d: Dict[str, Any] = {"role": role}
        if isinstance(content, list) and not content:
            if role == "assistant" and m.tool_calls:
                pass  # omit content key — null is valid for tool-call-only turns
            else:
                d["content"] = ""
        else:
            d["content"] = content
        if m.tool_calls:
            d["tool_calls"] = m.tool_calls
        if role == "assistant":
            if m.reasoning_details:
                d["reasoning_details"] = m.reasoning_details
            elif m.reasoning_content is not None:
                d["reasoning_content"] = m.reasoning_content
            elif include_reasoning and m.reasoning:
                d[reasoning_field] = m.reasoning
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        formatted.append(d)
    return formatted


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
    completion_details = getattr(raw_usage, "completion_tokens_details", None)
    output_details = getattr(raw_usage, "output_tokens_details", None)
    nested_reasoning_tokens = (
        getattr(completion_details, "reasoning_tokens", 0)
        or getattr(output_details, "reasoning_tokens", 0)
        or 0
    )
    reasoning_tokens = (
        getattr(raw_usage, "reasoning_tokens", 0)
        or nested_reasoning_tokens
        or 0
    )
    if nested_reasoning_tokens:
        # OpenAI reports reasoning_tokens as a breakdown already included in
        # completion_tokens / output_tokens. Flocks stores visible output and
        # reasoning separately, so remove that nested subset before persisting.
        completion_tokens = max(
            0,
            completion_tokens - nested_reasoning_tokens,
        )
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


def _supports_max_completion_tokens_fallback(exc: Exception) -> bool:
    """Return True when the provider rejects ``max_completion_tokens``."""
    message = str(exc).lower()
    return "max_completion_tokens" in message and any(
        marker in message
        for marker in (
            "unsupported parameter",
            "unknown parameter",
            "unrecognized parameter",
            "extra inputs are not permitted",
            "extra fields not permitted",
            "not permitted",
        )
    )


def resolve_openai_token_limit(kwargs: Dict[str, Any]) -> Optional[int]:
    """Resolve output token limits from either OpenAI naming variant."""
    max_completion_tokens = kwargs.get("max_completion_tokens")
    if max_completion_tokens is not None:
        return max_completion_tokens
    return kwargs.get("max_tokens")


def apply_openai_token_limit(
    params: Dict[str, Any],
    max_tokens: Optional[int],
    *,
    prefer_completion_tokens: bool = False,
    completion_tokens_explicit: bool = False,
) -> None:
    """Apply the preferred token-limit field to an OpenAI-style payload."""
    if max_tokens is None:
        return
    if completion_tokens_explicit or prefer_completion_tokens:
        params["max_completion_tokens"] = max_tokens
    else:
        params["max_tokens"] = max_tokens


def _is_effective_thinking_enabled(
    model_id: str,
    thinking: Any,
    extra_body: Dict[str, Any],
) -> bool:
    """Return the effective reasoning state represented by a request."""
    model_lower = model_id.lower()
    if "kimi-k2.7" in model_lower or "kimi-k3" in model_lower:
        return True

    if isinstance(thinking, dict):
        return thinking.get("type") != "disabled"
    if thinking:
        return True

    extra_thinking = extra_body.get("thinking")
    if isinstance(extra_thinking, dict):
        return extra_thinking.get("type") != "disabled"
    if extra_thinking:
        return True

    if extra_body.get("reasoning_effort") is not None:
        return True
    if extra_body.get("reasoning_split") is True:
        return True
    return extra_body.get("enable_thinking") is True


async def create_chat_completion_with_fallbacks(
    create_call,
    params: Dict[str, Any],
    *,
    max_tokens: Optional[int],
    logger: Any,
    log_prefix: str,
) -> Any:
    """Execute a chat completion request with transport compatibility fallbacks."""
    current_params = dict(params)
    include_usage_retried = False
    max_completion_tokens_retried = False

    while True:
        try:
            return await create_call(**current_params)
        except Exception as exc:
            if (
                not max_completion_tokens_retried
                and max_tokens is not None
                and "max_completion_tokens" in current_params
                and "max_tokens" not in current_params
                and _supports_max_completion_tokens_fallback(exc)
            ):
                max_completion_tokens_retried = True
                logger.warn(f"{log_prefix}.max_completion_tokens_unsupported", {
                    "model": current_params.get("model"),
                    "error": str(exc),
                })
                current_params = dict(current_params)
                current_params.pop("max_completion_tokens", None)
                current_params["max_tokens"] = max_tokens
                continue

            if (
                not include_usage_retried
                and "stream_options" in current_params
                and _supports_include_usage_fallback(exc)
            ):
                include_usage_retried = True
                logger.warn(f"{log_prefix}.stream.include_usage_unsupported", {
                    "model": current_params.get("model"),
                    "error": str(exc),
                })
                current_params = dict(current_params)
                current_params.pop("stream_options", None)
                continue

            raise


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
_REASONING_DETAILS_FIELDS = (
    "reasoning_details",
)


def _normalize_reasoning_details(value: Any) -> Optional[List[Dict[str, Any]]]:
    """Normalize provider reasoning_details payloads to a list of dicts."""
    if value is None:
        return None

    if isinstance(value, tuple):
        value = list(value)
    elif hasattr(value, "model_dump"):
        dumped = value.model_dump()
        if isinstance(dumped, list):
            value = dumped
        else:
            value = [dumped]

    if not isinstance(value, list):
        return None

    normalized: List[Dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            normalized.append(item)
        elif hasattr(item, "model_dump"):
            dumped = item.model_dump()
            if isinstance(dumped, dict):
                normalized.append(dumped)
        elif hasattr(item, "__dict__"):
            normalized.append(dict(item.__dict__))
    return normalized or None


def extract_reasoning_content_with_source(delta) -> tuple[Optional[str], Optional[str]]:
    """Extract reasoning text plus the field/source it came from."""
    if delta is None:
        return None, None
    for field in _REASONING_FIELDS:
        value = getattr(delta, field, None)
        if value is not None:
            return value, field
    extra = getattr(delta, "model_extra", None)
    if extra and isinstance(extra, dict):
        for field in _REASONING_FIELDS:
            value = extra.get(field)
            if value is not None:
                return value, field
    return None, None


def extract_reasoning_details(delta) -> Optional[List[Dict[str, Any]]]:
    """Extract structured reasoning details from streaming delta objects."""
    if delta is None:
        return None
    for field in _REASONING_DETAILS_FIELDS:
        value = getattr(delta, field, None)
        normalized = _normalize_reasoning_details(value)
        if normalized:
            return normalized
    extra = getattr(delta, "model_extra", None)
    if extra and isinstance(extra, dict):
        for field in _REASONING_DETAILS_FIELDS:
            normalized = _normalize_reasoning_details(extra.get(field))
            if normalized:
                return normalized
    return None


def build_reasoning_metadata(
    *,
    provider_id: str,
    model_id: str,
    reasoning_content: Optional[str] = None,
    reasoning_source: Optional[str] = None,
    reasoning_details: Optional[List[Dict[str, Any]]] = None,
    reasoning_field: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build normalized metadata for reasoning chunks."""
    if not any((reasoning_content is not None, reasoning_source, reasoning_details, reasoning_field)):
        return None

    field = reasoning_field
    if field is None:
        field = "reasoning_details" if reasoning_details else "reasoning_content"

    metadata: Dict[str, Any] = {
        "providerID": provider_id,
        "modelID": model_id,
        "reasoningField": field,
    }
    if reasoning_source:
        metadata["reasoningSource"] = reasoning_source
    if reasoning_content is not None:
        metadata["reasoningContent"] = reasoning_content
    if reasoning_details:
        metadata["reasoningDetails"] = reasoning_details
    return metadata


def extract_reasoning_content(delta) -> Optional[str]:
    """Extract reasoning/thinking content from a streaming delta object.

    Supports multiple provider/proxy formats:
    - Direct attribute: OpenAI o-series, DeepSeek R1 (reasoning_content)
    - Anthropic-compatible proxies (thinking, thinking_content)
    - model_extra dict: GLM, other OpenAI-compatible APIs

    This is a shared utility used by OpenAIBaseProvider, OpenAIProvider,
    and OpenAICompatibleProvider.
    """
    reasoning, _source = extract_reasoning_content_with_source(delta)
    return reasoning


class OpenAIBaseProvider(BaseProvider):
    """Base class for providers using OpenAI-compatible API.

    Subclasses should set:
        DEFAULT_BASE_URL: Default API endpoint
        ENV_API_KEY: List of env var names for API key lookup
        ENV_BASE_URL: Env var name for base URL override
        CATALOG_ID: ID in catalog.json (for get_meta / get_model_definitions)
        ALLOW_NO_API_KEY: When True, ``_get_client()`` falls back to a sentinel
            placeholder key instead of raising. Use for self-hosted endpoints
            (e.g. internal LLM gateways, vLLM behind a reverse proxy) that do
            not enforce auth. Default False keeps SaaS providers strict.
    """

    DEFAULT_BASE_URL: str = ""
    ENV_API_KEY: List[str] = []
    ENV_BASE_URL: str = ""
    CATALOG_ID: str = ""
    ALLOW_NO_API_KEY: bool = False
    PREFER_MAX_COMPLETION_TOKENS: bool = False
    NO_API_KEY_PLACEHOLDER: str = "not-needed"

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
                if self.ALLOW_NO_API_KEY:
                    # Self-hosted / no-auth endpoint: pass a sentinel so the
                    # OpenAI SDK still constructs a client.
                    api_key = self.NO_API_KEY_PLACEHOLDER
                else:
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
            # Honour the same env-var contract as ``OpenAIProvider``: by default
            # we follow ambient HTTP_PROXY / HTTPS_PROXY / NO_PROXY settings
            # (``trust_env=True``) so that corporate egress works out of the
            # box. Operators can opt out globally via FLOCKS_HTTP_TRUST_ENV=0
            # or per-provider via ``custom_settings.trust_env``.
            trust_env = _coerce_bool(
                os.getenv("FLOCKS_HTTP_TRUST_ENV"), True
            )
            if isinstance(custom_settings, dict) and "trust_env" in custom_settings:
                trust_env = _coerce_bool(custom_settings.get("trust_env"), trust_env)
            timeout = DEFAULT_HTTP_TIMEOUT
            http_client = httpx.AsyncClient(
                trust_env=trust_env,
                verify=verify_ssl,
                timeout=timeout,
            )

            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                http_client=http_client,
            )
            log.info("openai_base.client.created", {
                "provider_id": getattr(self._config, "id", None),
                "base_url": base_url,
                "trust_env": trust_env,
                "verify_ssl": verify_ssl,
            })
        return self._client

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

    # Thin static-method wrapper so existing call-sites
    # (``OpenAIBaseProvider._format_content``) keep working. Real logic lives
    # in the module-level :func:`format_openai_content` so all OpenAI-style
    # providers share one implementation.
    _format_content = staticmethod(format_openai_content)

    @staticmethod
    def _format_messages(messages: List[ChatMessage]) -> list:
        """Convert ChatMessage list to OpenAI API dicts, preserving tool_calls / tool results."""
        return format_openai_messages(messages)

    async def chat(
        self, model_id: str, messages: List[ChatMessage], **kwargs
    ) -> ChatResponse:
        """Send chat completion request via OpenAI-compatible API."""
        client = self._get_client()
        openai_messages = self._format_messages(messages)

        thinking = kwargs.get("thinking")
        max_tokens = resolve_openai_token_limit(kwargs)
        max_completion_tokens_explicit = kwargs.get("max_completion_tokens") is not None

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

        apply_openai_token_limit(
            params,
            max_tokens,
            prefer_completion_tokens=(
                self.PREFER_MAX_COMPLETION_TOKENS or "kimi-k3" in model_id.lower()
            ),
            completion_tokens_explicit=max_completion_tokens_explicit,
        )
        if kwargs.get("tools"):
            params["tools"] = kwargs["tools"]

        # Mirror ``chat_stream``'s diagnostic log so non-streaming multimodal
        # regressions are equally visible. Keep INFO payloads compact because
        # these request logs are emitted for every model call in long sessions.
        log.info("openai_base.chat.request", {
            "model": model_id,
            "thinking_enabled": _is_effective_thinking_enabled(
                model_id,
                thinking,
                extra_body,
            ),
            "has_extra_body": "extra_body" in params,
            "has_tools": bool(kwargs.get("tools")),
            "max_tokens": max_tokens,
            "has_temperature": "temperature" in params,
            "message_summary": _summarise_messages(openai_messages),
        })

        response = await create_chat_completion_with_fallbacks(
            client.chat.completions.create,
            params,
            max_tokens=max_tokens,
            logger=log,
            log_prefix="openai_base",
        )
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
        max_tokens = resolve_openai_token_limit(kwargs)
        max_completion_tokens_explicit = kwargs.get("max_completion_tokens") is not None

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

        apply_openai_token_limit(
            params,
            max_tokens,
            prefer_completion_tokens=(
                self.PREFER_MAX_COMPLETION_TOKENS or "kimi-k3" in model_id.lower()
            ),
            completion_tokens_explicit=max_completion_tokens_explicit,
        )
        if kwargs.get("tools"):
            params["tools"] = kwargs["tools"]

        # Inspect request shape so multimodal regressions surface in the log
        # without repeating the full message history on every turn.
        log.info("openai_base.stream.request", {
            "model": model_id,
            "thinking_enabled": _is_effective_thinking_enabled(
                model_id,
                thinking,
                extra_body,
            ),
            "has_extra_body": "extra_body" in params,
            "has_tools": bool(kwargs.get("tools")),
            "max_tokens": max_tokens,
            "has_temperature": "temperature" in params,
            "include_usage": True,
            "message_summary": _summarise_messages(openai_messages),
        })

        stream = await create_chat_completion_with_fallbacks(
            client.chat.completions.create,
            params,
            max_tokens=max_tokens,
            logger=log,
            log_prefix="openai_base",
        )
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
                reasoning, reasoning_source = extract_reasoning_content_with_source(delta)
                reasoning_details = extract_reasoning_details(delta)
                if reasoning is not None or reasoning_details:
                    emitted_substantive_chunk = True
                    reasoning_metadata = build_reasoning_metadata(
                        provider_id=self.id,
                        model_id=model_id,
                        reasoning_content=reasoning,
                        reasoning_source=reasoning_source,
                        reasoning_details=reasoning_details,
                    )
                    yield StreamChunk(
                        event_type="reasoning",
                        reasoning=reasoning or "",
                        finish_reason=None,
                        metadata=reasoning_metadata,
                    )

                # 2) Regular content – extract inline <think> tags if present
                delta_text = getattr(delta, "content", None)
                if delta_text:
                    segments = think_extractor.process(delta_text)
                    for seg_type, seg_text in segments:
                        if seg_type == "reasoning":
                            if seg_text:
                                emitted_substantive_chunk = True
                            reasoning_metadata = build_reasoning_metadata(
                                provider_id=self.id,
                                model_id=model_id,
                                reasoning_content=seg_text,
                                reasoning_source="think_tag",
                            )
                            yield StreamChunk(
                                event_type="reasoning",
                                reasoning=seg_text,
                                finish_reason=None,
                                metadata=reasoning_metadata,
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
                        reasoning_metadata = build_reasoning_metadata(
                            provider_id=self.id,
                            model_id=model_id,
                            reasoning_content=seg_text,
                            reasoning_source="think_tag",
                        )
                        yield StreamChunk(
                            event_type="reasoning",
                            reasoning=seg_text,
                            finish_reason=None,
                            metadata=reasoning_metadata,
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
