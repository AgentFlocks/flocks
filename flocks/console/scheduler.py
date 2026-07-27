"""Background scheduler for console heartbeat and profile sync."""

from __future__ import annotations

import asyncio
import time

from flocks.console.login import ConsoleLoginService
from flocks.storage.storage import Storage
from flocks.utils.log import Log

HEARTBEAT_INTERVAL_SECONDS = 3600
SESSION_REFRESH_INTERVAL_SECONDS = 86400
PROFILE_SYNC_INTERVAL_SECONDS = 86400
SCHEDULER_TICK_SECONDS = 60

_HEARTBEAT_TS_KEY = "console:sync:last_heartbeat_ts"
_REFRESH_TS_KEY = "console:sync:last_session_refresh_ts"
_PROFILE_TS_KEY = "console:sync:last_profile_sync_ts"

log = Log.create(service="console.sync.scheduler")


def _is_due(now_ts: int, last_ts: int | None, interval_seconds: int) -> bool:
    if not last_ts:
        return True
    return now_ts - last_ts >= interval_seconds


class ConsoleSyncScheduler:
    _task: asyncio.Task | None = None

    @classmethod
    async def start(cls) -> None:
        if cls._task and not cls._task.done():
            return
        cls._task = asyncio.create_task(cls._run_loop(), name="console-sync-scheduler")

    @classmethod
    async def stop(cls) -> None:
        if not cls._task:
            return
        cls._task.cancel()
        try:
            await cls._task
        except asyncio.CancelledError:
            pass
        cls._task = None

    @classmethod
    async def _run_loop(cls) -> None:
        while True:
            await cls._tick_once()
            await asyncio.sleep(SCHEDULER_TICK_SECONDS)

    @classmethod
    async def _tick_once(cls) -> None:
        now_ts = int(time.time())
        await cls._maybe_send_heartbeat(now_ts)
        await cls._maybe_refresh_session(now_ts)
        await cls._maybe_sync_profile(now_ts)

    @classmethod
    async def send_startup_heartbeat(cls) -> None:
        """Send one heartbeat on every server start, regardless of interval."""
        now_ts = int(time.time())
        try:
            result = await ConsoleLoginService.send_heartbeat()
            await Storage.set(_HEARTBEAT_TS_KEY, now_ts, "number")
            log.info("console.sync.startup_heartbeat.ok", {"at": now_ts, "result": result})
        except ValueError:
            # Not bound / invalid session is expected and should not spam logs.
            return
        except Exception as exc:
            log.warning("console.sync.startup_heartbeat.failed", {"error": str(exc)})

    @classmethod
    async def _maybe_send_heartbeat(cls, now_ts: int) -> None:
        raw_last = await Storage.get(_HEARTBEAT_TS_KEY)
        last_ts = int(raw_last) if raw_last else None
        if not _is_due(now_ts, last_ts, HEARTBEAT_INTERVAL_SECONDS):
            return
        try:
            result = await ConsoleLoginService.send_heartbeat()
            await Storage.set(_HEARTBEAT_TS_KEY, now_ts, "number")
            log.info("console.sync.heartbeat.ok", {"at": now_ts, "result": result})
        except ValueError:
            # Not bound / invalid session is expected and should not spam logs.
            return
        except Exception as exc:
            log.warning("console.sync.heartbeat.failed", {"error": str(exc)})

    @classmethod
    async def _maybe_refresh_session(cls, now_ts: int) -> None:
        raw_last = await Storage.get(_REFRESH_TS_KEY)
        last_ts = int(raw_last) if raw_last else None
        if not _is_due(now_ts, last_ts, SESSION_REFRESH_INTERVAL_SECONDS):
            return
        try:
            result = await ConsoleLoginService.refresh_console_session()
            await Storage.set(_REFRESH_TS_KEY, now_ts, "number")
            log.info("console.sync.refresh.ok", {"at": now_ts, "result": result})
        except ValueError:
            return
        except Exception as exc:
            log.warning("console.sync.refresh.failed", {"error": str(exc)})

    @classmethod
    async def _maybe_sync_profile(cls, now_ts: int) -> None:
        raw_last = await Storage.get(_PROFILE_TS_KEY)
        last_ts = int(raw_last) if raw_last else None
        if not _is_due(now_ts, last_ts, PROFILE_SYNC_INTERVAL_SECONDS):
            return
        try:
            result = await ConsoleLoginService.sync_node_profile(source="scheduled")
            await Storage.set(_PROFILE_TS_KEY, now_ts, "number")
            log.info("console.sync.profile.ok", {"at": now_ts, "result": result})
        except ValueError:
            return
        except Exception as exc:
            log.warning("console.sync.profile.failed", {"error": str(exc)})
