"""Pydantic models, DDL schemas, and feature flags for device integrations."""
from __future__ import annotations

import os
from typing import Dict, Optional

from pydantic import BaseModel, Field

from flocks.storage.storage import Storage

# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

#: Flip to True (or set env FLOCKS_DEVICE_MULTI_GROUP=1) to unlock multi-group
#: routes. The data layer is already multi-group ready; this is UI/API gating only.
MULTI_GROUP_ENABLED: bool = (
    os.environ.get("FLOCKS_DEVICE_MULTI_GROUP", "").lower() in {"1", "true", "yes"}
)

DEFAULT_GROUP_ID = "default-room"
DEFAULT_GROUP_NAME = "默认机房"

# ---------------------------------------------------------------------------
# DDL — registered once; Storage.init() picks them up on startup.
# ---------------------------------------------------------------------------

Storage.register_ddl("""
CREATE TABLE IF NOT EXISTS device_groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
""")

Storage.register_ddl("""
CREATE TABLE IF NOT EXISTS device_integrations (
    id          TEXT PRIMARY KEY,
    group_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    service_id  TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    verify_ssl  INTEGER NOT NULL DEFAULT 0,
    fields      TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'unknown',
    message     TEXT,
    latency_ms  INTEGER,
    checked_at  INTEGER,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_device_storage_key ON device_integrations(storage_key);
CREATE INDEX IF NOT EXISTS idx_device_group       ON device_integrations(group_id);
""")

# Upgrade hook for installations created before group_id was added.
# Storage wraps each DDL in try/except so the duplicate-column error on fresh
# installs is silently ignored.
Storage.register_ddl(
    "ALTER TABLE device_integrations ADD COLUMN group_id TEXT NOT NULL DEFAULT '';"
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class DeviceGroup(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    sort_order: int = 0
    created_at: int
    updated_at: int


class DeviceGroupCreate(BaseModel):
    name: str
    description: Optional[str] = None
    sort_order: int = 0


class DeviceGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None


class DeviceIntegration(BaseModel):
    id: str
    group_id: str
    name: str
    storage_key: str
    service_id: str
    enabled: bool = True
    verify_ssl: bool = False
    #: Non-sensitive fields returned as-is; sensitive fields returned as masked
    #: previews (e.g. ``sk-***abc``). Empty string means "not yet configured".
    fields: Dict[str, str] = Field(default_factory=dict)
    #: True for each key where a value is currently stored.
    fields_set: Dict[str, bool] = Field(default_factory=dict)
    status: str = "unknown"
    message: Optional[str] = None
    latency_ms: Optional[int] = None
    checked_at: Optional[int] = None
    created_at: int
    updated_at: int


class DeviceIntegrationCreate(BaseModel):
    name: str
    storage_key: str
    group_id: Optional[str] = None    # defaults to the single default group
    service_id: Optional[str] = None  # derived from storage_key if omitted
    enabled: bool = True
    verify_ssl: bool = False
    fields: Dict[str, str] = Field(default_factory=dict)


class DeviceIntegrationUpdate(BaseModel):
    name: Optional[str] = None
    group_id: Optional[str] = None
    enabled: Optional[bool] = None
    verify_ssl: Optional[bool] = None
    #: Partial update: absent keys keep existing value; empty-string secret
    #: fields keep the existing secret ("leave blank = keep current" UX).
    fields: Optional[Dict[str, str]] = None


class DeviceCredentialResponse(BaseModel):
    fields: Dict[str, str] = Field(default_factory=dict)


class DeviceTestResult(BaseModel):
    success: bool
    message: str
    latency_ms: Optional[int] = None
