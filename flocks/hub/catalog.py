"""Bundled Hub catalog reader and state merger."""

from __future__ import annotations

import json
import platform
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from flocks.hub import local
from flocks.hub.models import HubCatalogEntry, HubIndex, HubIndexEntry, HubPluginManifest, HubTaxonomy, PluginType
from flocks.hub.paths import get_bundled_hub_root

_LEGACY_REMOVED_PLUGINS: dict[tuple[PluginType, str], dict[str, Optional[str]]] = {
    ("agent", "alert-triage-agent"): {
        "replacement": "swarm-orchestrator",
        "reason": "Superseded by the pentest agent pack.",
    },
    ("workflow", "quick-alert-summary"): {
        "replacement": "tdp_alert_triage",
        "reason": "Replaced by maintained bundled triage workflows.",
    },
    ("tool", "hub_echo"): {
        "replacement": None,
        "reason": "Removed from bundled Hub plugins.",
    },
}

_TOOL_TYPE_DIRS = frozenset({"api", "device", "python", "mcp", "generated"})
_SKIP_PLUGIN_DIRS = frozenset({"__pycache__"})
_PATH_SIGNATURE_MISSING = -1


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _path_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), _PATH_SIGNATURE_MISSING, _PATH_SIGNATURE_MISSING)
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _is_plugin_dir(path: Path) -> bool:
    name = path.name
    return path.is_dir() and not name.startswith("_") and name not in _SKIP_PLUGIN_DIRS


def _iter_tool_plugin_dirs(tools_root: Path) -> Iterable[Path]:
    """Yield tool plugin roots under ``tools/`` without descending into payload dirs."""
    if not tools_root.is_dir():
        return
    for child in sorted(tools_root.iterdir(), key=lambda item: item.name):
        if not _is_plugin_dir(child):
            continue
        if child.name in _TOOL_TYPE_DIRS:
            if _has_direct_tool_payload(child):
                yield child
            for plugin_dir in sorted(child.iterdir(), key=lambda item: item.name):
                if _is_plugin_dir(plugin_dir):
                    yield plugin_dir
            continue
        yield child


def _plugin_manifest_signature(plugin_type: PluginType, root: Path) -> tuple[tuple[str, int, int], ...]:
    if plugin_type == "skill":
        candidates = [root / "SKILL.md"]
    elif plugin_type == "agent":
        candidates = [root / "agent.yaml"]
    elif plugin_type == "workflow":
        candidates = [root / "workflow.json", root / "workflow.md"]
    else:
        try:
            candidates = [
                path
                for path in root.iterdir()
                if path.is_file()
                and (
                    path.name == "_provider.yaml"
                    or (path.suffix in {".yaml", ".yml", ".py"} and not path.name.startswith("_"))
                )
            ]
        except OSError:
            candidates = []
    return tuple(_path_signature(path) for path in sorted(candidates, key=lambda item: item.name))


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value or "")


def legacy_removed_plugin_message(plugin_type: PluginType, plugin_id: str) -> Optional[str]:
    info = _LEGACY_REMOVED_PLUGINS.get((plugin_type, plugin_id))
    if not info:
        return None
    replacement = info.get("replacement")
    reason = info.get("reason") or "Removed from bundled Hub plugins."
    if replacement:
        return f"{plugin_type}:{plugin_id} has been removed. {reason} Use {replacement} instead."
    return f"{plugin_type}:{plugin_id} has been removed. {reason}"


def _frontmatter(path: Path) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            try:
                import yaml

                data = yaml.safe_load("\n".join(lines[1:index])) or {}
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
    return {}


@lru_cache(maxsize=1)
def load_index() -> HubIndex:
    root = get_bundled_hub_root()
    path = root / "index.json"
    if not path.is_file():
        return HubIndex(schemaVersion="hub.index.v1", plugins=[])
    return HubIndex.model_validate(_read_json(path))


@lru_cache(maxsize=1)
def load_taxonomy() -> HubTaxonomy:
    root = get_bundled_hub_root()
    path = root / "taxonomy.json"
    if not path.is_file():
        return HubTaxonomy(schemaVersion="hub.taxonomy.v1")
    return HubTaxonomy.model_validate(_read_json(path))


