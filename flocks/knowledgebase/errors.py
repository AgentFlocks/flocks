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
    return KnowledgebaseError(
        503, code, "Knowledgebase integration is unavailable. No request was redirected to RAGFlow."
    )
