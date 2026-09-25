from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, field_validator, model_validator

COMPONENT_ID = 'host-security-monitor'


class MonitoringPolicy(BaseModel):
    @model_validator(mode='before')
    @classmethod
    def migrate_legacy_executor(cls, value):
        if isinstance(value, dict) and value.get('investigation_engine', 'rules') == 'rules':
            value = dict(value)
            if value.get('timeout_seconds') == 480:
                value['timeout_seconds'] = 1200
        return value

    version: Literal[1] = 1
    scope: Literal['host-security-monitor'] = COMPONENT_ID
    session_policy: Literal['daily'] = 'daily'
    interaction_mode: Literal['unattended'] = 'unattended'
    owner: str = Field(min_length=1)
    project: str = Field(min_length=1)
    directory: str = Field(min_length=1)
    timezone: str = 'Asia/Shanghai'
    devices: list[str] = Field(default_factory=list)
    tool: str = 'sangfor_xdr_incidents'
    timeout_seconds: int = Field(default=1200, ge=1, le=1800)
    investigation_engine: Literal['agent-v1'] = 'agent-v1'

    @field_validator('investigation_engine', mode='before')
    @classmethod
    def retire_rules_engine(cls, value):
        return 'agent-v1' if value == 'rules' else value
    investigation_calls: int = Field(default=12, ge=1, le=24)
    correlation_devices: list[str] = Field(default_factory=list)
    correlation_notes: list[str] = Field(default_factory=list)
    max_pages: int = Field(default=1000, ge=1, le=10000)
    # Accept older saved policies but retire the development execution mode.
    development_sample: Literal[False] = False

    @field_validator('development_sample', mode='before')
    @classmethod
    def retire_development_sample(cls, value):
        return False

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        ZoneInfo(value)
        return value


class MonitoringDeclaration(BaseModel):
    """Base reads plus an explicitly enabled, versioned mail feedback policy."""
    schemaVersion: Literal[1]
    scope: Literal['host-security-monitor']
    cron: Literal['*/10 * * * *']
    sessionPolicy: Literal['daily']
    interactionMode: Literal['unattended']
    actions: list[Literal['list', 'get_entities', 'get_proof']]
    # This declaration governs unattended scheduling, never the confirmed UI path.
    dispositionEnabled: Literal[False]
    automaticStatusPolicy: Literal['xdr-evidence-v1'] | None = None
    mailFollowupPolicy: Literal['mail-feedback-v1'] | None = None
