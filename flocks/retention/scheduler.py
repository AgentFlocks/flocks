"""Run the data retention cleanup on a timer and whenever the policy changes."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from flocks.retention.cleanup import RetentionReport, apply_policy
from flocks.retention.policy import apply_log_retention, load_policy, load_saved_policy, policy_version
from flocks.utils.log import Log, log_cleanup_managed, set_log_cleanup_managed

log = Log.create(service="retention.scheduler")

LAST_RUN_STORAGE_KEY = "data_retention:last_run"


class DataRetentionScheduler:
    """One cleanup pass at a time: shortly after startup, every few hours, and on request."""

    STARTUP_DELAY_SECONDS = 120
    INTERVAL_SECONDS = 6 * 3600

    _task: Optional[asyncio.Task] = None
    _one_off: Optional[asyncio.Task] = None
    _wake: Optional[asyncio.Event] = None
    _lock: Optional[asyncio.Lock] = None
    # Bumped whenever the policy changes (request_run); an in-flight round stops
    # deleting once this moves past the generation it started with.
    _generation: int = 0
    _running: bool = False
    # Whether the logger had already left deleting to retention before start();
    # stop() puts that back instead of handing deleting to the logger.
    _log_managed_before: Optional[bool] = None

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    async def start(cls) -> None:
        try:
            apply_log_retention(await load_policy())
        except Exception as exc:
            log.warning("retention.policy_load_failed", {"error": str(exc)})
        if cls._task and not cls._task.done():
            return
        cls._wake = asyncio.Event()
        cls._task = asyncio.create_task(cls._run_loop(), name="data-retention-scheduler")
        # Expired logs go in its rounds, under the policy as last saved anywhere
        # (preload_log_retention has usually handed them over already).
        if cls._log_managed_before is None:
            cls._log_managed_before = log_cleanup_managed()
        set_log_cleanup_managed(True)

    @classmethod
    async def stop(cls) -> None:
        tasks = [task for task in (cls._task, cls._one_off) if task and not task.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        cls._task = None
        cls._one_off = None
        cls._wake = None
        cls._lock = None
        cls._running = False
        if cls._log_managed_before is not None:
            # As before start: a process that preloaded keeps its logger out of
            # deleting (shutdown may cross midnight with an outdated period).
            set_log_cleanup_managed(cls._log_managed_before)
            cls._log_managed_before = None

    @classmethod
    def is_running(cls) -> bool:
        return cls._running

    @classmethod
    def request_run(cls) -> None:
        """Run a cleanup pass soon, e.g. right after the policy was changed."""
        # The policy just changed; stop any in-flight round from deleting more
        # under the old policy, then schedule a fresh one.
        cls._generation += 1
        if cls._task and not cls._task.done() and cls._wake is not None:
            cls._wake.set()
            return
        # No timer loop (tests, tools): run once in the background instead.
        if cls._one_off and not cls._one_off.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        cls._one_off = loop.create_task(cls.run_once(), name="data-retention-once")

    @classmethod
    async def _run_loop(cls) -> None:
        delay = cls.STARTUP_DELAY_SECONDS
        wake = cls._wake
        while wake is not None:
            try:
                await asyncio.wait_for(wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            wake.clear()
            await cls.run_once()
            delay = cls.INTERVAL_SECONDS

    @classmethod
    def _lock_path(cls):
        from flocks.config.config import Config

        return Config.get_data_path() / "locks" / "data_retention.lock"

    @staticmethod
    def _acquire_process_lock(path):
        """Exclusive cross-process lock so only one worker cleans at a time.

        Returns an open handle to hold, or None. None means either no fcntl
        (Windows — a single process, the in-process lock is enough) or another
        worker already holds it; the caller tells the two apart via _HAS_FCNTL.
        """
        try:
            import fcntl
        except ImportError:
            return None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(path, "w")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except OSError:
            try:
                handle.close()
            except Exception:
                pass
            return None

    @classmethod
    async def run_once(cls) -> Optional[RetentionReport]:
        async with cls._get_lock():
            handle = await asyncio.to_thread(cls._acquire_process_lock, cls._lock_path())
            # With fcntl available, a missing handle means another worker is
            # already cleaning; skip rather than delete twice.
            if handle is None:
                try:
                    import fcntl  # noqa: F401
                    return None
                except ImportError:
                    pass
            cls._running = True
            try:
                from flocks.config.config import Config

                # The shared version first: a save writes the policy, then moves
                # it, so a policy read after it (from every source, past any
                # cache) can only be as new or newer.
                version = await policy_version()
                policy = await load_saved_policy()
                Config.clear_cache()
                apply_log_retention(policy)
                generation = cls._generation
                report = await apply_policy(
                    policy,
                    should_continue=lambda: cls._generation == generation,
                    policy_version=version,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("retention.run_failed", {"error": str(exc)})
                await cls._store_last_run({
                    "startedAt": int(time.time() * 1000),
                    "finishedAt": int(time.time() * 1000),
                    "failed": True,
                })
                return None
            finally:
                cls._running = False
                if handle is not None:
                    try:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                        handle.close()
                    except Exception:
                        pass
            await cls._store_last_run(report.to_dict())
            return report

    @classmethod
    async def _store_last_run(cls, payload: Dict[str, Any]) -> None:
        from flocks.storage.storage import Storage

        try:
            await Storage.set(LAST_RUN_STORAGE_KEY, payload)
        except Exception as exc:
            log.warning("retention.last_run_store_failed", {"error": str(exc)})

    @classmethod
    async def last_run(cls) -> Optional[Dict[str, Any]]:
        from flocks.storage.storage import Storage

        try:
            value = await Storage.get(LAST_RUN_STORAGE_KEY)
        except Exception:
            return None
        return value if isinstance(value, dict) else None