@lru_cache(maxsize=1)
def _manifest_path_lookup() -> dict[tuple[str, str], str]:
    return {(item.type, item.id): item.manifestPath for item in load_index().plugins}


def manifest_path(plugin_type: PluginType, plugin_id: str) -> Path:
    root = get_bundled_hub_root()
    direct = root / "plugins" / f"{plugin_type}s" / plugin_id / "manifest.json"
    if direct.is_file():
        return direct
    # Device plugins ship inside ``plugins/tools/device/<id>/`` — there
    # is no ``plugins/devices/`` directory. Try the canonical tool path
    # before falling back to the index lookup so on-disk manifest files
    # (when present) are still picked up.
    if plugin_type == "device":
        tool_direct = root / "plugins" / "tools" / "device" / plugin_id / "manifest.json"
        if tool_direct.is_file():
            return tool_direct
    manifest_rel = _manifest_path_lookup().get((plugin_type, plugin_id))
    if manifest_rel:
        path = (root / manifest_rel).resolve()
        bundled = root.resolve()
        if bundled not in path.parents and path != bundled:
            raise ValueError("manifest path escapes hub root")
        return path
    return direct


def load_manifest(plugin_type: PluginType, plugin_id: str) -> HubPluginManifest:
    path = manifest_path(plugin_type, plugin_id)
    if path.is_file():
        return HubPluginManifest.model_validate(_read_json(path))
    manifest = system_plugin_manifest(plugin_type, plugin_id)
    if manifest:
        return manifest
    return HubPluginManifest.model_validate(_read_json(path))


def _safe_tags(values: Iterable[str]) -> list[str]:
    allowed = set(load_taxonomy().tags)
    return [value for value in values if value in allowed]


def _system_manifest_path(plugin_type: PluginType, plugin_id: str) -> str:
    return f"system/{plugin_type}s/{plugin_id}/manifest.json"


def _system_source_path(root: Path) -> str:
    project_root = local._project_plugins_root().parent.parent
    try:
        return root.relative_to(project_root).as_posix()
    except Exception:
        return str(root)


def _base_manifest(
    *,
    plugin_type: PluginType,
    plugin_id: str,
    name: str,
    description: str,
    category: str,
    version: str = "1.0.0",
    description_cn: Optional[str] = None,
    tags: Optional[list[str]] = None,
    use_cases: Optional[list[str]] = None,
    capabilities: Optional[list[str]] = None,
    entrypoints: Optional[list[str]] = None,
    root: Path,
    network: bool = False,
    shell: bool = False,
    filesystem: str = "none",
    tools: Optional[list[str]] = None,
    risk_level: str = "low",
) -> HubPluginManifest:
    return HubPluginManifest(
        schemaVersion="hub.plugin.v1",
        id=plugin_id,
        type=plugin_type,
        name=name or plugin_id,
        description=description or "",
        descriptionCn=description_cn,
        version=version or "1.0.0",
        author="Flocks Team",
        license="MIT",
        homepage="",
        category=category,
        tags=_safe_tags(tags or []),
        useCases=use_cases or ["other"],
        domains=["security-ops"],
        capabilities=capabilities or [],
        trust="official",
        source={"kind": "bundled", "path": _system_source_path(root)},
        compatibility={"flocks": ">=0.8.0", "os": ["darwin", "linux", "windows"]},
        dependencies={"skills": [], "tools": [], "python": [], "external": []},
        permissions={
            "tools": tools or [],
            "network": network,
            "shell": shell,
            "filesystem": filesystem,
        },
        risk={"level": risk_level, "reasons": []},
        entrypoints=entrypoints or [],
        checksums={},
    )


def _skill_manifest(plugin_id: str, root: Path) -> Optional[HubPluginManifest]:
    skill_file = root / "SKILL.md"
    if not skill_file.is_file():
        return None
    data = _frontmatter(skill_file)
    return _base_manifest(
        plugin_type="skill",
        plugin_id=plugin_id,
        name=str(data.get("name") or plugin_id),
        description=str(data.get("description") or ""),
        category="devtools" if plugin_id == "agent-browser" else "threat-intel",
        tags=_safe_tags(["api-security"] if plugin_id == "agent-browser" else ["ndr"]),
        use_cases=["integration"] if plugin_id == "agent-browser" else ["threat-intelligence"],
        capabilities=["file-analysis"],
        entrypoints=["SKILL.md"],
        root=root,
        filesystem="read",
    )


