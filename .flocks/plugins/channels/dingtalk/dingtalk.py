"""
DingTalk ChannelPlugin for flocks.

Launches runner.ts (via npm) as a subprocess. runner.ts constructs a minimal
OpenClaw runtime shim that drives plugin.ts's DWClient WebSocket connection
to DingTalk. All AI inference requests are served through flocks's
POST /v1/chat/completions endpoint.

Location:
    .flocks/plugins/channels/dingtalk/dingtalk.py

Directory layout:
    dingtalk/
    ├── dingtalk.py               ← this file (auto-loaded by flocks)
    ├── runner.ts                 ← Node.js bridge layer (no modification needed)
    └── dingtalk-openclaw-connector/
        └── plugin.ts             ← original connector (no modification needed)

flocks.json configuration example:
    {
      "channels": {
        "dingtalk": {
          "enabled": true,
          "clientId": "dingXXXXXX",
          "clientSecret": "your_secret",
          "defaultAgent": "rex"
        }
      }
    }

Optional extra fields (passed through to plugin.ts):
    gatewayToken            Bearer auth token (usually not needed; flocks has no local auth)
    debug                   true/false, enables plugin.ts debug logging
    separateSessionByConversation  true (default)
    groupSessionScope       "group" (default) / "group_sender"
    sharedMemoryAcrossConversations  false (default)
    dmPolicy                "open" (default) / "allowlist"
    allowFrom               list of allowed senderStaffId values
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from flocks.channel.base import (
    ChannelCapabilities,
    ChannelMeta,
    ChannelPlugin,
    ChatType,
    DeliveryResult,
    InboundMessage,
    OutboundContext,
)
from flocks.utils.log import Log

log = Log.create(service="channel.dingtalk")

# Directory containing runner.ts (same level as this file)
_PLUGIN_DIR = Path(__file__).parent
_RUNNER_TS = _PLUGIN_DIR / "runner.ts"
_CONNECTOR_DIR = _PLUGIN_DIR / "dingtalk-openclaw-connector"
_CONNECTOR_PACKAGE = _CONNECTOR_DIR / "package.json"


def _find_npm() -> str:
    """Return the npm executable path, raising if not found."""
    if npm := os.environ.get("NPM_PATH"):
        return npm

    import shutil

    for candidate in ("npm", "npm.cmd"):
        if npm := shutil.which(candidate):
            return npm

    raise RuntimeError(
        "npm not found. Please install Node.js (which includes npm) or set the NPM_PATH environment variable."
    )


class DingTalkChannel(ChannelPlugin):
    """DingTalk channel — bridges to plugin.ts via a runner.ts subprocess."""

    def __init__(self) -> None:
        super().__init__()
        self._proc: Optional[subprocess.Popen] = None
        self._monitor_task: Optional[asyncio.Task] = None

    # ── Metadata ──────────────────────────────────────────────────────────────

    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            id="dingtalk",
            label="DingTalk",
            aliases=["dingding", "dingtalk-connector"],
            order=30,
        )

    def capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            chat_types=[ChatType.DIRECT, ChatType.GROUP],
            media=True,
            threads=False,
            reactions=False,
            edit=False,
            rich_text=True,
        )

    def validate_config(self, config: dict) -> Optional[str]:
        for key in ("clientId", "clientSecret"):
            if not config.get(key):
                return f"Missing required config field: {key}"
        if not _RUNNER_TS.exists():
            return f"runner.ts not found: {_RUNNER_TS}"
        if not _CONNECTOR_PACKAGE.exists():
            return f"package.json not found: {_CONNECTOR_PACKAGE}"
        node_modules = _CONNECTOR_DIR / "node_modules"
        if not node_modules.is_dir():
            return (
                f"node_modules not found in {_CONNECTOR_DIR}. "
                "Run `npm install` (or `bun install`) inside that directory first."
            )
        return None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(
        self,
        config: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        abort_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Start the runner.ts subprocess and monitor it until abort_event fires.

        Design note: DingTalk inbound messages are handled entirely inside the
        runner.ts ↔ plugin.ts layer, which calls the flocks Session API directly.
        The `on_message` / InboundDispatcher path (used by Feishu, WeCom, Telegram)
        is intentionally NOT used here; this means dedup, debounce, channel.inbound
        hooks and session-binding are the responsibility of plugin.ts itself.
        """
        self._config = config
        self._on_message = on_message

        npm = _find_npm()
        flocks_port = self._get_flocks_port()

        env = {
            **os.environ,
            "DINGTALK_CLIENT_ID":     config.get("clientId", ""),
            "DINGTALK_CLIENT_SECRET": config.get("clientSecret", ""),
            "FLOCKS_PORT":            str(flocks_port),
            "FLOCKS_AGENT":           config.get("defaultAgent", ""),
            "FLOCKS_GATEWAY_TOKEN":   config.get("gatewayToken", ""),
            "DINGTALK_DEBUG":         "true" if config.get("debug") else "false",
            "DINGTALK_ACCOUNT_ID":    config.get("_account_id", "__default__"),
            # Optional policy / behaviour fields forwarded to plugin.ts
            "DINGTALK_DM_POLICY":             str(config.get("dmPolicy", "")),
            "DINGTALK_ALLOW_FROM":            ",".join(config.get("allowFrom") or []),
            "DINGTALK_SEPARATE_SESSION":      "true" if config.get("separateSessionByConversation", True) else "false",
            "DINGTALK_GROUP_SESSION_SCOPE":   str(config.get("groupSessionScope", "")),
            "DINGTALK_SHARED_MEMORY":         "true" if config.get("sharedMemoryAcrossConversations") else "false",
        }

        log.info("dingtalk.start", {
            "runner": str(_RUNNER_TS),
            "flocks_port": flocks_port,
            "client_id": config.get("clientId", ""),
        })

        self._start_process(npm, env)
        self.mark_connected()

        # Monitor subprocess until abort_event is set
        self._monitor_task = asyncio.create_task(
            self._monitor(abort_event)
        )
        await self._monitor_task

    async def stop(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        await self._kill_process_async()
        self.mark_disconnected()

    # ── Outbound messages ─────────────────────────────────────────────────────
    # plugin.ts replies to DingTalk directly via sessionWebhook; flocks does not
    # need to route through send_text. This method is required by the framework
    # and is kept as a placeholder for proactive push support.

    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        """
        Proactively push a text message (for agent-initiated DingTalk messages).
        Passive replies from plugin.ts go through sessionWebhook and bypass this path.
        Reserved for future extension; currently returns not-supported.
        """
        log.warning("dingtalk.send_text.not_implemented", {
            "to": ctx.to,
            "hint": "Proactive push requires the dingtalk-connector.send GatewayMethod",
        })
        return DeliveryResult(
            channel_id="dingtalk",
            message_id="",
            success=False,
            error="Proactive push not yet implemented; plugin.ts passive replies go through sessionWebhook",
        )

    # ── Internal methods ──────────────────────────────────────────────────────

    def _get_flocks_port(self) -> int:
        """Get the flocks HTTP port from the environment variable or fall back to the default."""
        return int(os.environ.get("FLOCKS_PORT", "8000"))

    def _start_process(self, npm: str, env: dict) -> None:
        """Start the runner.ts subprocess."""
        self._proc = subprocess.Popen(
            [npm, "run", "start:runner"],
            cwd=str(_CONNECTOR_DIR),
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        log.info("dingtalk.process.started", {"pid": self._proc.pid})

    async def _kill_process_async(self) -> None:
        """Terminate the subprocess without blocking the asyncio event loop."""
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        pid = proc.pid
        log.info("dingtalk.process.terminating", {"pid": pid})
        proc.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await asyncio.to_thread(proc.wait)
        log.info("dingtalk.process.stopped", {"pid": pid})

    async def _monitor(self, abort_event: Optional[asyncio.Event]) -> None:
        """Monitor the subprocess; raise RuntimeError on non-zero exit; stop when abort_event fires."""
        exit_code: Optional[int] = None
        try:
            while True:
                if abort_event and abort_event.is_set():
                    log.info("dingtalk.monitor.abort")
                    break

                # Non-blocking check whether the process has exited
                if self._proc and self._proc.poll() is not None:
                    exit_code = self._proc.returncode
                    if exit_code != 0:
                        log.error("dingtalk.process.exited_unexpectedly", {"returncode": exit_code})
                    else:
                        log.info("dingtalk.process.exited_normally", {"returncode": exit_code})
                    break

                await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass
        finally:
            # Non-blocking cleanup: must not block the event loop while waiting for
            # the Node.js process to exit (can take up to 5s with SIGTERM).
            await self._kill_process_async()

        # Raise after cleanup so the gateway reconnect loop applies exponential backoff.
        if exit_code is not None and exit_code != 0:
            raise RuntimeError(f"runner.ts exited unexpectedly, exit code={exit_code}")


# Discovered by flocks PluginLoader via this variable
CHANNELS = [DingTalkChannel()]
