"""Ingress payload contract for listener-driven workflow dispatch.

Syslog/Kafka listeners authenticate their own inbound traffic, so the dispatch
they hand to the ingress hooks must present the legacy-compat carrier at the
top level (where ingress policies read it) as well as inside ``metadata`` (the
action recheck copy). With only the metadata copy, an enforcing policy engine
demanded a managed identity the listener cannot present and stopped every
syslog/kafka triggered execution.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from flocks.ingest.kafka import manager as kafka_manager
from flocks.ingest.syslog import manager as syslog_manager
from flocks.workflow.triggers.models import TriggerDefinition


def _capture(monkeypatch: pytest.MonkeyPatch, module: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def _fake_execute_with_hooks(payload, effect, **_kwargs):
        captured.update(payload)
        return {}

    monkeypatch.setattr(module, "execute_with_hooks", _fake_execute_with_hooks)
    monkeypatch.setattr(module, "build_workflow_tool_context", AsyncMock(return_value=object()))
    monkeypatch.setattr(module, "cleanup_workflow_tool_context", AsyncMock())
    return captured


@pytest.mark.asyncio
async def test_syslog_dispatch_presents_top_level_legacy_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch, syslog_manager)
    manager = syslog_manager.SyslogManager()
    trigger = TriggerDefinition.model_validate(
        {"id": "syslog-1", "type": "syslog", "mapping": {"syslog_message": "$.body"}}
    )

    await manager._trigger_workflow(
        "wf-syslog", None, {"body": "<13>hello"}, "syslog_message", trigger=trigger, source="syslog"
    )

    assert captured["operation"] == "workflow.trigger.syslog"
    assert captured["transport"] == "headless"
    assert captured["legacy_compat"] is True  # top-level: where ingress policies read it
    assert captured["evidence"] == {"auth_scheme": "syslog", "trigger_id": "syslog-1", "trigger_type": "syslog"}
    assert captured["metadata"] == {"legacy_compat": True, "trigger_type": "syslog"}


@pytest.mark.asyncio
async def test_kafka_dispatch_presents_top_level_legacy_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch, kafka_manager)
    manager = kafka_manager.KafkaManager()
    trigger = TriggerDefinition.model_validate(
        {"id": "kafka-1", "type": "kafka", "mapping": {"kafka_message": "$.body"}}
    )

    await manager._trigger_workflow(
        "wf-kafka", None, {"body": "{\"a\":1}"}, "kafka_message", trigger=trigger, source="kafka"
    )

    assert captured["operation"] == "workflow.trigger.kafka"
    assert captured["transport"] == "headless"
    assert captured["legacy_compat"] is True
    assert captured["evidence"] == {"auth_scheme": "kafka", "trigger_id": "kafka-1", "trigger_type": "kafka"}
    assert captured["metadata"] == {"legacy_compat": True, "trigger_type": "kafka"}
