"""
The SOC suite's data, on SQLite.

The alert operations suite (the triage workflow, the SOC pages, the query
tool) keeps no database code of its own.  It writes and reads through this
package:

* the triage workflow writes alerts through the ``soc`` service a workflow's
  python nodes get (:class:`SOCWriteCapability`,
  ``flocks.coordination.workflow_services``);
* the dashboard and overview pages read through ``ctx.services.soc``
  (:func:`page_service`, ``flocks.coordination.page_api_services``);
* the query tool imports :mod:`.alerts`;
* the alert investigation page's contract names the ``builtin-soc-alerts``
  source, which ``flocks.contracts.access.driver`` reads from here.

A database backend replaces this package as a whole (see
``flocks.db_backend``) and answers the same calls from its own tables, so the
suite's files are the same on every backend.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Optional

from .triage import ensure_schema, persist_triage_alerts

#: The database backend this implementation is for.
BACKEND = "sqlite"

#: How a caller should batch alerts for one ``write_alerts`` call.  A backend
#: may refuse a larger call; this one does not.
MAX_ALERT_ROWS = 500
MAX_ALERT_BYTES = 2 * 1024 * 1024


class SOCWriteCapability:
    """What a workflow's python node writes triaged alerts through."""

    #: What a node may call (flocks.coordination.workflow_services), and how long an isolated node waits.
    exposed_methods = ("write_alerts",)
    rpc_timeout_s = 60.0

    def __init__(self, cancel_checker: Optional[Callable[[], bool]] = None) -> None:
        self.cancel_checker = cancel_checker

    def write_alerts(self, alerts: list[dict[str, Any]], run_id: str, source_file: str = "") -> dict[str, Any]:
        if self.cancel_checker and self.cancel_checker():
            raise RuntimeError("Workflow cancelled before SOC persistence")
        return persist_triage_alerts(alerts, run_id, source_file)


def page_service() -> SimpleNamespace:
    """``ctx.services.soc`` for a WebUI page's API handlers: what the SOC pages read."""
    from . import dashboard, overview

    return SimpleNamespace(backend=BACKEND, dashboard=dashboard, overview=overview)


__all__ = [
    "BACKEND",
    "MAX_ALERT_BYTES",
    "MAX_ALERT_ROWS",
    "SOCWriteCapability",
    "ensure_schema",
    "page_service",
    "persist_triage_alerts",
]
