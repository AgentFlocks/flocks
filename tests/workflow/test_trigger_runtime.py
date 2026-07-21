from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from flocks.tool import ToolContext
from flocks.workflow.triggers import runtime as runtime_module
from flocks.workflow.triggers.models import TriggerDefinition


@pytest.mark.asyncio
async def test_trigger_execution_builds_tool_context_for_workflow_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_context = ToolContext(
        session_id="trigger-parent",
        message_id="trigger-message",
        agent="rex",
    )
    build_context = AsyncMock(return_value=tool_context)
    cleanup_context = AsyncMock()

    def _fake_run_workflow(**kwargs):  # noqa: ANN003
        missing_context = kwargs.get("tool_context") is None
        return SimpleNamespace(
            status="FAILED" if missing_context else "SUCCEEDED",
            outputs={},
            error="Parent session not found" if missing_context else None,
            history=[],
            last_node_id="notify",
            steps=1,
        )

    monkeypatch.setattr(
        runtime_module,
        "build_workflow_tool_context",
        build_context,
    )
    monkeypatch.setattr(runtime_module, "cleanup_workflow_tool_context", cleanup_context)
    monkeypatch.setattr(runtime_module, "run_workflow", Mock(side_effect=_fake_run_workflow))
    monkeypatch.setattr(
        runtime_module,
        "create_execution_record",
        AsyncMock(return_value={"id": "exec-1"}),
    )
    monkeypatch.setattr(runtime_module, "record_execution_result", AsyncMock())

    trigger = TriggerDefinition.model_validate({"id": "webhook-trigger", "type": "custom_webhook"})
    runtime = runtime_module.TriggerRuntime()

    result = await runtime._execute_workflow(  # noqa: SLF001
        workflow_id="wf-trigger",
        workflow_json={"start": "notify", "nodes": [], "edges": []},
        trigger=trigger,
        mapped_inputs={"message": "hello"},
    )

    assert result["status"] == "success"
    build_context.assert_awaited_once_with(
        workflow_id="wf-trigger",
        action_name="trigger:custom_webhook",
    )
    assert runtime_module.run_workflow.call_args.kwargs["tool_context"] is tool_context
    cleanup_context.assert_awaited_once_with(tool_context)


@pytest.mark.asyncio
async def test_sync_legacy_configs_disables_explicit_empty_trigger_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[str, dict]] = []

    async def _fake_put_config(workflow_id: str, config: dict, *, kind: str) -> None:
        writes.append((f"{kind}/{workflow_id}", config))

    monkeypatch.setattr(runtime_module.WorkflowStore, "put_config", _fake_put_config)

    runtime = runtime_module.TriggerRuntime()
    triggers = await runtime._sync_legacy_configs_from_workflow(  # noqa: SLF001
        "wf-empty",
        {"start": "n1", "nodes": [], "edges": [], "triggers": []},
    )

    assert triggers == []
    assert {key for key, _value in writes} == {
        "workflow_poller_config/wf-empty",
        "workflow_syslog_config/wf-empty",
        "workflow_kafka_config/wf-empty",
    }
    assert all(value["enabled"] is False for _key, value in writes)


@pytest.mark.asyncio
async def test_custom_adapter_restarts_when_definition_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started_modes: list[str] = []
    stopped_modes: list[str] = []

    class _FakeAdapter:
        def __init__(self, definition: dict) -> None:
            self._definition = definition

        def start(self, definition: dict, emit) -> None:  # noqa: ANN001
            del emit
            started_modes.append(str((definition.get("source") or {}).get("mode")))

        def stop(self) -> None:
            stopped_modes.append(str((self._definition.get("source") or {}).get("mode")))

    monkeypatch.setattr(
        runtime_module,
        "list_trigger_plugins",
        lambda: [{"id": "demo-adapter", "handlerPath": "/tmp/demo-handler.py"}],
    )
    monkeypatch.setattr(
        runtime_module,
        "load_trigger_plugin_module",
        lambda _plugin_spec: SimpleNamespace(create_trigger_adapter=lambda definition: _FakeAdapter(definition)),
    )

    runtime = runtime_module.TriggerRuntime()
    initial_workflow = {
        "triggers": [
            {
                "id": "custom-trigger",
                "type": "custom_adapter",
                "enabled": True,
                "source": {"adapterId": "demo-adapter", "mode": "initial"},
            }
        ]
    }
    updated_workflow = {
        "triggers": [
            {
                "id": "custom-trigger",
                "type": "custom_adapter",
                "enabled": True,
                "source": {"adapterId": "demo-adapter", "mode": "updated"},
            }
        ]
    }

    await runtime._start_custom_adapters_for_workflow("wf-custom", initial_workflow)  # noqa: SLF001
    await asyncio.sleep(0)

    await runtime._start_custom_adapters_for_workflow("wf-custom", updated_workflow)  # noqa: SLF001
    await asyncio.sleep(0)

    assert started_modes == ["initial", "updated"]
    assert stopped_modes == ["initial"]

    await runtime.stop_all()
