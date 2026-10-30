"""Usage tracking API routes."""

import re
from datetime import date, datetime, time, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from flocks.provider.types import UsageRecord
from flocks.provider.usage_service import (
    RecordUsageRequest,
    UsageStatsResponse,
    get_usage_stats,
    record_usage,
)

router = APIRouter()

# YYYY-MM-DD with an optional time (T or space) and offset.
_ISO_BOUND_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)?"
)


def _parse_usage_date(name: str, value: Optional[str], *, end: bool = False) -> Optional[datetime]:
    """A date bound as an aware UTC datetime; 400 when it is not ISO 8601.

    An unreadable bound used to answer 200 with empty statistics. A bound
    without an offset is read as UTC, the zone usage records are stored in.
    A bare date as the end bound covers that whole day.
    """
    if value is None or value == "":
        return None
    parsed: Optional[datetime] = None
    if _ISO_BOUND_RE.fullmatch(value):
        try:
            if len(value) == 10:
                day = date.fromisoformat(value)
                parsed = datetime.combine(day, time.max if end else time.min)
            else:
                parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = None
    if parsed is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{name} must be an ISO 8601 date or datetime, e.g. 2026-10-20 or 2026-10-20T08:00:00",
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc)
    except OverflowError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{name} is out of range",
        ) from None


# ==================== Routes ====================


@router.post(
    "/record",
    response_model=UsageRecord,
    summary="Record usage",
    description="Record a single LLM usage entry",
)
async def api_record_usage(body: RecordUsageRequest) -> UsageRecord:
    """Record an LLM usage entry."""
    return await record_usage(body)


@router.get(
    "/summary",
    response_model=UsageStatsResponse,
    summary="Get usage statistics",
    description="Get aggregated usage statistics with optional date range",
)
async def api_get_usage_stats(
    start_date: Optional[str] = Query(None, description="Start date or datetime (ISO 8601; no offset means UTC)"),
    end_date: Optional[str] = Query(None, description="End date or datetime (ISO 8601; a bare date covers that whole day)"),
    provider_id: Optional[str] = Query(None, description="Filter by provider"),
    model_id: Optional[str] = Query(None, description="Filter by model"),
) -> UsageStatsResponse:
    """Get aggregated usage statistics (HTTP route handler).

    The bounds are validated here and handed on as UTC ISO strings; the
    service compares them with each record's instant.
    """
    start = _parse_usage_date("start_date", start_date)
    end = _parse_usage_date("end_date", end_date, end=True)
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must not be later than end_date",
        )
    return await get_usage_stats(
        start_date=start.isoformat() if start else None,
        end_date=end.isoformat() if end else None,
        provider_id=provider_id, model_id=model_id,
    )
