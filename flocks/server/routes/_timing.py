"""Helpers for route timing logs."""

from __future__ import annotations

import time
from typing import Any

from flocks.utils.log import Logger

DEFAULT_SLOW_ROUTE_LOG_THRESHOLD_MS = 300


def log_route_timing(
    logger: Logger,
    event: str,
    *,
    started_at: float,
    extra: dict[str, Any] | None = None,
    slow_threshold_ms: int = DEFAULT_SLOW_ROUTE_LOG_THRESHOLD_MS,
) -> int:
    """Log route timings at INFO only when a request is slow enough."""
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    payload = {"duration_ms": duration_ms, **(extra or {})}
    if duration_ms >= slow_threshold_ms:
        logger.info(event, payload)
    else:
        logger.debug(event, payload)
    return duration_ms
