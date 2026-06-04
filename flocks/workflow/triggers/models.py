"""Workflow trigger schema models and compatibility helpers."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

TriggerType = Literal[
    "manual",
    "schedule",
    "webhook",
    "syslog",
    "kafka",
    "internal_event",
    "custom_webhook",
    "custom_adapter",
    "plugin",
]

_TRIGGER_ID_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _sanitize_trigger_id(value: str) -> str:
    cleaned = _TRIGGER_ID_SANITIZE_RE.sub("-", (value or "").strip()).strip("-")
    return cleaned or "trigger"


def default_trigger_id(trigger_type: str, *, source: Optional[Dict[str, Any]] = None) -> str:
    base = (trigger_type or "trigger").strip().lower() or "trigger"
    src = source or {}
    for candidate_key in ("path", "topic", "event", "name", "adapterId", "pluginId"):
        candidate = src.get(candidate_key)
        if isinstance(candidate, str) and candidate.strip():
            return f"{base}-{_sanitize_trigger_id(candidate)}"
    return f"{base}-default"


class TriggerAuth(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = "none"
    secretRef: Optional[str] = None
    headerName: Optional[str] = None
    queryParam: Optional[str] = None
    apiKey: Optional[str] = None


class TriggerFilter(BaseModel):
    model_config = ConfigDict(extra="allow")

    expr: Optional[str] = None
    mode: Optional[str] = None
    path: Optional[str] = None
    equals: Optional[Any] = None


class TriggerConcurrency(BaseModel):
    model_config = ConfigDict(extra="allow")

    policy: Literal["allow", "no_overlap", "queue", "drop_oldest", "drop_newest"] = "allow"
    maxParallel: int = Field(1, ge=1)
    queueSize: int = Field(100, ge=1)


class TriggerTestSample(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    payload: Any = None
    headers: Dict[str, Any] = Field(default_factory=dict)
    query: Dict[str, Any] = Field(default_factory=dict)


class TriggerDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[str] = None
    name: Optional[str] = None
    type: TriggerType
    enabled: bool = True
    description: Optional[str] = None
    source: Dict[str, Any] = Field(default_factory=dict)
    auth: Optional[TriggerAuth] = None
    filter: Optional[TriggerFilter] = None
    mapping: Dict[str, str] = Field(default_factory=dict)
    inputs: Dict[str, Any] = Field(default_factory=dict)
    concurrency: TriggerConcurrency = Field(default_factory=TriggerConcurrency)
    runtime: Dict[str, Any] = Field(default_factory=dict)
    testSamples: List[TriggerTestSample] = Field(default_factory=list)
    updatedAt: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_nested_values(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        auth = normalized.get("auth")
        if isinstance(auth, dict):
            normalized["auth"] = TriggerAuth.model_validate(auth)
        filter_value = normalized.get("filter")
        if isinstance(filter_value, dict):
            normalized["filter"] = TriggerFilter.model_validate(filter_value)
        concurrency = normalized.get("concurrency")
        if isinstance(concurrency, dict):
            normalized["concurrency"] = TriggerConcurrency.model_validate(concurrency)
        samples = normalized.get("testSamples")
        if isinstance(samples, list):
            normalized["testSamples"] = [
                TriggerTestSample.model_validate(item) if not isinstance(item, TriggerTestSample) else item
                for item in samples
                if isinstance(item, (dict, TriggerTestSample))
            ]
        return normalized

    @model_validator(mode="after")
    def _ensure_id(self) -> "TriggerDefinition":
        source = self.source if isinstance(self.source, dict) else {}
        self.id = _sanitize_trigger_id(self.id or default_trigger_id(self.type, source=source))
        return self


class TriggerEventSource(BaseModel):
    model_config = ConfigDict(extra="allow")

    workflowId: str
    triggerId: str
    triggerType: str
    source: Optional[str] = None
    deliveryId: Optional[str] = None
    receivedAt: Optional[int] = None
    attempt: int = 1


class TriggerEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: TriggerEventSource
    body: Any = None
    headers: Dict[str, Any] = Field(default_factory=dict)
    query: Dict[str, Any] = Field(default_factory=dict)
    pathParams: Dict[str, Any] = Field(default_factory=dict)
    payload: Any = None
    raw: Any = None


class TriggerRuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    workflowId: str
    triggerId: str
    triggerType: str
    state: str
    error: Optional[str] = None


def normalize_trigger_definitions(raw_triggers: Optional[Iterable[Any]]) -> List[TriggerDefinition]:
    if not raw_triggers:
        return []
    deduped: Dict[str, TriggerDefinition] = {}
    for raw in raw_triggers:
        if raw is None:
            continue
        trigger = raw if isinstance(raw, TriggerDefinition) else TriggerDefinition.model_validate(raw)
        deduped[trigger.id or default_trigger_id(trigger.type)] = trigger
    return list(deduped.values())


def workflow_trigger_definitions_from_json(workflow_json: Dict[str, Any]) -> List[TriggerDefinition]:
    raw = workflow_json.get("triggers")
    if raw is None:
        metadata = workflow_json.get("metadata")
        if isinstance(metadata, dict):
            raw = metadata.get("triggers")
    if not isinstance(raw, list):
        return []
    return normalize_trigger_definitions(raw)


def workflow_json_declares_triggers(workflow_json: Dict[str, Any]) -> bool:
    if not isinstance(workflow_json, dict):
        return False
    if "triggers" in workflow_json:
        return isinstance(workflow_json.get("triggers"), list)
    metadata = workflow_json.get("metadata")
    return isinstance(metadata, dict) and isinstance(metadata.get("triggers"), list)


def trigger_definitions_to_json(triggers: Iterable[TriggerDefinition]) -> List[Dict[str, Any]]:
    return [
        trigger.model_dump(mode="json", by_alias=True, exclude_none=True)
        for trigger in normalize_trigger_definitions(triggers)
    ]


def set_workflow_json_triggers(
    workflow_json: Dict[str, Any],
    triggers: Iterable[TriggerDefinition],
) -> Dict[str, Any]:
    updated = dict(workflow_json)
    updated["triggers"] = trigger_definitions_to_json(triggers)
    metadata = updated.get("metadata")
    if isinstance(metadata, dict) and "triggers" in metadata:
        metadata = dict(metadata)
        metadata.pop("triggers", None)
        updated["metadata"] = metadata
    return updated

