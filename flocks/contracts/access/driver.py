"""Driver proxy and builtin drivers for data access contracts."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from flocks.contracts.access.models import DriverResult, Predicate, QueryPlan, ContractRuntimeError

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JSON_DATA_SUFFIXES = frozenset({".jsonl", ".json"})


class DriverProxy:
    def __init__(
        self,
        jsonl_executor: "JsonlDriverExecutor | None" = None,
        sqlite_executor: "SqliteJsonDriverExecutor | None" = None,
    ) -> None:
        self._jsonl_executor = jsonl_executor or JsonlDriverExecutor()
        self._sqlite_executor = sqlite_executor or SqliteJsonDriverExecutor()

    def execute(self, plan: QueryPlan) -> DriverResult:
        self._validate_plan(plan)
        if plan.binding.adapter_kind == "builtin-sqlite-json":
            return self._sqlite_executor.execute(plan)
        return self._jsonl_executor.execute(plan)

    def _validate_plan(self, plan: QueryPlan) -> None:
        if plan.binding.adapter_kind not in {"builtin-jsonl", "builtin-sqlite-json"}:
            raise ContractRuntimeError(
                "adapter_sandbox_unavailable",
                status_code=400,
                user_message="WebUI contract adapter is not available.",
                admin_message=f"Unsupported adapter kind: {plan.binding.adapter_kind}",
            )

        missing = plan.driver_projection - plan.binding.driver_available_fields
        if missing:
            raise ContractRuntimeError(
                "policy_filter_not_enforceable",
                status_code=400,
                user_message="WebUI contract data source cannot provide required fields.",
                admin_message=f"Driver projection contains unavailable fields: {sorted(missing)}",
        )


class SqliteJsonDriverExecutor:
    def execute(self, plan: QueryPlan) -> DriverResult:
        db_path = plan.binding.source_root
        self._assert_allowed(db_path, plan.binding.driver_allowlist_roots)
        if not db_path.is_file():
            raise ContractRuntimeError(
                "data_source_unavailable",
                status_code=404,
                user_message="WebUI contract SQLite database is not available.",
                admin_message=f"SQLite source does not exist: {db_path}",
            )

        options = plan.binding.driver_options
        table = _sqlite_identifier(options.get("table"), "records")
        record_column = _sqlite_identifier(options.get("recordColumn"), "record_json")
        date_column = _sqlite_identifier(options.get("dateColumn"), "record_date")
        query = f"SELECT {record_column} FROM {table}"
        query_params: list[Any] = []
        start_date, end_date = JsonlDriverExecutor()._request_date_range(plan.params)
        if start_date and end_date and date_column:
            query += f" WHERE {date_column} BETWEEN ? AND ?"
            query_params.extend([start_date, end_date])
        query += " ORDER BY rowid"

        rows: list[dict[str, Any]] = []
        seen_record_ids: set[str] = set()
        total_raw = 0
        duplicates = 0
        filtered_unique = 0
        parse_errors = 0
        try:
            connection = sqlite3.connect(db_path)
            cursor = connection.execute(query, query_params)
            raw_records = cursor.fetchall()
            connection.close()
        except sqlite3.Error as exc:
            raise ContractRuntimeError(
                "data_source_unavailable",
                status_code=500,
                user_message="WebUI contract SQLite database cannot be queried.",
                admin_message=f"SQLite query failed for {db_path}: {exc}",
            ) from exc

        for (record_value,) in raw_records:
            try:
                record = json.loads(record_value) if isinstance(record_value, str) else None
            except json.JSONDecodeError:
                record = None
            if not isinstance(record, dict):
                parse_errors += 1
                continue
            if record.get("_type") == "file_header":
                continue

            total_raw += 1
            if record.get("is_duplicate") is True:
                duplicates += 1
                continue
            if not self._matches_predicates(record, plan.policy_plan.driver_predicates):
                continue

            record_id = _read_string(record.get("id"), "")
            if record_id:
                if record_id in seen_record_ids:
                    duplicates += 1
                    continue
                seen_record_ids.add(record_id)

            filtered_unique += 1
            if len(rows) < plan.limit:
                rows.append(
                    {
                        field: record[field]
                        for field in plan.driver_projection
                        if field in record
                    }
                )

        return DriverResult(
            rows=rows,
            source_files=(db_path,),
            total_raw=total_raw,
            total_unique=max(total_raw - duplicates, 0),
            duplicates=duplicates,
            filtered_unique=filtered_unique,
            parse_errors=parse_errors,
        )

    def _assert_allowed(self, path: Path, allowlist_roots: tuple[Path, ...]) -> None:
        JsonlDriverExecutor()._assert_allowed(path, allowlist_roots)

    def _matches_predicates(self, record: dict[str, Any], predicates: tuple[Predicate, ...]) -> bool:
        return JsonlDriverExecutor()._matches_predicates(record, predicates)


class JsonlDriverExecutor:
    def execute(self, plan: QueryPlan) -> DriverResult:
        files = tuple(self._resolve_source_files(plan))
        rows: list[dict[str, Any]] = []
        seen_record_ids: set[str] = set()
        total_raw = 0
        duplicates = 0
        filtered_unique = 0
        parse_errors = 0
        for path in files:
            self._assert_allowed(path, plan.binding.driver_allowlist_roots)
            for record in self._iter_records(path):
                if record is None:
                    parse_errors += 1
                    continue
                if record.get("_type") == "file_header":
                    continue

                total_raw += 1
                if record.get("is_duplicate") is True:
                    duplicates += 1
                    continue
                if not self._matches_predicates(record, plan.policy_plan.driver_predicates):
                    continue

                record_id = _read_string(record.get("id"), "")
                if record_id:
                    if record_id in seen_record_ids:
                        duplicates += 1
                        continue
                    seen_record_ids.add(record_id)

                filtered_unique += 1
                if len(rows) < plan.limit:
                    rows.append(
                        {
                            field: record[field]
                            for field in plan.driver_projection
                            if field in record
                        }
                    )

        return DriverResult(
            rows=rows,
            source_files=files,
            total_raw=total_raw,
            total_unique=max(total_raw - duplicates, 0),
            duplicates=duplicates,
            filtered_unique=filtered_unique,
            parse_errors=parse_errors,
        )

    def _resolve_source_files(self, plan: QueryPlan) -> Iterable[Path]:
        root = plan.binding.source_root
        if not root.is_dir():
            raise ContractRuntimeError(
                "data_source_unavailable",
                status_code=404,
                user_message="WebUI contract source files are not available.",
                admin_message=f"Source root does not exist: {root}",
            )

        all_files = sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in JSON_DATA_SUFFIXES and not path.name.startswith(".")
        )
        if not all_files:
            return ()

        start_date, end_date = self._request_date_range(plan.params)
        if start_date and end_date:
            matched = [
                path
                for path in all_files
                if (file_date := _data_file_date(root, path)) and start_date <= file_date <= end_date
            ]
            return matched

        dated_files = [(date, path) for path in all_files if (date := _data_file_date(root, path))]
        if dated_files:
            latest_date = max(date for date, _path in dated_files)
            return [path for date, path in dated_files if date == latest_date]
        return all_files

    def _request_date_range(self, params: dict[str, Any]) -> tuple[str, str] | tuple[None, None]:
        from_date = _date_from_value(params.get("from") or params.get("startDate") or params.get("date"))
        to_date = _date_from_value(params.get("to") or params.get("endDate") or params.get("date"))
        if from_date and not to_date:
            to_date = from_date
        if to_date and not from_date:
            from_date = to_date
        if from_date and to_date and from_date > to_date:
            from_date, to_date = to_date, from_date
        if from_date and to_date:
            return from_date, to_date
        return None, None

    def _assert_allowed(self, path: Path, allowlist_roots: tuple[Path, ...]) -> None:
        resolved = path.resolve()
        for root in allowlist_roots:
            try:
                resolved.relative_to(root.resolve())
                return
            except ValueError:
                continue
        raise ContractRuntimeError(
            "data_source_unavailable",
            status_code=403,
            user_message="WebUI contract data source path is not allowed.",
            admin_message=f"Driver rejected path outside allowlist: {resolved}",
        )

    def _iter_records(self, path: Path) -> Iterable[dict[str, Any] | None]:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    value = json.loads(stripped)
                except json.JSONDecodeError:
                    yield None
                    continue
                yield value if isinstance(value, dict) else None

    def _matches_predicates(self, record: dict[str, Any], predicates: tuple[Predicate, ...]) -> bool:
        for predicate in predicates:
            value = record.get(predicate.field)
            if predicate.operator == "in":
                allowed = {_normalize_compare(item) for item in predicate.values}
                if _normalize_compare(value) not in allowed:
                    return False
            else:
                return False
        return True


def _data_file_date(root: Path, path: Path) -> str:
    try:
        parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        parts = path.parts
    for part in parts[:-1]:
        if DATE_RE.fullmatch(part):
            return part
    return ""


def _date_from_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if DATE_RE.fullmatch(text[:10]):
        return text[:10]
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def _normalize_compare(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip().lower()


def _read_string(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _sqlite_identifier(value: Any, fallback: str) -> str:
    text = str(value or fallback).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise ContractRuntimeError(
            "data_source_unavailable",
            status_code=400,
            user_message="WebUI contract SQLite source is misconfigured.",
            admin_message=f"Invalid SQLite identifier: {text}",
        )
    return text
