"""A source of sealed hub packages, for tests.

It offers the two pro-edition scene suites the bundled hub announces, the
way an extension delivers them: listed by their manifests and page ids, with
the payload -- prebuilt pages, no page source -- written out only by
``unseal``.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from flocks.contracts.webui.builder import RUNTIME_NAME, RUNTIME_VERSION, SDK_IMPORT_NAME, bundle_digest
from flocks.hub.models import HubPluginManifest
from flocks.hub.sealed import (
    SealedPackage,
    SealedPayloadUnavailable,
    SealedSourceUnavailable,
    clear_sealed_cache,
    register_sealed_source,
    unregister_sealed_source,
)

SUITES = {
    "code-audit-workspace": ("code_audit_ui", {"code_audit_overview": "code-audit-overview", "code_audit_findings": "code-audit-findings"}),
    "ai-redteam-workspace": ("ai_redteam_ui", {"ai_redteam_overview": "ai-redteam-overview", "ai_redteam_surface": "ai-redteam-surface"}),
}
PAGE_BUNDLE = "export default function Page(){return null}\n"


def _manifest(plugin_type: str, plugin_id: str, name: str, version: str, components=None) -> dict:
    return {
        "schemaVersion": "hub.plugin.v1",
        "id": plugin_id,
        "type": plugin_type,
        "name": name,
        "nameCn": name,
        "description": f"{name} (sealed)",
        "version": version,
        "category": "workflow-automation",
        "tags": ["integration"],
        "trust": "official",
        "edition": "pro",
        "source": {"kind": "sealed", "path": f"plugins/{plugin_type}s/{plugin_id}"},
        "entrypoints": ["component.json"] if plugin_type == "component" else ["workspace.json"],
        "components": components or [],
    }


def suite_package(suite_id: str, version: str = "1.0.0") -> tuple[SealedPackage, dict]:
    webui_id, pages = SUITES[suite_id]
    component = _manifest("component", suite_id, suite_id, version, [{"type": "webui", "id": webui_id}])
    webui = _manifest("webui", webui_id, webui_id, version)
    package = SealedPackage(
        id=suite_id,
        version=version,
        plugins=(HubPluginManifest.model_validate(component), HubPluginManifest.model_validate(webui)),
        pages={webui_id: tuple(pages.values())},
    )
    return package, {"component": component, "webui": webui}


def write_tree(suite_id: str, destination: Path, manifests: dict, *, runtime_version: int = RUNTIME_VERSION,
               bundle: str = PAGE_BUNDLE, record_bundle: Optional[str] = None) -> None:
    """The package tree as an extension unseals it."""
    webui_id, pages = SUITES[suite_id]
    component_dir = destination / "plugins" / "components" / suite_id
    component_dir.mkdir(parents=True)
    (component_dir / "manifest.json").write_text(json.dumps(manifests["component"]), encoding="utf-8")
    (component_dir / "component.json").write_text(
        json.dumps({"schemaVersion": "hub.component.v1", "id": suite_id, "name": suite_id,
                    "components": manifests["component"]["components"]}),
        encoding="utf-8",
    )
    webui_dir = destination / "plugins" / "webuis" / webui_id
    webui_dir.mkdir(parents=True)
    (webui_dir / "manifest.json").write_text(json.dumps(manifests["webui"]), encoding="utf-8")
    (webui_dir / "workspace.json").write_text(
        json.dumps({"id": webui_id, "version": manifests["webui"]["version"], "title": webui_id,
                    "placement": "sceneWorkspace", "defaultPageId": next(iter(pages.values()))}),
        encoding="utf-8",
    )
    for order, (directory, page_id) in enumerate(pages.items()):
        page_dir = webui_dir / directory
        (page_dir / "dist").mkdir(parents=True)
        (page_dir / "manifest.json").write_text(
            json.dumps({"id": page_id, "title": page_id, "route": f"/contracts/webui/{page_id}",
                        "order": order, "enabled": True, "placement": "home.after", "entry": "src/index.tsx"}),
            encoding="utf-8",
        )
        (page_dir / "dist" / "page.js").write_text(bundle, encoding="utf-8")
        (page_dir / "dist" / "meta.json").write_text(
            json.dumps({"status": "ready", "hash": bundle_digest((record_bundle or bundle).encode()),
                        "builtAt": 1, "error": None, "runtime": RUNTIME_NAME,
                        "runtimeVersion": runtime_version, "sdkImport": SDK_IMPORT_NAME}),
            encoding="utf-8",
        )


class FakeSealedSource:
    """Both pro suites, sealed. Tests adjust the attributes to misbehave."""

    name = "test-sealed"

    def __init__(self) -> None:
        self.versions = {suite_id: "1.0.0" for suite_id in SUITES}
        self.unavailable = False
        self.unseal_error: Optional[str] = None
        # Applied to the unsealed tree: (suite_id, destination, manifests) -> None.
        self.tamper = None
        self.tree_options: dict = {}
        self.unsealed: list[tuple[str, Path]] = []

    def _packages(self) -> dict[str, tuple[SealedPackage, dict]]:
        return {suite_id: suite_package(suite_id, version) for suite_id, version in self.versions.items()}

    def packages(self):
        if self.unavailable:
            raise SealedSourceUnavailable("the core cannot be reached")
        return [package for package, _ in self._packages().values()]

    def signature(self) -> str:
        if self.unavailable:
            return "unavailable"
        return ",".join(f"{suite_id}@{version}" for suite_id, version in sorted(self.versions.items()))

    async def unseal(self, package: SealedPackage, destination: Path) -> None:
        if self.unseal_error:
            raise SealedPayloadUnavailable(self.unseal_error)
        _, manifests = self._packages()[package.id]
        write_tree(package.id, destination, manifests, **self.tree_options)
        if self.tamper is not None:
            self.tamper(package.id, destination, manifests)
        self.unsealed.append((package.id, destination))


@contextmanager
def registered_sealed_source():
    from flocks.hub.catalog import clear_catalog_caches
    from flocks.hub.suite_license import clear_page_suite_cache

    source = FakeSealedSource()
    register_sealed_source(source)
    clear_catalog_caches()
    clear_page_suite_cache()
    try:
        yield source
    finally:
        unregister_sealed_source(source)
        clear_sealed_cache()
        clear_catalog_caches()
        clear_page_suite_cache()
