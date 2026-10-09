"""Driver proxy and builtin drivers for data access contracts."""

from __future__ import annotations

import json
import re
import time
from flocks.db.sync_query import connect
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from flocks.contracts.access.models import DriverResult, Predicate, QueryPlan, ContractRuntimeError

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JSON_DATA_SUFFIXES = frozenset({".jsonl", ".json"})
MAX_PG_SCAN_ROWS = 50_000
MAX_PG_SCAN_BYTES = 32 * 1024 * 1024
MAX_PG_SCAN_SECONDS = 30
SOURCE_ROW_ID_FIELD = "__flocks_source_row_id"


class DriverProxy:
    def __init__(
        self,
        jsonl_executor: "JsonlDriverExecutor | None" = None,
        postgres_executor: "PostgresJsonDriverExecutor | None" = None,
    ) -> None:
        self._jsonl_executor = jsonl_executor or JsonlDriverExecutor()
        self._postgres_executor = postgres_executor or PostgresJsonDriverExecutor()

    def execute(self, plan: QueryPlan) -> DriverResult:
        self._validate_plan(plan)
        if plan.binding.adapter_kind == "builtin-postgres-json":
            return self._postgres_executor.execute(plan)
        return self._jsonl_executor.execute(plan)

    def _validate_plan(self, plan: QueryPlan) -> None:
        if plan.binding.adapter_kind not in {"builtin-jsonl", "builtin-postgres-json"}:
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


class PostgresJsonDriverExecutor:
    def execute(self, plan: QueryPlan) -> DriverResult:
        db_path = plan.binding.source_root
        self._assert_allowed(db_path, plan.binding.driver_allowlist_roots)

        options = plan.binding.driver_options
        table = _postgres_identifier(options.get("table"), "records")
        record_column = _postgres_identifier(options.get("recordColumn"), "record_json")
        date_column = _postgres_identifier(options.get("dateColumn"), "record_date")
        event_time_column = _postgres_identifier_optional(options.get("eventTimeColumn"))
        row_id_column = _postgres_identifier_optional(options.get("rowIdColumn"))
        projection = record_column + (f", {row_id_column}" if row_id_column else "")
        query = f"SELECT {projection} FROM {table}"
        conditions: list[str] = []
        query_params: list[Any] = []
        start_date, end_date = JsonlDriverExecutor()._request_date_range(plan.params)
        if start_date and end_date and date_column:
            conditions.append(f"{date_column} BETWEEN ? AND ?")
            query_params.extend([start_date, end_date])
        start_time, end_time = JsonlDriverExecutor()._request_event_time_range(plan.params)
        sql_event_time_filtered = start_time is not None and end_time is not None and bool(event_time_column)
        if sql_event_time_filtered:
            conditions.append(f"{event_time_column} BETWEEN ? AND ?")
            query_params.extend([start_time, end_time])
        if conditions:
            query += f" WHERE {' AND '.join(conditions)}"
        order_column = _postgres_identifier(options.get("orderColumn"), "rowid")
        query += f" ORDER BY {order_column} LIMIT {MAX_PG_SCAN_ROWS + 1}"

        rows: list[dict[str, Any]] = []
        seen_record_ids: set[str] = set()
        total_raw = 0
        duplicates = 0
        filtered_unique = 0
        parse_errors = 0
        def records():
            try:
                with connect(readonly=True, timeout=30) as connection:
                    if not connection.table_exists(table):
                        return
                    # The synchronous query facade streams fixed-size cursor
                    # batches; only the requested result window is retained.
                    started = time.monotonic()
                    scanned_bytes = 0
                    for count, row in enumerate(connection.execute(query, query_params), start=1):
                        raw = row[0]
                        scanned_bytes += len((raw if isinstance(raw, str) else json.dumps(raw)).encode("utf-8"))
                        if (count > MAX_PG_SCAN_ROWS or scanned_bytes > MAX_PG_SCAN_BYTES
                                or time.monotonic() - started > MAX_PG_SCAN_SECONDS):
                            raise ContractRuntimeError(
                                "query_range_too_large", status_code=422,
                                user_message="Query range is too large; select a shorter time range.",
                                admin_message="PostgreSQL contract scan exceeded its row, byte or time budget.",
                            )
                        yield row
            except ContractRuntimeError:
                raise
            except Exception as exc:
                raise ContractRuntimeError(
                    "data_source_unavailable", status_code=500,
                    user_message="WebUI contract PostgreSQL data source cannot be queried.",
                    admin_message=f"PostgreSQL query failed ({type(exc).__name__}).",
                ) from exc

        for source_row in records():
            record_value = source_row[0]
            try:
                record = json.loads(record_value) if isinstance(record_value, str) else record_value
            except json.JSONDecodeError:
                record = None
            if not isinstance(record, dict):
                parse_errors += 1
                continue
            # Only an explicitly configured database column can supply source
            # identity. A JSON payload cannot impersonate another stored row.
            record = {key: value for key, value in record.items() if key != SOURCE_ROW_ID_FIELD}
            if row_id_column and source_row[1] is not None:
                record[SOURCE_ROW_ID_FIELD] = str(source_row[1])
            if record.get("_type") == "file_header":
                continue
            if not sql_event_time_filtered and not self._matches_event_time_range(record, start_time, end_time):
                continue

            total_raw += 1
            if record.get("is_duplicate") is True:
                duplicates += 1
                continue
            if not self._matches_predicates(
                record,
                plan.policy_plan.driver_predicates,
                plan.binding.predicate_value_resolver,
            ):
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

    def _matches_predicates(
        self,
        record: dict[str, Any],
        predicates: tuple[Predicate, ...],
        value_resolver: Callable[[dict[str, Any], str], Any] | None = None,
    ) -> bool:
        return JsonlDriverExecutor()._matches_predicates(record, predicates, value_resolver)

    def _matches_event_time_range(self, record: dict[str, Any], start_time: int | None, end_time: int | None) -> bool:
        return JsonlDriverExecutor()._matches_event_time_range(record, start_time, end_time)