def _agent_manifest(plugin_id: str, root: Path) -> Optional[HubPluginManifest]:
    agent_file = root / "agent.yaml"
    if not agent_file.is_file():
        return None
    data = _read_yaml(agent_file)
    tools = [str(item) for item in data.get("tools", []) if isinstance(item, str)]
    network = any(item in {"websearch", "webfetch"} or item.endswith("_query") for item in tools)
    shell = "bash" in tools
    filesystem = "write" if any(item in {"edit", "write"} for item in tools) else "read"
    return _base_manifest(
        plugin_type="agent",
        plugin_id=plugin_id,
        name=str(data.get("name") or plugin_id),
        description=str(data.get("description") or data.get("description_cn") or ""),
        description_cn=str(data.get("description_cn") or "") or None,
        category="detection",
        tags=_safe_tags([str(item) for item in data.get("tags", []) if isinstance(item, str)]),
        use_cases=["alert-triage"],
        capabilities=["llm-agent"],
        entrypoints=[item for item in ("agent.yaml", str(data.get("prompt_file") or "prompt.md")) if (root / item).exists()],
        root=root,
        network=network,
        shell=shell,
        filesystem=filesystem,
        tools=tools,
        risk_level="medium" if network or shell else "low",
    )


def _workflow_manifest(plugin_id: str, root: Path) -> Optional[HubPluginManifest]:
    workflow_file = root / "workflow.json"
    if not workflow_file.is_file() and not (root / "workflow.md").is_file():
        return None
    data = _read_json(workflow_file) if workflow_file.is_file() else {}
    entrypoints = [name for name in ("workflow.json", "workflow.md") if (root / name).exists()]
    description_raw = str(data.get("description") or "")
    description_en = str(data.get("description_en") or "").strip()
    description_cn = str(data.get("description_cn") or data.get("descriptionCn") or "").strip()
    description = description_en or description_raw
    if not description_cn and _contains_cjk(description_raw):
        description_cn = description_raw
    return _base_manifest(
        plugin_type="workflow",
        plugin_id=plugin_id,
        name=str(data.get("name") or data.get("id") or plugin_id),
        description=description,
        category="workflow-automation",
        description_cn=description_cn or None,
        tags=_safe_tags(["siem", "ndr"] if "tdp" in plugin_id else ["hids", "linux"]),
        use_cases=["alert-triage"] if "tdp" in plugin_id else ["endpoint-forensics"],
        capabilities=["workflow"],
        entrypoints=entrypoints,
        root=root,
        filesystem="write",
    )


def _has_direct_tool_payload(root: Path) -> bool:
    return any(
        path.is_file()
        and (
            (path.suffix in {".yaml", ".yml"} and not path.name.startswith("_"))
            or (path.suffix == ".py" and path.name != "__init__.py")
        )
        for path in root.iterdir()
    )


