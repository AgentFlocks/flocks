from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from flocks.auth.context import AuthUser
from flocks.contracts.access.discovery import discover_contract_plugins
from flocks.contracts.access.models import (
    Binding,
    Contract,
    ContractOperation,
    ContractRuntimeError,
    DriverResult,
    InternalDataRow,
    PolicyContext,
    RuntimeContext,
    WebUIContractPlugin,
)
from flocks.contracts.access.pipeline import OverlayStore
from flocks.contracts.access.plans import FieldDependencyPlanCompiler, PolicyPlanCompiler
from flocks.contracts.access.runtime import BindingTestHarness, NO_POLICY_SCOPE, OperationRuntime, PolicyContextResolver
from flocks.contracts.webui.store import WebUIPagesStore
from flocks.plugin.loader import PluginLoader

SOURCE_PAGE_ID = "contract-source"
PAGE_ID = "test/records"
CONTRACT_ID = "test.records"
CONTRACT_VERSION = "1.0"
DRIVER_FIELDS = frozenset({"id", "tenant", "asset_group", "status", "severity", "time"})


def _write_contract_assets(store: WebUIPagesStore, records: list[dict[str, Any]]) -> None:
    store.create_page(page_id=SOURCE_PAGE_ID, title="Contract source")
    asset_dir = store.asset_path(SOURCE_PAGE_ID, "2026-06-25")
    asset_dir.mkdir(parents=True, exist_ok=True)
    asset_path = asset_dir / "records.jsonl"
    lines = [{"_type": "file_header", "date": "2026-06-25"}, *records]
    asset_path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
        encoding="utf-8",
    )


