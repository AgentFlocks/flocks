"""
Generic model-response transform extension point.

A deployment may need to process what a model returns before anything else
sees it -- before the UI renders it, before it is saved, before a tool consumes
it.  This is the one place that happens.

Why a stream object rather than a hook call per chunk
-----------------------------------------------------
A transform usually needs state across chunks: a marker can straddle a chunk
boundary, so the transform has to hold a partial tail until the next chunk
arrives.  Opening one stream per model call gives it somewhere to keep that
state and lets an out-of-process implementation reuse a single connection for
the whole response instead of paying a round trip per token.

What the caller may assume
--------------------------
- ``feed`` is called with chunks in order, and returns the text that is safe to
  emit *now*.  Returning ``""`` is normal: the transform is holding a partial
  marker.
- ``flush`` returns whatever is still held once the model stops producing.
- Anything the transform holds counts against ``max_pending_chars``.  A
  transform that stops returning output does not get to grow without bound; the
  stream fails instead, and the caller terminates the in-flight response rather
  than emitting text it could not process.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from flocks.extensions import ensure_callable_methods
from flocks.utils.log import Log

log = Log.create(service="transform.response")

DEFAULT_MAX_PENDING_CHARS = 1_000_000


class ResponseTransformError(RuntimeError):
    """The transform failed, so the response must not be delivered as-is."""


class PendingBufferOverflow(ResponseTransformError):
    """The transform held more text than the caller is willing to buffer."""

    def __init__(self, pending: int, limit: int) -> None:
        self.pending = pending
        self.limit = limit
        super().__init__(f"响应转换缓冲超限: 已缓存 {pending} 字符, 上限 {limit}")


@runtime_checkable
class ResponseTransformStream(Protocol):
    """Per-model-call transform state."""

    async def feed(self, text: str) -> str: ...

    async def flush(self) -> str: ...

    async def close(self) -> None: ...


@runtime_checkable
class ResponseTransform(Protocol):
    """Contract for a registered response transform."""

    @property
    def name(self) -> str: ...

    async def open_stream(self, metadata: Dict[str, Any]) -> ResponseTransformStream:
        """Begin a transform for one model call."""
        ...

    async def transform_text(self, text: str, metadata: Dict[str, Any]) -> str:
        """Transform a complete, non-streamed response."""
        ...

    async def transform_value(self, value: Any, metadata: Dict[str, Any]) -> Any:
        """Transform a structured payload, e.g. assembled tool arguments."""
        ...


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class _PassthroughStream:
    """What a build with no transform registered uses: returns text unchanged."""

    async def feed(self, text: str) -> str:
        return text

    async def flush(self) -> str:
        return ""

    async def close(self) -> None:
        return None


class PassthroughResponseTransform:
    """Default transform: the response is delivered exactly as produced."""

    @property
    def name(self) -> str:
        return "passthrough"

    async def open_stream(self, metadata: Dict[str, Any]) -> ResponseTransformStream:
        return _PassthroughStream()

    async def transform_text(self, text: str, metadata: Dict[str, Any]) -> str:
        return text

    async def transform_value(self, value: Any, metadata: Dict[str, Any]) -> Any:
        return value


class GuardedTransformStream:
    """Wraps a transform's stream with ordering and buffer-cap enforcement.

    The caller always talks to this rather than to the transform directly, so
    the pending-size limit and the "a failed transform terminates the response"
    rule hold no matter what the transform does.
    """

    def __init__(
        self,
        inner: ResponseTransformStream,
        *,
        transform_name: str,
        max_pending_chars: int = DEFAULT_MAX_PENDING_CHARS,
    ) -> None:
        self._inner = inner
        self._transform_name = transform_name
        self._max_pending_chars = max_pending_chars
        self._fed_chars = 0
        self._emitted_chars = 0
        self._closed = False
        self._failed = False

    @property
    def pending_chars(self) -> int:
        """Characters handed to the transform that have not come back out.

        A transform may legitimately emit more than it was fed (a short marker
        expanding into a longer value), so this floors at zero rather than
        going negative.
        """
        return max(self._fed_chars - self._emitted_chars, 0)

    def _check_pending(self) -> None:
        pending = self.pending_chars
        if pending > self._max_pending_chars:
            self._failed = True
            raise PendingBufferOverflow(pending, self._max_pending_chars)

    async def feed(self, text: str) -> str:
        if self._failed:
            raise ResponseTransformError(f"响应转换已失败: {self._transform_name}")
        if self._closed:
            raise ResponseTransformError("响应转换流已关闭")
        if not text:
            return ""

        self._fed_chars += len(text)
        try:
            out = await _maybe_await(self._inner.feed(text))
        except ResponseTransformError:
            self._failed = True
            raise
        except Exception as exc:
            self._failed = True
            raise ResponseTransformError(
                f"响应转换 {self._transform_name} 处理失败: {exc}"
            ) from exc

        out = out or ""
        self._emitted_chars += len(out)
        self._check_pending()
        return out

    async def flush(self) -> str:
        if self._failed:
            raise ResponseTransformError(f"响应转换已失败: {self._transform_name}")
        if self._closed:
            return ""
        try:
            out = await _maybe_await(self._inner.flush())
        except Exception as exc:
            self._failed = True
            raise ResponseTransformError(
                f"响应转换 {self._transform_name} 收尾失败: {exc}"
            ) from exc
        out = out or ""
        self._emitted_chars += len(out)
        return out

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await _maybe_await(self._inner.close())
        except Exception as exc:
            # Closing is cleanup; a failure here must not mask the real outcome
            # of the response that already completed.
            log.warn(
                "transform.stream.close_failed",
                {"transform": self._transform_name, "error": str(exc)},
            )


class _ResponseTransformService:
    """Holds the single registered transform for this process."""

    _transform: ResponseTransform = PassthroughResponseTransform()
    _max_pending_chars: int = DEFAULT_MAX_PENDING_CHARS

    @classmethod
    def register_transform(
        cls,
        transform: ResponseTransform,
        *,
        max_pending_chars: Optional[int] = None,
    ) -> None:
        if transform is None:
            raise ValueError("response transform 不能为空")
        ensure_callable_methods(
            transform,
            ("open_stream", "transform_text", "transform_value"),
            label="response transform",
        )
        if not getattr(transform, "name", None):
            raise ValueError("response transform 必须提供 name")
        if max_pending_chars is not None:
            if max_pending_chars <= 0:
                raise ValueError("max_pending_chars 必须为正数")
            cls._max_pending_chars = int(max_pending_chars)
        cls._transform = transform
        log.info(
            "transform.registered",
            {"transform": transform.name, "max_pending_chars": cls._max_pending_chars},
        )

    @classmethod
    def reset(cls) -> None:
        cls._transform = PassthroughResponseTransform()
        cls._max_pending_chars = DEFAULT_MAX_PENDING_CHARS

    @classmethod
    def get_transform(cls) -> ResponseTransform:
        return cls._transform

    @classmethod
    def is_active(cls) -> bool:
        """Whether anything beyond the default passthrough is installed."""
        return not isinstance(cls._transform, PassthroughResponseTransform)

    @classmethod
    async def open_stream(cls, metadata: Optional[Dict[str, Any]] = None) -> GuardedTransformStream:
        transform = cls._transform
        try:
            inner = await _maybe_await(transform.open_stream(dict(metadata or {})))
        except Exception as exc:
            raise ResponseTransformError(
                f"响应转换 {transform.name} 无法建立流: {exc}"
            ) from exc
        return GuardedTransformStream(
            inner,
            transform_name=transform.name,
            max_pending_chars=cls._max_pending_chars,
        )

    @classmethod
    async def transform_text(cls, text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        if not text:
            return text
        transform = cls._transform
        try:
            return await _maybe_await(transform.transform_text(text, dict(metadata or {})))
        except Exception as exc:
            raise ResponseTransformError(
                f"响应转换 {transform.name} 处理失败: {exc}"
            ) from exc

    @classmethod
    async def transform_value(cls, value: Any, metadata: Optional[Dict[str, Any]] = None) -> Any:
        transform = cls._transform
        try:
            return await _maybe_await(transform.transform_value(value, dict(metadata or {})))
        except Exception as exc:
            raise ResponseTransformError(
                f"响应转换 {transform.name} 处理失败: {exc}"
            ) from exc


register_response_transform = _ResponseTransformService.register_transform
reset_response_transform = _ResponseTransformService.reset
get_response_transform = _ResponseTransformService.get_transform
response_transform_active = _ResponseTransformService.is_active
open_response_stream = _ResponseTransformService.open_stream
transform_response_text = _ResponseTransformService.transform_text
transform_response_value = _ResponseTransformService.transform_value


__all__ = [
    "DEFAULT_MAX_PENDING_CHARS",
    "GuardedTransformStream",
    "PassthroughResponseTransform",
    "PendingBufferOverflow",
    "ResponseTransform",
    "ResponseTransformError",
    "ResponseTransformStream",
    "get_response_transform",
    "open_response_stream",
    "register_response_transform",
    "reset_response_transform",
    "response_transform_active",
    "transform_response_text",
    "transform_response_value",
]
