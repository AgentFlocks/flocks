"""Loader for ``_test.yaml`` manifests bundled with API service plugins.

The leading underscore makes :mod:`flocks.plugin.loader` skip the file, so a
manifest is never registered as a callable tool. Two pieces of metadata are
exposed:

* ``connectivity`` — a ``(tool, params)`` pair used by
  ``POST /api/provider/{id}/test-credentials`` instead of heuristic tool
  selection.
* ``fixtures`` — predeclared parameter sets per business tool, served by
  ``GET /api/tools/{name}/fixtures`` for the WebUI sample drop-down.

See ``.flocks/plugins/tools/api/_TEST_YAML_DESIGN.md`` for the full schema.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from flocks.utils.log import Log

log = Log.create(service="tool.probe_loader")

_TEST_FILENAME = "_test.yaml"

# --- Public data classes ----------------------------------------------------


@dataclass(frozen=True)
class ConnectivitySpec:
    """Declared connectivity probe.

    The probe passes iff :attr:`flocks.tool.registry.ToolResult.success` is
    ``True``. Richer assertions are intentionally out of scope for v1.
    """

    tool: str
    params: dict[str, Any]


@dataclass(frozen=True)
class Fixture:
    """One predeclared test sample for a business tool.

    ``label`` is the default UI string (typically English). ``label_cn``
    is an optional Chinese override; the WebUI picks one based on the
    user's locale, mirroring the ``description``/``description_cn``
    pattern already used by ``ToolInfo``.
    """

    label: str
    params: dict[str, Any]
    tags: tuple[str, ...] = ()
    assertion: dict[str, Any] = field(default_factory=dict)
    label_cn: Optional[str] = None


@dataclass
class TestManifest:
    """Parsed ``_test.yaml`` for one API service plugin."""

    __test__ = False  # pytest must not collect this dataclass as a test class

    provider_id: str            # storage_key, e.g. "ngtip_api_v5_1_5"
    plugin_dir: Path
    connectivity: Optional[ConnectivitySpec]
    fixtures: dict[str, list[Fixture]]


# --- Cache ------------------------------------------------------------------

_cache: dict[str, Optional[TestManifest]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """Drop this module's manifest cache.

    Does not touch :mod:`flocks.config.api_versioning`'s descriptor cache;
    callers that need to rediscover plugin directories on disk should call
    ``discover_api_service_descriptors(refresh=True)`` separately.
    """
    with _cache_lock:
        _cache.clear()


# --- Public API -------------------------------------------------------------


def load_test_manifest(provider_id: str) -> Optional[TestManifest]:
    """Return the parsed manifest for ``provider_id``, or ``None``.

    ``None`` results are cached too, so a missing ``_test.yaml`` is only
    walked once per process.
    """
    with _cache_lock:
        if provider_id in _cache:
            return _cache[provider_id]

    manifest = _load_uncached(provider_id)

    with _cache_lock:
        _cache[provider_id] = manifest
    return manifest


def get_connectivity_spec(provider_id: str) -> Optional[ConnectivitySpec]:
    """Return the connectivity probe declared for ``provider_id``, if any.

    The caller falls back to heuristic tool selection on ``None``.
    """
    manifest = load_test_manifest(provider_id)
    return manifest.connectivity if manifest else None


def get_tool_fixtures(provider_id: str, tool_name: str) -> list[Fixture]:
    """Return fixtures declared for ``tool_name`` within ``provider_id``."""
    manifest = load_test_manifest(provider_id)
    return manifest.fixtures.get(tool_name, []) if manifest else []


def get_tool_fixtures_by_tool_name(tool_name: str) -> list[Fixture]:
    """Return fixtures for ``tool_name`` without knowing its provider.

    Resolution:

    1. Look up the tool in :class:`~flocks.tool.registry.ToolRegistry`. If
       found, only descriptors whose ``service_id`` **or** ``storage_key``
       matches the tool's ``provider`` field are eligible (the loader stores
       the versioned ``storage_key`` in ``ToolInfo.provider``). This prevents
       fixture leakage across co-existing versions of the same product and
       across unrelated services that happen to share a tool name.
    2. If the tool is not registered (registry not ready, or non-API tool),
       scan every manifest and return the first match.
    """
    from flocks.config.api_versioning import discover_api_service_descriptors

    target_service_id = _service_id_for_tool(tool_name)
    for descriptor in discover_api_service_descriptors():
        if target_service_id is not None and (
            descriptor.service_id != target_service_id
            and descriptor.storage_key != target_service_id
        ):
            continue
        manifest = load_test_manifest(descriptor.storage_key)
        if manifest is None:
            continue
        fixtures = manifest.fixtures.get(tool_name)
        if fixtures:
            return fixtures
    return []


# --- Internals --------------------------------------------------------------


def _service_id_for_tool(tool_name: str) -> Optional[str]:
    """Return the registered tool's ``provider`` field, or ``None``."""
    try:
        from flocks.tool.registry import ToolRegistry
        tool = ToolRegistry.get(tool_name)
    except Exception:
        return None
    return getattr(tool.info, "provider", None) if tool else None


