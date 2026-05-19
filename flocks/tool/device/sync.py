"""Synchronise LLM tool visibility with device enabled/disabled state.

Rule (per storage_key):
  ≥1 enabled device instance sharing a storage_key  →  api_services[storage_key].enabled = True
  0  enabled device instances for a storage_key      →  api_services[storage_key].enabled = False

Using per-storage-key logic means two instances of the same product type
(different storage_keys) are enabled/disabled independently.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from flocks.storage.storage import Storage
from flocks.utils.log import Log

log = Log.create(service="tool.device.sync")


async def sync_service_tool_state(
    service_id: str,
    deleted_storage_keys: Optional[List[str]] = None,
) -> None:
    """Sync tool visibility for every storage_key that belongs to *service_id*.

    For each distinct storage_key under the service, enable it if at least one
    device instance with that storage_key is enabled; disable it otherwise.

    Two correctness traps this function guards against:

    1. **Just-deleted rows** — when a caller has removed device rows in this
       same request, those rows are already gone from the DB by the time we
       query.  Without an explicit ``deleted_storage_keys`` hint we would never
       observe the now-zero count and would leave the api_service entry stuck
       at its previous (typically ``enabled=True``) state.

    2. **Pre-existing dirty config** — on startup (or after a crash mid-delete)
       the config file may already contain stale ``enabled=true`` entries for
       storage_keys whose last device row has long since been removed.  Those
       keys are invisible to the DB query above, so we additionally scan the
       existing api_services config for any entry whose storage_key derives
       back to this service_id and treat unseen ones as "must disable".
    """
    try:
        from flocks.config.config_writer import ConfigWriter
        from flocks.tool.registry import ToolRegistry
        from flocks.tool.device.store import storage_key_to_service_id

        # Aggregate enabled state per storage_key from the live DB rows.
        key_enabled: Dict[str, bool] = defaultdict(bool)
        async with Storage.connect(Storage.get_db_path()) as db:
            async with db.execute(
                "SELECT storage_key, enabled FROM device_integrations WHERE service_id = ?",
                (service_id,),
            ) as cur:
                rows = await cur.fetchall()

        for row in rows:
            sk, enabled = row[0], bool(row[1])
            # OR logic within each storage_key group
            key_enabled[sk] = key_enabled[sk] or enabled

        # Trap #1: storage_keys that were just deleted in this request — the
        # caller knows about them but the DB no longer does.
        for sk in (deleted_storage_keys or []):
            key_enabled.setdefault(sk, False)

        # Trap #2: storage_keys that already exist in the api_services config
        # under this service_id but have no surviving DB rows at all.  Pull
        # them in so we can flip their stale ``enabled=true`` back to false.
        try:
            existing_services = ConfigWriter.list_api_services_raw() or {}
        except Exception:
            existing_services = {}
        for sk in existing_services.keys():
            if not isinstance(sk, str):
                continue
            try:
                if storage_key_to_service_id(sk) != service_id:
                    continue
            except Exception:
                continue
            key_enabled.setdefault(sk, False)

        for sk, should_enable in key_enabled.items():
            existing = ConfigWriter.get_api_service_raw(sk)
            entry = existing if isinstance(existing, dict) else {}
            # Skip the write when the config already matches — avoids
            # rewriting flocks.json on every startup when nothing changed.
            if entry.get("enabled") == should_enable and isinstance(existing, dict):
                continue
            entry["enabled"] = should_enable
            ConfigWriter.set_api_service(sk, entry)

        ToolRegistry._sync_api_service_states()

        log.info("tool.device.sync", {
            "service_id": service_id,
            "storage_keys": {k: v for k, v in key_enabled.items()},
        })
    except Exception as exc:
        log.warn("tool.device.sync.failed", {"service_id": service_id, "error": str(exc)})
