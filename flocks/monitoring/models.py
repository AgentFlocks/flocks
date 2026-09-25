from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, field_validator

COMPONENT_ID = 'host-security-monitor'


class MonitoringPolicy(BaseModel):
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
    timeout_seconds: int = Field(default=480, ge=1, le=540)
    max_pages: int = Field(default=1000, ge=1, le=10000)

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
