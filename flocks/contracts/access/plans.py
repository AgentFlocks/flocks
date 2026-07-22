"""Plan compilers for page data access contract operations."""

from __future__ import annotations

from typing import Any

from flocks.contracts.access.models import (
    Binding,
    ContractOperation,
    FieldDependencyPlan,
    MutationPlan,
    PolicyContext,
    PolicyEnforcementPlan,
    Predicate,
    QueryPlan,
    RuntimeContext,
    ContractRuntimeError,
)


class PolicyPlanCompiler:
    def compile(
        self,
        *,
        operation: ContractOperation,
        binding: Binding,
        policy_context: PolicyContext,
        params: dict[str, Any],
    ) -> PolicyEnforcementPlan:
        policy_predicates: list[Predicate] = []
        if policy_context.tenant_ids and operation.tenant_policy_field:
            policy_predicates.append(
                Predicate(
                    field=operation.tenant_policy_field,
                    operator="in",
                    values=policy_context.tenant_ids,
                    source="policy.tenantIds",
                    enforcement="driver-required",
                    filter_stage="driver-native",
                )
            )
        if policy_context.asset_groups and operation.asset_group_policy_field:
            policy_predicates.append(
                Predicate(
                    field=operation.asset_group_policy_field,
                    operator="in",
                    values=policy_context.asset_groups,
                    source="policy.assetGroups",
                    enforcement="driver-required",
                    filter_stage="driver-native",
                )
            )

        frontend_predicates = self._compile_frontend_predicates(operation, params)
        for predicate in (*policy_predicates, *frontend_predicates):
            if predicate.filter_stage != "driver-native":
                continue
            if predicate.field not in binding.driver_available_fields or predicate.field not in operation.filter_fields:
                raise ContractRuntimeError(
                    "policy_filter_not_enforceable",
                    status_code=400,
                    user_message="WebUI contract data source cannot enforce one of the requested filters.",
                    admin_message=f"Binding {binding.binding_id} cannot enforce {predicate.field} before adapter execution.",
                )

        return PolicyEnforcementPlan(
            policy_predicates=tuple(policy_predicates),
            frontend_predicates=tuple(frontend_predicates),
        )

    def _compile_frontend_predicates(
        self,
        operation: ContractOperation,
        params: dict[str, Any],
    ) -> tuple[Predicate, ...]:
        raw_filters = params.get("filters")
        if not isinstance(raw_filters, dict):
            return ()

        predicates: list[Predicate] = []
        for param_name, field_name in operation.filter_param_fields.items():
            values = _coerce_values(raw_filters.get(param_name))
            if not values:
                continue
            predicates.append(
                Predicate(
                    field=field_name,
                    operator="in",
                    values=values,
                    source=f"params.filters.{param_name}",
                    enforcement="native-or-post-filter",
                    filter_stage="driver-native",
                )
            )
        return tuple(predicates)


class FieldDependencyPlanCompiler:
    def compile(self, *, operation: ContractOperation, policy_plan: PolicyEnforcementPlan) -> FieldDependencyPlan:
        policy_fields = frozenset(predicate.field for predicate in policy_plan.policy_predicates)
        frontend_filter_fields = frozenset(predicate.field for predicate in policy_plan.frontend_predicates)
        driver_required_fields = frozenset(
            operation.adapter_required_fields
            | operation.identity_fields
            | policy_fields
            | operation.cursor_fields
            | operation.sort_fields
            | frontend_filter_fields
        )
        return FieldDependencyPlan(
            driver_required_fields=driver_required_fields,
            internal_fields=driver_required_fields,
            identity_fields=operation.identity_fields,
            policy_fields=policy_fields,
            cursor_fields=operation.cursor_fields,
            sort_fields=operation.sort_fields,
            filter_fields=frontend_filter_fields,
            public_fields=operation.public_fields,
        )


class QueryPlanCompiler:
    def compile(
        self,
        *,
        context: RuntimeContext,
        binding: Binding,
        operation: ContractOperation,
        params: dict[str, Any],
        policy_plan: PolicyEnforcementPlan,
        field_plan: FieldDependencyPlan,
    ) -> QueryPlan:
        limit = _read_int(params.get("limit"), operation.default_limit)
        limit = max(1, min(limit, operation.max_limit))
        return QueryPlan(
            context=context,
            binding=binding,
            operation=operation,
            params=params,
            policy_plan=policy_plan,
            field_plan=field_plan,
            limit=limit,
        )


class MutationPlanCompiler:
    def compile(
        self,
        *,
        context: RuntimeContext,
        binding: Binding,
        operation: ContractOperation,
        payload: dict[str, Any],
    ) -> MutationPlan:
        params = payload.get("params")
        if not isinstance(params, dict):
            raise ContractRuntimeError("invalid_request", user_message="Mutation params are required.")

        idempotency_key = payload.get("idempotencyKey")
        if operation.requires_idempotency_key and not isinstance(idempotency_key, str):
            raise ContractRuntimeError("idempotency_key_required", user_message="idempotencyKey is required for mutations.")

        expected_overlay_version = payload.get("expectedOverlayVersion")
        if operation.requires_expected_overlay_version and "expectedOverlayVersion" not in payload:
            raise ContractRuntimeError(
                "overlay_version_required",
                status_code=409,
                user_message="expectedOverlayVersion is required for this mutation.",
            )
        if expected_overlay_version is not None and not isinstance(expected_overlay_version, int):
            raise ContractRuntimeError("invalid_request", user_message="expectedOverlayVersion must be an integer or null.")

        entity_type = params.get("entityType")
        entity_id = params.get("entityId")
        if not isinstance(entity_type, str) or not isinstance(entity_id, str):
            raise ContractRuntimeError("invalid_request", user_message="entityType and entityId are required.")
        if operation.mutation_entity_types and entity_type not in operation.mutation_entity_types:
            raise ContractRuntimeError(
                "invalid_request",
                user_message="Mutation entity type is not allowed for this operation.",
            )

        return MutationPlan(
            context=context,
            binding=binding,
            operation=operation,
            params=params,
            entity_type=entity_type,
            entity_id=entity_id,
            idempotency_key=idempotency_key or "",
            expected_overlay_version=expected_overlay_version,
        )


def _coerce_values(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(item for item in value if item not in (None, ""))
    if value == "":
        return ()
    return (value,)


def _read_int(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return fallback
    return fallback
