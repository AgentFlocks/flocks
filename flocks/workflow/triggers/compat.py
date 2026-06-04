"""Compatibility helpers between unified triggers and legacy config storage."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .models import TriggerDefinition

LEGACY_POLLER_CONFIG_PREFIX = "workflow_poller_config/"
LEGACY_SYSLOG_CONFIG_PREFIX = "workflow_syslog_config/"
LEGACY_KAFKA_CONFIG_PREFIX = "workflow_kafka_config/"


def legacy_schedule_trigger_from_config(config: Optional[Dict[str, Any]]) -> Optional[TriggerDefinition]:
    if not isinstance(config, dict):
        return None
    cron_expression = str(config.get("cronExpression") or "").strip()
    return TriggerDefinition.model_validate(
        {
            "id": "schedule-default",
            "type": "schedule",
            "enabled": bool(config.get("enabled")),
            "source": {
                "mode": "cron" if cron_expression else "interval",
                "intervalSeconds": int(config.get("intervalSeconds") or 30),
                "cron": cron_expression or None,
            },
            "runtime": {
                "timeoutSeconds": int(config.get("timeoutSeconds") or 7200),
                "noOverlap": bool(config.get("noOverlap", True)),
            },
            "inputs": dict(config.get("inputs") or {}),
            "updatedAt": config.get("updatedAt"),
        }
    )


def legacy_syslog_trigger_from_config(config: Optional[Dict[str, Any]]) -> Optional[TriggerDefinition]:
    if not isinstance(config, dict):
        return None
    return TriggerDefinition.model_validate(
        {
            "id": "syslog-default",
            "type": "syslog",
            "enabled": bool(config.get("enabled")),
            "source": {
                "protocol": config.get("protocol") or "udp",
                "host": config.get("host") or "0.0.0.0",
                "port": int(config.get("port") or 5140),
                "format": config.get("format") or "auto",
            },
            "mapping": {
                str(config.get("inputKey") or "syslog_message"): "$.body",
            },
            "updatedAt": config.get("updatedAt"),
        }
    )


def legacy_kafka_trigger_from_config(config: Optional[Dict[str, Any]]) -> Optional[TriggerDefinition]:
    if not isinstance(config, dict):
        return None
    return TriggerDefinition.model_validate(
        {
            "id": "kafka-default",
            "type": "kafka",
            "enabled": bool(config.get("enabled")),
            "source": {
                "inputBroker": config.get("inputBroker") or "",
                "inputTopic": config.get("inputTopic") or "",
                "inputGroupId": config.get("inputGroupId") or "",
                "autoOffsetReset": config.get("autoOffsetReset") or "latest",
            },
            "mapping": {
                str(config.get("inputKey") or "kafka_message"): "$.body",
            },
            "inputs": dict(config.get("inputs") or {}),
            "updatedAt": config.get("updatedAt"),
        }
    )


def schedule_trigger_to_legacy_config(workflow_id: str, trigger: TriggerDefinition) -> Dict[str, Any]:
    source = dict(trigger.source or {})
    runtime = dict(trigger.runtime or {})
    cron_expression = str(source.get("cron") or source.get("cronExpression") or "").strip()
    return {
        "workflowId": workflow_id,
        "enabled": trigger.enabled,
        "intervalSeconds": int(source.get("intervalSeconds") or 30),
        "cronExpression": cron_expression or None,
        "timeoutSeconds": int(runtime.get("timeoutSeconds") or 7200),
        "noOverlap": bool(runtime.get("noOverlap", True)),
        "inputs": dict(trigger.inputs or {}),
        "updatedAt": trigger.updatedAt,
    }


def syslog_trigger_to_legacy_config(workflow_id: str, trigger: TriggerDefinition) -> Dict[str, Any]:
    source = dict(trigger.source or {})
    mapping = dict(trigger.mapping or {})
    input_key = next(iter(mapping.keys()), "syslog_message")
    return {
        "workflowId": workflow_id,
        "enabled": trigger.enabled,
        "protocol": source.get("protocol") or "udp",
        "host": source.get("host") or "0.0.0.0",
        "port": int(source.get("port") or 5140),
        "format": source.get("format") or "auto",
        "inputKey": input_key,
        "updatedAt": trigger.updatedAt,
    }


def kafka_trigger_to_legacy_config(workflow_id: str, trigger: TriggerDefinition) -> Dict[str, Any]:
    source = dict(trigger.source or {})
    mapping = dict(trigger.mapping or {})
    input_key = next(iter(mapping.keys()), "kafka_message")
    return {
        "workflowId": workflow_id,
        "enabled": trigger.enabled,
        "inputBroker": source.get("inputBroker") or "",
        "inputTopic": source.get("inputTopic") or "",
        "inputGroupId": source.get("inputGroupId") or "",
        "inputKey": input_key,
        "autoOffsetReset": source.get("autoOffsetReset") or "latest",
        "inputs": dict(trigger.inputs or {}),
        "updatedAt": trigger.updatedAt,
    }


def trigger_to_legacy_config(workflow_id: str, trigger: TriggerDefinition) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    if trigger.type == "schedule":
        return f"{LEGACY_POLLER_CONFIG_PREFIX}{workflow_id}", schedule_trigger_to_legacy_config(workflow_id, trigger)
    if trigger.type == "syslog":
        return f"{LEGACY_SYSLOG_CONFIG_PREFIX}{workflow_id}", syslog_trigger_to_legacy_config(workflow_id, trigger)
    if trigger.type == "kafka":
        return f"{LEGACY_KAFKA_CONFIG_PREFIX}{workflow_id}", kafka_trigger_to_legacy_config(workflow_id, trigger)
    return None, None
