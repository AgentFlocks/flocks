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
    """Re-sync tool visibility for every service_id we know about.

    "Know about" includes both:
      * service_ids that still have rows in ``device_integrations``, and
      * service_ids that have entries in ``api_services`` config but no
        surviving DB rows (e.g. the user just deleted the last device of a
        service before restart).  Without sweeping the config we'd leave
        stale ``enabled=true`` flags on tools whose owning devices no
        longer exist.
    """
    try:
        async with Storage.connect(Storage.get_db_path()) as db:
            cur = await db.execute("SELECT DISTINCT service_id FROM device_integrations")
            db_service_ids = [r[0] for r in await cur.fetchall()]

        # Also discover service_ids from existing api_services entries so we
        # can clear out config rows whose backing devices have been removed.
        config_service_ids: list[str] = []
        try:
            from flocks.config.config_writer import ConfigWriter
            from flocks.tool.device.store import storage_key_to_service_id

            existing = ConfigWriter.list_api_services_raw() or {}
            for sk in existing.keys():
                if not isinstance(sk, str):
                    continue
                try:
                    config_service_ids.append(storage_key_to_service_id(sk))
                except Exception:
                    continue
        except Exception as cfg_exc:
            log.warn("tool.device.startup.sync.config_scan_failed", {"error": str(cfg_exc)})

        # Deduplicate while preserving order (DB first, then config-only).
        seen: set[str] = set()
        service_ids: list[str] = []
        for sid in [*db_service_ids, *config_service_ids]:
            if sid and sid not in seen:
                seen.add(sid)
                service_ids.append(sid)

        for sid in service_ids:
            await sync_service_tool_state(sid)
        if service_ids:
            log.info("tool.device.startup.sync", {"service_ids": service_ids})
    except Exception as exc:
        log.warn("tool.device.startup.sync.failed", {"error": str(exc)})