def _plugin_dir_for(provider_id: str) -> Optional[Path]:
    """Resolve the plugin directory for ``provider_id`` via the descriptor registry."""
    from flocks.config.api_versioning import discover_api_service_descriptors

    for descriptor in discover_api_service_descriptors():
        if descriptor.storage_key == provider_id:
            return descriptor.provider_yaml.parent
    return None


def _load_uncached(provider_id: str) -> Optional[TestManifest]:
    plugin_dir = _plugin_dir_for(provider_id)
    if plugin_dir is None:
        return None

    path = plugin_dir / _TEST_FILENAME
    if not path.is_file():
        return None

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warn("manifest.parse_error", {"provider_id": provider_id, "path": str(path), "error": str(exc)})
        return None

    if not isinstance(raw, dict):
        log.warn("manifest.not_a_dict", {"provider_id": provider_id, "path": str(path)})
        return None

    return TestManifest(
        provider_id=provider_id,
        plugin_dir=plugin_dir,
        connectivity=_parse_connectivity(provider_id, raw, path),
        fixtures=_parse_fixtures(provider_id, raw),
    )


def _parse_connectivity(provider_id: str, raw: dict, path: Path) -> Optional[ConnectivitySpec]:
    section = raw.get("connectivity")
    if section is None:
        return None
    if not isinstance(section, dict):
        log.warn("manifest.connectivity_not_dict", {"provider_id": provider_id, "path": str(path)})
        return None

    tool = section.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        log.warn("manifest.connectivity_missing_tool", {"provider_id": provider_id, "path": str(path)})
        return None
    tool = tool.strip()

    params_raw = section.get("params") or {}
    if not isinstance(params_raw, dict):
        log.warn("manifest.connectivity_params_not_dict", {"provider_id": provider_id, "tool": tool})
        return None

    if "success_when" in section:
        # Reserved for a future richer assertion schema; ignore for now.
        log.warn("manifest.success_when_ignored", {"provider_id": provider_id, "tool": tool})

    _warn_if_tool_unknown(provider_id, tool)
    return ConnectivitySpec(tool=tool, params=dict(params_raw))


def _warn_if_tool_unknown(provider_id: str, tool_name: str) -> None:
    """Best-effort hint to plugin authors. Never raises, never blocks.

    We do NOT compare ``tool.info.provider`` to ``provider_id``: manifests are
    keyed by ``storage_key`` (versioned) but tool YAMLs declare the
    unversioned ``service_id``, so equality would always warn. The real
    binding is enforced at fixture lookup time.
    """
    try:
        from flocks.tool.registry import ToolRegistry
        tool = ToolRegistry.get(tool_name)
    except Exception:
        return
    if tool is None:
        log.warn("manifest.connectivity_tool_not_found", {"provider_id": provider_id, "tool": tool_name})


def _parse_fixtures(provider_id: str, raw: dict) -> dict[str, list[Fixture]]:
    section = raw.get("fixtures")
    if section is None:
        return {}
    if not isinstance(section, dict):
        log.warn("manifest.fixtures_not_dict", {"provider_id": provider_id})
        return {}

    result: dict[str, list[Fixture]] = {}
    for tool_name, samples in section.items():
        if not isinstance(tool_name, str):
            continue
        if not isinstance(samples, list):
            log.warn("manifest.fixtures_samples_not_list", {"provider_id": provider_id, "tool": tool_name})
            continue
        parsed = [
            f for idx, sample in enumerate(samples)
            if (f := _parse_fixture(provider_id, tool_name, idx, sample)) is not None
        ]
        if parsed:
            result[tool_name] = parsed
    return result


def _parse_fixture(provider_id: str, tool_name: str, idx: int, sample: Any) -> Optional[Fixture]:
    if not isinstance(sample, dict):
        log.warn("manifest.fixture_not_dict", {"provider_id": provider_id, "tool": tool_name, "index": idx})
        return None

    label = sample.get("label")
    if not isinstance(label, str) or not label.strip():
        log.warn("manifest.fixture_missing_label", {"provider_id": provider_id, "tool": tool_name, "index": idx})
        return None

    params_raw = sample.get("params") or {}
    if not isinstance(params_raw, dict):
        log.warn("manifest.fixture_params_not_dict", {"provider_id": provider_id, "tool": tool_name, "index": idx})
        return None

    tags_raw = sample.get("tags")
    tags = tuple(
        str(t) for t in tags_raw if isinstance(t, (str, int, float))
    ) if isinstance(tags_raw, (list, tuple)) else ()

    assertion_raw = sample.get("assert")
    assertion = dict(assertion_raw) if isinstance(assertion_raw, dict) else {}

    label_cn_raw = sample.get("label_cn")
    label_cn_clean = label_cn_raw.strip() if isinstance(label_cn_raw, str) else ""
    label_cn = label_cn_clean[:80] if label_cn_clean else None

    return Fixture(
        label=label.strip()[:80],
        params=dict(params_raw),
        tags=tags,
        assertion=assertion,
        label_cn=label_cn,
    )
