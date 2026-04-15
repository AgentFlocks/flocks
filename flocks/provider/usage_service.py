"""
Shared usage tracking service.

Provides a single source of truth for usage persistence, aggregation,
and historical backfill without depending on HTTP route modules.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Dict, List, Optional, Sequence, Tuple

import aiosqlite
from pydantic import BaseModel, Field

from flocks.provider.cost_calculator import CostCalculator
from flocks.provider.provider import Provider
from flocks.provider.types import PriceConfig, UsageCost, UsageRecord
from flocks.session.message import Message
from flocks.session.session import Session
from flocks.storage.storage import Storage
from flocks.utils.log import Log

log = Log.create(service="usage-service")
_auto_backfill_complete = False
_auto_backfill_lock = asyncio.Lock()


class RecordUsageRequest(BaseModel):
    """Usage write request."""

    provider_id: str
    model_id: str
    credential_id: Optional[str] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: Optional[int] = None
    pricing: Optional[PriceConfig] = None
    cost_override: Optional[UsageCost] = None
    source: str = "live"
    created_at: Optional[datetime] = None
    backfilled_at: Optional[datetime] = None


class CurrencyCostSummary(BaseModel):
    """Usage cost grouped by currency."""

    currency: str
    total_cost: float = 0.0


class UsageSummary(BaseModel):
    """Aggregated usage summary."""

    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    total_requests: int = 0
    currency: str = "USD"
    cost_by_currency: List[CurrencyCostSummary] = Field(default_factory=list)


class ProviderUsageSummary(BaseModel):
    """Usage summary per provider."""

    provider_id: str
    total_tokens: int = 0
    total_cost: float = 0.0
    request_count: int = 0
    currency: str = "USD"
    cost_by_currency: List[CurrencyCostSummary] = Field(default_factory=list)


class ModelUsageSummary(BaseModel):
    """Usage summary per model."""

    provider_id: str
    model_id: str
    total_tokens: int = 0
    total_cost: float = 0.0
    request_count: int = 0
    currency: str = "USD"
    cost_by_currency: List[CurrencyCostSummary] = Field(default_factory=list)


class DailyUsageSummary(BaseModel):
    """Usage summary per day."""

    date: str
    total_tokens: int = 0
    total_cost: float = 0.0
    request_count: int = 0
    currency: str = "USD"
    cost_by_currency: List[CurrencyCostSummary] = Field(default_factory=list)


class UsageStatsResponse(BaseModel):
    """Full usage statistics."""

    summary: UsageSummary
    by_provider: List[ProviderUsageSummary]
    by_model: List[ModelUsageSummary]
    daily: List[DailyUsageSummary]


class BackfillUsageResult(BaseModel):
    """Historical usage backfill summary."""

    scanned_messages: int = 0
    inserted_records: int = 0
    skipped_existing: int = 0
    skipped_missing_data: int = 0


def resolve_usage_pricing(provider_id: str, model_id: str) -> Optional[PriceConfig]:
    """Resolve runtime pricing for a provider/model pair."""
    model_info = None
    provider = Provider.get(provider_id)
    if provider:
        for candidate in getattr(provider, "_config_models", []):
            if candidate.id == model_id:
                model_info = candidate
                break

    if model_info is None:
        model_info = Provider.get_model(model_id)

    pricing = getattr(model_info, "pricing", None) if model_info else None
    if pricing is None:
        return None

    if isinstance(pricing, PriceConfig):
        return pricing

    if hasattr(pricing, "input") and hasattr(pricing, "output"):
        return PriceConfig(
            input=getattr(pricing, "input", 0.0),
            output=getattr(pricing, "output", 0.0),
            unit=getattr(pricing, "unit", 1_000_000),
            currency=getattr(pricing, "currency", "USD"),
            cache_read=getattr(pricing, "cache_read", None),
            cache_write=getattr(pricing, "cache_write", None),
        )

    if isinstance(pricing, dict):
        return PriceConfig(
            input=pricing.get("input", 0.0),
            output=pricing.get("output", 0.0),
            unit=pricing.get("unit", 1_000_000),
            currency=pricing.get("currency", "USD"),
            cache_read=pricing.get("cache_read"),
            cache_write=pricing.get("cache_write"),
        )

    return None


def _currency_rollup(cost_rows: Sequence[Tuple[str, float]]) -> tuple[float, str, List[CurrencyCostSummary]]:
    """Return legacy single-currency fields plus grouped totals."""
    grouped = [
        CurrencyCostSummary(currency=currency, total_cost=round(total_cost, 6))
        for currency, total_cost in cost_rows
    ]
    if len(grouped) == 1:
        return grouped[0].total_cost, grouped[0].currency, grouped
    if len(grouped) == 0:
        return 0.0, "USD", []
    return 0.0, "MIXED", grouped


def _usage_record_from_row(row: aiosqlite.Row) -> UsageRecord:
    """Convert a database row into a UsageRecord."""
    created_at = datetime.fromisoformat(row["created_at"])
    backfilled_at = (
        datetime.fromisoformat(row["backfilled_at"])
        if row["backfilled_at"]
        else None
    )
    return UsageRecord(
        id=row["id"],
        provider_id=row["provider_id"],
        model_id=row["model_id"],
        credential_id=row["credential_id"],
        session_id=row["session_id"],
        message_id=row["message_id"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cached_tokens=row["cached_tokens"],
        cache_write_tokens=row["cache_write_tokens"],
        reasoning_tokens=row["reasoning_tokens"],
        total_tokens=row["total_tokens"],
        input_cost=row["input_cost"],
        output_cost=row["output_cost"],
        total_cost=row["total_cost"],
        currency=row["currency"],
        latency_ms=row["latency_ms"],
        source=row["source"],
        created_at=created_at,
        backfilled_at=backfilled_at,
    )


def _build_filters(
    *,
    start_date: Optional[str],
    end_date: Optional[str],
    provider_id: Optional[str],
    model_id: Optional[str],
    session_ids: Optional[Sequence[str]],
) -> tuple[str, List[str]]:
    """Build SQL filters for usage queries."""
    clauses: List[str] = []
    params: List[str] = []

    if start_date:
        clauses.append("created_at >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("created_at <= ?")
        params.append(end_date)
    if provider_id:
        clauses.append("provider_id = ?")
        params.append(provider_id)
    if model_id:
        clauses.append("model_id = ?")
        params.append(model_id)
    if session_ids is not None:
        if not session_ids:
            clauses.append("1 = 0")
        else:
            placeholders = ", ".join("?" for _ in session_ids)
            clauses.append(f"session_id IN ({placeholders})")
            params.extend(session_ids)

    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


async def _get_existing_usage_record(
    db: aiosqlite.Connection,
    *,
    session_id: Optional[str],
    message_id: Optional[str],
) -> Optional[UsageRecord]:
    """Look up an existing usage record for an assistant message."""
    if not session_id or not message_id:
        return None
    async with db.execute(
        """SELECT id, provider_id, model_id, credential_id, session_id, message_id,
                  input_tokens, output_tokens, cached_tokens, cache_write_tokens, reasoning_tokens,
                  total_tokens, input_cost, output_cost, total_cost, currency,
                  latency_ms, source, created_at, backfilled_at
           FROM usage_records
           WHERE session_id = ? AND message_id = ?
           LIMIT 1""",
        (session_id, message_id),
    ) as cursor:
        row = await cursor.fetchone()
    return _usage_record_from_row(row) if row else None


async def usage_record_exists(*, session_id: str, message_id: str) -> bool:
    """Check whether a usage row already exists for a message."""
    await Storage._ensure_init()
    async with aiosqlite.connect(Storage._db_path) as db:
        async with db.execute(
            "SELECT 1 FROM usage_records WHERE session_id = ? AND message_id = ? LIMIT 1",
            (session_id, message_id),
        ) as cursor:
            row = await cursor.fetchone()
    return row is not None


async def _get_recorded_message_ids(session_id: str) -> set[str]:
    """Return all assistant message ids already present in usage_records."""
    await Storage._ensure_init()
    async with aiosqlite.connect(Storage._db_path) as db:
        async with db.execute(
            "SELECT message_id FROM usage_records WHERE session_id = ? AND message_id IS NOT NULL",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return {row[0] for row in rows if row[0]}


async def ensure_usage_backfilled() -> None:
    """Run a one-time historical backfill before serving stats."""
    global _auto_backfill_complete
    if _auto_backfill_complete:
        return
    async with _auto_backfill_lock:
        if _auto_backfill_complete:
            return
        result = await backfill_usage_records()
        log.info("usage.backfill.auto_complete", result.model_dump())
        _auto_backfill_complete = True


async def record_usage(req: RecordUsageRequest) -> UsageRecord:
    """Record a usage entry in SQLite."""
    await Storage._ensure_init()

    total_tokens = req.input_tokens + req.output_tokens + req.reasoning_tokens
    created_at = req.created_at or datetime.now(UTC)

    if req.cost_override:
        input_cost = req.cost_override.input_cost
        output_cost = req.cost_override.output_cost
        total_cost = req.cost_override.total_cost
        currency = req.cost_override.currency
    elif req.pricing:
        computed_cost = CostCalculator.calculate(
            input_tokens=req.input_tokens,
            output_tokens=req.output_tokens,
            pricing=req.pricing,
            cached_tokens=req.cached_tokens,
            cache_write_tokens=req.cache_write_tokens,
        )
        input_cost = computed_cost.input_cost
        output_cost = computed_cost.output_cost
        total_cost = computed_cost.total_cost
        currency = computed_cost.currency
    else:
        input_cost = 0.0
        output_cost = 0.0
        total_cost = 0.0
        currency = "USD"

    async with aiosqlite.connect(Storage._db_path) as db:
        db.row_factory = aiosqlite.Row
        existing = await _get_existing_usage_record(
            db,
            session_id=req.session_id,
            message_id=req.message_id,
        )
        if existing is not None:
            return existing

        record_id = str(uuid.uuid4())
        await db.execute(
            """INSERT INTO usage_records
               (id, provider_id, model_id, credential_id, session_id, message_id,
                input_tokens, output_tokens, cached_tokens, cache_write_tokens, reasoning_tokens,
                total_tokens, input_cost, output_cost, total_cost, currency,
                latency_ms, source, created_at, backfilled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                req.provider_id,
                req.model_id,
                req.credential_id,
                req.session_id,
                req.message_id,
                req.input_tokens,
                req.output_tokens,
                req.cached_tokens,
                req.cache_write_tokens,
                req.reasoning_tokens,
                total_tokens,
                input_cost,
                output_cost,
                total_cost,
                currency,
                req.latency_ms,
                req.source,
                created_at.isoformat(),
                req.backfilled_at.isoformat() if req.backfilled_at else None,
            ),
        )
        await db.commit()

    return UsageRecord(
        id=record_id,
        provider_id=req.provider_id,
        model_id=req.model_id,
        credential_id=req.credential_id,
        session_id=req.session_id,
        message_id=req.message_id,
        input_tokens=req.input_tokens,
        output_tokens=req.output_tokens,
        cached_tokens=req.cached_tokens,
        cache_write_tokens=req.cache_write_tokens,
        reasoning_tokens=req.reasoning_tokens,
        total_tokens=total_tokens,
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=total_cost,
        currency=currency,
        latency_ms=req.latency_ms,
        source=req.source,
        created_at=created_at,
        backfilled_at=req.backfilled_at,
    )