class JsonlDriverExecutor:
    def execute(self, plan: QueryPlan) -> DriverResult:
        files = tuple(self._resolve_source_files(plan))
        rows: list[dict[str, Any]] = []
        seen_record_ids: set[str] = set()
        total_raw = 0
        duplicates = 0
        filtered_unique = 0
        parse_errors = 0
        start_time, end_time = self._request_event_time_range(plan.params)
        for path in files:
            self._assert_allowed(path, plan.binding.driver_allowlist_roots)
            for record in self._iter_records(path):
                if record is None:
                    parse_errors += 1
                    continue
                if record.get("_type") == "file_header":
                    continue
                if not self._matches_event_time_range(record, start_time, end_time):
                    continue

                total_raw += 1
                if record.get("is_duplicate") is True:
                    duplicates += 1
                    continue
                if not self._matches_predicates(
                    record,
                    plan.policy_plan.driver_predicates,
                    plan.binding.predicate_value_resolver,
                ):
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

    def _request_event_time_range(self, params: dict[str, Any]) -> tuple[int | None, int | None]:
        start_time = _epoch_seconds_from_value(
            params.get("startTime") or params.get("fromTime") or params.get("eventStartTime")
        )
        end_time = _epoch_seconds_from_value(
            params.get("endTime") or params.get("toTime") or params.get("eventEndTime")
        )
        if start_time is not None and end_time is None:
            end_time = start_time
        if end_time is not None and start_time is None:
            start_time = end_time
        if start_time is not None and end_time is not None and start_time > end_time:
            start_time, end_time = end_time, start_time
        return start_time, end_time

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

    def _matches_predicates(
        self,
        record: dict[str, Any],
        predicates: tuple[Predicate, ...],
        value_resolver: Callable[[dict[str, Any], str], Any] | None = None,
    ) -> bool:
        for predicate in predicates:
            value = (
                value_resolver(record, predicate.field)
                if value_resolver is not None
                else record.get(predicate.field)
            )
            if predicate.operator == "in":
                allowed = {_normalize_compare(item) for item in predicate.values}
                if _normalize_compare(value) not in allowed:
                    return False
            else:
                return False
        return True

    def _matches_event_time_range(self, record: dict[str, Any], start_time: int | None, end_time: int | None) -> bool:
        if start_time is None or end_time is None:
            return True
        record_time = _epoch_seconds_from_value(record.get("time"))
        if record_time is None:
            meta = record.get("_syslog_meta")
            if isinstance(meta, dict):
                record_time = _epoch_seconds_from_value(meta.get("timestamp"))
        if record_time is None:
            return False
        return start_time <= record_time <= end_time


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


def _epoch_seconds_from_value(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        return int(seconds)
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return int(parsed.timestamp())
    if seconds > 10_000_000_000:
        seconds /= 1000
    return int(seconds)


def _normalize_compare(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip().lower()


def _read_string(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _empty_postgres_result(db_path: Path) -> DriverResult:
    return DriverResult(
        rows=[],
        source_files=(db_path,),
        total_raw=0,
        total_unique=0,
        duplicates=0,
        filtered_unique=0,
        parse_errors=0,
    )


def _postgres_identifier(value: Any, fallback: str) -> str:
    text = str(value or fallback).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise ContractRuntimeError(
            "data_source_unavailable",
            status_code=400,
            user_message="WebUI contract PostgreSQL source is misconfigured.",
            admin_message=f"Invalid PostgreSQL identifier: {text}",
        )
    return text.lower()


def _postgres_identifier_optional(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise ContractRuntimeError(
            "data_source_unavailable",
            status_code=400,
            user_message="WebUI contract PostgreSQL source is misconfigured.",
            admin_message=f"Invalid PostgreSQL identifier: {text}",
        )
    return text.lower()