def _write_contract_sqlite(db_path: Path, records: list[dict[str, Any]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE records (
            id TEXT PRIMARY KEY,
            record_date TEXT NOT NULL,
            event_time INTEGER,
            record_json TEXT NOT NULL
        )
        """
    )
    connection.executemany(
        "INSERT INTO records (id, record_date, event_time, record_json) VALUES (?, ?, ?, ?)",
        [
            (
                str(record.get("id") or index),
                str(record.get("record_date") or "2026-06-25"),
                int(record.get("event_time") or record.get("time") or 0),
                json.dumps(record, ensure_ascii=False),
            )
            for index, record in enumerate(records, start=1)
        ],
    )
    connection.commit()
    connection.close()


def _contract_record(**overrides: Any) -> dict[str, Any]:
    record = {
        "id": "record-1",
        "tenant": "tenant-a",
        "asset_group": "core",
        "status": "open",
        "severity": "high",
        "time": 1779086941,
    }
    record.update(overrides)
    return record


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebUIPagesStore:
    root = tmp_path / "contracts-webui"
    monkeypatch.setenv("FLOCKS_CONTRACTS_WEBUI_ROOT", str(root))
    return WebUIPagesStore(root=root)


def _contract() -> Contract:
    return Contract(
        contract_id=CONTRACT_ID,
        version=CONTRACT_VERSION,
        page_id=PAGE_ID,
        operations={
            "list": ContractOperation(
                name="list",
                operation_type="query",
                adapter_required_fields=DRIVER_FIELDS,
                identity_fields=frozenset({"id"}),
                public_fields=frozenset({"summary", "items", "meta"}),
                filter_fields=frozenset({"tenant", "asset_group", "status", "severity"}),
                filter_param_fields={"status": "status", "severity": "severity"},
                tenant_policy_field="tenant",
                asset_group_policy_field="asset_group",
                cursor_fields=frozenset({"time", "id"}),
                sort_fields=frozenset({"time", "id"}),
                default_limit=100,
                max_limit=1000,
            ),
            "update": ContractOperation(
                name="update",
                operation_type="mutation",
                adapter_required_fields=frozenset(),
                identity_fields=frozenset({"entityType", "entityId"}),
                public_fields=frozenset({"ok", "entityType", "entityId", "overlayVersion", "writeThrough"}),
                requires_idempotency_key=True,
                requires_expected_overlay_version=True,
                mutation_entity_types=frozenset({"record"}),
            ),
        },
    )


class _BindingResolver:
    def __init__(
        self,
        store: WebUIPagesStore,
        *,
        capabilities: frozenset[str] | None = None,
        adapter_kind: str = "builtin-jsonl",
        source_root: Path | None = None,
        driver_options: dict[str, Any] | None = None,
    ) -> None:
        self._store = store
        self._capabilities = capabilities or frozenset({"query", "mutation"})
        self._adapter_kind = adapter_kind
        self._source_root = source_root
        self._driver_options = driver_options or {}

    def resolve(self, *, page_id: str, slot_id: str, contract_id: str, contract_version: str) -> Binding:
        source_root = self._source_root or self._store.asset_path(SOURCE_PAGE_ID, "")
        return Binding(
            binding_id=f"test-{self._adapter_kind}",
            binding_version=1,
            page_id=page_id,
            slot_id=slot_id,
            contract_id=contract_id,
            contract_version=contract_version,
            adapter_kind=self._adapter_kind,
            source_page_id=SOURCE_PAGE_ID,
            source_root=source_root,
            driver_available_fields=DRIVER_FIELDS,
            driver_allowlist_roots=(source_root if source_root.is_dir() else source_root.parent,),
            driver_options=self._driver_options,
            capabilities=self._capabilities,
        )


class _Adapter:
    def normalize(self, driver_result: DriverResult) -> list[InternalDataRow]:
        return [
            InternalDataRow(
                raw=row,
                identity={"entityType": "record", "entityId": f"record:{row['id']}"},
            )
            for row in driver_result.rows
        ]


class _ResponsePipeline:
    def __init__(self, overlay_store: OverlayStore) -> None:
        self._overlay_store = overlay_store

    def run_query(
        self,
        *,
        context: RuntimeContext,
        binding_source_page_id: str,
        driver_result: DriverResult,
        rows: list[InternalDataRow],
        filter_stages_applied: list[dict[str, str]],
    ) -> dict[str, Any]:
        merged_rows = self._overlay_store.merge(rows, context)
        items = [
            {
                **row.raw,
                "entityType": row.identity["entityType"],
                "entityId": row.identity["entityId"],
                "overlayVersion": row.raw.get("_overlay_version", 0),
            }
            for row in merged_rows
        ]
        return {
            "summary": {
                "totalRaw": driver_result.total_raw,
                "filteredUnique": driver_result.filtered_unique,
                "closed": sum(1 for item in items if item.get("manualStatus") == "closed"),
            },
            "items": items,
            "meta": {
                "sourcePageId": binding_source_page_id,
                "filterStagesApplied": filter_stages_applied,
            },
        }


def _plugin(
    store: WebUIPagesStore,
    *,
    capabilities: frozenset[str] | None = None,
    adapter_kind: str = "builtin-jsonl",
    source_root: Path | None = None,
    driver_options: dict[str, Any] | None = None,
) -> WebUIContractPlugin:
    overlay_store = OverlayStore()
    return WebUIContractPlugin(
        plugin_id="test-records",
        contracts=(_contract(),),
        binding_resolver=_BindingResolver(
            store,
            capabilities=capabilities,
            adapter_kind=adapter_kind,
            source_root=source_root,
            driver_options=driver_options,
        ),
        adapter=_Adapter(),
        response_pipeline=_ResponsePipeline(overlay_store),
        overlay_store=overlay_store,
    )


def _runtime(
    store: WebUIPagesStore,
    policy_context: PolicyContext | None = None,
    *,
    capabilities: frozenset[str] | None = None,
) -> OperationRuntime:
    class Resolver(PolicyContextResolver):
        def resolve(self, _principal: Any) -> PolicyContext:
            return policy_context or PolicyContext()

    return OperationRuntime(
        plugins=(_plugin(store, capabilities=capabilities),),
        policy_context_resolver=Resolver(),
    )


def test_query_uses_shared_runtime_and_jsonl_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    _write_contract_assets(store, [_contract_record()])
    runtime = _runtime(store)

    response = runtime.execute(
        page_id=PAGE_ID,
        contract_id=CONTRACT_ID,
        operation_name="list",
        payload={"params": {"limit": 10}},
        principal=AuthUser(id="u1", username="alice", role="admin"),
    )

    assert response.body["summary"]["totalRaw"] == 1
    assert response.body["items"][0]["entityId"] == "record:record-1"
    assert response.body["items"][0]["status"] == "open"
    assert response.body["meta"]["sourcePageId"] == SOURCE_PAGE_ID


def test_query_can_use_sqlite_json_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    db_path = tmp_path / "contract_records.db"
    _write_contract_sqlite(
        db_path,
        [
            _contract_record(id="allowed", status="open", severity="high", record_date="2026-06-25"),
            _contract_record(id="hidden", status="closed", severity="low", record_date="2026-06-25"),
            _contract_record(id="outside-date", status="open", severity="high", record_date="2026-06-26"),
        ],
    )
    runtime = OperationRuntime(
        plugins=(
            _plugin(
                store,
                adapter_kind="builtin-sqlite-json",
                source_root=db_path,
                driver_options={
                    "table": "records",
                    "recordColumn": "record_json",
                    "dateColumn": "record_date",
                },
            ),
        ),
    )

    response = runtime.execute(
        page_id=PAGE_ID,
        contract_id=CONTRACT_ID,
        operation_name="list",
        payload={"params": {"date": "2026-06-25", "filters": {"status": ["open"]}, "limit": 10}},
        principal=AuthUser(id="u1", username="alice", role="admin"),
    )

    assert response.body["summary"]["totalRaw"] == 2
    assert [item["id"] for item in response.body["items"]] == ["allowed"]
    assert response.body["items"][0]["entityId"] == "record:allowed"


def test_query_can_filter_sqlite_json_driver_by_event_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    db_path = tmp_path / "contract_records.db"
    _write_contract_sqlite(
        db_path,
        [
            _contract_record(id="early", time=1000),
            _contract_record(id="middle", time=2000),
            _contract_record(id="late", time=3000),
        ],
    )
    runtime = OperationRuntime(
        plugins=(
            _plugin(
                store,
                adapter_kind="builtin-sqlite-json",
                source_root=db_path,
                driver_options={
                    "table": "records",
                    "recordColumn": "record_json",
                    "dateColumn": "record_date",
                    "eventTimeColumn": "event_time",
                },
            ),
        ),
    )

    response = runtime.execute(
        page_id=PAGE_ID,
        contract_id=CONTRACT_ID,
        operation_name="list",
        payload={"params": {"startTime": 1500, "endTime": 2500, "limit": 10}},
        principal=AuthUser(id="u1", username="alice", role="admin"),
    )

    assert response.body["summary"]["totalRaw"] == 1
    assert response.body["summary"]["filteredUnique"] == 1
    assert [item["id"] for item in response.body["items"]] == ["middle"]


def test_query_rejects_page_supplied_binding_or_idempotency_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    _write_contract_assets(store, [_contract_record()])
    runtime = _runtime(store)

    with pytest.raises(ContractRuntimeError) as binding_error:
        runtime.execute(
            page_id=PAGE_ID,
            contract_id=CONTRACT_ID,
            operation_name="list",
            payload={"bindingId": "bad", "params": {}},
            principal=None,
        )
    assert binding_error.value.code == "forbidden_request_field"

    with pytest.raises(ContractRuntimeError) as idempotency_error:
        runtime.execute(
            page_id=PAGE_ID,
            contract_id=CONTRACT_ID,
            operation_name="list",
            payload={"idempotencyKey": "query-key", "params": {}},
            principal=None,
        )
    assert idempotency_error.value.code == "forbidden_request_field"


def test_default_policy_resolver_filters_member_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    _write_contract_assets(
        store,
        [
            _contract_record(id="allowed", tenant="tenant-a", asset_group="core"),
            _contract_record(id="blocked", tenant="tenant-b", asset_group="core"),
        ],
    )
    runtime = OperationRuntime(plugins=(_plugin(store),))

    response = runtime.execute(
        page_id=PAGE_ID,
        contract_id=CONTRACT_ID,
        operation_name="list",
        payload={"params": {"limit": 10}},
        principal=AuthUser(
            id="u1",
            username="analyst",
            role="member",
            tenant_ids=("tenant-a",),
            asset_groups=("core",),
        ),
    )

    assert [item["id"] for item in response.body["items"]] == ["allowed"]
    assert response.body["meta"]["filterStagesApplied"][:2] == [
        {
            "field": "tenant",
            "source": "policy.tenantIds",
            "stage": "driver-native",
            "enforcement": "driver-required",
        },
        {
            "field": "asset_group",
            "source": "policy.assetGroups",
            "stage": "driver-native",
            "enforcement": "driver-required",
        },
    ]


def test_default_policy_resolver_fails_closed_for_unscoped_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    _write_contract_assets(store, [_contract_record(id="hidden", tenant="tenant-a", asset_group="core")])
    runtime = OperationRuntime(plugins=(_plugin(store),))

    response = runtime.execute(
        page_id=PAGE_ID,
        contract_id=CONTRACT_ID,
        operation_name="list",
        payload={"params": {"limit": 10}},
        principal=AuthUser(id="u1", username="analyst", role="member"),
    )

    assert response.body["items"] == []
    assert response.body["meta"]["filterStagesApplied"][0]["source"] == "policy.tenantIds"
    assert response.body["meta"]["filterStagesApplied"][0]["field"] == "tenant"
    assert response.body["meta"]["filterStagesApplied"][0]["stage"] == "driver-native"
    assert PolicyContextResolver().resolve(AuthUser(id="u1", username="analyst", role="member")).tenant_ids == (
        NO_POLICY_SCOPE,
    )


def test_policy_and_field_dependency_plans_drive_query_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    _write_contract_assets(store, [_contract_record()])
    provider = _plugin(store)
    binding = provider.binding_resolver.resolve(
        page_id=PAGE_ID,
        slot_id="primary",
        contract_id=CONTRACT_ID,
        contract_version=CONTRACT_VERSION,
    )
    operation = provider.contracts[0].operations["list"]
    policy_plan = PolicyPlanCompiler().compile(
        operation=operation,
        binding=binding,
        policy_context=PolicyContext(tenant_ids=("tenant-a",), asset_groups=("core",)),
        params={"filters": {"status": ["open"]}},
    )
    field_plan = FieldDependencyPlanCompiler().compile(operation=operation, policy_plan=policy_plan)

    assert {"tenant", "asset_group", "status", "id", "time"}.issubset(field_plan.driver_required_fields)


def test_policy_filter_not_enforceable_when_binding_lacks_required_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    _write_contract_assets(store, [_contract_record()])
    provider = _plugin(store)
    binding = provider.binding_resolver.resolve(
        page_id=PAGE_ID,
        slot_id="primary",
        contract_id=CONTRACT_ID,
        contract_version=CONTRACT_VERSION,
    )
    binding = binding.__class__(
        **{
            **binding.__dict__,
            "driver_available_fields": frozenset({"id"}),
        }
    )

    with pytest.raises(ContractRuntimeError) as exc:
        PolicyPlanCompiler().compile(
            operation=provider.contracts[0].operations["list"],
            binding=binding,
            policy_context=PolicyContext(tenant_ids=("tenant-a",)),
            params={},
        )

    assert exc.value.code == "policy_filter_not_enforceable"


def test_mutation_pipeline_enforces_overlay_version_and_idempotency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    _write_contract_assets(store, [_contract_record()])
    runtime = _runtime(store)
    payload = {
        "idempotencyKey": "key-1",
        "expectedOverlayVersion": None,
        "params": {
            "entityType": "record",
            "entityId": "record:record-1",
            "manualStatus": "closed",
            "note": "confirmed",
        },
    }

    response = runtime.execute(
        page_id=PAGE_ID,
        contract_id=CONTRACT_ID,
        operation_name="update",
        payload=payload,
        principal=None,
    )
    replay = runtime.execute(
        page_id=PAGE_ID,
        contract_id=CONTRACT_ID,
        operation_name="update",
        payload=payload,
        principal=None,
    )

    assert response.body["overlayVersion"] == 1
    assert replay.body == response.body

    with pytest.raises(ContractRuntimeError) as exc:
        runtime.execute(
            page_id=PAGE_ID,
            contract_id=CONTRACT_ID,
            operation_name="update",
            payload={**payload, "params": {**payload["params"], "note": "different"}},
            principal=None,
        )
    assert exc.value.code == "conflict"


def test_mutation_overlay_is_visible_on_followup_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    _write_contract_assets(store, [_contract_record()])
    runtime = _runtime(store)

    runtime.execute(
        page_id=PAGE_ID,
        contract_id=CONTRACT_ID,
        operation_name="update",
        payload={
            "idempotencyKey": "overlay-query-1",
            "expectedOverlayVersion": None,
            "params": {
                "entityType": "record",
                "entityId": "record:record-1",
                "manualStatus": "closed",
                "note": "confirmed from logs",
            },
        },
        principal=None,
    )
    updated = runtime.execute(
        page_id=PAGE_ID,
        contract_id=CONTRACT_ID,
        operation_name="list",
        payload={"params": {"limit": 10}},
        principal=None,
    )

    item = updated.body["items"][0]
    assert updated.body["summary"]["closed"] == 1
    assert item["manualStatus"] == "closed"
    assert item["note"] == "confirmed from logs"
    assert item["overlayVersion"] == 1


def test_mutation_rejects_unsupported_entity_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    _write_contract_assets(store, [_contract_record()])
    runtime = _runtime(store)

    with pytest.raises(ContractRuntimeError) as exc:
        runtime.execute(
            page_id=PAGE_ID,
            contract_id=CONTRACT_ID,
            operation_name="update",
            payload={
                "idempotencyKey": "bad-entity-type",
                "expectedOverlayVersion": None,
                "params": {
                    "entityType": "case",
                    "entityId": "case-1",
                    "manualStatus": "closed",
                },
            },
            principal=None,
        )

    assert exc.value.code == "invalid_request"


def test_runtime_rejects_operations_outside_binding_capabilities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    _write_contract_assets(store, [_contract_record()])
    runtime = _runtime(store, capabilities=frozenset({"query"}))

    with pytest.raises(ContractRuntimeError) as exc:
        runtime.execute(
            page_id=PAGE_ID,
            contract_id=CONTRACT_ID,
            operation_name="update",
            payload={
                "idempotencyKey": "readonly",
                "expectedOverlayVersion": None,
                "params": {
                    "entityType": "record",
                    "entityId": "record:record-1",
                },
            },
            principal=None,
        )

    assert exc.value.code == "operation_not_supported"


def test_binding_test_harness_reuses_operation_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = _store(tmp_path, monkeypatch)
    _write_contract_assets(store, [_contract_record()])
    harness = BindingTestHarness(runtime=_runtime(store))

    results = harness.run(
        page_id=PAGE_ID,
        contract_id=CONTRACT_ID,
        operation_name="list",
        profiles=(AuthUser(id="u1", username="alice", role="admin"),),
    )

    assert results == [{"ok": True, "statusCode": 200}]


def test_discovery_keeps_user_plugin_when_project_plugin_has_same_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    user_root = tmp_path / "user-plugins"
    project_dir = tmp_path / "project"
    user_access = user_root / "contracts" / "access"
    project_access = project_dir / ".flocks" / "plugins" / "contracts" / "access"
    user_access.mkdir(parents=True)
    project_access.mkdir(parents=True)

    plugin_template = """
from flocks.contracts.access.models import WebUIContractPlugin

CONTRACTS = [
    WebUIContractPlugin(
        plugin_id="same-id",
        contracts=(),
        binding_resolver=object(),
        adapter=object(),
        response_pipeline=object(),
        version="{version}",
    )
]
"""
    (user_access / "plugin.py").write_text(plugin_template.format(version="user"), encoding="utf-8")
    (project_access / "plugin.py").write_text(plugin_template.format(version="project"), encoding="utf-8")

    monkeypatch.setattr(PluginLoader, "_plugin_root", user_root)
    monkeypatch.setattr(PluginLoader, "_extension_points", dict(PluginLoader._extension_points))
    PluginLoader.clear_extension_points()

    plugins = discover_contract_plugins(project_dir=project_dir)

    assert [(plugin.plugin_id, plugin.version) for plugin in plugins] == [("same-id", "user")]


def test_discovery_keeps_user_contract_when_project_plugin_has_same_contract_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    user_root = tmp_path / "user-plugins"
    project_dir = tmp_path / "project"
    user_access = user_root / "contracts" / "access"
    project_access = project_dir / ".flocks" / "plugins" / "contracts" / "access"
    user_access.mkdir(parents=True)
    project_access.mkdir(parents=True)

    plugin_template = """
from flocks.contracts.access.models import Contract, WebUIContractPlugin

CONTRACTS = [
    WebUIContractPlugin(
        plugin_id="{plugin_id}",
        contracts=(Contract(contract_id="same.contract", version="1.0", page_id="page", operations={{}}),),
        binding_resolver=object(),
        adapter=object(),
        response_pipeline=object(),
        version="{version}",
    )
]
"""
    (user_access / "user_plugin.py").write_text(
        plugin_template.format(plugin_id="user-plugin", version="user"),
        encoding="utf-8",
    )
    (project_access / "project_plugin.py").write_text(
        plugin_template.format(plugin_id="project-plugin", version="project"),
        encoding="utf-8",
    )

    monkeypatch.setattr(PluginLoader, "_plugin_root", user_root)
    monkeypatch.setattr(PluginLoader, "_extension_points", dict(PluginLoader._extension_points))
    PluginLoader.clear_extension_points()

    plugins = discover_contract_plugins(project_dir=project_dir)

    assert [(plugin.plugin_id, plugin.version) for plugin in plugins] == [("user-plugin", "user")]