def _tool_tags(plugin_id: str, description: str) -> list[str]:
    manual_tags: dict[str, list[str]] = {
        "fofa": ["network-mapping", "network", "threat-intelligence"],
        "greynoise": ["threat-intelligence", "network", "ioc"],
        "ngsoc": ["siem", "network", "threat-intelligence"],
        "ngtip_api": ["threat-intelligence", "ioc"],
        "onesec": ["edr", "hids", "threat-intelligence"],
        # onesig is a Secure Internet Gateway — network only, not web-security/EDR
        "onesig": ["network", "threat-intelligence"],
        "qingteng": ["hids", "edr", "vulnerability"],
        "sangfor_af": ["waf", "network"],
        "sangfor_sip": ["siem", "network", "threat-intelligence"],
        "sangfor_xdr": ["xdr", "edr", "ndr"],
        "skyeye_api": ["ndr", "network", "threat-intelligence"],
        "tdp_api": ["ndr", "network", "threat-intelligence"],
        "threatbook-cn": ["threat-intelligence", "ioc"],
        "threatbook-io": ["threat-intelligence", "ioc"],
        "urlscan": ["network-mapping", "web-security", "threat-intelligence"],
        "virustotal": ["threat-intelligence", "ioc"],
        "mcp": ["integration"],
        "python": ["integration"],
    }
    # Support versioned plugin IDs: "sangfor_af_v8_0_48" matches key "sangfor_af".
    for key, tags in manual_tags.items():
        if plugin_id == key or plugin_id.startswith(f"{key}_"):
            return _safe_tags(tags)

    text = (f"{plugin_id} {description}").lower()
    inferred: list[str] = []
    if any(token in text for token in ("threat", "intel", "ioc", "情报", "virustotal", "threatbook", "greynoise")):
        inferred.append("threat-intelligence")
    if any(token in text for token in ("xdr",)):
        inferred.extend(["xdr", "edr", "ndr"])
    if any(token in text for token in ("edr", "endpoint", "终端")):
        inferred.append("edr")
    if any(token in text for token in ("hids", "host intrusion", "主机安全")):
        inferred.append("hids")
    if any(token in text for token in ("ndr", "traffic", "network detection", "流量")):
        inferred.append("ndr")
    if any(token in text for token in ("fofa", "mapping", "测绘", "asset discovery")):
        inferred.append("network-mapping")
    if any(token in text for token in ("network", "gateway", "dns", "ip")):
        inferred.append("network")
    if any(token in text for token in ("web", "url", "waf")):
        inferred.append("web-security")
    if not inferred:
        inferred = ["threat-intelligence"]
    return _safe_tags(inferred)


def _provider_integration_type(provider: dict[str, Any]) -> Optional[str]:
    value = provider.get("integration_type")
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    return cleaned or None


def _tool_manifest(plugin_id: str, root: Path) -> Optional[HubPluginManifest]:
    if not _has_direct_tool_payload(root):
        return None
    provider = _read_yaml(root / "_provider.yaml") if (root / "_provider.yaml").is_file() else {}
    yaml_files = sorted(path for path in root.glob("*.y*ml") if not path.name.startswith("_"))
    first_tool = _read_yaml(yaml_files[0]) if yaml_files else {}
    provider_description = str(provider.get("description") or "").strip()
    provider_description_cn = str(provider.get("description_cn") or "").strip()
    first_tool_description = str(first_tool.get("description") or "").strip()
    first_tool_description_cn = str(first_tool.get("description_cn") or "").strip()
    description = provider_description or first_tool_description or provider_description_cn or first_tool_description_cn
    description_cn = provider_description_cn or first_tool_description_cn
    if not description_cn and _contains_cjk(description):
        description_cn = description
    entrypoints = [
        path.name
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.suffix in {".yaml", ".yml", ".py"}
    ]
    # ``integration_type: device`` in ``_provider.yaml`` upgrades the
    # plugin to the first-class ``device`` Hub type. This drives:
    #   * the marketplace "Device" tab (filterable as ``type=device``),
    #   * the standard install path ``<plugins>/tools/device/<id>/``
    #     (resolved by ``hub.local.install_root("device", ...)``),
    #   * the device-access wizard, which still consumes
    #     ``api_services[storage_key]`` and filters by ``integration_type``
    #     so devices keep showing up there too.
    integration_type = _provider_integration_type(provider)
    plugin_type: PluginType = "device" if integration_type == "device" else "tool"
    return _base_manifest(
        plugin_type=plugin_type,
        plugin_id=plugin_id,
        name=str(provider.get("name") or first_tool.get("name") or plugin_id),
        description=description,
        category="integration",
        version=str(provider.get("version") or "1.0.0"),
        description_cn=description_cn or None,
        tags=_tool_tags(plugin_id, f"{description} {description_cn}"),
        use_cases=["integration", "threat-intelligence"],
        capabilities=["external-api"],
        entrypoints=entrypoints,
        root=root,
        network=True,
        filesystem="none",
        risk_level="medium",
    )


def _system_plugin_roots_cache_key() -> tuple[tuple[str, int, int], ...]:
    signature: list[tuple[str, int, int]] = []

    for plugin_type in ("skill", "agent", "workflow"):
        base = local.install_root(plugin_type, "project")
        signature.append(_path_signature(base))
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir(), key=lambda item: item.name):
            if not _is_plugin_dir(child):
                continue
            signature.append(_path_signature(child))
            signature.extend(_plugin_manifest_signature(plugin_type, child))

    tools_root = local.install_root("tool", "project")
    signature.append(_path_signature(tools_root))
    for directory in _iter_tool_plugin_dirs(tools_root):
        signature.append(_path_signature(directory))
        signature.extend(_plugin_manifest_signature("tool", directory))

    return tuple(signature)


