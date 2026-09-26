from typing import Any


class KBError(Exception):
    """Public, sanitized error. Never put credentials or upstream response bodies here."""

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
