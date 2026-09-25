"""Task identity, waiting and cancellation must not affect sibling executions."""

import asyncio
from contextvars import ContextVar
import gc
import weakref

import pytest

from flocks.task.background import BackgroundManager, LaunchInput, ResumeInput


async def _start(manager, kind, index):
    if kind == "launch":
        return await manager.launch(LaunchInput(
            description=str(index), prompt="test", agent="rex",
            parent_session_id="parent", parent_message_id="message", parent_agent="rex",
        ))
    if kind == "resume":
        return await manager.resume(ResumeInput(
            session_id=f"child-{index}", prompt="continue", parent_session_id="parent",
            parent_message_id="message", parent_agent="rex",
        ))
    return await manager.run_existing_session(f"child-{index}", str(index), "rex")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["launch", "resume", "existing"])
async def test_real_generated_ids_keep_cancel_and_wait_isolated(monkeypatch, kind):
    manager = BackgroundManager()
    release = asyncio.Event()
    handles = []

    async def run(task, _input):
        handles.append(asyncio.current_task())
        task.status = "running"
        await release.wait()
        task.status = "completed"
        task.output = task.id

    for name in ("_run_task", "_run_resume", "_run_existing_session"):
        monkeypatch.setattr(manager, name, run)
    tasks = await asyncio.gather(*(_start(manager, kind, index) for index in range(5)))
    await asyncio.sleep(0)
    try:
        assert len({task.id for task in tasks}) == len(tasks)
        assert len(manager.list_tasks()) == len(tasks)
        assert manager.cancel(tasks[0].id) == 1
        await asyncio.sleep(0)
        assert handles[0].cancelled()
        assert all(not handle.done() for handle in handles[1:])
        release.set()
        for task in tasks[1:]:
            result = await manager.wait_for(task.id, timeout_ms=1000)
            assert result is task
            assert result.status == "completed" and result.output == task.id
        assert tasks[0].status == "cancelled"
    finally:
        for handle in handles:
            if not handle.done():
                handle.cancel()
        await asyncio.gather(*handles, return_exceptions=True)


@pytest.mark.asyncio
async def test_wait_timeout_leaves_task_available_for_later_wait(monkeypatch):
    manager = BackgroundManager()
    release = asyncio.Event()

    async def run(task, _input):
        await release.wait()
        task.status = "completed"

    monkeypatch.setattr(manager, "_run_task", run)
    task = await _start(manager, "launch", 0)
    handle = manager._task_handles[task.id]
    try:
        assert await manager.wait_for(task.id, timeout_ms=1) is None
        assert not handle.done(), "a polling timeout cancelled the real execution"
        release.set()
        assert await manager.wait_for(task.id, timeout_ms=1000) is task
        assert task.status == "completed"
    finally:
        handle.cancel()
        await asyncio.gather(handle, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelling_waiter_still_cancels_owned_execution(monkeypatch):
    manager = BackgroundManager()

    async def run(task, _input):
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_run_task", run)
    task = await _start(manager, "launch", 0)
    handle = manager._task_handles[task.id]
    waiter = asyncio.create_task(manager.wait_for(task.id))
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    await asyncio.sleep(0)
    assert handle.cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["launch", "resume", "existing"])
async def test_completed_handles_release_context_but_keep_history(monkeypatch, kind):
    manager = BackgroundManager()
    carrier_var = ContextVar("background-test-carrier")

    class Carrier:
        pass

    async def run(task, _input):
        assert isinstance(carrier_var.get(), Carrier)
        task.status = "completed"
        task.output = "saved result"

    for name in ("_run_task", "_run_resume", "_run_existing_session"):
        monkeypatch.setattr(manager, name, run)
    carrier = Carrier()
    retained = weakref.ref(carrier)
    token = carrier_var.set(carrier)  # secret-guard: allow
    tasks = [await _start(manager, kind, index) for index in range(5)]
    carrier_var.reset(token)
    del carrier
    try:
        for _ in range(3):
            await asyncio.sleep(0)
        gc.collect()
        assert retained() is None, "finished task handles retain the caller's execution context"
        assert not manager._task_handles
        assert manager.list_tasks() == tasks
        for task in tasks:
            assert manager.get_task(task.id) is task
            assert await manager.wait_for(task.id, timeout_ms=1) is task
            assert task.status == "completed" and task.output == "saved result"
            assert manager.cancel(task.id) == 0
    finally:
        await asyncio.gather(*manager._task_handles.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_done_callback_consumes_unexpected_exception(monkeypatch):
    manager = BackgroundManager()
    consumed = []

    class TrackedTask(asyncio.Task):
        def exception(self):
            consumed.append(self)
            return super().exception()

    async def run(task, _input):
        raise RuntimeError("unexpected lifecycle failure")

    monkeypatch.setattr(manager, "_run_task", run)
    monkeypatch.setattr(asyncio, "create_task", lambda coro: TrackedTask(coro))
    task = await _start(manager, "launch", 0)
    handle = manager._task_handles[task.id]
    try:
        for _ in range(3):
            await asyncio.sleep(0)
        assert consumed == [handle]
        assert task.id not in manager._task_handles
        assert task.status == "error" and task.error == "unexpected lifecycle failure"
        assert task.completed_at is not None
        assert await manager.wait_for(task.id) is task
    finally:
        await asyncio.gather(handle, return_exceptions=True)
