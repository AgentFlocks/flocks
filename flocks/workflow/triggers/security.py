"""Neutral action-gateway adapter for workflow trigger execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from flocks.security.action_gateway import SecurityAction, execute_action


T = TypeVar("T")

_DEFERRED_WEBHOOK_TRIGGER_TYPES = frozenset({"webhook", "custom_webhook"})
_LEGACY_COMPAT_TRIGGER_TYPES = frozenset({"kafka", "syslog"})


def _mapped_input_hash(mapped_inputs: dict[str, Any]) -> str | None:
    try:
        encoded = json.dumps(
            mapped_inputs,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


async def execute_trigger_action(
    *,
    workflow_id: str,
    trigger_id: str | None,
    trigger_type: str,
    mapped_inputs: dict[str, Any],
    effect: Callable[[], Awaitable[T]],
    subject: Any = None,
) -> T:
    """Run a non-webhook trigger effect through the OSS-neutral gateway.

    Webhook triggers remain explicitly deferred for eventual removal.  Kafka
    and Syslog retain their service-account-free legacy execution path, tagged
    for FlocksPro migration/audit without changing its availability.
    """
    if trigger_type in _DEFERRED_WEBHOOK_TRIGGER_TYPES:
        return await effect()

    return await execute_action(
        SecurityAction(
            action="workflow_trigger_execute",
            resource={"type": "workflow", "id": workflow_id},
            canonical_input={
                "trigger_id": trigger_id,
                "trigger_type": trigger_type,
                "mapped_input_hash": _mapped_input_hash(mapped_inputs),
            },
            execution_domain="workflow_trigger",
            metadata={
                "entry": "workflow_trigger",
                "trigger_type": trigger_type,
                "legacy_compat": trigger_type in _LEGACY_COMPAT_TRIGGER_TYPES,
            },
        ),
        effect,
        subject=subject,
    )
