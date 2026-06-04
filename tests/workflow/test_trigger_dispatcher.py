from __future__ import annotations

import pytest

from flocks.workflow.triggers.dispatcher import (
    EventDispatcher,
    build_trigger_event,
    evaluate_trigger_filter,
    lookup_mapping_path,
    preview_trigger_mapping,
)
from flocks.workflow.triggers.models import TriggerDefinition


def test_lookup_mapping_path_supports_nested_access() -> None:
    payload = {
        "body": {
            "data": [
                {"severity": "high", "source": {"ip": "1.1.1.1"}},
            ]
        }
    }

    assert lookup_mapping_path(payload, "$.body.data[0].severity") == "high"
    assert lookup_mapping_path(payload, "$.body.data[0].source.ip") == "1.1.1.1"
    assert lookup_mapping_path(payload, "$.body.data[1]") is None


def test_preview_trigger_mapping_builds_flocks_envelope() -> None:
    trigger = TriggerDefinition.model_validate(
        {
            "id": "custom-webhook",
            "type": "custom_webhook",
            "mapping": {
                "alert_data": "$.body.data[0]",
            },
            "inputs": {"static_value": 7},
        }
    )
    event = build_trigger_event(
        workflow_id="wf-1",
        trigger=trigger,
        body={"data": [{"severity": "high"}]},
    )

    mapped = preview_trigger_mapping(trigger, event)

    assert mapped["static_value"] == 7
    assert mapped["alert_data"] == {"severity": "high"}
    assert mapped["_flocks"]["trigger"]["id"] == "custom-webhook"
    assert mapped["_flocks"]["trigger"]["type"] == "custom_webhook"


def test_trigger_filter_expression_matches_expected_payload() -> None:
    trigger = TriggerDefinition.model_validate(
        {
            "id": "high-only",
            "type": "custom_webhook",
            "filter": {"expr": "body.data[0].severity in ['high', 'critical']"},
        }
    )
    event = build_trigger_event(
        workflow_id="wf-1",
        trigger=trigger,
        body={"data": [{"severity": "high"}]},
    )

    matched, error = evaluate_trigger_filter(trigger, event)

    assert matched is True
    assert error is None


@pytest.mark.asyncio
async def test_event_dispatcher_skips_execution_when_filter_does_not_match() -> None:
    dispatcher = EventDispatcher()
    trigger = TriggerDefinition.model_validate(
        {
            "id": "critical-only",
            "type": "custom_webhook",
            "filter": {"expr": "body.severity == 'critical'"},
            "mapping": {"severity": "$.body.severity"},
        }
    )
    event = build_trigger_event(
        workflow_id="wf-1",
        trigger=trigger,
        body={"severity": "low"},
    )

    async def _executor(_inputs: dict[str, object]) -> dict[str, bool]:
        raise AssertionError("executor must not run when the filter misses")

    result = await dispatcher.dispatch(trigger=trigger, event=event, executor=_executor)

    assert result["matched"] is False
    assert result["executed"] is False
    assert result["inputs"]["severity"] == "low"

