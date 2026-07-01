"""Plugin discovery for page data access contract providers."""

from __future__ import annotations

from pathlib import Path

from flocks.plugin import ExtensionPoint, PluginLoader
from flocks.contracts.access.models import WebUIContractPlugin

CONTRACTS_ATTR = "CONTRACTS"


def discover_contract_plugins(project_dir: Path | None = None) -> tuple[WebUIContractPlugin, ...]:
    plugins: list[WebUIContractPlugin] = []
    seen_plugin_ids: set[str] = set()
    seen_contract_ids: set[tuple[str, str]] = set()

    def collect(items: list[WebUIContractPlugin], source: str) -> None:
        for item in items:
            contract_ids = {(contract.contract_id, contract.version) for contract in item.contracts}
            if item.plugin_id in seen_plugin_ids:
                continue
            if seen_contract_ids.intersection(contract_ids):
                continue
            seen_plugin_ids.add(item.plugin_id)
            seen_contract_ids.update(contract_ids)
            plugins.append(
                WebUIContractPlugin(
                    plugin_id=item.plugin_id,
                    contracts=item.contracts,
                    binding_resolver=item.binding_resolver,
                    adapter=item.adapter,
                    response_pipeline=item.response_pipeline,
                    overlay_store=item.overlay_store,
                    version=item.version,
                    source=source,
                )
            )

    PluginLoader.register_extension_point(
        ExtensionPoint(
            attr_name=CONTRACTS_ATTR,
            subdir="contracts/access",
            consumer=collect,
            item_type=WebUIContractPlugin,
            dedup_key=lambda plugin: plugin.plugin_id,
            recursive=True,
            max_depth=2,
        )
    )
    PluginLoader.load_extension(CONTRACTS_ATTR, project_dir=project_dir or Path.cwd())
    return tuple(plugins)
