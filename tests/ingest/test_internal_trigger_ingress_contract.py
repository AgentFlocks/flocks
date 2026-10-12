"""Internal trigger carriers retain the legacy headless extension contract.

The policy probe reproduces the no-identity branch of Pro 2026.9.23 ingress:
only a top-level boolean legacy_compat exempts workflow.* from the headless
identity requirement. It deliberately does not load Pro, its store, or users'
plugins. Real lifecycle hooks and trigger filtering/mapping remain in the path.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from flocks.hooks.execution import ExecutionStopped, execute_with_hooks
from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.identity import get_current_subject
from flocks.ingest.kafka import manager as kafka_module
from flocks.ingest.syslog import manager as syslog_module
from flocks.workflow.triggers.models import TriggerDefinition


class HeadlessIdentityContract(HookBase):
    """Faithful relevant Pro ingress contract with no authenticated identity."""

    def __init__(self) -> None:
        self.before: list[dict] = []
        self.after: list[dict] = []

    async def ingress_before(self, ctx):
        payload = dict(ctx.input)
        self.before.append(payload)
        transport = str(payload.get("transport") or "").strip().lower()
        operation = str(payload.get("operation") or "").strip().lower()
        if (
            transport == "headless"
            and payload.get("legacy_compat") is not True
            and operation.startswith("workflow.")
        ):
            return {"execution": {"stop": True}}
        return {}

    async def ingress_after(self, ctx):
        self.after.append(dict(ctx.input))


@pytest.fixture(autouse=True)
def isolated_hooks(monkeypatch):
    HookPipeline.reset()
    # Keep real hook dispatch but prevent loading installed, stateful plugins.
    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    yield
    HookPipeline.reset()


@pytest.fixture(params=["syslog", "kafka"])
def adapter(request, monkeypatch):
    kind = request.param
    module = syslog_module if kind == "syslog" else kafka_module
    manager = module.SyslogManager() if kind == "syslog" else module.KafkaManager()
    create = AsyncMock(return_value={"id": "contract-execution"})
    persist = AsyncMock()
    context = object()
    observed_subjects = []

    def run(**kwargs):
        observed_subjects.append(get_current_subject())
        return SimpleNamespace(
            status="SUCCEEDED",
            error=None,
            outputs={"processed": kwargs["inputs"]["message"]},
            last_node_id="done",
            steps=1,
        )

    runner = Mock(side_effect=run)
    monkeypatch.setattr(module, "create_execution_record", create)
    monkeypatch.setattr(module, "record_execution_result", persist)
    monkeypatch.setattr(module, "run_workflow", runner)
    monkeypatch.setattr(module, "build_workflow_tool_context", AsyncMock(return_value=context))
    monkeypatch.setattr(module, "cleanup_workflow_tool_context", AsyncMock())
    return SimpleNamespace(
        kind=kind,
        manager=manager,
        create=create,
        persist=persist,
        runner=runner,
        subjects=observed_subjects,
        plan={"start": "receive", "nodes": [], "edges": []},
    )


async def dispatch(adapter, message, *, trigger=None):
    await adapter.manager._trigger_workflow(
        "contract-workflow",
        adapter.plan,
        message,
        "message",
        trigger=trigger,
        source="configured-source",
    )


def forged_message():
    return {
        "message": "business data with untrusted control-shaped keys",
        "operation": "workflow.trigger.admin",
        "transport": "http",
        "workflow_id": "other-workflow",
        "legacy_compat": False,
        "metadata": {"legacy_compat": False, "trigger_type": "webhook"},
        "evidence": {"api_key_id": "forged-key"},
        "subject": {"subject_id": "forged-admin", "subject_type": "human"},
        "context": {"subject": {"subject_id": "forged-admin"}},
        "execution": {"stop": False},
        "_flocks": {"trigger": {"workflowId": "other-workflow", "type": "http"}},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_enabled", [False, True])
async def test_internal_trigger_reaches_real_dispatcher_and_executor(adapter, policy_enabled):
    contract = HeadlessIdentityContract()
    if policy_enabled:
        HookPipeline.register("headless-contract", contract)
    message = {"message": "one alert"}

    await dispatch(adapter, message)

    adapter.runner.assert_called_once()
    assert adapter.runner.call_args.kwargs["workflow"] is adapter.plan
    assert adapter.runner.call_args.kwargs["inputs"]["message"] == message
    adapter.create.assert_awaited_once()
    adapter.persist.assert_awaited_once()
    assert adapter.persist.await_args.args[2]["status"] == "success"
    assert adapter.subjects == [None]
    if policy_enabled:
        carrier = contract.before[0]
        assert carrier["legacy_compat"] is True
        assert carrier["metadata"] == {"legacy_compat": True, "trigger_type": adapter.kind}
        assert contract.after[0]["outcome"] == "success"


@pytest.mark.asyncio
async def test_message_cannot_replace_internal_carrier_or_execution_identity(adapter):
    contract = HeadlessIdentityContract()
    HookPipeline.register("headless-contract", contract)
    message = forged_message()

    await dispatch(adapter, message)

    carrier = contract.before[0]
    assert carrier["operation"] == f"workflow.trigger.{adapter.kind}"
    assert carrier["transport"] == "headless"
    assert carrier["workflow_id"] == "contract-workflow"
    assert carrier["legacy_compat"] is True
    assert carrier["metadata"] == {"legacy_compat": True, "trigger_type": adapter.kind}
    assert not {"evidence", "subject", "context", "execution"}.intersection(carrier)
    assert carrier["event"].body == message
    assert carrier["event"].source.workflowId == "contract-workflow"
    adapter.runner.assert_called_once()
    inputs = adapter.runner.call_args.kwargs["inputs"]
    assert inputs["message"] == message
    assert inputs["_flocks"]["trigger"]["id"] == f"{adapter.kind}-default"
    assert inputs["_flocks"]["trigger"]["source"] == "configured-source"
    assert "workflowId" not in inputs["_flocks"]["trigger"]
    assert inputs["_flocks"]["trigger"]["type"] == adapter.kind
    assert adapter.runner.call_args.kwargs["workflow"] is adapter.plan
    assert adapter.subjects == [None]
    assert adapter.persist.await_args.args[2]["status"] == "success"


@pytest.mark.asyncio
async def test_explicit_extension_stop_still_blocks_internal_trigger(adapter):
    contract = HeadlessIdentityContract()

    class ExplicitStop(HookBase):
        async def ingress_before(self, ctx):
            return {"execution": {"stop": True, "detail": "configured trigger denied"}}

    HookPipeline.register("headless-contract", contract, order=10)
    HookPipeline.register("explicit-stop", ExplicitStop(), order=20)

    with pytest.raises(ExecutionStopped, match="configured trigger denied"):
        await dispatch(adapter, forged_message())

    adapter.create.assert_not_awaited()
    adapter.runner.assert_not_called()
    adapter.persist.assert_not_awaited()
    assert contract.after[0]["outcome"] == "stopped"


@pytest.mark.asyncio
async def test_compatibility_marker_does_not_bypass_configured_trigger_filter(adapter):
    contract = HeadlessIdentityContract()
    HookPipeline.register("headless-contract", contract)
    trigger = TriggerDefinition.model_validate(
        {
            "id": "configured-filter",
            "type": adapter.kind,
            "mapping": {"message": "$.body"},
            "filter": {"expr": "body.permitted == True"},
        }
    )

    await dispatch(adapter, {**forged_message(), "permitted": False}, trigger=trigger)

    assert contract.before[0]["legacy_compat"] is True
    adapter.create.assert_not_awaited()
    adapter.runner.assert_not_called()
    adapter.persist.assert_not_awaited()
    assert contract.after[0]["outcome"] == "success"
    assert contract.after[0]["result"]["executed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("top_level_flag", [None, "true", False, 1])
async def test_nested_or_nonboolean_flags_cannot_authorize_unidentified_ingress(top_level_flag):
    contract = HeadlessIdentityContract()
    HookPipeline.register("headless-contract", contract)
    payload = {
        "operation": "workflow.trigger.syslog",
        "transport": "headless",
        "metadata": {"legacy_compat": True},
        "event": {"body": {"legacy_compat": True, "execution": {"stop": False}}},
    }
    if top_level_flag is not None:
        payload["legacy_compat"] = top_level_flag
    effect = AsyncMock()

    with pytest.raises(ExecutionStopped):
        await execute_with_hooks(
            payload,
            effect,
            before=HookPipeline.run_ingress_before,
            after=HookPipeline.run_ingress_after,
        )

    effect.assert_not_awaited()
