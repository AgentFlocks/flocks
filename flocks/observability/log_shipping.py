"""Whether the log shipper still gets this node's logs out.

Logs are written here and shipped by a collector (Filebeat) to an external
store (Elasticsearch).  When the store is unreachable the collector buffers on
disk; nothing on the serving path depends on it, so it never affects
readiness -- but someone has to know before the buffer is full and logs are
lost.  With ``FLOCKS_LOG_SHIPPER_STATS_URL`` set (Filebeat's monitoring
endpoint, ``http://127.0.0.1:5066/stats``), the process reads the collector's
counters every ``FLOCKS_LOG_SHIPPER_CHECK_S`` seconds (default 60) and raises:

* ``log_shipping_stalled`` -- sends keep failing and nothing was acknowledged
  over a whole interval (the store is unreachable or refusing);
* ``log_shipping_queue_full`` -- the collector's queue is at least 90% full;
* ``log_shipping_unobservable`` -- the counters cannot be read at all.

Each clears once the condition is gone.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from flocks.observability.alerts import clear_alert, raise_alert
from flocks.utils.log import Log

log = Log.create(service="log_shipping")

QUEUE_FULL_RATIO = 0.9


@dataclass(frozen=True)
class ShipperCounters:
    acked: int
    failed: int
    write_errors: int
    queue_ratio: Optional[float]


def _dig(data: Mapping[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def counters_from_stats(stats: Mapping[str, Any]) -> ShipperCounters:
    """The counters that matter, read from a Filebeat ``/stats`` document.

    Field names vary a little across versions: the queue fill is taken from
    ``filled.pct`` when there is one, else from filled over maximum events.
    """
    output = _dig(stats, "libbeat", "output") or {}
    queue = _dig(stats, "libbeat", "pipeline", "queue") or {}
    ratio = _number(_dig(queue, "filled", "pct"))
    if ratio is None:
        filled, maximum = _number(_dig(queue, "filled", "events")), _number(_dig(queue, "max_events"))
        if filled is not None and maximum:
            ratio = filled / maximum
    return ShipperCounters(
        acked=int(_number(_dig(output, "events", "acked")) or 0),
        failed=int(_number(_dig(output, "events", "failed")) or 0),
        write_errors=int(_number(_dig(output, "write", "errors")) or 0),
        queue_ratio=ratio,
    )


def judge(previous: Optional[ShipperCounters], current: ShipperCounters) -> dict[str, Optional[str]]:
    """Which alerts hold now: name -> detail when raised, None when clear."""
    verdict: dict[str, Optional[str]] = {"log_shipping_queue_full": None, "log_shipping_stalled": None}
    if current.queue_ratio is not None and current.queue_ratio >= QUEUE_FULL_RATIO:
        verdict["log_shipping_queue_full"] = f"log shipper queue {current.queue_ratio:.0%} full"
    if previous is not None:
        failing = current.failed > previous.failed or current.write_errors > previous.write_errors
        if failing and current.acked <= previous.acked:
            verdict["log_shipping_stalled"] = (
                f"log shipper sends failing ({current.failed - previous.failed} failed, "
                f"{current.write_errors - previous.write_errors} write errors) and none acknowledged"
            )
    return verdict


async def _read_stats(url: str) -> Mapping[str, Any]:
    import httpx

    # The collector's stats endpoint runs beside the service (Filebeat's http.host
    # on 127.0.0.1); no proxy stands between them.
    async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:  # outbound: loopback
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def run_log_shipping_monitor(url: Optional[str] = None, interval: Optional[float] = None) -> None:
    """Watch the collector until cancelled; does nothing without a stats URL."""
    url = url if url is not None else os.environ.get("FLOCKS_LOG_SHIPPER_STATS_URL", "").strip()
    if not url:
        return
    period = interval if interval is not None else float(os.environ.get("FLOCKS_LOG_SHIPPER_CHECK_S", "60") or 60)
    previous: Optional[ShipperCounters] = None
    while True:
        try:
            current = counters_from_stats(await _read_stats(url))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise_alert("log_shipping_unobservable", f"log shipper counters unreadable ({type(exc).__name__})")
        else:
            clear_alert("log_shipping_unobservable")
            for name, detail in judge(previous, current).items():
                if detail is None:
                    clear_alert(name)
                else:
                    raise_alert(name, detail)
            previous = current
        await asyncio.sleep(max(period, 1.0))


__all__ = ["ShipperCounters", "counters_from_stats", "judge", "run_log_shipping_monitor"]
