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
    await _heal_stale_service_ids()
    await _sync_all()


async def _heal_stale_service_ids() -> None:
    """Rewrite ``device_integrations.service_id`` rows that disagree with
    the descriptor-aware ``storage_key_to_service_id``.

    Rows written by older builds (or before the plugin's
    ``_provider.yaml`` was installed) may carry an over-stripped value —
    e.g. ``onesig`` instead of ``onesig_v2_5_3_D20250710_api`` for plugins
    whose ``service_id`` already contains its own ``_v…`` token.

    Self-healing once at startup keeps the live route handlers and the
    sync loop honest without forcing the user to re-create each device.
    """
    try:
        from flocks.tool.device.store import storage_key_to_service_id

        async with Storage.connect(Storage.get_db_path()) as db:
            cur = await db.execute("SELECT id, storage_key, service_id FROM device_integrations")
            rows = await cur.fetchall()
            updates: list[tuple[str, str]] = []
            for row in rows:
                derived = storage_key_to_service_id(row["storage_key"] or "")
                if derived and derived != (row["service_id"] or ""):
                    updates.append((derived, row["id"]))
            for new_sid, dev_id in updates:
                await db.execute(
                    "UPDATE device_integrations SET service_id = ? WHERE id = ?",
                    (new_sid, dev_id),
                )
            if updates:
                await db.commit()
                log.info("tool.device.startup.service_id_healed", {"count": len(updates)})
    except Exception as exc:
        log.warn("tool.device.startup.heal_failed", {"error": str(exc)})


def _device_type_storage_keys() -> set[str]:
    """Return the set of ``storage_key`` values whose ``_provider.yaml``
    declares ``integration_type: device``.

    Pure-API plugins (``integration_type: api``) must be excluded from the
    device sync loop: they have no rows in ``device_integrations``, so
    ``sync_service_tool_state`` would always find 0 enabled devices and
    incorrectly set ``api_services[sk].enabled = false``, silently disabling
    those tools on every restart.

    Scans every descriptor's YAML once per call (cheap at startup; we
    intentionally don't memoize across calls so test fixtures that swap
    plugin directories still see fresh data).
    """
    keys: set[str] = set()
    try:
        import yaml as _yaml
        from flocks.config.api_versioning import discover_api_service_descriptors

        for desc in discover_api_service_descriptors():
            try:
                data = _yaml.safe_load(desc.provider_yaml.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and data.get("integration_type") == "device":
                keys.add(desc.storage_key)
    except Exception:
        pass
    return keys


async def _sync_all() -> None:
    """Re-sync tool visibility for every *device-type* service_id we know about.

    "Know about" includes both:
      * service_ids that still have rows in ``device_integrations``, and
      * service_ids that have entries in ``api_services`` config but no
        surviving DB rows (e.g. the user just deleted the last device of a
        service before restart).  Without sweeping the config we'd leave
        stale ``enabled=true`` flags on tools whose owning devices no
        longer exist.

    Pure API integrations (``integration_type: api``) are intentionally
    excluded: they never have ``device_integrations`` rows, so the sync
    would always judge them as "0 enabled devices" and disable their tools
    on every startup.
    """
    try:
        async with Storage.connect(Storage.get_db_path()) as db:
            cur = await db.execute("SELECT DISTINCT service_id FROM device_integrations")
            db_service_ids = [r[0] for r in await cur.fetchall()]

        # Also discover service_ids from existing api_services entries so we
        # can clear out config rows whose backing devices have been removed.
        # IMPORTANT: only include storage_keys whose integration_type is
        # "device"; pure-API services are managed independently and must
        # not be touched here.
        config_service_ids: list[str] = []
        try:
            from flocks.config.config_writer import ConfigWriter
            from flocks.tool.device.store import storage_key_to_service_id

            device_keys = _device_type_storage_keys()
            existing = ConfigWriter.list_api_services_raw() or {}
            for sk in existing.keys():
                if not isinstance(sk, str):
                    continue
                if sk not in device_keys:
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