async def get_usage_records(
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    provider_id: Optional[str] = None,
    model_id: Optional[str] = None,
    session_ids: Optional[Sequence[str]] = None,
) -> List[UsageRecord]:
    """Return raw usage rows for callers that need custom aggregation."""
    await ensure_usage_backfilled()
    await Storage._ensure_init()
    where_sql, params = _build_filters(
        start_date=start_date,
        end_date=end_date,
        provider_id=provider_id,
        model_id=model_id,
        session_ids=session_ids,
    )
    async with aiosqlite.connect(Storage._db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""SELECT id, provider_id, model_id, credential_id, session_id, message_id,
                       input_tokens, output_tokens, cached_tokens, cache_write_tokens, reasoning_tokens,
                       total_tokens, input_cost, output_cost, total_cost, currency,
                       latency_ms, source, created_at, backfilled_at
                FROM usage_records{where_sql}
                ORDER BY created_at ASC""",
            params,
        ) as cursor:
            rows = await cursor.fetchall()
    return [_usage_record_from_row(row) for row in rows]


async def get_usage_stats(
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    provider_id: Optional[str] = None,
    model_id: Optional[str] = None,
    session_ids: Optional[Sequence[str]] = None,
) -> UsageStatsResponse:
    """Query aggregated usage stats from SQLite."""
    await ensure_usage_backfilled()
    await Storage._ensure_init()
    where_sql, params = _build_filters(
        start_date=start_date,
        end_date=end_date,
        provider_id=provider_id,
        model_id=model_id,
        session_ids=session_ids,
    )

    async with aiosqlite.connect(Storage._db_path) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            f"""SELECT
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(input_tokens), 0) AS total_input,
                    COALESCE(SUM(output_tokens), 0) AS total_output,
                    COUNT(*) AS total_requests
                FROM usage_records{where_sql}""",
            params,
        ) as cursor:
            totals_row = await cursor.fetchone()

        async with db.execute(
            f"""SELECT currency, COALESCE(SUM(total_cost), 0) AS total_cost
                FROM usage_records{where_sql}
                GROUP BY currency
                ORDER BY currency ASC""",
            params,
        ) as cursor:
            summary_cost_rows = await cursor.fetchall()

        summary_total_cost, summary_currency, summary_cost_by_currency = _currency_rollup(
            [(row["currency"], row["total_cost"]) for row in summary_cost_rows]
        )
        summary = UsageSummary(
            total_tokens=totals_row["total_tokens"],
            total_input_tokens=totals_row["total_input"],
            total_output_tokens=totals_row["total_output"],
            total_cost=summary_total_cost,
            total_requests=totals_row["total_requests"],
            currency=summary_currency,
            cost_by_currency=summary_cost_by_currency,
        )

        async with db.execute(
            f"""SELECT provider_id, currency,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(total_cost), 0) AS total_cost,
                       COUNT(*) AS request_count
                FROM usage_records{where_sql}
                GROUP BY provider_id, currency
                ORDER BY provider_id ASC, currency ASC""",
            params,
        ) as cursor:
            provider_rows = await cursor.fetchall()

        provider_agg: Dict[str, Dict[str, object]] = {}
        for row in provider_rows:
            aggregate = provider_agg.setdefault(
                row["provider_id"],
                {
                    "provider_id": row["provider_id"],
                    "total_tokens": 0,
                    "request_count": 0,
                    "cost_rows": [],
                },
            )
            aggregate["total_tokens"] += row["total_tokens"]
            aggregate["request_count"] += row["request_count"]
            aggregate["cost_rows"].append((row["currency"], row["total_cost"]))

        by_provider: List[ProviderUsageSummary] = []
        for provider_key, aggregate in sorted(provider_agg.items()):
            total_cost, currency, cost_by_currency = _currency_rollup(aggregate["cost_rows"])
            by_provider.append(
                ProviderUsageSummary(
                    provider_id=provider_key,
                    total_tokens=aggregate["total_tokens"],
                    total_cost=total_cost,
                    request_count=aggregate["request_count"],
                    currency=currency,
                    cost_by_currency=cost_by_currency,
                )
            )

        async with db.execute(
            f"""SELECT provider_id, model_id, currency,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(total_cost), 0) AS total_cost,
                       COUNT(*) AS request_count
                FROM usage_records{where_sql}
                GROUP BY provider_id, model_id, currency
                ORDER BY provider_id ASC, model_id ASC, currency ASC""",
            params,
        ) as cursor:
            model_rows = await cursor.fetchall()

        model_agg: Dict[tuple[str, str], Dict[str, object]] = {}
        for row in model_rows:
            model_key = (row["provider_id"], row["model_id"])
            aggregate = model_agg.setdefault(
                model_key,
                {
                    "provider_id": row["provider_id"],
                    "model_id": row["model_id"],
                    "total_tokens": 0,
                    "request_count": 0,
                    "cost_rows": [],
                },
            )
            aggregate["total_tokens"] += row["total_tokens"]
            aggregate["request_count"] += row["request_count"]
            aggregate["cost_rows"].append((row["currency"], row["total_cost"]))

        by_model: List[ModelUsageSummary] = []
        for (_, _), aggregate in sorted(model_agg.items()):
            total_cost, currency, cost_by_currency = _currency_rollup(aggregate["cost_rows"])
            by_model.append(
                ModelUsageSummary(
                    provider_id=aggregate["provider_id"],
                    model_id=aggregate["model_id"],
                    total_tokens=aggregate["total_tokens"],
                    total_cost=total_cost,
                    request_count=aggregate["request_count"],
                    currency=currency,
                    cost_by_currency=cost_by_currency,
                )
            )

        async with db.execute(
            f"""SELECT DATE(created_at) AS date, currency,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(total_cost), 0) AS total_cost,
                       COUNT(*) AS request_count
                FROM usage_records{where_sql}
                GROUP BY DATE(created_at), currency
                ORDER BY date DESC, currency ASC
                LIMIT 180""",
            params,
        ) as cursor:
            daily_rows = await cursor.fetchall()

        daily_agg: Dict[str, Dict[str, object]] = {}
        for row in daily_rows:
            aggregate = daily_agg.setdefault(
                row["date"],
                {
                    "date": row["date"],
                    "total_tokens": 0,
                    "request_count": 0,
                    "cost_rows": [],
                },
            )
            aggregate["total_tokens"] += row["total_tokens"]
            aggregate["request_count"] += row["request_count"]
            aggregate["cost_rows"].append((row["currency"], row["total_cost"]))

        daily: List[DailyUsageSummary] = []
        for date_key in sorted(daily_agg.keys(), reverse=True)[:90]:
            aggregate = daily_agg[date_key]
            total_cost, currency, cost_by_currency = _currency_rollup(aggregate["cost_rows"])
            daily.append(
                DailyUsageSummary(
                    date=date_key,
                    total_tokens=aggregate["total_tokens"],
                    total_cost=total_cost,
                    request_count=aggregate["request_count"],
                    currency=currency,
                    cost_by_currency=cost_by_currency,
                )
            )

    return UsageStatsResponse(
        summary=summary,
        by_provider=by_provider,
        by_model=by_model,
        daily=daily,
    )


def _token_fields(tokens) -> tuple[int, int, int, int, int]:
    """Extract token counts from message metadata."""
    if not tokens:
        return 0, 0, 0, 0, 0
    cache = getattr(tokens, "cache", None)
    return (
        getattr(tokens, "input", 0) or 0,
        getattr(tokens, "output", 0) or 0,
        getattr(tokens, "reasoning", 0) or 0,
        getattr(cache, "read", 0) if cache else 0,
        getattr(cache, "write", 0) if cache else 0,
    )


def _message_timestamp_ms(info) -> Optional[int]:
    """Return the best available message timestamp."""
    time_info = getattr(info, "time", None) or {}
    return time_info.get("completed") or time_info.get("created")


async def backfill_usage_records(
    *,
    session_ids: Optional[Sequence[str]] = None,
    project_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> BackfillUsageResult:
    """Backfill usage records from persisted assistant message metadata."""
    await Storage._ensure_init()
    result = BackfillUsageResult()
    backfilled_at = datetime.now(UTC)
    start_dt = datetime.fromisoformat(start_date) if start_date else None
    end_dt = datetime.fromisoformat(end_date) if end_date else None

    sessions = await Session.list_all()
    selected_session_ids = set(session_ids) if session_ids is not None else None

    for session in sessions:
        if selected_session_ids is not None and session.id not in selected_session_ids:
            continue
        if project_id is not None and session.project_id != project_id:
            continue

        existing_message_ids = await _get_recorded_message_ids(session.id)
        messages = await Message.list_with_parts(session.id)
        for msg in messages:
            info = msg.info
            if getattr(info, "role", None) != "assistant":
                continue

            result.scanned_messages += 1
            backfill_status = await _backfill_message_if_needed(
                session_id=session.id,
                message=msg,
                existing_message_ids=existing_message_ids,
                backfilled_at=backfilled_at,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            if backfill_status == "inserted":
                result.inserted_records += 1
                message_id = getattr(msg.info, "id", None)
                if message_id:
                    existing_message_ids.add(message_id)
            elif backfill_status == "existing":
                result.skipped_existing += 1
            elif backfill_status == "missing":
                result.skipped_missing_data += 1

    return result


async def _backfill_message_if_needed(
    *,
    session_id: str,
    message,
    existing_message_ids: set[str],
    backfilled_at: datetime,
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
) -> str:
    """Backfill one assistant message if it is not already recorded."""
    info = message.info
    message_id = getattr(info, "id", None)
    provider_id = getattr(info, "providerID", None)
    model_id = getattr(info, "modelID", None)
    if not message_id or not provider_id or not model_id:
        return "missing"

    if message_id in existing_message_ids:
        return "existing"

    timestamp_ms = _message_timestamp_ms(info)
    created_at = (
        datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        if timestamp_ms
        else None
    )
    if created_at and start_dt and created_at < start_dt:
        return "filtered"
    if created_at and end_dt and created_at > end_dt:
        return "filtered"

    tokens = getattr(info, "tokens", None)
    input_tokens, output_tokens, reasoning_tokens, cached_tokens, cache_write_tokens = _token_fields(tokens)
    has_tokens = any((input_tokens, output_tokens, reasoning_tokens, cached_tokens, cache_write_tokens))
    cost = getattr(info, "cost", 0.0) or 0.0
    if not has_tokens and cost <= 0:
        return "missing"

    pricing = resolve_usage_pricing(provider_id, model_id)
    cost_override = None
    if cost > 0:
        currency = pricing.currency if pricing else "USD"
        cost_override = UsageCost(total_cost=cost, currency=currency)

    await record_usage(
        RecordUsageRequest(
            provider_id=provider_id,
            model_id=model_id,
            session_id=session_id,
            message_id=message_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=reasoning_tokens,
            pricing=pricing if cost_override is None else None,
            cost_override=cost_override,
            source="backfill",
            created_at=created_at,
            backfilled_at=backfilled_at,
        )
    )
    return "inserted"
