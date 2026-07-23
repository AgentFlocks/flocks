"""Incremental JSON projection for mutable Strix TUI events."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EventProjector:
    """Turn mutable native events into a monotonic update stream."""

    cursor: int = 0
    versions: dict[str, int] = field(default_factory=dict)
    updates: list[dict[str, Any]] = field(default_factory=list)

    def project(
        self,
        native_events: list[dict[str, Any]],
        *,
        after: int = 0,
    ) -> list[dict[str, Any]]:
        """Return event revisions created after the requested cursor."""
        for native_event in native_events:
            event_key = str(native_event.get("id", ""))
            version = int(native_event.get("version", 0))
            if self.versions.get(event_key) == version:
                continue
            self.cursor += 1
            update = copy.deepcopy(native_event)
            update["event_key"] = event_key
            update["id"] = self.cursor
            self.updates.append(update)
            self.versions[event_key] = version
        return [copy.deepcopy(event) for event in self.updates if event["id"] > after]
