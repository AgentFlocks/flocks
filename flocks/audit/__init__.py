"""
Audit sink facade (OSS default: no-op sink).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from flocks.extensions import ensure_callable_methods


@runtime_checkable
class AuditSink(Protocol):
    @classmethod
    async def emit(cls, event_type: str, payload: dict[str, Any]) -> None: ...


class NullAuditSink:
    @classmethod
    async def emit(cls, event_type: str, payload: dict[str, Any]) -> None:
        return None


class _AuditService:
    _sink: type[AuditSink] = NullAuditSink

    @classmethod
    def register_sink(cls, sink: type[AuditSink]) -> None:
        if sink is None:
            raise ValueError("sink 不能为空")
        ensure_callable_methods(sink, ("emit",), label="audit sink")
        cls._sink = sink

    @classmethod
    def get_sink(cls) -> type[AuditSink]:
        return cls._sink

    @classmethod
    async def emit(cls, event_type: str, payload: dict[str, Any]) -> None:
        await cls._sink.emit(event_type=event_type, payload=payload)


register_sink = _AuditService.register_sink
get_sink = _AuditService.get_sink
emit_audit_event = _AuditService.emit

__all__ = [
    "AuditSink",
    "NullAuditSink",
    "register_sink",
    "get_sink",
    "emit_audit_event",
]

