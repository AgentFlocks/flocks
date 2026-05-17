"""Synchronise LLM tool visibility with device enabled/disabled state.

Rule:
  ≥1 enabled device instance for service_id  →  api_services[storage_key].enabled = True
  0  enabled device instances                 →  api_services[storage_key].enabled = False

After writing ``flocks.json``, triggers ``ToolRegistry._sync_api_service_states()``
so the change is visible to the LLM immediately, without a server restart.
"""
from __future__ import annotations

from flocks.storage.storage import Storage
from flocks.utils.log import Log

log = Log.create(service="tool.device.sync")


async def sync_service_tool_state(service_id: str) -> None:
    """Sync tool visibility for every storage_key that belongs to *service_id*."""
    try:
        from flocks.config.config_writer import ConfigWriter
        from flocks.tool.registry import ToolRegistry

        async with Storage.connect(Storage.get_db_path()) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM device_integrations WHERE service_id = ? AND enabled = 1",
                (service_id,),
            )
            row = await cur.fetchone()
            enabled_count = int(row[0]) if row else 0

            cur2 = await db.execute(
                "SELECT DISTINCT storage_key FROM device_integrations WHERE service_id = ?",
                (service_id,),
            )
            storage_keys = [r[0] for r in await cur2.fetchall()]

        should_enable = enabled_count > 0
        for sk in storage_keys:
            existing = ConfigWriter.get_api_service_raw(sk)
            entry = existing if isinstance(existing, dict) else {}
            entry["enabled"] = should_enable
            ConfigWriter.set_api_service(sk, entry)

        ToolRegistry._sync_api_service_states()

        log.info("tool.device.sync", {
            "service_id": service_id,
            "enabled_devices": enabled_count,
            "tools_enabled": should_enable,
            "storage_keys": storage_keys,
        })
    except Exception as exc:
        log.warn("tool.device.sync.failed", {"service_id": service_id, "error": str(exc)})
