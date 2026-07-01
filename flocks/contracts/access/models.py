"""Shared models for page data access contract operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

OperationType = Literal["query", "mutation"]
PredicateEnforcement = Literal["driver-required", "native-or-post-filter"]
FilterStage = Literal["driver-native", "post-filter"]


@dataclass(frozen=True)
class RuntimeContext:
    workspace_id: str
    page_id: str
    slot_id: str
    contract_id: str
    contract_version: str
    operation: str
    operation_type: OperationType
    request_id: str
    principal_ref: str
    policy_context: "PolicyContext"
    binding_id: str
    binding_version: int
    test_mode: bool = False


@dataclass(frozen=True)
class PolicyContext:
    tenant_ids: tuple[str, ...] = ()
    asset_groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContractOperation:
    name: str
    operation_type: OperationType
    adapter_required_fields: frozenset[str]
    identity_fields: frozenset[str]
    public_fields: frozenset[str]
    filter_fields: frozenset[str] = field(default_factory=frozenset)
    filter_param_fields: dict[str, str] = field(default_factory=dict)
    tenant_policy_field: str | None = None
    asset_group_policy_field: str | None = None
    cursor_fields: frozenset[str] = field(default_factory=frozenset)
    sort_fields: frozenset[str] = field(default_factory=frozenset)
    default_limit: int = 100
    max_limit: int = 1000
    requires_idempotency_key: bool = False
    requires_expected_overlay_version: bool = False
    mutation_entity_types: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Contract:
    contract_id: str
    version: str
    page_id: str
    operations: dict[str, ContractOperation]


@dataclass(frozen=True)
class Binding:
    binding_id: str
    binding_version: int
    page_id: str
    slot_id: str
    contract_id: str
    contract_version: str
    adapter_kind: str
    source_page_id: str
    source_root: Path
    driver_available_fields: frozenset[str]
    driver_allowlist_roots: tuple[Path, ...]
    driver_options: dict[str, Any] = field(default_factory=dict)
    capabilities: frozenset[str] = frozenset({"query"})


@dataclass(frozen=True)
class Predicate:
    field: str
    operator: str
    values: tuple[Any, ...]
    source: str
    enforcement: PredicateEnforcement
    filter_stage: FilterStage


@dataclass(frozen=True)
class PolicyEnforcementPlan:
    policy_predicates: tuple[Predicate, ...]
    frontend_predicates: tuple[Predicate, ...]

    @property
    def driver_predicates(self) -> tuple[Predicate, ...]:
        return tuple(
            predicate
            for predicate in (*self.policy_predicates, *self.frontend_predicates)
            if predicate.filter_stage == "driver-native"
        )

    @property
    def filter_stages_applied(self) -> list[dict[str, str]]:
        return [
            {
                "field": predicate.field,
                "source": predicate.source,
                "stage": predicate.filter_stage,
                "enforcement": predicate.enforcement,
            }
            for predicate in (*self.policy_predicates, *self.frontend_predicates)
        ]


@dataclass(frozen=True)
class FieldDependencyPlan:
    driver_required_fields: frozenset[str]
    internal_fields: frozenset[str]
    identity_fields: frozenset[str]
    policy_fields: frozenset[str]
    cursor_fields: frozenset[str]
    sort_fields: frozenset[str]
    filter_fields: frozenset[str]
    public_fields: frozenset[str]


@dataclass(frozen=True)
class QueryPlan:
    context: RuntimeContext
    binding: Binding
    operation: ContractOperation
    params: dict[str, Any]
    policy_plan: PolicyEnforcementPlan
    field_plan: FieldDependencyPlan
    limit: int

    @property
    def driver_projection(self) -> frozenset[str]:
        return self.field_plan.driver_required_fields


@dataclass(frozen=True)
class MutationPlan:
    context: RuntimeContext
    binding: Binding
    operation: ContractOperation
    params: dict[str, Any]
    entity_type: str
    entity_id: str
    idempotency_key: str
    expected_overlay_version: int | None
    write_through_enabled: bool = False


@dataclass(frozen=True)
class DriverResult:
    rows: list[dict[str, Any]]
    source_files: tuple[Path, ...]
    total_raw: int
    total_unique: int
    duplicates: int
    filtered_unique: int
    parse_errors: int = 0


@dataclass(frozen=True)
class InternalDataRow:
    raw: dict[str, Any]
    identity: dict[str, Any]


@dataclass(frozen=True)
class OperationResponse:
    status_code: int
    body: dict[str, Any]


@dataclass(frozen=True)
class WebUIContractPlugin:
    plugin_id: str
    contracts: tuple[Contract, ...]
    binding_resolver: Any
    adapter: Any
    response_pipeline: Any
    overlay_store: Any | None = None
    version: str = "1.0"
    source: str = ""


class ContractRuntimeError(Exception):
    """Structured contract error that can be rendered by HTTP routes."""

    def __init__(
        self,
        code: str,
        *,
        status_code: int = 400,
        user_message: str | None = None,
        admin_message: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(admin_message or user_message or code)
        self.code = code
        self.status_code = status_code
        self.user_message = user_message or code
        self.admin_message = admin_message or self.user_message
        self.request_id = request_id

    def to_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "userMessage": self.user_message,
            "adminMessage": self.admin_message,
            "requestId": self.request_id,
        }