def _discover_system_plugin_roots() -> dict[tuple[PluginType, str], Path]:
    roots: dict[tuple[PluginType, str], Path] = {}

    for plugin_type, detector in (
        ("skill", _skill_manifest),
        ("agent", _agent_manifest),
        ("workflow", _workflow_manifest),
    ):
        base = local.install_root(plugin_type, "project")
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir(), key=lambda item: item.name):
            if _is_plugin_dir(child) and detector(child.name, child):
                roots[(plugin_type, child.name)] = child

    tools_root = local.install_root("tool", "project")
    for directory in _iter_tool_plugin_dirs(tools_root):
        manifest = _tool_manifest(directory.name, directory)
        if manifest:
            # The manifest type already reflects ``integration_type:
            # device`` (see :func:`_tool_manifest`), so we just defer
            # to it instead of hardcoding ``"tool"``.
            roots[(manifest.type, directory.name)] = directory

    return roots


@lru_cache(maxsize=8)
def _cached_system_plugin_roots(
    _signature: tuple[tuple[str, int, int], ...],
) -> tuple[tuple[PluginType, str, Path], ...]:
    return tuple(
        (plugin_type, plugin_id, path)
        for (plugin_type, plugin_id), path in _discover_system_plugin_roots().items()
    )


def _system_plugin_roots() -> dict[tuple[PluginType, str], Path]:
    return {
        (plugin_type, plugin_id): path
        for plugin_type, plugin_id, path in _cached_system_plugin_roots(_system_plugin_roots_cache_key())
    }


def _bundled_tool_roots_cache_key() -> tuple[tuple[str, int, int], ...]:
    from flocks.hub.paths import bundled_tool_plugin_roots

    signature: list[tuple[str, int, int]] = []
    for tools_root in bundled_tool_plugin_roots():
        signature.append(_path_signature(tools_root))
        for directory in _iter_tool_plugin_dirs(tools_root):
            signature.append(_path_signature(directory))
            signature.extend(_plugin_manifest_signature("tool", directory))
    return tuple(signature)


def _discover_bundled_tool_roots() -> dict[tuple[PluginType, str], Path]:
    """Tool plugin directories shipped pre-bundled inside flockshub.

    Returns ``(plugin_type, plugin_id) -> Path`` for every directory
    under ``<flockshub>/plugins/tools/`` that carries an actual tool
    payload (``_provider.yaml`` or any non-underscore YAML/PY file).

    These bundled tools are surfaced in the FlocksHub UI as ``available``
    entries so users can install them through the standard Hub flow
    (``hub.install``); the runtime registry only loads tools that have
    been installed into ``<plugins>/tools/...``.

    Project- and user-installed copies take precedence in the catalog
    listing (see :func:`list_catalog`); this helper does not attempt
    that resolution itself so it stays a pure on-disk discovery.
    """
    from flocks.hub.paths import bundled_tool_plugin_roots

    roots: dict[tuple[PluginType, str], Path] = {}
    for tools_root in bundled_tool_plugin_roots():
        for directory in _iter_tool_plugin_dirs(tools_root):
            name = directory.name
            manifest = _tool_manifest(name, directory)
            if not manifest:
                continue
            # First-wins: keep the highest-priority bundled root entry
            # and let later collisions fall through silently — same
            # contract as ``_system_plugin_roots``. ``manifest.type``
            # honours ``integration_type: device`` so device plugins
            # surface with ``("device", id)`` keys.
            roots.setdefault((manifest.type, name), directory)
    return roots


@lru_cache(maxsize=8)
def _cached_bundled_tool_roots(
    _signature: tuple[tuple[str, int, int], ...],
) -> tuple[tuple[PluginType, str, Path], ...]:
    return tuple(
        (plugin_type, plugin_id, path)
        for (plugin_type, plugin_id), path in _discover_bundled_tool_roots().items()
    )


def _bundled_tool_roots() -> dict[tuple[PluginType, str], Path]:
    return {
        (plugin_type, plugin_id): path
        for plugin_type, plugin_id, path in _cached_bundled_tool_roots(_bundled_tool_roots_cache_key())
    }


