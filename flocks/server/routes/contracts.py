"""Page data access contract routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from flocks.auth.context import AuthUser
from flocks.server.auth import require_user
from flocks.contracts.access.models import ContractRuntimeError
from flocks.contracts.access.runtime import OperationRuntime

router = APIRouter()
_runtime: OperationRuntime | None = None
_runtime_override = False
_runtime_signature: tuple[tuple[str, int, int], ...] = ()


@router.post("/contracts/webui/pages/{page_path:path}/access/{contract_id}/operations/{operation_name}")
async def execute_webui_contract_operation(
    page_path: str,
    contract_id: str,
    operation_name: str,
    body: dict[str, Any] | None = Body(default=None),
    user: AuthUser = Depends(require_user),
):
    try:
        runtime = _get_runtime()
        response = runtime.execute(
            page_id=page_path,
            contract_id=contract_id,
            operation_name=operation_name,
            payload=body,
            principal=user,
        )
    except ContractRuntimeError as exc:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.to_detail()})
    return JSONResponse(status_code=response.status_code, content=response.body)


def reset_route_dependencies(*, runtime: OperationRuntime | None = None) -> None:
    """Test helper to inject isolated route dependencies."""
    global _runtime, _runtime_override, _runtime_signature
    _runtime = runtime
    _runtime_override = runtime is not None
    _runtime_signature = _contract_plugin_signature()


def _get_runtime() -> OperationRuntime:
    global _runtime, _runtime_signature
    if _runtime_override and _runtime is not None:
        return _runtime

    signature = _contract_plugin_signature()
    if _runtime is None or signature != _runtime_signature:
        _runtime = OperationRuntime()
        _runtime_signature = signature
    return _runtime


def _contract_plugin_signature() -> tuple[tuple[str, int, int], ...]:
    roots = (
        Path.home() / ".flocks" / "plugins" / "contracts" / "access",
        Path.cwd() / ".flocks" / "plugins" / "contracts" / "access",
    )
    entries: list[tuple[str, int, int]] = []
    seen: set[Path] = set()
    for root in roots:
        resolved_root = root.resolve()
        if resolved_root in seen or not resolved_root.is_dir():
            continue
        seen.add(resolved_root)
        plugin_files = [
            path
            for suffix in ("*.py", "*.json", "*.yaml", "*.yml")
            for path in resolved_root.rglob(suffix)
        ]
        for path in sorted(plugin_files):
            try:
                relative_parts = path.relative_to(resolved_root).parts
            except ValueError:
                continue
            if any(part.startswith(".") or part == "__pycache__" for part in relative_parts):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((str(path.resolve()), stat.st_mtime_ns, stat.st_size))
    return tuple(entries)
