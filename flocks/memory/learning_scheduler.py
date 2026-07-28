"""Background scheduler for Dream bridging and proposal recovery."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from flocks.config import Config
from flocks.memory.config import MemoryConfig
from flocks.memory.learning import (
    DreamTarget,
    list_dream_targets,
    recover_pending_skill_proposals,
    run_dream_bridge,
)
from flocks.storage import Storage
from flocks.utils.log import Log


_TICK_SECONDS = 30 * 60
_FAILURE_RETRY_SECONDS = 15 * 60
_LAST_SUCCESS_KEY = "memory:learning:dream:last_success_ts"

log = Log.create(service="memory.learning.scheduler")


class MemoryLearningScheduler:
    """Run due Dream batches without blocking request or Session lifecycles."""

    _task: Optional[asyncio.Task[None]] = None
    _retry_after_by_target: dict[str, float] = {}

    @classmethod
    async def start(cls) -> None:
        if cls._task and not cls._task.done():
            return
        cls._task = asyncio.create_task(
            cls._run_loop(),
            name="memory-learning-scheduler",
        )

    @classmethod
    async def stop(cls) -> None:
        if cls._task is None:
            return
        cls._task.cancel()
        try:
            await cls._task
        except asyncio.CancelledError:
            pass
        cls._task = None
        cls._retry_after_by_target.clear()

    @classmethod
    async def _run_loop(cls) -> None:
        try:
            app_config = await Config.get()
            config = getattr(app_config, "memory", None)
            skill_recovery_enabled = (
                isinstance(config, MemoryConfig)
                and config.enabled
                and config.learning.enabled
                and config.learning.skill.enabled
            )
            recovered = await recover_pending_skill_proposals() if skill_recovery_enabled else 0
            if recovered:
                log.info(
                    "memory.learning.proposals_recovered",
                    {"count": recovered},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warn(
                "memory.learning.proposal_recovery_failed",
                {"error": str(exc)},
            )

        while True:
            try:
                await cls._tick_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warn(
                    "memory.learning.scheduler_tick_failed",
                    {
                        "error": str(exc),
                    },
                )
            await asyncio.sleep(_TICK_SECONDS)

    @classmethod
    async def _tick_once(cls, now_ts: Optional[float] = None) -> None:
        now = time.time() if now_ts is None else now_ts
        app_config = await Config.get()
        config = getattr(app_config, "memory", None)
        if not isinstance(config, MemoryConfig):
            return
        if not config.enabled or not config.learning.enabled or not config.learning.dream.enabled:
            return

        interval_seconds = config.learning.dream.interval_hours * 60 * 60
        for target in await list_dream_targets():
            target_key = target.scheduler_key
            retry_after = cls._retry_after_by_target.get(target_key, 0)
            if now < retry_after:
                continue
            success_key = cls._last_success_key(target)
            raw_last_success = await Storage.get(success_key)
            last_success = (
                float(raw_last_success) if raw_last_success else None
            )
            if (
                last_success is not None
                and now - last_success < interval_seconds
            ):
                continue

            try:
                result = await run_dream_bridge(target)
                cls._retry_after_by_target.pop(target_key, None)
                if result.backlog:
                    log.info(
                        "memory.learning.dream_backlog",
                        {
                            "target": target_key,
                            "processed_sources": result.processed_sources,
                            "changed": result.changed,
                        },
                    )
                    continue
                await Storage.set(success_key, now, "number")
                log.info(
                    "memory.learning.dream_complete",
                    {
                        "target": target_key,
                        "processed_sources": result.processed_sources,
                        "changed": result.changed,
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retry_after = now + _FAILURE_RETRY_SECONDS
                cls._retry_after_by_target[target_key] = retry_after
                log.warn(
                    "memory.learning.dream_failed",
                    {
                        "target": target_key,
                        "error": str(exc),
                        "retry_after_ts": retry_after,
                    },
                )

    @staticmethod
    def _last_success_key(target: DreamTarget) -> str:
        """Preserve the legacy Global cursor while isolating Project cadence."""
        if target.scope.value == "global":
            return _LAST_SUCCESS_KEY
        return f"{_LAST_SUCCESS_KEY}:{target.scope.value}:{target.scope_id}"
