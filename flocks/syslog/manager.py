"""Lifecycle manager for syslog listeners → workflow runs."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from flocks.storage.storage import Storage
from flocks.utils.log import Log
from flocks.workflow.fs_store import read_workflow_from_fs
from flocks.workflow.runner import run_workflow

from flocks.syslog.constants import WORKFLOW_SYSLOG_CONFIG_PREFIX
from flocks.syslog.listener import run_tcp_syslog_server, run_udp_syslog_server

log = Log.create(service="syslog.manager")


class SyslogManager:
    """One async listener task per workflow id (when enabled)."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._abort_events: dict[str, asyncio.Event] = {}

    @staticmethod
    def _config_key(workflow_id: str) -> str:
        return f"{WORKFLOW_SYSLOG_CONFIG_PREFIX}{workflow_id}"

    async def start_all(self) -> None:
        try:
            keys = await Storage.list_keys(WORKFLOW_SYSLOG_CONFIG_PREFIX)
        except Exception as exc:
            log.warning("syslog.list_keys_failed", {"error": str(exc)})
            return

        for key in keys:
            if not key.startswith(WORKFLOW_SYSLOG_CONFIG_PREFIX):
                continue
            workflow_id = key[len(WORKFLOW_SYSLOG_CONFIG_PREFIX) :]
            if not workflow_id:
                continue
            try:
                data = await Storage.read(key)
            except Exception as exc:
                log.warning("syslog.config_read_failed", {"key": key, "error": str(exc)})
                continue
            if isinstance(data, dict) and data.get("enabled"):
                await self.restart_workflow(workflow_id)

    async def stop_all(self) -> None:
        for workflow_id in list(self._tasks.keys()):
            await self.stop_workflow(workflow_id)

    async def stop_workflow(self, workflow_id: str) -> None:
        ev = self._abort_events.pop(workflow_id, None)
        if ev is not None:
            ev.set()
        task = self._tasks.pop(workflow_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def restart_workflow(self, workflow_id: str) -> None:
        await self.stop_workflow(workflow_id)
        key = self._config_key(workflow_id)
        try:
            data = await Storage.read(key)
        except Exception as exc:
            log.warning("syslog.restart_read_failed", {"workflow_id": workflow_id, "error": str(exc)})
            return
        if not isinstance(data, dict) or not data.get("enabled"):
            return

        abort = asyncio.Event()
        self._abort_events[workflow_id] = abort
        task = asyncio.create_task(
            self._listener_loop(workflow_id, data, abort),
            name=f"syslog-{workflow_id}",
        )
        self._tasks[workflow_id] = task
        log.info("syslog.listener_scheduled", {"workflow_id": workflow_id})

    async def _listener_loop(
        self,
        workflow_id: str,
        config: Dict[str, Any],
        abort: asyncio.Event,
    ) -> None:
        host = str(config.get("host") or "0.0.0.0")
        port = int(config.get("port") or 5140)
        protocol = str(config.get("protocol") or "udp").lower()
        format_hint = str(config.get("format") or "auto")
        input_key = str(config.get("inputKey") or "syslog_message")

        async def on_msg(parsed: dict) -> None:
            await self._trigger_workflow(workflow_id, parsed, input_key)

        try:
            if protocol == "tcp":
                await run_tcp_syslog_server(
                    host,
                    port,
                    format_hint,
                    on_msg,
                    abort_event=abort,
                )
            else:
                await run_udp_syslog_server(
                    host,
                    port,
                    format_hint,
                    on_msg,
                    abort_event=abort,
                )
        except asyncio.CancelledError:
            raise
        except OSError as exc:
            log.error(
                "syslog.bind_failed",
                {"workflow_id": workflow_id, "error": str(exc), "host": host, "port": port, "protocol": protocol},
            )
        except Exception as exc:
            log.error("syslog.listener_error", {"workflow_id": workflow_id, "error": str(exc)})

    async def _trigger_workflow(self, workflow_id: str, syslog_msg: dict, input_key: str) -> None:
        data = read_workflow_from_fs(workflow_id)
        if not data:
            log.warning("syslog.workflow_not_found", {"workflow_id": workflow_id})
            return
        workflow_json = data.get("workflowJson")
        if not workflow_json:
            log.warning("syslog.workflow_json_missing", {"workflow_id": workflow_id})
            return
        inputs = {input_key: syslog_msg}
        try:
            await asyncio.to_thread(
                run_workflow,
                workflow=workflow_json,
                inputs=inputs,
                trace=False,
            )
        except Exception as exc:
            log.error(
                "syslog.workflow_run_failed",
                {"workflow_id": workflow_id, "error": str(exc)},
            )


default_manager = SyslogManager()
