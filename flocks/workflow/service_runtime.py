"""Runtime HTTP service for published workflows."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from flocks.hooks.execution import (
    ExecutionStopped,
    current_execution_context,
    execute_with_hooks,
)
from flocks.hooks.pipeline import HookPipeline
from flocks.mcp import MCP, get_manager
from flocks.utils.log import Log
from flocks.workflow.runner import RunWorkflowResult, run_workflow
from flocks.workflow.tool_context import build_workflow_tool_context

log = Log.create(service="workflow.service_runtime")
_SERVICE_API_KEY_ENV = "FLOCKS_WORKFLOW_SERVICE_API_KEY"


def api_key_fingerprint(api_key: str) -> str:
    """Return the secret-safe stable ID emitted for a validated service key."""
    return f"sha256:{hashlib.sha256(api_key.encode('utf-8')).hexdigest()}"


def _load_runtime_plugins() -> str | None:
    """Load generic plugins, returning only a generic critical error marker."""
    try:
        from flocks.plugin import PluginLoader

        result = PluginLoader.load_all(project_dir=Path.cwd())
        if result.has_critical_entrypoint_failure:
            return "critical plugin entrypoint failure"
    except Exception as exc:
        log.warning("workflow_service.plugins.load_failed", {"error": str(exc)})
    return None


class InvokeRequest(BaseModel):
    """Request payload for workflow invoke."""

    inputs: Dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None
    timeout_s: Optional[float] = None
    trace: bool = False
    ensure_requirements: bool = False


def create_service_app(
    *,
    workflow_json: Dict[str, Any],
    workflow_id: str,
    release_id: str,
    api_key: Optional[str] = None,
) -> FastAPI:
    """Build service app bound to one workflow snapshot."""
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _app.state.mcp_ready = False
        _app.state.mcp_error = None
        critical_plugin_error = _load_runtime_plugins()
        if critical_plugin_error is not None:
            _app.state.mcp_error = critical_plugin_error
        else:
            try:
                await MCP.init()
            except Exception as exc:
                _app.state.mcp_error = str(exc)
                log.warning("workflow_service.mcp.init_failed", {"error": str(exc)})
            else:
                _app.state.mcp_ready = True
        try:
            yield
        finally:
            try:
                await get_manager().shutdown()
            except Exception as exc:
                log.warning("workflow_service.mcp.shutdown_failed", {"error": str(exc)})

    app = FastAPI(title="Flocks Workflow Service", version="0.2.0", lifespan=lifespan)
    app.state.workflow_json = workflow_json
    app.state.workflow_id = workflow_id
    app.state.release_id = release_id
    app.state.api_key = api_key

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        payload = {
            "ok": bool(app.state.mcp_ready),
            "mcp_ready": bool(app.state.mcp_ready),
            "mcp_error": app.state.mcp_error,
            "workflow_id": app.state.workflow_id,
            "release_id": app.state.release_id,
        }
        if app.state.mcp_ready:
            return payload
        return JSONResponse(status_code=503, content=payload)

    @app.post("/invoke")
    async def invoke(
        req: InvokeRequest,
        x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
    ) -> Dict[str, Any]:
        started = time.time()
        expected_api_key = app.state.api_key
        if expected_api_key and (
            not x_api_key or not hmac.compare_digest(str(x_api_key), str(expected_api_key))
        ):
            raise HTTPException(status_code=401, detail="Invalid API key")

        if not app.state.mcp_ready:
            raise HTTPException(
                status_code=503,
                detail={
                    "request_id": req.request_id,
                    "workflow_id": app.state.workflow_id,
                    "release_id": app.state.release_id,
                    "status": "FAILED",
                    "error": app.state.mcp_error or "MCP subsystem is not ready",
                    "duration_ms": int((time.time() - started) * 1000),
                },
            )

        try:
            async def _effect() -> RunWorkflowResult:
                context_kwargs: dict[str, Any] = {}
                execution_context = current_execution_context()
                if execution_context:
                    context_kwargs["execution_context"] = execution_context
                tool_context = await build_workflow_tool_context(
                    workflow_id=app.state.workflow_id,
                    action_name="invoke",
                    **context_kwargs,
                )
                return await asyncio.to_thread(
                    run_workflow,
                    workflow=app.state.workflow_json,
                    inputs=req.inputs,
                    timeout_s=req.timeout_s,
                    trace=req.trace,
                    ensure_requirements=req.ensure_requirements,
                    tool_context=tool_context,
                )

            action_payload = {
                "operation": "workflow.service.invoke",
                "workflow_id": app.state.workflow_id,
                "release_id": app.state.release_id,
                "inputs": req.inputs,
            }
            ingress_payload = {
                **action_payload,
                "transport": "headless",
                "entry": "workflow_service",
            }
            if expected_api_key:
                ingress_payload["evidence"] = {
                    "auth_scheme": "api_key",
                    "api_key_id": api_key_fingerprint(str(expected_api_key)),
                }

            result = await execute_with_hooks(
                ingress_payload,
                lambda: execute_with_hooks(action_payload, _effect),
                before=HookPipeline.run_ingress_before,
                after=HookPipeline.run_ingress_after,
            )
            return {
                "request_id": req.request_id,
                "workflow_id": app.state.workflow_id,
                "release_id": app.state.release_id,
                "status": result.status,
                "run_id": result.run_id,
                "outputs": result.outputs,
                "error": result.error,
                "duration_ms": int((time.time() - started) * 1000),
            }
        except ExecutionStopped as exc:
            raise HTTPException(
                status_code=403,
                detail="Workflow invocation stopped by extension",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "request_id": req.request_id,
                    "workflow_id": app.state.workflow_id,
                    "release_id": app.state.release_id,
                    "status": "FAILED",
                    "error": str(exc),
                    "duration_ms": int((time.time() - started) * 1000),
                },
            ) from exc

    return app


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run workflow service runtime")
    parser.add_argument("--workflow", required=True, help="Path to workflow snapshot json")
    parser.add_argument("--workflow-id", required=True, help="Workflow id")
    parser.add_argument("--release-id", required=True, help="Release id")
    parser.add_argument("--host", default="127.0.0.1", help="Service host")
    parser.add_argument("--port", type=int, default=8000, help="Service port")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    with open(args.workflow, "r", encoding="utf-8") as f:
        workflow_json = json.load(f)

    app = create_service_app(
        workflow_json=workflow_json,
        workflow_id=args.workflow_id,
        release_id=args.release_id,
        api_key=os.getenv(_SERVICE_API_KEY_ENV),
    )

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
