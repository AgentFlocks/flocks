"""Operation runtime for page data access contracts."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from flocks.auth.context import AuthUser
from flocks.contracts.access.discovery import discover_contract_plugins
from flocks.contracts.access.driver import DriverProxy
from flocks.contracts.access.models import (
    OperationResponse,
    PolicyContext,
    RuntimeContext,
    ContractRuntimeError,
    WebUIContractPlugin,
)
from flocks.contracts.access.pipeline import IdempotencyService, MutationPipeline, OverlayStore
from flocks.contracts.access.plans import (
    FieldDependencyPlanCompiler,
    MutationPlanCompiler,
    PolicyPlanCompiler,
    QueryPlanCompiler,
)
from flocks.contracts.access.registry import (
    DEFAULT_CONTRACT_VERSION,
    DEFAULT_SLOT_ID,
    FORBIDDEN_REQUEST_FIELDS,
    QUERY_FORBIDDEN_REQUEST_FIELDS,
    ContractRegistry,
)


NO_POLICY_SCOPE = "__flocks_no_policy_scope__"


class PolicyContextResolver:
    def resolve(self, principal: AuthUser | None) -> PolicyContext:
        if principal is not None and principal.role == "admin":
            return PolicyContext()
        if principal is None:
            return PolicyContext(tenant_ids=(NO_POLICY_SCOPE,), asset_groups=(NO_POLICY_SCOPE,))

        tenant_ids = _clean_policy_values(principal.tenant_ids)
        asset_groups = _clean_policy_values(principal.asset_groups)
        return PolicyContext(
            tenant_ids=tenant_ids or (NO_POLICY_SCOPE,),
            asset_groups=asset_groups or (NO_POLICY_SCOPE,),
        )


class OperationRuntime:
    def __init__(
        self,
        *,
        plugins: tuple[WebUIContractPlugin, ...] | None = None,
        registry: ContractRegistry | None = None,
        policy_context_resolver: PolicyContextResolver | None = None,
        driver_proxy: DriverProxy | None = None,
        overlay_store: OverlayStore | None = None,
        idempotency_service: IdempotencyService | None = None,
        project_dir: Path | None = None,
    ) -> None:
        discovered = plugins if plugins is not None else discover_contract_plugins(project_dir=project_dir)
        self._registry = registry or ContractRegistry(discovered)
        self._policy_context_resolver = policy_context_resolver or PolicyContextResolver()
        self._policy_plan_compiler = PolicyPlanCompiler()
        self._field_plan_compiler = FieldDependencyPlanCompiler()
        self._query_plan_compiler = QueryPlanCompiler()
        self._mutation_plan_compiler = MutationPlanCompiler()
        self._driver_proxy = driver_proxy or DriverProxy()
        self._overlay_store = overlay_store or OverlayStore()
        self._idempotency_service = idempotency_service or IdempotencyService()

    def execute(
        self,
        *,
        page_id: str,
        contract_id: str,
        operation_name: str,
        payload: dict[str, Any] | None,
        principal: AuthUser | None,
        contract_version: str = DEFAULT_CONTRACT_VERSION,
        slot_id: str = DEFAULT_SLOT_ID,
        test_mode: bool = False,
    ) -> OperationResponse:
        request_id = f"req-{uuid.uuid4().hex}"
        contract = self._registry.get(contract_id, contract_version)
        provider = self._registry.provider_for(contract_id, contract_version)
        if contract is None or contract.page_id != page_id:
            raise ContractRuntimeError(
                "contract_not_found",
                status_code=404,
                user_message="WebUI contract is not available.",
                request_id=request_id,
            )
        if provider is None:
            raise ContractRuntimeError(
                "contract_provider_not_found",
                status_code=404,
                user_message="WebUI contract provider is not available.",
                request_id=request_id,
            )
        operation = contract.operations.get(operation_name)
        if operation is None:
            raise ContractRuntimeError(
                "operation_not_found",
                status_code=404,
                user_message="WebUI contract operation is not available.",
                request_id=request_id,
            )

        body = payload or {}
        if not isinstance(body, dict):
            raise ContractRuntimeError("invalid_request", user_message="Operation body must be an object.", request_id=request_id)

        forbidden = QUERY_FORBIDDEN_REQUEST_FIELDS if operation.operation_type == "query" else FORBIDDEN_REQUEST_FIELDS
        forbidden_path = _find_forbidden_field(body, forbidden)
        if forbidden_path:
            raise ContractRuntimeError(
                "forbidden_request_field",
                status_code=400,
                user_message="Request contains fields that pages are not allowed to submit.",
                admin_message=f"Forbidden request field: {forbidden_path}",
                request_id=request_id,
            )

        params = body.get("params", {})
        if operation.operation_type == "query" and not isinstance(params, dict):
            raise ContractRuntimeError("invalid_request", user_message="Query params must be an object.", request_id=request_id)

        binding = provider.binding_resolver.resolve(
            page_id=page_id,
            slot_id=slot_id,
            contract_id=contract.contract_id,
            contract_version=contract.version,
        )
        if operation.operation_type not in binding.capabilities:
            raise ContractRuntimeError(
                "operation_not_supported",
                status_code=400,
                user_message="WebUI contract data source does not support this operation.",
                admin_message=(
                    f"Binding {binding.binding_id} capabilities do not include "
                    f"{operation.operation_type}."
                ),
                request_id=request_id,
            )
        policy_context = self._policy_context_resolver.resolve(principal)
        context = RuntimeContext(
            workspace_id="default",
            page_id=page_id,
            slot_id=slot_id,
            contract_id=contract.contract_id,
            contract_version=contract.version,
            operation=operation.name,
            operation_type=operation.operation_type,
            request_id=request_id,
            principal_ref=_principal_ref(principal),
            policy_context=policy_context,
            binding_id=binding.binding_id,
            binding_version=binding.binding_version,
            test_mode=test_mode,
        )

        try:
            if operation.operation_type == "query":
                return self._execute_query(
                    context=context,
                    binding=binding,
                    operation=operation,
                    params=params,
                    provider=provider,
                )
            return self._execute_mutation(
                context=context,
                binding=binding,
                operation=operation,
                payload=body,
                provider=provider,
            )
        except ContractRuntimeError as exc:
            if exc.request_id is None:
                exc.request_id = request_id
            raise

    def _execute_query(
        self,
        *,
        context,
        binding,
        operation,
        params: dict[str, Any],
        provider: WebUIContractPlugin,
    ) -> OperationResponse:
        policy_plan = self._policy_plan_compiler.compile(
            operation=operation,
            binding=binding,
            policy_context=context.policy_context,
            params=params,
        )
        field_plan = self._field_plan_compiler.compile(operation=operation, policy_plan=policy_plan)
        query_plan = self._query_plan_compiler.compile(
            context=context,
            binding=binding,
            operation=operation,
            params=params,
            policy_plan=policy_plan,
            field_plan=field_plan,
        )
        driver_result = self._driver_proxy.execute(query_plan)
        internal_rows = provider.adapter.normalize(driver_result)
        body = provider.response_pipeline.run_query(
            context=context,
            binding_source_page_id=binding.source_page_id,
            driver_result=driver_result,
            rows=internal_rows,
            filter_stages_applied=policy_plan.filter_stages_applied,
        )
        return OperationResponse(status_code=200, body=body)

    def _execute_mutation(
        self,
        *,
        context,
        binding,
        operation,
        payload: dict[str, Any],
        provider: WebUIContractPlugin,
    ) -> OperationResponse:
        mutation_plan = self._mutation_plan_compiler.compile(
            context=context,
            binding=binding,
            operation=operation,
            payload=payload,
        )
        pipeline = MutationPipeline(
            provider.overlay_store or self._overlay_store,
            self._idempotency_service,
        )
        return OperationResponse(status_code=200, body=pipeline.run(mutation_plan))


class BindingTestHarness:
    def __init__(self, runtime: OperationRuntime | None = None) -> None:
        self._runtime = runtime or OperationRuntime()

    def run(self, *, page_id: str, contract_id: str, operation_name: str, profiles: tuple[AuthUser | None, ...]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for profile in profiles:
            try:
                response = self._runtime.execute(
                    page_id=page_id,
                    contract_id=contract_id,
                    operation_name=operation_name,
                    payload={"params": {"limit": 1}},
                    principal=profile,
                    test_mode=True,
                )
                results.append({"ok": True, "statusCode": response.status_code})
            except ContractRuntimeError as exc:
                results.append({"ok": False, "statusCode": exc.status_code, "error": exc.to_detail()})
        return results


def _find_forbidden_field(value: Any, forbidden: frozenset[str], path: str = "") -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{path}.{key}" if path else str(key)
            if key in forbidden:
                return current
            nested = _find_forbidden_field(item, forbidden, current)
            if nested:
                return nested
    elif isinstance(value, list):
        for index, item in enumerate(value):
            nested = _find_forbidden_field(item, forbidden, f"{path}[{index}]")
            if nested:
                return nested
    return ""


def _principal_ref(principal: AuthUser | None) -> str:
    if principal is None:
        return "principal:anonymous"
    return f"principal:user:{principal.id}"


def _clean_policy_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(value.strip() for value in values if isinstance(value, str) and value.strip())
