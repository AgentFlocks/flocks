"""Unified trigger runtime with legacy manager compatibility."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from flocks.utils.log import Log
from flocks.workflow.execution_store import (
    compact_history_for_storage,
    compact_outputs_for_storage,
    create_execution_record,
    record_execution_result,
    resolve_execution_outcome,
)
from flocks.workflow.fs_store import read_workflow_dir, workflow_scan_dirs
from flocks.workflow.execution_manager import run_workflow_managed
from flocks.workflow.store import WorkflowStore

from .compat import (
    LEGACY_KAFKA_CONFIG_PREFIX,
    LEGACY_POLLER_CONFIG_PREFIX,
    LEGACY_SYSLOG_CONFIG_PREFIX,
    kafka_trigger_to_legacy_config,
    schedule_trigger_to_legacy_config,
    syslog_trigger_to_legacy_config,
    trigger_to_legacy_config,
)
from .custom_loader import list_trigger_plugins, load_trigger_plugin_module
from .dispatcher import EventDispatcher, TriggerDispatchError, build_trigger_event
from .models import (
    TriggerDefinition,
    TriggerEvent,
    TriggerRuntimeStatus,
    workflow_json_declares_triggers,
    workflow_trigger_definitions_from_json,
)

log = Log.create(service="workflow.trigger.runtime")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _config_kind_from_legacy_key(key: str) -> Optional[str]:
    if key.startswith(LEGACY_POLLER_CONFIG_PREFIX):
        return "workflow_poller_config"
    if key.startswith(LEGACY_SYSLOG_CONFIG_PREFIX):
        return "workflow_syslog_config"
    if key.startswith(LEGACY_KAFKA_CONFIG_PREFIX):
        return "workflow_kafka_config"
    return None


class TriggerRuntime:
    """Unified trigger runtime that wraps legacy managers and custom adapters."""

    def __init__(self) -> None:
        self._dispatcher = EventDispatcher()
        self._custom_adapter_tasks: Dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._custom_adapters: Dict[tuple[str, str], Any] = {}
        self._custom_status: Dict[tuple[str, str], Dict[str, Any]] = {}
        self._custom_adapter_signatures: Dict[tuple[str, str], str] = {}

    def _iter_workflows(self) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for root, source in workflow_scan_dirs():
            if not root.is_dir():
                continue
            for entry in sorted(root.iterdir()):
                if not entry.is_dir():
                    continue
                data = read_workflow_dir(entry, entry.name, source)
                if data is not None:
                    merged[entry.name] = data
        return list(merged.values())

    async def _write_disabled_legacy_configs(self, workflow_id: str) -> None:
        now_ms = _now_ms()
        await WorkflowStore.put_config(
            workflow_id,
            {"workflowId": workflow_id, "enabled": False, "updatedAt": now_ms},
            kind="workflow_poller_config",
        )
        await WorkflowStore.put_config(
            workflow_id,
            {"workflowId": workflow_id, "enabled": False, "updatedAt": now_ms},
            kind="workflow_syslog_config",
        )
        await WorkflowStore.put_config(
            workflow_id,
            {"workflowId": workflow_id, "enabled": False, "updatedAt": now_ms},
            kind="workflow_kafka_config",
        )

    @staticmethod
    def _trigger_signature(trigger: TriggerDefinition) -> str:
        payload = trigger.model_dump(mode="json", exclude_none=True)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    async def _sync_legacy_configs_from_workflow(
        self, workflow_id: str, workflow_json: Dict[str, Any]
    ) -> List[TriggerDefinition]:
        triggers = workflow_trigger_definitions_from_json(workflow_json)
        if not triggers:
            if workflow_json_declares_triggers(workflow_json):
                await self._write_disabled_legacy_configs(workflow_id)
            return []

        by_type = {trigger.type: trigger for trigger in triggers}
        for trigger in triggers:
            key, value = trigger_to_legacy_config(workflow_id, trigger)
            if key and value is not None:
                kind = _config_kind_from_legacy_key(key)
                if kind:
                    await WorkflowStore.put_config(workflow_id, value, kind=kind)

        if "schedule" not in by_type:
            await WorkflowStore.put_config(
                workflow_id,
                {"workflowId": workflow_id, "enabled": False, "updatedAt": _now_ms()},
                kind="workflow_poller_config",
            )
        if "syslog" not in by_type:
            await WorkflowStore.put_config(
                workflow_id,
                {"workflowId": workflow_id, "enabled": False, "updatedAt": _now_ms()},
                kind="workflow_syslog_config",
            )
        if "kafka" not in by_type:
            await WorkflowStore.put_config(
                workflow_id,
                {"workflowId": workflow_id, "enabled": False, "updatedAt": _now_ms()},
                kind="workflow_kafka_config",
            )
        return triggers

    async def start_all(self) -> None:
        for workflow in self._iter_workflows():
            try:
                await self._sync_legacy_configs_from_workflow(workflow["id"], workflow.get("workflowJson") or {})
            except Exception as exc:
                log.warning("trigger.sync_legacy.failed", {"workflow_id": workflow.get("id"), "error": str(exc)})

        from flocks.ingest.syslog.manager import default_manager as syslog_manager
        from flocks.ingest.kafka.manager import default_manager as kafka_manager
        from flocks.workflow.poller_manager import default_manager as poller_manager

        await syslog_manager.start_all()
        await kafka_manager.start_all()
        await poller_manager.start_all()

        for workflow in self._iter_workflows():
            await self._start_custom_adapters_for_workflow(workflow["id"], workflow.get("workflowJson") or {})

    async def stop_all(self) -> None:
        from flocks.ingest.syslog.manager import default_manager as syslog_manager
        from flocks.ingest.kafka.manager import default_manager as kafka_manager
        from flocks.workflow.poller_manager import default_manager as poller_manager

        for workflow_id, trigger_id in list(self._custom_adapter_tasks.keys()):
            await self._stop_custom_adapter(workflow_id, trigger_id)

        await syslog_manager.stop_all()
        await kafka_manager.stop_all()
        await poller_manager.stop_all()

    async def restart_workflow(
        self,
        workflow_id: str,
        workflow_json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if workflow_json is None:
            workflow = next((item for item in self._iter_workflows() if item.get("id") == workflow_id), None)
            workflow_json = (workflow or {}).get("workflowJson") or {}
        triggers = await self._sync_legacy_configs_from_workflow(workflow_id, workflow_json or {})

        from flocks.ingest.syslog.manager import default_manager as syslog_manager
        from flocks.ingest.kafka.manager import default_manager as kafka_manager
        from flocks.workflow.poller_manager import default_manager as poller_manager

        statuses: Dict[str, Any] = {}
        by_type = {trigger.type: trigger for trigger in triggers}

        if "syslog" in by_type:
            statuses["syslog"] = await syslog_manager.restart_workflow(workflow_id)
        else:
            await syslog_manager.stop_workflow(workflow_id)
            statuses["syslog"] = {"state": "stopped", "error": None}
        if "kafka" in by_type:
            statuses["kafka"] = await kafka_manager.restart_workflow(workflow_id)
        else:
            await kafka_manager.stop_workflow(workflow_id)
            statuses["kafka"] = {"state": "stopped", "error": None}
        if "schedule" in by_type:
            statuses["schedule"] = await poller_manager.restart_workflow(workflow_id)
        else:
            await poller_manager.stop_workflow(workflow_id)
            statuses["schedule"] = {"state": "stopped", "error": None}

        await self._start_custom_adapters_for_workflow(workflow_id, workflow_json or {})
        return statuses

    async def _execute_workflow(
        self,
        *,
        workflow_id: str,
        workflow_json: Dict[str, Any],
        trigger: TriggerDefinition,
        mapped_inputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        exec_data = await create_execution_record(
            workflow_id,
            input_params=mapped_inputs,
        )
        exec_id = exec_data["id"]
        started_at = time.time()
        try:
            result = await run_workflow_managed(
                workflow_id=workflow_id,
                workflow=workflow_json,
                inputs=mapped_inputs,
                exec_id=exec_id,
                trace=False,
            )
            status_value, error_message = resolve_execution_outcome(result)
            exec_data.update(
                {
                    "status": status_value,
                    "outputResults": compact_outputs_for_storage(result.outputs),
                    "finishedAt": _now_ms(),
                    "duration": time.time() - started_at,
                    "errorMessage": error_message,
                    "executionLog": compact_history_for_storage(result.history),
                    "currentNodeId": result.last_node_id,
                    "currentPhase": status_value,
                    "currentStepIndex": result.steps,
                    "triggerId": trigger.id,
                    "triggerType": trigger.type,
                    "deliveryId": mapped_inputs.get("_flocks", {}).get("trigger", {}).get("deliveryId"),
                    "attempt": mapped_inputs.get("_flocks", {}).get("trigger", {}).get("attempt"),
                    "triggerSource": mapped_inputs.get("_flocks", {}).get("trigger", {}).get("source"),
                }
            )
        except Exception as exc:
            exec_data.update(
                {
                    "status": "error",
                    "finishedAt": _now_ms(),
                    "duration": time.time() - started_at,
                    "errorMessage": str(exc),
                    "triggerId": trigger.id,
                    "triggerType": trigger.type,
                    "deliveryId": mapped_inputs.get("_flocks", {}).get("trigger", {}).get("deliveryId"),
                    "attempt": mapped_inputs.get("_flocks", {}).get("trigger", {}).get("attempt"),
                    "triggerSource": mapped_inputs.get("_flocks", {}).get("trigger", {}).get("source"),
                }
            )
        await record_execution_result(workflow_id, exec_id, exec_data)
        return exec_data

    async def dispatch_event(
        self,
        *,
        workflow_id: str,
        workflow_json: Dict[str, Any],
        trigger: TriggerDefinition,
        event: TriggerEvent,
    ) -> Dict[str, Any]:
        async def _executor(mapped_inputs: Dict[str, Any]) -> Dict[str, Any]:
            return await self._execute_workflow(
                workflow_id=workflow_id,
                workflow_json=workflow_json,
                trigger=trigger,
                mapped_inputs=mapped_inputs,
            )

        return await self._dispatcher.dispatch(trigger=trigger, event=event, executor=_executor)

    async def _stop_custom_adapter(self, workflow_id: str, trigger_id: str) -> None:
        key = (workflow_id, trigger_id)
        adapter = self._custom_adapters.pop(key, None)
        task = self._custom_adapter_tasks.pop(key, None)
        if adapter is not None and hasattr(adapter, "stop"):
            try:
                result = adapter.stop()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass
        self._custom_adapter_signatures.pop(key, None)
        self._custom_status[key] = {
            "workflowId": workflow_id,
            "triggerId": trigger_id,
            "triggerType": "custom_adapter",
            "state": "stopped",
            "error": None,
        }

    async def _start_custom_adapters_for_workflow(self, workflow_id: str, workflow_json: Dict[str, Any]) -> None:
        triggers = workflow_trigger_definitions_from_json(workflow_json)
        desired_signatures = {
            (workflow_id, trigger.id or ""): self._trigger_signature(trigger)
            for trigger in triggers
            if trigger.type == "custom_adapter" and trigger.enabled
        }
        for active_workflow_id, active_trigger_id in list(self._custom_adapter_tasks.keys()):
            key = (active_workflow_id, active_trigger_id)
            if active_workflow_id != workflow_id:
                continue
            if key not in desired_signatures:
                await self._stop_custom_adapter(active_workflow_id, active_trigger_id)
                continue
            if self._custom_adapter_signatures.get(key) != desired_signatures[key]:
                await self._stop_custom_adapter(active_workflow_id, active_trigger_id)

        for trigger in triggers:
            if trigger.type != "custom_adapter" or not trigger.enabled:
                continue
            key = (workflow_id, trigger.id or "")
            trigger_signature = desired_signatures[key]
            if key in self._custom_adapter_tasks and self._custom_adapter_signatures.get(key) == trigger_signature:
                continue
            plugin_id = str(
                (trigger.source or {}).get("adapterId") or (trigger.source or {}).get("pluginId") or ""
            ).strip()
            plugin_spec = next((item for item in list_trigger_plugins() if item.get("id") == plugin_id), None)
            if plugin_spec is None:
                self._custom_status[key] = {
                    "workflowId": workflow_id,
                    "triggerId": trigger.id,
                    "triggerType": trigger.type,
                    "state": "failed",
                    "error": f"custom trigger plugin not found: {plugin_id}",
                }
                continue
            module = load_trigger_plugin_module(plugin_spec)
            if module is None:
                self._custom_status[key] = {
                    "workflowId": workflow_id,
                    "triggerId": trigger.id,
                    "triggerType": trigger.type,
                    "state": "failed",
                    "error": "failed to load custom trigger plugin module",
                }
                continue
            try:
                adapter = None
                if hasattr(module, "create_trigger_adapter"):
                    adapter = module.create_trigger_adapter(trigger.model_dump(mode="json"))
                elif hasattr(module, "TriggerAdapter"):
                    adapter = module.TriggerAdapter(trigger.model_dump(mode="json"))
                if adapter is None:
                    raise RuntimeError("plugin must expose create_trigger_adapter() or TriggerAdapter")
            except Exception as exc:
                self._custom_status[key] = {
                    "workflowId": workflow_id,
                    "triggerId": trigger.id,
                    "triggerType": trigger.type,
                    "state": "failed",
                    "error": str(exc),
                }
                continue

            async def _emit(payload: Any, *, _trigger: TriggerDefinition = trigger) -> Dict[str, Any]:
                event = (
                    payload
                    if isinstance(payload, TriggerEvent)
                    else build_trigger_event(
                        workflow_id=workflow_id,
                        trigger=_trigger,
                        body=payload,
                        raw=payload,
                    )
                )
                try:
                    result = await self.dispatch_event(
                        workflow_id=workflow_id,
                        workflow_json=workflow_json,
                        trigger=_trigger,
                        event=event,
                    )
                    self._custom_status[key] = {
                        "workflowId": workflow_id,
                        "triggerId": _trigger.id,
                        "triggerType": _trigger.type,
                        "state": "running",
                        "error": None,
                        "lastDeliveryId": event.source.deliveryId,
                        "lastMatched": result.get("matched"),
                    }
                    return result
                except TriggerDispatchError as exc:
                    self._custom_status[key] = {
                        "workflowId": workflow_id,
                        "triggerId": _trigger.id,
                        "triggerType": _trigger.type,
                        "state": "failed",
                        "error": str(exc),
                    }
                    raise

            async def _runner() -> None:
                self._custom_status[key] = {
                    "workflowId": workflow_id,
                    "triggerId": trigger.id,
                    "triggerType": trigger.type,
                    "state": "running",
                    "error": None,
                    "pluginId": plugin_id,
                }
                try:
                    result = adapter.start(trigger.model_dump(mode="json"), _emit)
                    if asyncio.iscoroutine(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._custom_status[key] = {
                        "workflowId": workflow_id,
                        "triggerId": trigger.id,
                        "triggerType": trigger.type,
                        "state": "failed",
                        "error": str(exc),
                        "pluginId": plugin_id,
                    }

            self._custom_adapters[key] = adapter
            self._custom_adapter_signatures[key] = trigger_signature
            self._custom_adapter_tasks[key] = asyncio.create_task(
                _runner(),
                name=f"trigger-custom-{workflow_id}-{trigger.id}",
            )

    async def get_trigger_status(self, workflow_id: str, trigger: TriggerDefinition) -> Dict[str, Any]:
        if trigger.type == "syslog":
            from flocks.ingest.syslog.manager import default_manager as syslog_manager

            status = syslog_manager.get_listener_status(workflow_id)
            return {"workflowId": workflow_id, "triggerId": trigger.id, "triggerType": trigger.type, **status}
        if trigger.type == "kafka":
            from flocks.ingest.kafka.manager import default_manager as kafka_manager

            status = kafka_manager.get_consumer_status(workflow_id)
            return {"workflowId": workflow_id, "triggerId": trigger.id, "triggerType": trigger.type, **status}
        if trigger.type == "schedule":
            from flocks.workflow.poller_manager import default_manager as poller_manager

            status = poller_manager.get_status(workflow_id)
            return {"workflowId": workflow_id, "triggerId": trigger.id, "triggerType": trigger.type, **status}
        if trigger.type in {"webhook", "custom_webhook"}:
            return {
                "workflowId": workflow_id,
                "triggerId": trigger.id,
                "triggerType": trigger.type,
                "state": "ready" if trigger.enabled else "stopped",
                "error": None,
                "path": (trigger.source or {}).get("path"),
                "method": (trigger.source or {}).get("method", "POST"),
            }
        if trigger.type == "custom_adapter":
            return self._custom_status.get(
                (workflow_id, trigger.id or ""),
                {
                    "workflowId": workflow_id,
                    "triggerId": trigger.id,
                    "triggerType": trigger.type,
                    "state": "stopped",
                    "error": None,
                },
            )
        return {
            "workflowId": workflow_id,
            "triggerId": trigger.id,
            "triggerType": trigger.type,
            "state": "ready" if trigger.enabled else "stopped",
            "error": None,
        }

    async def get_workflow_trigger_statuses(
        self,
        workflow_id: str,
        workflow_json: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        triggers = workflow_trigger_definitions_from_json(workflow_json)
        return [await self.get_trigger_status(workflow_id, trigger) for trigger in triggers]

    def list_plugin_specs(self) -> List[Dict[str, Any]]:
        return list_trigger_plugins()


default_runtime = TriggerRuntime()
