from __future__ import annotations

from typing import Any


class KnowledgebaseError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.details = details or {}

    def public(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "request_id": self.request_id,
                "details": self.details,
            }
        }


def unavailable(code: str = "knowledgebase_unavailable") -> KnowledgebaseError:
    return KnowledgebaseError(503, code, "Knowledgebase integration is unavailable.")


class KBError(Exception):
    """Internal engine error, translated at the Core client boundary."""

    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


class UpstreamError(KBError):
    def __init__(self, code: str = "upstream_unavailable", *, upstream_code: int | str | None = None):
        details = {"upstream_code": upstream_code} if upstream_code is not None else {}
        super().__init__(502, code, "The knowledge engine could not complete this operation.", details)
