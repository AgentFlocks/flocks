from __future__ import annotations

import pytest

from flocks.task.manager import TaskManager
from flocks.task.models import ExecutionMode, ExecutionTriggerType, SchedulerMode, TaskTrigger


@pytest.mark.asyncio
async def test_update_scheduler_accepts_context_for_workflow_inputs(client):
    scheduler = await TaskManager.create_scheduler(
        title="工作流定时任务",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="0 9 * * *", timezone="Asia/Shanghai"),
        execution_mode=ExecutionMode.WORKFLOW,
        workflow_id="demo-workflow",
        context={"keyword": "before"},
    )

    response = await client.put(
        f"/api/task-schedulers/{scheduler.id}",
        json={
            "context": {"keyword": "after", "limit": 5},
            "workflowID": "demo-workflow",
        },
    )

    assert response.status_code == 200
    assert response.json()["context"] == {"keyword": "after", "limit": 5}

    updated = await TaskManager.get_scheduler(scheduler.id)
    assert updated is not None
    assert updated.context == {"keyword": "after", "limit": 5}

    execution = await TaskManager.create_execution_from_scheduler(
        updated,
        trigger_type=ExecutionTriggerType.SCHEDULED,
        enqueue=False,
    )
    assert execution.execution_input_snapshot["context"] == {
        "keyword": "after",
        "limit": 5,
    }
