"""Storage-key versioning for API service credentials.

Allows multiple versions of the same API product (same ``service_id``) to
coexist in ``flocks.json`` under distinct ``<service_id>_v<version>`` keys.

Glossary
--------
service_id    Stable product identifier declared in ``_provider.yaml``
              (e.g. ``tdp_api``). Independent of the product version.
              Tool YAMLs reference this via their ``provider:`` field.

version       Product version declared in ``_provider.yaml`` (top-level
              ``version`` or ``defaults.product_version``).

storage_key   Composite key used in ``flocks.json``
              (``api_services[storage_key]``). Equal to ``service_id``
              when no version is declared, preserving back-compat.

Migration model — copy-only, idempotent:
    The legacy block ``api_services[service_id]`` is **copied** (not
    moved) to ``api_services[storage_key]`` if the latter is absent.
    The legacy block is preserved so older code paths that still query
    by bare ``service_id`` keep working until they are retired.

Startup ordering invariant:
    :func:`migrate_api_services` MUST run before :class:`ToolRegistry`
    initialises so that tools loaded from YAML observe the post-migration
    ``api_services`` layout. The lifespan in ``flocks/server/app.py``
    enforces this by calling migration right after ``ensure_config_files``
    and before any path that triggers ``ToolRegistry.init()``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import yaml

from flocks.config.config import Config
from flocks.utils.log import Log

log = Log.create(service="config.versioning")

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

_VERSION_SANITIZER = re.compile(r"[^A-Za-z0-9]+")
_VERSION_SUFFIX_PATTERN = re.compile(r"_v[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class ApiServiceDescriptor:
    """Versioning metadata for one plugin-defined API service."""

    service_id: str
    version: Optional[str]
    storage_key: str
    provider_yaml: Path


def derive_storage_key(service_id: str, version: Optional[str]) -> str:
    """Compose the ``flocks.json`` key from ``service_id`` + optional ``version``.

    Examples::

        derive_storage_key("tdp_api", "3.3.10") -> "tdp_api_v3_3_10"
        derive_storage_key("foo",     None)     -> "foo"
        derive_storage_key("onesig_api", "2.5.3 D20260321")
            -> "onesig_api_v2_5_3_D20260321"
    """
    if not service_id:
        raise ValueError("service_id must be a non-empty string")
    if not version:
        return service_id
    sanitized = _VERSION_SANITIZER.sub("_", str(version)).strip("_")
    return f"{service_id}_v{sanitized}" if sanitized else service_id


def discover_api_service_descriptors(*, refresh: bool = False) -> List[ApiServiceDescriptor]:
    """Return one descriptor per plugin ``_provider.yaml`` found on disk.

    Project-level discoveries (``<cwd>/.flocks/plugins/tools/api/``) win
    over user-level entries that share the same ``storage_key``.

    Results are cached in-process. Pass ``refresh=True`` to re-scan
    (e.g. after a plugin install or test fixture change).
    """
    global _descriptor_cache
    if _descriptor_cache is not None and not refresh:
        return _descriptor_cache

    seen: Dict[str, ApiServiceDescriptor] = {}
    for root in _api_plugin_roots():
        if not root.is_dir():
            continue
        for plugin_dir in sorted(root.iterdir()):
            descriptor = _descriptor_for_plugin_dir(plugin_dir)
            if descriptor is not None:
                seen.setdefault(descriptor.storage_key, descriptor)

    _descriptor_cache = list(seen.values())
    return _descriptor_cache


def legacy_service_id_for(storage_key: str) -> Optional[str]:
    """Return the unversioned ``service_id`` that ``storage_key`` was derived from.

    Resolution order:

    1. Exact match against the discovered descriptor registry — preferred
       because it is unambiguous even when ``service_id`` itself ends in
       a ``_v...`` token.
    2. Heuristic strip of the trailing ``_v<token>`` suffix — falls back
       gracefully when the registry has not been populated (e.g. tests).

    Returns ``None`` when ``storage_key`` does not look versioned.
    """
    for descriptor in discover_api_service_descriptors():
        if descriptor.storage_key == storage_key:
            return None if descriptor.service_id == storage_key else descriptor.service_id

    match = _VERSION_SUFFIX_PATTERN.search(storage_key)
    if not match:
        return None
    legacy = storage_key[: match.start()]
    return legacy or None


def versioned_storage_key_for(service_id: str) -> Optional[str]:
    """Return the unique versioned storage key for ``service_id``, if any.

    Used by legacy callers (e.g. tool handlers that hard-code an
    unversioned ``SERVICE_ID``) to transparently reach the new versioned
    credentials after migration.

    Returns ``None`` when:

    * no descriptor matches ``service_id``,
    * only an unversioned descriptor exists (storage_key == service_id),
    * multiple versioned descriptors coexist (ambiguous; explicit choice
      required so we refuse to guess).
    """
    matches = [
        d.storage_key
        for d in discover_api_service_descriptors()
        if d.service_id == service_id and d.storage_key != service_id
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def shadowed_legacy_ids(present_keys: Iterable[str]) -> Set[str]:
    """Return legacy ``service_id``\u200bs that are shadowed by a versioned key
    present in ``present_keys``.

    Listing endpoints use this to hide a legacy ``api_services`` block once
    its versioned counterpart exists, so the WebUI does not show duplicate
    entries after migration.
    """
    present = set(present_keys)
    return {
        d.service_id
        for d in discover_api_service_descriptors()
        if d.storage_key != d.service_id and d.storage_key in present
    }


def resolve_api_service(
    service_id: str, services: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Three-step version-aware lookup into an ``api_services`` mapping.

    1. **Versioned shadow** — when ``service_id`` is unversioned but a
       single versioned storage key exists for it, prefer the shadow so
       handlers that hard-code ``SERVICE_ID = "qingteng"`` transparently
       see the post-migration credentials.
    2. **Direct hit** — typical case (caller already passed the storage_key).
    3. **Legacy fallback** — caller passed a versioned key but only the
       unversioned block exists yet (partially-upgraded environments
       and isolated tests).

    Returns ``None`` when no entry matches.
    """
    shadow = versioned_storage_key_for(service_id)
    if shadow and shadow != service_id and shadow in services:
        return services[shadow]

    direct = services.get(service_id)
    if direct is not None:
        return direct

    legacy = legacy_service_id_for(service_id)
    if legacy and legacy != service_id:
        return services.get(legacy)
    return None


