"""Usage tracking API routes."""

from typing import Optional

from fastapi import APIRouter, Query

from flocks.provider.types import UsageRecord
from flocks.provider.usage_service import (
    RecordUsageRequest,
    UsageStatsResponse,
    get_usage_stats,
    record_usage,
)

router = APIRouter()


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
    start_date: Optional[str] = Query(None, description="Start date (ISO format)"),
    end_date: Optional[str] = Query(None, description="End date (ISO format)"),
    provider_id: Optional[str] = Query(None, description="Filter by provider"),
    model_id: Optional[str] = Query(None, description="Filter by model"),
) -> UsageStatsResponse:
    """Get aggregated usage statistics (HTTP route handler)."""
    return await get_usage_stats(
        start_date=start_date, end_date=end_date,
        provider_id=provider_id, model_id=model_id,
    )