def clear_catalog_caches() -> None:
    """Clear Hub filesystem discovery caches after installs or explicit refreshes."""
    load_index.cache_clear()
    load_taxonomy.cache_clear()
    _manifest_path_lookup.cache_clear()
    _cached_system_plugin_roots.cache_clear()
    _cached_bundled_tool_roots.cache_clear()


def system_plugin_root(plugin_type: PluginType, plugin_id: str) -> Optional[Path]:
    """Resolve the on-disk root for a plugin, preferring local installs.

    Lookup order (first hit wins):
      1. Project-/user-level installed plugins (``_system_plugin_roots``)
      2. Bundled flockshub tool plugins (``_bundled_tool_roots``)

    The bundled fallback makes ``plugin_root``/``file_tree``/``read_file_content``
    work for FlocksHub-bundled tools even when no copy has been installed
    locally yet, so the UI's plugin detail panel can preview them.
    """
    installed = _system_plugin_roots().get((plugin_type, plugin_id))
    if installed is not None:
        return installed
    # ``tool`` and ``device`` plugins are both materialised as bundled
    # flockshub directories under ``plugins/tools/`` — the device variant
    # just sets ``integration_type: device`` in ``_provider.yaml``.
    if plugin_type in {"tool", "device"}:
        return _bundled_tool_roots().get((plugin_type, plugin_id))
    return None


def system_plugin_manifest(plugin_type: PluginType, plugin_id: str) -> Optional[HubPluginManifest]:
    root = system_plugin_root(plugin_type, plugin_id)
    if root is None:
        return None
    return _manifest_for_system_root(plugin_type, plugin_id, root)


def _manifest_for_system_root(plugin_type: PluginType, plugin_id: str, root: Path) -> Optional[HubPluginManifest]:
    if plugin_type == "skill":
        return _skill_manifest(plugin_id, root)
    if plugin_type == "agent":
        return _agent_manifest(plugin_id, root)
    if plugin_type == "workflow":
        return _workflow_manifest(plugin_id, root)
    if plugin_type in {"tool", "device"}:
        # ``_tool_manifest`` infers the final ``device`` vs ``tool``
        # split from ``_provider.yaml``'s ``integration_type``. Callers
        # that already know the expected type still pass through here
        # so we can confirm the inferred type matches the requested one
        # (e.g. avoid returning a device manifest for a tool query).
        manifest = _tool_manifest(plugin_id, root)
        if manifest and manifest.type != plugin_type:
            return None
        return manifest
    return None


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value or "")
    return tuple(int(part) for part in parts) if parts else (0,)


def _os_compatible(manifest: HubPluginManifest) -> bool:
    allowed = {item.lower() for item in manifest.compatibility.os}
    if not allowed:
        return True
    current = platform.system().lower()
    aliases = {"darwin": "darwin", "linux": "linux", "windows": "windows"}
    return aliases.get(current, current) in allowed or current in allowed


def _entry_from_manifest(manifest: HubPluginManifest) -> HubCatalogEntry:
    record = local.get_record(manifest.type, manifest.id)
    install_path = _resolve_install_path(manifest.type, manifest.id, record, local.infer_local_installs())

    state = "available"
    installed_version: Optional[str] = None
    if install_path:
        installed_version = record.version if record else manifest.version
        state = "installed"
        if _version_tuple(installed_version) < _version_tuple(manifest.version):
            state = "updateAvailable"
    if not _os_compatible(manifest):
        state = "incompatible"

    return HubCatalogEntry(
        id=manifest.id,
        type=manifest.type,
        name=manifest.name,
        description=manifest.description,
        descriptionCn=getattr(manifest, "descriptionCn", None),
        version=manifest.version,
        category=manifest.category,
        tags=manifest.tags,
        useCases=manifest.useCases,
        domains=manifest.domains,
        capabilities=manifest.capabilities,
        trust=manifest.trust,
        riskLevel=manifest.risk.level,
        state=state,
        installedVersion=installed_version,
        source=manifest.source.kind,
        manifestPath=str(manifest_path(manifest.type, manifest.id).relative_to(get_bundled_hub_root())),
        installPath=str(install_path) if install_path else None,
        native=False,
    )