def warn_if_shadowing_legacy(service_id: str, services: Dict[str, Any]) -> None:
    """Log a warning when writing to a legacy id whose versioned shadow
    is already present.

    Subsequent reads via :func:`resolve_api_service` would prefer the
    shadow, so the just-written value would be silently invisible —
    almost always a sign the caller should be using the storage_key.
    """
    shadow = versioned_storage_key_for(service_id)
    if shadow and shadow != service_id and shadow in services:
        log.warning("api_service.write.shadowed_legacy", {
            "service_id": service_id,
            "shadow": shadow,
            "hint": (
                "Writes to the legacy id are invisible to readers — "
                "pass the versioned storage key instead."
            ),
        })


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def migrate_api_services(*, backup: bool = True) -> Dict[str, str]:
    """Copy unversioned ``api_services`` blocks into their versioned slots.

    Idempotent: re-running on already-migrated config performs no writes.

    For each plugin descriptor whose ``service_id`` differs from its
    ``storage_key``:

    * ``copied``     — ``api_services[storage_key]`` was missing and was
                       populated from ``api_services[service_id]``.
    * ``existed``    — ``api_services[storage_key]`` already present.
    * ``no-source``  — neither key present; nothing to do.

    A timestamped backup of ``flocks.json`` is created beside the
    original file before the first write of the run.

    Returns a ``{storage_key: action}`` map describing what happened.
    """
    descriptors = discover_api_service_descriptors(refresh=True)
    versioned = [d for d in descriptors if d.service_id != d.storage_key]
    if not versioned:
        return {}

    config_path = _resolve_config_path()
    if not config_path.exists():
        return {}

    try:
        raw = config_path.read_text(encoding="utf-8") or "{}"
        data = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        log.error("versioning.migrate.read_failed", {
            "path": str(config_path), "error": str(exc),
        })
        return {}

    services = data.get("api_services")
    if not isinstance(services, dict):
        services = {}

    actions: Dict[str, str] = {}
    pending: List[ApiServiceDescriptor] = []
    for desc in versioned:
        if desc.storage_key in services:
            actions[desc.storage_key] = "existed"
        elif desc.service_id in services:
            pending.append(desc)
        else:
            actions[desc.storage_key] = "no-source"

    if not pending:
        return actions

    if backup:
        _backup_config(config_path)

    for desc in pending:
        # Deep-copy via JSON round-trip; api_services blocks are pure JSON.
        services[desc.storage_key] = json.loads(json.dumps(services[desc.service_id]))
        actions[desc.storage_key] = "copied"
        log.info("versioning.migrated", {
            "service_id": desc.service_id,
            "storage_key": desc.storage_key,
            "version": desc.version,
        })

    data["api_services"] = services
    _atomic_write_json(config_path, data)
    return actions


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

