"""Server startup hook for the device subsystem.

Call order:
  1. ensure_default_group — the FK target must exist before any device rows are written.
  2. migrate_from_config  — idempotent import from legacy flocks.json.
  3. _sync_all            — re-apply each service's enabled state to the ToolRegistry.
"""
from __future__ import annotations

from flocks.storage.storage import Storage
from flocks.utils.log import Log

from .migration import migrate_from_config
from .store import ensure_default_group
from .sync import sync_service_tool_state

log = Log.create(service="tool.device.startup")


async def device_startup() -> None:
    await ensure_default_group()
    await migrate_from_config()
    await _sync_all()


async def _sync_all() -> None:
    """Re-sync tool visibility for every registered service_id in the DB."""
    try:
        async with Storage.connect(Storage.get_db_path()) as db:
            cur = await db.execute("SELECT DISTINCT service_id FROM device_integrations")
            service_ids = [r[0] for r in await cur.fetchall()]
        for sid in service_ids:
            await sync_service_tool_state(sid)
        if service_ids:
            log.info("tool.device.startup.sync", {"service_ids": service_ids})
    except Exception as exc:
        log.warn("tool.device.startup.sync.failed", {"error": str(exc)})
