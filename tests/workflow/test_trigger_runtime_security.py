"""Security gateway coverage for non-webhook workflow trigger execution."""

from unittest.mock import AsyncMock

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.security.action_gateway import ActionDeniedError
from flocks.workflow.triggers.models import TriggerDefinition
from flocks.workflow.triggers.runtime import TriggerRuntime
from flocks.workflow.triggers.security import execute_trigger_action


@pytest.mark.asyncio
async def test_schedule_trigger_is_denied_before_creating_execution_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DenyTrigger(HookBase):
        async def action_before(self, ctx) -> None:
            ctx.output["policy_engine_present"] = True
            ctx.output["decision"] = {"action": "deny", "reason": "trigger_denied"}

    HookPipeline.reset()
    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    HookPipeline.register("deny-trigger", _DenyTrigger(), critical=True)
    create_record = AsyncMock(side_effect=AssertionError("execution record reached"))
    monkeypatch.setattr(
        "flocks.workflow.triggers.runtime.create_execution_record",
        create_record,
    )
    trigger = TriggerDefinition.model_validate(
        {
            "id": "schedule-1",
            "type": "schedule",
            "enabled": True,
            "source": {"intervalSeconds": 60},
        }
    )
    try:
        with pytest.raises(ActionDeniedError, match="trigger_denied"):
            await TriggerRuntime()._execute_workflow(
                workflow_id="wf-secure",
                workflow_json={"nodes": [], "edges": []},
                trigger=trigger,
                mapped_inputs={"message": "hello"},
            )
    finally:
        HookPipeline.reset()

    create_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_trigger_remains_outside_b1_b3_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explicitly deferred webhook retirement is not silently broadened."""
    before = AsyncMock(side_effect=AssertionError("webhook should not enter B1/B3 action gateway"))
    HookPipeline.reset()
    monkeypatch.setattr(HookPipeline, "run_action_before", before)
    create_record = AsyncMock(
        return_value={"id": "exec-webhook", "workflowId": "wf-webhook", "status": "running"}
    )
    monkeypatch.setattr(
        "flocks.workflow.triggers.runtime.create_execution_record",
        create_record,
    )
    monkeypatch.setattr(
        "flocks.workflow.triggers.runtime.record_execution_result",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "flocks.workflow.triggers.runtime.run_workflow",
        lambda **_kwargs: type(
            "Result", (), {"outputs": {}, "history": [], "last_node_id": None, "steps": 0}
        )(),
    )
    trigger = TriggerDefinition.model_validate(
        {"id": "webhook-1", "type": "webhook", "enabled": True, "source": {}}
    )

    await TriggerRuntime()._execute_workflow(
        workflow_id="wf-webhook",
        workflow_json={"nodes": [], "edges": []},
        trigger=trigger,
        mapped_inputs={},
    )

    before.assert_not_awaited()


@pytest.mark.asyncio
async def test_kafka_legacy_path_is_tagged_but_runs_without_service_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = []

    class _ObserveLegacy(HookBase):
        async def action_before(self, ctx) -> None:
            observed.append(ctx.input)
            ctx.output["policy_engine_present"] = True
            ctx.output["decision"] = {"action": "allow"}

    HookPipeline.reset()
    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    HookPipeline.register("observe-legacy", _ObserveLegacy(), critical=True)
    effect = AsyncMock(return_value={"ok": True})
    try:
        result = await execute_trigger_action(
            workflow_id="wf-legacy",
            trigger_id="kafka-legacy",
            trigger_type="kafka",
            mapped_inputs={"event": "x"},
            effect=effect,
        )
    finally:
        HookPipeline.reset()

    assert result == {"ok": True}
    effect.assert_awaited_once()
    assert observed[0]["metadata"]["legacy_compat"] is True
    assert observed[0]["subject"] == {}
