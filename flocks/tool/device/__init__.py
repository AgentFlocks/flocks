"""Device integration domain — public API.

Place in the tool hierarchy because device integrations are fundamentally
tool-enablement infrastructure: they configure which external security
devices the agent can call.
"""
from .models import (
    DEFAULT_GROUP_ID,
    DEFAULT_GROUP_NAME,
    MULTI_GROUP_ENABLED,
    DeviceGroup,
    DeviceGroupCreate,
    DeviceGroupUpdate,
    DeviceCredentialResponse,
    DeviceIntegration,
    DeviceIntegrationCreate,
    DeviceIntegrationUpdate,
    DeviceTestResult,
)
from .startup import device_startup
from .store import get_device_credentials

__all__ = [
    # Constants / feature flags
    "MULTI_GROUP_ENABLED",
    "DEFAULT_GROUP_ID",
    "DEFAULT_GROUP_NAME",
    # Models
    "DeviceGroup",
    "DeviceGroupCreate",
    "DeviceGroupUpdate",
    "DeviceCredentialResponse",
    "DeviceIntegration",
    "DeviceIntegrationCreate",
    "DeviceIntegrationUpdate",
    "DeviceTestResult",
    # Entry points
    "device_startup",
    "get_device_credentials",
]