def _resolve_install_path(
    plugin_type: PluginType,
    plugin_id: str,
    record: Optional[local.InstalledPluginRecord],
    inferred_installs: Optional[dict[tuple[PluginType, str], Path]] = None,
) -> Optional[Path]:
    if record and record.installPath:
        path = Path(record.installPath)
        if local.has_install_payload(plugin_type, path):
            return path
        local.remove_installed_record(plugin_type, plugin_id)
    if inferred_installs is not None:
        return inferred_installs.get((plugin_type, plugin_id))
    return local.infer_local_install(plugin_type, plugin_id)


def _entry_from_index(
    item: HubIndexEntry,
    records: dict[str, local.InstalledPluginRecord],
    inferred_installs: dict[tuple[PluginType, str], Path],
) -> HubCatalogEntry:
    record = records.get(f"{item.type}:{item.id}")
    install_path = _resolve_install_path(item.type, item.id, record, inferred_installs)

    state = "available"
    installed_version: Optional[str] = None
    if install_path:
        installed_version = record.version if record else item.version
        state = "installed"
        if _version_tuple(installed_version) < _version_tuple(item.version):
            state = "updateAvailable"

    return HubCatalogEntry(
        id=item.id,
        type=item.type,
        name=item.name,
        description=item.description,
        descriptionCn=item.descriptionCn,
        version=item.version,
        category=item.category,
        tags=item.tags,
        useCases=item.useCases,
        trust=item.trust,
        riskLevel=item.riskLevel,
        state=state,
        installedVersion=installed_version,
        source="bundled",
        manifestPath=item.manifestPath,
        installPath=str(install_path) if install_path else None,
        native=False,
    )


def _entry_from_system_manifest(manifest: HubPluginManifest, root: Path) -> HubCatalogEntry:
    return HubCatalogEntry(
        id=manifest.id,
        type=manifest.type,
        name=manifest.name,
        description=manifest.description,
        descriptionCn=getattr(manifest, "descriptionCn", None),
        version=manifest.version,
        category=manifest.category,
        tags=manifest.tags,
        useCases=manifest.useCases,
        domains=manifest.domains,
        capabilities=manifest.capabilities,
        trust=manifest.trust,
        riskLevel=manifest.risk.level,
        state="installed",
        installedVersion=manifest.version,
        source="system",
        manifestPath=_system_manifest_path(manifest.type, manifest.id),
        installPath=str(root),
        native=True,
    )


def _entry_from_bundled_tool(
    manifest: HubPluginManifest,
    root: Path,
    records: dict[str, local.InstalledPluginRecord],
    inferred_installs: dict[tuple[PluginType, str], Path],
) -> HubCatalogEntry:
    """Catalog entry for a tool bundled inside flockshub.

    State is computed exactly like :func:`_entry_from_index`:
      * ``installed``       — the user installed it under
                              ``~/.flocks/plugins/tools/<group>/<id>/``
                              (record or on-disk payload found).
      * ``updateAvailable`` — installed copy is older than the bundled
                              version.
      * ``available``       — bundled in flockshub, no local install.
                              The runtime ``ToolRegistry`` only picks up
                              installed tools, so an "available" entry
                              here mirrors the on-disk state truthfully
                              and matches the user's mental model
                              ("not installed" until they click install).
      * ``incompatible``    — manifest declares an incompatible OS.
    """
    record = records.get(f"{manifest.type}:{manifest.id}")
    install_path = _resolve_install_path(manifest.type, manifest.id, record, inferred_installs)

    state = "available"
    installed_version: Optional[str] = None
    if install_path:
        installed_version = record.version if record else manifest.version
        state = "installed"
        if _version_tuple(installed_version) < _version_tuple(manifest.version):
            state = "updateAvailable"
    if not _os_compatible(manifest):
        state = "incompatible"

    bundled_root = get_bundled_hub_root().resolve()
    try:
        manifest_rel = (root.resolve() / "manifest.json").relative_to(bundled_root).as_posix()
    except ValueError:
        manifest_rel = _system_manifest_path(manifest.type, manifest.id)

    return HubCatalogEntry(
        id=manifest.id,
        type=manifest.type,
        name=manifest.name,
        description=manifest.description,
        descriptionCn=getattr(manifest, "descriptionCn", None),
        version=manifest.version,
        category=manifest.category,
        tags=manifest.tags,
        useCases=manifest.useCases,
        domains=manifest.domains,
        capabilities=manifest.capabilities,
        trust=manifest.trust,
        riskLevel=manifest.risk.level,
        state=state,
        installedVersion=installed_version,
        source="bundled",
        manifestPath=manifest_rel,
        installPath=str(install_path) if install_path else None,
        native=False,
    )


