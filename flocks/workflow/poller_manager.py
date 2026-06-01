"""Lifecycle manager for workflow pollers.

This mirrors the Kafka/syslog managers: one background poller task per workflow
id that periodically triggers ``run_workflow`` with configured inputs.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict

from flocks.storage.storage import Storage
from flocks.utils.log import Log
from flocks.workflow.fs_store import read_workflow_from_fs
from flocks.workflow.runner import RunWorkflowResult, run_workflow

WORKFLOW_POLLER_CONFIG_PREFIX = "workflow_poller_config/"
DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_TIMEOUT_SECONDS = 7200

log = Log.create(service="workflow.poller")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _today_string() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class WorkflowPollerManager:
    """Manage one background poller loop per workflow id."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._abort_events: dict[str, asyncio.Event] = {}
        self._run_tasks: dict[str, set[asyncio.Task[Any]]] = {}
        self._run_cancel_events: dict[str, set[threading.Event]] = {}
        self._status: dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _config_key(workflow_id: str) -> str:
        return f"{WORKFLOW_POLLER_CONFIG_PREFIX}{workflow_id}"

    def _normalize_config(self, workflow_id: str, data: Any) -> Dict[str, Any]:
        raw = data if isinstance(data, dict) else {}
        interval_seconds = int(raw.get("intervalSeconds") or DEFAULT_INTERVAL_SECONDS)
        timeout_seconds = int(raw.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS)
        inputs = raw.get("inputs") if isinstance(raw.get("inputs"), dict) else {}
        return {
            "workflowId": workflow_id,
            "enabled": bool(raw.get("enabled")),
            "intervalSeconds": max(1, interval_seconds),
            "timeoutSeconds": max(1, timeout_seconds),
            "noOverlap": bool(raw.get("noOverlap", True)),
            "inputs": dict(inputs),
            "updatedAt": raw.get("updatedAt"),
        }

    def _cleanup_done_runs(self, workflow_id: str) -> int:
        tasks = self._run_tasks.get(workflow_id)
        if not tasks:
            return 0
        active_tasks = {task for task in tasks if not task.done()}
        if active_tasks:
            self._run_tasks[workflow_id] = active_tasks
            return len(active_tasks)
        self._run_tasks.pop(workflow_id, None)
        return 0

    def _register_run_task(self, workflow_id: str, task: asyncio.Task[Any]) -> None:
        task_set = self._run_tasks.setdefault(workflow_id, set())
        task_set.add(task)

        def _discard(done_task: asyncio.Task[Any]) -> None:
            tasks = self._run_tasks.get(workflow_id)
            if tasks is not None:
                tasks.discard(done_task)
                if not tasks:
                    self._run_tasks.pop(workflow_id, None)

        task.add_done_callback(_discard)

    def _build_inputs(self, config: Dict[str, Any]) -> Dict[str, Any]:
        inputs = dict(config.get("inputs") or {})
        if not str(inputs.get("input_date") or "").strip():
            inputs["input_date"] = _today_string()
        inputs["_trigger"] = "poller"
        inputs["_poller_run_id"] = f"poller-{_now_ms()}-{uuid.uuid4().hex[:8]}"
        return inputs

    def _summarize_outputs(self, outputs: Any) -> Dict[str, Any]:
        if not isinstance(outputs, dict):
            return {}

        summary: Dict[str, Any] = {}
        load_stats = outputs.get("load_stats")
        if isinstance(load_stats, dict) and isinstance(load_stats.get("record_count"), int):
            summary["selectedCount"] = load_stats["record_count"]

        if isinstance(outputs.get("processed_mark_count"), int):
            summary["processedMarkCount"] = outputs["processed_mark_count"]

        if isinstance(outputs.get("kafka_message_count"), int):
            summary["kafkaMessageCount"] = outputs["kafka_message_count"]

        channel_status = outputs.get("channel_notify_status")
        if channel_status is not None:
            summary["channelNotifyStatus"] = channel_status

        return summary

    def _base_status(self, workflow_id: str) -> Dict[str, Any]:
        return {
            "workflowId": workflow_id,
            "state": "stopped",
            "error": None,
            "activeRuns": 0,
            "lastRunAt": None,
            "lastStatus": None,
            "lastError": None,
            "lastDurationMs": None,
            "selectedCount": None,
            "processedMarkCount": None,
            "channelNotifyStatus": None,
            "kafkaMessageCount": None,
            "nextRunAt": None,
            "lastRunId": None,
        }

    def get_status(self, workflow_id: str) -> Dict[str, Any]:
        status = dict(self._base_status(workflow_id))
        status.update(self._status.get(workflow_id) or {})
        status["activeRuns"] = self._cleanup_done_runs(workflow_id)
        if workflow_id not in self._tasks and status.get("state") == "running":
            status["state"] = "stopped"
            status["nextRunAt"] = None
        return status

    async def start_all(self) -> None:
        try:
            keys = await Storage.list_keys(WORKFLOW_POLLER_CONFIG_PREFIX)
        except Exception as exc:
            log.warning("poller.list_keys_failed", {"error": str(exc)})
            return

        for key in keys:
            if not key.startswith(WORKFLOW_POLLER_CONFIG_PREFIX):
                continue
            workflow_id = key[len(WORKFLOW_POLLER_CONFIG_PREFIX):]
            if not workflow_id:
                continue
            try:
                data = await Storage.read(key)
            except Exception as exc:
                log.warning("poller.config_read_failed", {"key": key, "error": str(exc)})
                continue
            if isinstance(data, dict) and data.get("enabled"):
                await self.restart_workflow(workflow_id)

    async def stop_all(self) -> None:
        for workflow_id in list(self._tasks.keys()):
            await self.stop_workflow(workflow_id)

    async def stop_workflow(self, workflow_id: str) -> None:
        abort_event = self._abort_events.get(workflow_id)
        if abort_event is not None:
            abort_event.set()

        for cancel_event in self._run_cancel_events.pop(workflow_id, set()):
            cancel_event.set()

        task = self._tasks.pop(workflow_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        run_tasks = list(self._run_tasks.pop(workflow_id, set()))
        for run_task in run_tasks:
            if not run_task.done():
                run_task.cancel()
        if run_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*run_tasks, return_exceptions=True),
                    timeout=1.0,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        self._abort_events.pop(workflow_id, None)
        current = self._status.get(workflow_id) or self._base_status(workflow_id)
        current["state"] = "stopped"
        current["error"] = None
        current["nextRunAt"] = None
        current["activeRuns"] = 0
        self._status[workflow_id] = current

    async def restart_workflow(self, workflow_id: str) -> Dict[str, Any]:
        await self.stop_workflow(workflow_id)
        try:
            stored = await Storage.read(self._config_key(workflow_id))
        except Exception as exc:
            log.warning("poller.restart_read_failed", {"workflow_id": workflow_id, "error": str(exc)})
            return {"workflowId": workflow_id, "state": "failed", "error": str(exc)}

        config = self._normalize_config(workflow_id, stored)
        if not config.get("enabled"):
            self._status[workflow_id] = {
                **self._base_status(workflow_id),
                "workflowId": workflow_id,
                "state": "stopped",
                "error": None,
            }
            return self.get_status(workflow_id)

        wf_data = read_workflow_from_fs(workflow_id)
        if not wf_data:
            err = "workflow_not_found"
            self._status[workflow_id] = {
                **self.get_status(workflow_id),
                "workflowId": workflow_id,
                "state": "failed",
                "error": err,
            }
            return self.get_status(workflow_id)

        workflow_json = wf_data.get("workflowJson")
        if not workflow_json:
            err = "workflow_json_missing"
            self._status[workflow_id] = {
                **self.get_status(workflow_id),
                "workflowId": workflow_id,
                "state": "failed",
                "error": err,
            }
            return self.get_status(workflow_id)

        abort_event = asyncio.Event()
        self._abort_events[workflow_id] = abort_event
        self._status[workflow_id] = {
            **self.get_status(workflow_id),
            "workflowId": workflow_id,
            "state": "running",
            "error": None,
            "enabled": True,
            "intervalSeconds": config["intervalSeconds"],
            "timeoutSeconds": config["timeoutSeconds"],
            "noOverlap": config["noOverlap"],
            "nextRunAt": _now_ms(),
        }
        task = asyncio.create_task(
            self._poller_loop(workflow_id, workflow_json, config, abort_event),
            name=f"workflow-poller-{workflow_id}",
        )
        self._tasks[workflow_id] = task
        return self.get_status(workflow_id)

    async def run_once(self, workflow_id: str) -> Dict[str, Any]:
        try:
            stored = await Storage.read(self._config_key(workflow_id))
        except Exception as exc:
            log.warning("poller.run_once_read_failed", {"workflow_id": workflow_id, "error": str(exc)})
            current = self.get_status(workflow_id)
            current["lastStatus"] = "failed"
            current["lastError"] = str(exc)
            return current

        config = self._normalize_config(workflow_id, stored)
        wf_data = read_workflow_from_fs(workflow_id)
        if not wf_data:
            current = self.get_status(workflow_id)
            current["state"] = "failed" if workflow_id in self._tasks else current.get("state", "stopped")
            current["lastStatus"] = "failed"
            current["lastError"] = "workflow_not_found"
            self._status[workflow_id] = current
            return self.get_status(workflow_id)

        workflow_json = wf_data.get("workflowJson")
        if not workflow_json:
            current = self.get_status(workflow_id)
            current["state"] = "failed" if workflow_id in self._tasks else current.get("state", "stopped")
            current["lastStatus"] = "failed"
            current["lastError"] = "workflow_json_missing"
            self._status[workflow_id] = current
            return self.get_status(workflow_id)

        return await self._execute_run(workflow_id, workflow_json, config)

    async def _poller_loop(
        self,
        workflow_id: str,
        workflow_json: Dict[str, Any],
        config: Dict[str, Any],
        abort_event: asyncio.Event,
    ) -> None:
        interval_seconds = config["intervalSeconds"]
        try:
            while not abort_event.is_set():
                await self._schedule_run(workflow_id, workflow_json, config)
                next_run_at = _now_ms() + interval_seconds * 1000
                current = self._status.get(workflow_id) or self._base_status(workflow_id)
                current["nextRunAt"] = next_run_at
                current["activeRuns"] = self._cleanup_done_runs(workflow_id)
                self._status[workflow_id] = current
                try:
                    await asyncio.wait_for(abort_event.wait(), timeout=interval_seconds)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            current = self._status.get(workflow_id) or self._base_status(workflow_id)
            current["state"] = "failed"
            current["error"] = str(exc)
            current["nextRunAt"] = None
            self._status[workflow_id] = current
            log.warning("poller.loop_failed", {"workflow_id": workflow_id, "error": str(exc)})
        finally:
            if workflow_id in self._tasks and self._tasks.get(workflow_id) is asyncio.current_task():
                current = self._status.get(workflow_id) or self._base_status(workflow_id)
                if current.get("state") != "failed":
                    current["state"] = "stopped"
                    current["error"] = None
                current["nextRunAt"] = None
                current["activeRuns"] = self._cleanup_done_runs(workflow_id)
                self._status[workflow_id] = current

    async def _schedule_run(
        self,
        workflow_id: str,
        workflow_json: Dict[str, Any],
        config: Dict[str, Any],
    ) -> None:
        active_runs = self._cleanup_done_runs(workflow_id)
        if config.get("noOverlap", True) and active_runs > 0:
            current = self._status.get(workflow_id) or self._base_status(workflow_id)
            current["lastStatus"] = "skipped"
            current["lastError"] = "previous_run_still_active"
            current["activeRuns"] = active_runs
            self._status[workflow_id] = current
            return

        run_task = asyncio.create_task(
            self._execute_run(workflow_id, workflow_json, config),
            name=f"workflow-poller-run-{workflow_id}",
        )
        self._register_run_task(workflow_id, run_task)

    async def _execute_run(
        self,
        workflow_id: str,
        workflow_json: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        started_at_ms = _now_ms()
        cancel_event = threading.Event()
        cancel_events = self._run_cancel_events.setdefault(workflow_id, set())
        cancel_events.add(cancel_event)
        current = self._status.get(workflow_id) or self._base_status(workflow_id)
        current["lastRunAt"] = started_at_ms
        current["activeRuns"] = self._cleanup_done_runs(workflow_id)
        self._status[workflow_id] = current

        try:
            result = await asyncio.to_thread(
                run_workflow,
                workflow=workflow_json,
                inputs=self._build_inputs(config),
                timeout_s=config["timeoutSeconds"],
                trace=False,
                cancel=cancel_event.is_set,
            )
            if not isinstance(result, RunWorkflowResult):
                result = RunWorkflowResult(status="failed", error="invalid_run_result")
            duration_ms = _now_ms() - started_at_ms
            summary = self._summarize_outputs(result.outputs)
            current = self._status.get(workflow_id) or self._base_status(workflow_id)
            current.update(summary)
            current["lastRunAt"] = started_at_ms
            current["lastDurationMs"] = duration_ms
            current["lastRunId"] = result.run_id
            current["lastStatus"] = result.status
            current["lastError"] = result.error
            current["activeRuns"] = self._cleanup_done_runs(workflow_id)
            if workflow_id in self._tasks and current.get("state") != "failed":
                current["state"] = "running"
                current["error"] = None
            self._status[workflow_id] = current
        except Exception as exc:
            duration_ms = _now_ms() - started_at_ms
            current = self._status.get(workflow_id) or self._base_status(workflow_id)
            current["lastRunAt"] = started_at_ms
            current["lastDurationMs"] = duration_ms
            current["lastStatus"] = "failed"
            current["lastError"] = str(exc)
            current["activeRuns"] = self._cleanup_done_runs(workflow_id)
            if workflow_id in self._tasks and current.get("state") != "failed":
                current["state"] = "running"
                current["error"] = None
            self._status[workflow_id] = current
            log.warning("poller.run_failed", {"workflow_id": workflow_id, "error": str(exc)})
        finally:
            cancel_events.discard(cancel_event)
            if not cancel_events:
                self._run_cancel_events.pop(workflow_id, None)
            current = self._status.get(workflow_id) or self._base_status(workflow_id)
            current["activeRuns"] = self._cleanup_done_runs(workflow_id)
            if workflow_id not in self._tasks and current.get("state") == "running":
                current["state"] = "stopped"
                current["nextRunAt"] = None
            self._status[workflow_id] = current

        return self.get_status(workflow_id)


default_manager = WorkflowPollerManager()
