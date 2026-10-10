from __future__ import annotations

import asyncio

import pytest

from flocks.task.background import BackgroundManager, LaunchInput, ResumeInput


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch):
    async def run_until_cancelled(self, task, *_args, **_kwargs):
        task.status = "running"
        await asyncio.Event().wait()

    monkeypatch.setattr(BackgroundManager, "_run_task", run_until_cancelled)
    monkeypatch.setattr(BackgroundManager, "_run_resume", run_until_cancelled)
    monkeypatch.setattr(BackgroundManager, "_run_existing_session", run_until_cancelled)
    return BackgroundManager(max_concurrency=8)


async def _stop_all(manager: BackgroundManager) -> None:
    handles = list(manager._task_handles.values())
    manager.cancel(all_tasks=True)
    await asyncio.gather(*handles, return_exceptions=True)


async def test_tasks_started_back_to_back_get_distinct_ids(manager: BackgroundManager):
    tasks = [
        await manager.launch(LaunchInput("a", "p", "explore", None, None, None)),
        await manager.launch(LaunchInput("b", "p", "explore", None, None, None)),
        await manager.resume(ResumeInput("ses_resume", "p", None, None, None)),
        await manager.run_existing_session("ses_existing_a", "c", "verifier"),
        await manager.run_existing_session("ses_existing_b", "d", "threat-modeler"),
    ]
    try:
        ids = [task.id for task in tasks]
        assert len(set(ids)) == len(ids), ids
        for task in tasks:
            assert manager.get_task(task.id) is task
            assert task.id in manager._task_handles
    finally:
        await _stop_all(manager)


async def test_cancelling_one_task_leaves_a_concurrent_task_running(manager: BackgroundManager):
    # Two audits on the same server: one audit's verifier is running when the
    # other audit's freshly started worker gets cancelled.
    verifier = await manager.run_existing_session("ses_verifier", "verify", "verifier")
    other = await manager.run_existing_session("ses_other", "model threats", "threat-modeler")
    verifier_handle = manager._task_handles[verifier.id]
    try:
        await asyncio.sleep(0)
        assert manager.cancel(task_id=other.id) == 1
        await asyncio.sleep(0)

        assert not verifier_handle.done()
        assert manager.get_task(verifier.id) is verifier
        assert verifier.status == "running"
        assert other.status == "cancelled"
    finally:
        await _stop_all(manager)
