"""The alert operations suite's files hold no database code: they go through flocks.workflow.soc_store.

One set of files serves every database backend.  A backend replaces
``flocks.workflow.soc_store`` (flocks.db_backend.REPLACEABLE_MODULES); the
suite's five database clients must therefore reach their data only through
that package, and say so with the marker a deployment tool recognises them by.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGINS = Path(__file__).resolve().parents[2] / ".flocks" / "flockshub" / "plugins"

#: The suite's five database clients, by the kind a deployment tool knows them as.
CLIENTS = {
    "workflow": PLUGINS / "workflows" / "stream_alert_triage" / "workflow.json",
    "query": PLUGINS / "tools" / "python" / "soc_workspace_query" / "soc_workspace_query.py",
    "dashboard": PLUGINS / "webuis" / "soc_ui" / "soc_dashboard" / "api" / "handlers.py",
    "overview": PLUGINS / "webuis" / "soc_ui" / "soc_overview" / "api" / "handlers.py",
    "contract": PLUGINS / "webuis" / "soc_ui" / "access" / "soc_alerts_operations.py",
}

#: What marks a file as a client of the platform's SOC store, whatever its content otherwise is.
MARKER = "flocks-soc-store-client"

#: What a file that reached a database itself would contain.
DATABASE_CODE = (
    "sqlite", "soc.db", "postgres", "psycopg", "asyncpg", "json_extract", "pragma ",
    "alert_records", "insert into", "create table", "select count", " from workflow_", ".connect(",
)


@pytest.mark.parametrize("kind", sorted(CLIENTS))
def test_client_is_marked_and_holds_no_database_code(kind: str) -> None:
    text = CLIENTS[kind].read_text(encoding="utf-8")

    assert MARKER in text
    lowered = text.lower()
    assert [needle for needle in DATABASE_CODE if needle in lowered] == []


@pytest.mark.parametrize("kind", ["query", "dashboard", "overview", "contract"])
def test_python_client_imports_no_database_driver(kind: str) -> None:
    tree = ast.parse(CLIENTS[kind].read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not imported & {"sqlite3", "psycopg", "asyncpg", "flocks.db", "flocks.db.sync_query"}


def test_workflow_writes_only_through_the_soc_service() -> None:
    workflow = json.loads(CLIENTS["workflow"].read_text(encoding="utf-8"))
    code = next(node["code"] for node in workflow["nodes"] if node["id"] == "concurrent_triage")
    tree = ast.parse(code)
    writes = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "soc"
    ]

    assert [call.func.attr for call in writes] == ["write_alerts"]
    assert "import sqlite3" not in code


def test_soc_store_is_a_module_a_database_backend_replaces() -> None:
    from flocks import db_backend
    from flocks.workflow import soc_store

    assert "flocks.workflow.soc_store" in db_backend.REPLACEABLE_MODULES
    assert soc_store.BACKEND == "sqlite"
    # What every implementation of the package answers to.
    for name in ("ensure_schema", "persist_triage_alerts", "SOCWriteCapability", "page_service",
                 "MAX_ALERT_ROWS", "MAX_ALERT_BYTES"):
        assert hasattr(soc_store, name), name
    service = soc_store.page_service()
    for name in ("ensure_schema", "find_sources", "available_dates", "read_denoise", "read_triage", "iter_records",
                 "workflow_denoise_stats", "workflow_progress", "workflow_recent_events", "token_usage",
                 "task_center_tasks", "task_center_workflows", "activity", "source_status"):
        assert callable(getattr(service.dashboard, name)), name
    for name in ("settings", "source_status", "find_sources", "available_dates", "iter_records", "workflow_call_count"):
        assert callable(getattr(service.overview, name)), name
    from flocks.workflow.soc_store import alerts

    for name in ("search", "record_fields", "latest_date", "source_info", "workflow_runs", "contract_source"):
        assert callable(getattr(alerts, name)), name
    assert alerts.FILTER_FIELDS


def test_page_runtime_hands_pages_the_soc_service() -> None:
    from flocks.contracts.webui.api_runtime import WebUIPageApiRuntime
    from flocks.workflow.soc_store import dashboard, overview

    ctx = WebUIPageApiRuntime()._create_context("soc-dashboard", None)

    assert ctx.services.soc.dashboard is dashboard
    assert ctx.services.soc.overview is overview
    assert ctx.database is None


def test_page_runtime_tolerates_a_backend_without_page_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """A backend's coordination module written before pages were handed services has no such function."""
    from flocks import coordination
    from flocks.contracts.webui.api_runtime import WebUIPageApiRuntime

    monkeypatch.delattr(coordination, "page_api_services")

    ctx = WebUIPageApiRuntime()._create_context("soc-dashboard", None)

    assert vars(ctx.services) == {}


@pytest.mark.parametrize("page", ["soc_dashboard", "soc_overview"])
def test_page_handlers_say_what_is_missing_on_a_platform_without_the_service(
    page: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed on an older Flocks, a page explains itself instead of failing on a missing attribute."""
    import builtins
    import importlib.util

    path = PLUGINS / "webuis" / "soc_ui" / page / "api" / "handlers.py"
    spec = importlib.util.spec_from_file_location(f"soc_missing_service_{page}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real_import = builtins.__import__

    def no_soc_store(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "flocks.workflow" and "soc_store" in (fromlist or ()):
            raise ImportError("No module named 'flocks.workflow.soc_store'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", no_soc_store)

    with pytest.raises(RuntimeError, match="update Flocks"):
        module.get_stats(SimpleNamespace(), SimpleNamespace(query_params={}))


def test_contract_names_the_platform_alert_store_and_no_file() -> None:
    import importlib.util

    from flocks.contracts.access.driver import SOC_ALERTS_ADAPTER_KIND

    spec = importlib.util.spec_from_file_location("soc_contract_binding_test", CLIENTS["contract"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    binding = module._BindingResolver().resolve(
        page_id="soc-alerts", slot_id="main", contract_id=module.CONTRACT_ID, contract_version=module.CONTRACT_VERSION,
    )

    assert binding.adapter_kind == SOC_ALERTS_ADAPTER_KIND == "builtin-soc-alerts"
    assert binding.driver_allowlist_roots == ()
    assert not binding.source_root.is_absolute()
    assert set(binding.driver_options) == {"settings", "settingsDir", "facetFields"}
