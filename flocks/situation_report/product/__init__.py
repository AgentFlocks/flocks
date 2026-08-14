"""Phase-one product runtime for situation-report Sessions.

This package is intentionally separate from the historical Agent/RAG
evaluation modules in ``flocks.situation_report``.
"""

from .session_state import ReportSessionState

__all__ = ["ReportSessionState"]
