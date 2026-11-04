"""SOC query facets must survive zero matches without broadening policy scope."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from flocks.auth.context import AuthUser
from flocks.contracts.access.runtime import OperationRuntime
from flocks.contracts.access.models import ContractRuntimeError
from flocks.ingest.syslog.sangfor import normalize_sangfor_alert

STAMP = 1793102400
ADMIN = AuthUser(id="admin", username="admin", role="admin")


@pytest.fixture(params=["sqlite", "jsonl"])
def soc_query(tmp_path, monkeypatch, request):
    path = (
        Path(__file__).resolve().parents[3] / ".flocks/flockshub/plugins/webuis/soc_ui/access/soc_alerts_operations.py"
    )
    spec = importlib.util.spec_from_file_location("soc_filter_test", path)
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)
    db = tmp_path / "soc.db"
    monkeypatch.setattr(
        ops,
        "_sqlite_settings",
        lambda: {
            "db_path": db,
            "table": "alert_records",
            "record_column": "record_json",
            "date_column": "asset_date",
            "event_time_column": "event_time",
        },
    )

    jsonl = tmp_path / "jsonl"
    if request.param == "jsonl":
        jsonl.mkdir()
        resolve = ops._BindingResolver.resolve

        def jsonl_binding(self, **kwargs):
            return replace(
                resolve(self, **kwargs),
                adapter_kind="builtin-jsonl",
                source_root=jsonl,
                driver_allowlist_roots=(jsonl,),
            )

        monkeypatch.setattr(ops._BindingResolver, "resolve", jsonl_binding)

    def insert(records):
        if request.param == "jsonl":
            (jsonl / "records.jsonl").write_text("\n".join(json.dumps(r) for r in records))
            return
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE alert_records(asset_date TEXT, event_time INTEGER, record_json TEXT)")
            conn.executemany(
                "INSERT INTO alert_records VALUES (?, ?, ?)",
                [("2026-10-27", r.get("time", STAMP), json.dumps(r)) for r in records],
            )

    def query(filters=None, *, limit=10000, plugin=None, principal=ADMIN):
        return (
            OperationRuntime(plugins=(plugin or ops.CONTRACTS[0],))
            .execute(
                page_id="soc-alerts",
                contract_id="soc.alerts.operations",
                operation_name="list",
                payload={
                    "params": {"startTime": STAMP - 10, "endTime": STAMP + 10, "filters": filters or {}, "limit": limit}
                },
                principal=principal,
            )
            .body
        )

    return ops, insert, query


def test_se_unknown_protocol_and_mixed_sources_are_visible_without_defaults(soc_query):
    _, insert, query = soc_query
    sip = normalize_sangfor_alert(
        {
            "alert_id": "sip-1",
            "attack_ip": "198.51.100.1",
            "suffer_ip": "192.0.2.1",
            "last_time": STAMP,
            "module_type_name": "账号攻击",
        }
    )
    insert([sip, {"id": "tdp-1", "time": STAMP, "_source_type": "tdp", "net_type": "http"}])
    all_alerts = query()
    assert len(all_alerts["incidents"]) == 2
    assert all_alerts["summary"]["availableCount"] == 2
    assert set(all_alerts["filterOptions"]["_source_type"]) == {"sangfor-sip", "tdp"}
    assert set(all_alerts["filterOptions"]["net_type"]) == {"unknown", "http"}
    filtered = query({"_source_type": ["sangfor-sip"], "net_type": ["unknown"]})
    assert [i["id"] for i in filtered["incidents"]] == ["sip-1"]
    assert filtered["incidents"][0]["tableCells"]["net_type"]["value"] == "unknown"
    empty = query({"_source_type": ["tdp"], "net_type": ["dns"]})
    assert empty["incidents"] == []
    assert empty["summary"]["availableCount"] == 2
    assert empty["filterOptions"] == all_alerts["filterOptions"]


def test_facets_cover_rows_beyond_list_limit_and_obey_time_and_duplicate_rules(soc_query):
    _, insert, query = soc_query
    insert(
        [
            {"id": "a", "time": STAMP, "_source_type": "tdp"},
            {"id": "a", "time": STAMP, "_source_type": "tdp"},
            {"id": "b", "time": STAMP, "_source_type": "sangfor-sip"},
            {"id": "old", "time": STAMP - 100, "_source_type": "old-source"},
            {"id": "dup", "time": STAMP, "_source_type": "duplicate-source", "is_duplicate": True},
        ]
    )
    result = query(limit=1)
    assert len(result["incidents"]) == 1
    assert result["summary"]["availableCount"] == 2
    assert set(result["filterOptions"]["_source_type"]) == {"tdp", "sangfor-sip"}


def test_facets_never_expose_values_outside_the_callers_policy_scope(soc_query):
    ops, insert, query = soc_query
    insert(
        [
            {"id": "allowed", "time": STAMP, "_source_type": "sangfor-sip", "asset_group_name": "my-assets"},
            {"id": "hidden", "time": STAMP, "_source_type": "secret-source", "asset_group_name": "other-assets"},
        ]
    )
    contract = ops.CONTRACTS[0].contracts[0]
    operation = replace(
        contract.operations["list"],
        asset_group_policy_field="asset_group_name",
        filter_fields=contract.operations["list"].filter_fields | {"asset_group_name"},
    )
    plugin = replace(ops.CONTRACTS[0], contracts=(replace(contract, operations={"list": operation}),))
    result = query(
        {"_source_type": ["nonexistent"]},
        plugin=plugin,
        principal=AuthUser(id="member", username="member", role="member", asset_groups=("my-assets",)),
    )
    assert result["incidents"] == []
    assert result["summary"]["availableCount"] == 1
    assert result["filterOptions"]["_source_type"] == ["sangfor-sip"]


def test_unwritten_store_has_zero_available_alerts(soc_query):
    _, _, query = soc_query
    result = query()
    assert result["incidents"] == []
    assert result["summary"]["availableCount"] == 0


def test_bindings_cannot_facet_private_or_undeclared_fields(soc_query):
    ops, _, query = soc_query
    original = ops.CONTRACTS[0].binding_resolver

    class InvalidResolver:
        def resolve(self, **kwargs):
            binding = original.resolve(**kwargs)
            return replace(binding, driver_options={**binding.driver_options, "facetFields": ["triage_report"]})

    plugin = replace(ops.CONTRACTS[0], binding_resolver=InvalidResolver())
    with pytest.raises(ContractRuntimeError) as error:
        query(plugin=plugin)
    assert error.value.code == "policy_filter_not_enforceable"