def list_manifests() -> list[HubPluginManifest]:
    root = get_bundled_hub_root()
    index = load_index()
    result: list[HubPluginManifest] = []
    for item in index.plugins:
        path = root / item.manifestPath
        try:
            result.append(HubPluginManifest.model_validate(_read_json(path)))
        except Exception:
            continue
    return result


def _contains_any(values: Iterable[str], selected: Optional[list[str]]) -> bool:
    if not selected:
        return True
    value_set = {value.lower() for value in values}
    return any(item.lower() in value_set for item in selected)


def list_catalog(
    *,
    plugin_type: Optional[PluginType] = None,
    category: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    use_cases: Optional[list[str]] = None,
    state: Optional[list[str]] = None,
    trust: Optional[list[str]] = None,
    risk: Optional[list[str]] = None,
    q: Optional[str] = None,
) -> list[HubCatalogEntry]:
    records = local.load_installed_records()
    inferred_installs = local.infer_local_installs()
    entries = [
        _entry_from_index(item, records, inferred_installs)
        for item in load_index().plugins
    ]
    seen: set[tuple[PluginType, str]] = {(entry.type, entry.id) for entry in entries}
    for (system_type, system_id), root in _system_plugin_roots().items():
        if (system_type, system_id) in seen:
            continue
        manifest = _manifest_for_system_root(system_type, system_id, root)
        if manifest:
            entries.append(_entry_from_system_manifest(manifest, root))
            seen.add((system_type, system_id))

    # FlocksHub-bundled tool plugins. The runtime ``ToolRegistry`` only
    # discovers tools that have been installed into ``<plugins>/tools``,
    # so the catalog needs an explicit pass to surface bundled-but-not-
    # yet-installed plugins like ``onesig_v2_5_3_D20250710``. These show
    # up as ``state="available"`` until the user installs them, at which
    # point the standard ``records``/``inferred_installs`` flow upgrades
    # them to ``state="installed"`` (see :func:`_entry_from_bundled_tool`).
    for (bundled_type, bundled_id), root in _bundled_tool_roots().items():
        if (bundled_type, bundled_id) in seen:
            continue
        manifest = _manifest_for_system_root(bundled_type, bundled_id, root)
        if manifest:
            entries.append(_entry_from_bundled_tool(manifest, root, records, inferred_installs))
            seen.add((bundled_type, bundled_id))
    query = (q or "").strip().lower()

    def keep(entry: HubCatalogEntry) -> bool:
        if plugin_type and entry.type != plugin_type:
            return False
        if category and entry.category not in category:
            return False
        if not _contains_any(entry.tags, tags):
            return False
        if not _contains_any(entry.useCases, use_cases):
            return False
        if state and entry.state not in state:
            return False
        if trust and entry.trust not in trust:
            return False
        if risk and entry.riskLevel not in risk:
            return False
        if query:
            haystack = " ".join(
                [
                    entry.id,
                    entry.name,
                    entry.description,
                    entry.descriptionCn or "",
                    entry.category,
                    *entry.tags,
                    *entry.useCases,
                ]
            ).lower()
            if query not in haystack:
                return False
        return True

    return [entry for entry in entries if keep(entry)]


def category_counts() -> dict:
    taxonomy = load_taxonomy().model_dump(mode="json")
    entries = list_catalog()

    def count_by(attr: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in entries:
            values = getattr(entry, attr)
            if isinstance(values, str):
                values = [values]
            for value in values:
                counts[value] = counts.get(value, 0) + 1
        return counts

    taxonomy["counts"] = {
        "type": count_by("type"),
        "category": count_by("category"),
        "tags": count_by("tags"),
        "useCases": count_by("useCases"),
        "state": count_by("state"),
        "trust": count_by("trust"),
        "riskLevel": count_by("riskLevel"),
    }
    return taxonomy
