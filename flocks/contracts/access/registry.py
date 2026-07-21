"""Contract registry for discovered WebUI contract plugins."""

from __future__ import annotations

from flocks.contracts.access.models import Contract, ContractRuntimeError, WebUIContractPlugin

DEFAULT_CONTRACT_VERSION = "1.0"
DEFAULT_SLOT_ID = "primary"

FORBIDDEN_REQUEST_FIELDS = frozenset(
    {
        "bindingId",
        "driver",
        "adapterId",
        "connectionRef",
        "table",
        "sql",
        "index",
        "secret",
    }
)

QUERY_FORBIDDEN_REQUEST_FIELDS = FORBIDDEN_REQUEST_FIELDS | {"idempotencyKey"}


class ContractRegistry:
    def __init__(self, plugins: tuple[WebUIContractPlugin, ...]) -> None:
        self._contracts: dict[tuple[str, str], Contract] = {}
        self._providers: dict[tuple[str, str], WebUIContractPlugin] = {}
        for plugin in plugins:
            for contract in plugin.contracts:
                key = (contract.contract_id, contract.version)
                if key in self._contracts:
                    raise ContractRuntimeError(
                        "duplicate_contract",
                        status_code=500,
                        user_message="Duplicate WebUI contract registration.",
                        admin_message=f"Duplicate contract {contract.contract_id}@{contract.version}",
                    )
                self._contracts[key] = contract
                self._providers[key] = plugin

    def get(self, contract_id: str, version: str = DEFAULT_CONTRACT_VERSION) -> Contract | None:
        return self._contracts.get((contract_id, version))

    def provider_for(self, contract_id: str, version: str = DEFAULT_CONTRACT_VERSION) -> WebUIContractPlugin | None:
        return self._providers.get((contract_id, version))
