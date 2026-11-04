"""BackgroundManager.cancel must not rewrite the outcome of a task that already finished."""
from __future__ import annotations

import asyncio

import pytest

from flocks.task.background import BackgroundManager, BackgroundTask


@pytest.mark.asyncio
async def test_cancel_leaves_finished_tasks_untouched():
    mgr = BackgroundManager()
    done = BackgroundTask(id="bg_done", status="completed", description="d", prompt="p", agent="rex", completed_at=1234)
    mgr._tasks["bg_done"] = done

    async def _finished():
        return "ok"

    handle = asyncio.get_running_loop().create_task(_finished())
    await handle
    mgr._task_handles["bg_done"] = handle

    assert mgr.cancel(task_id="bg_done") == 0
    assert done.status == "completed" and done.completed_at == 1234


@pytest.mark.asyncio
async def test_cancel_running_task_marks_cancelled():
    mgr = BackgroundManager()
    running = BackgroundTask(id="bg_run", status="running", description="d", prompt="p", agent="rex")
    mgr._tasks["bg_run"] = running

    async def _forever():
        await asyncio.sleep(60)

    handle = asyncio.get_running_loop().create_task(_forever())
    mgr._task_handles["bg_run"] = handle
    await asyncio.sleep(0)

    assert mgr.cancel(task_id="bg_run") == 1
    assert running.status == "cancelled" and running.completed_at is not None
    with pytest.raises(asyncio.CancelledError):
        await handle