_descriptor_cache: Optional[List[ApiServiceDescriptor]] = None


def _api_plugin_roots() -> List[Path]:
    """Return plugin api roots in priority order (project, user)."""
    from flocks.plugin.loader import DEFAULT_PLUGIN_ROOT

    candidates = [
        Path.cwd() / ".flocks" / "plugins" / "tools" / "api",
        DEFAULT_PLUGIN_ROOT / "tools" / "api",
    ]
    seen: Set[str] = set()
    unique: List[Path] = []
    for root in candidates:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _descriptor_for_plugin_dir(plugin_dir: Path) -> Optional[ApiServiceDescriptor]:
    if not plugin_dir.is_dir():
        return None
    provider_file = plugin_dir / "_provider.yaml"
    if not provider_file.is_file():
        return None
    try:
        data = yaml.safe_load(provider_file.read_text(encoding="utf-8"))
    except Exception as exc:
        log.debug("versioning.provider_yaml.read_failed", {
            "path": str(provider_file), "error": str(exc),
        })
        return None
    if not isinstance(data, dict):
        return None
    service_id = data.get("service_id") or data.get("name")
    if not isinstance(service_id, str) or not service_id.strip():
        return None
    service_id = service_id.strip()
    version = _extract_version(data)
    return ApiServiceDescriptor(
        service_id=service_id,
        version=version,
        storage_key=derive_storage_key(service_id, version),
        provider_yaml=provider_file,
    )


def _extract_version(provider_cfg: Dict[str, Any]) -> Optional[str]:
    """Local mirror of ``tool_loader.extract_provider_version`` to avoid a heavy import."""
    raw = provider_cfg.get("version")
    if raw is None:
        defaults = provider_cfg.get("defaults") or {}
        if isinstance(defaults, dict):
            raw = defaults.get("product_version") or defaults.get("version")
    return str(raw) if raw is not None else None


def _resolve_config_path() -> Path:
    config_dir = Config.get_config_path()
    jsonc_path = config_dir / "flocks.jsonc"
    return jsonc_path if jsonc_path.exists() else Config.get_config_file()


def _backup_config(path: Path) -> None:
    """Create a timestamped backup beside ``path``.

    Backups use one-second timestamp resolution; multiple migrations
    within the same second intentionally collapse to one backup so the
    config directory does not accumulate near-duplicate files when
    something repeatedly triggers migration in quick succession.
    """
    suffix = time.strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.name}.bak.{suffix}")
    if backup_path.exists():
        return
    try:
        shutil.copy2(path, backup_path)
        log.info("versioning.backup.created", {"backup": str(backup_path)})
    except Exception as exc:
        log.warning("versioning.backup.failed", {
            "path": str(path), "error": str(exc),
        })


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Atomic write (temp + rename) and clear ``Config`` cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".flocks_", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    try:
        Config.clear_cache()
    except Exception:
        pass


def _reset_descriptor_cache() -> None:
    """Test hook: clear cached descriptors so the next call rescans disk."""
    global _descriptor_cache
    _descriptor_cache = None
