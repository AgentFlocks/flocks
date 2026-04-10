"""
GatewayManager — lifecycle manager for all enabled channel connections.

Started during ``flocks serve`` / ``flocks tui`` and integrated into
the FastAPI lifespan.  Supports automatic exponential-backoff reconnection
and per-channel health monitoring.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from flocks.channel.base import ChannelPlugin, ChannelStatus
from flocks.channel.inbound.dispatcher import InboundDispatcher
from flocks.channel.registry import ChannelRegistry, default_registry
from flocks.utils.log import Log

log = Log.create(service="channel.gateway")


class GatewayManager:
    """Manages all enabled channel connections.

    All state is instance-level so that multiple managers can co-exist
    (e.g. in tests).  The module-level ``default_manager`` singleton is
    used by the FastAPI lifespan and route handlers.
    """

    RECONNECT_BASE_DELAY = 1.0
    RECONNECT_MAX_DELAY = 60.0
    RECONNECT_MAX_ATTEMPTS: Optional[int] = None

    def __init__(self, registry: Optional[ChannelRegistry] = None) -> None:
        self._registry = registry or default_registry
        self._running: dict[str, asyncio.Task] = {}
        self._running_plugins: dict[str, ChannelPlugin] = {}
        self._abort_events: dict[str, asyncio.Event] = {}
        self._dispatcher: Optional[InboundDispatcher] = None
        self._started_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_all(self) -> None:
        """Start connections for every enabled channel in config."""
        from flocks.config.config import Config

        cfg = await Config.get()
        channels_config = cfg.get_channel_configs()

        self._registry.init()
        self._dispatcher = InboundDispatcher()
        self._started_at = time.monotonic()

        for channel_id, ch_config in channels_config.items():
            if not ch_config.enabled:
                continue
            plugin = self._registry.get(channel_id)
            if not plugin:
                log.warning("gateway.plugin_not_found", {"channel": channel_id})
                continue

            config_dict = ch_config.model_dump(by_alias=True, exclude_none=True)
            error = plugin.validate_config(config_dict)
            if error:
                log.error("gateway.config_invalid", {
                    "channel": channel_id, "error": error,
                })
                continue

            abort_event = asyncio.Event()
            self._abort_events[channel_id] = abort_event

            task = asyncio.create_task(
                self._run_with_reconnect(
                    channel_id=channel_id,
                    plugin=plugin,
                    config=config_dict,
                    on_message=self._dispatcher.dispatch,
                    abort_event=abort_event,
                ),
                name=f"channel-{channel_id}",
            )
            self._running[channel_id] = task
            self._running_plugins[channel_id] = plugin
            log.info("gateway.channel_started", {"channel": channel_id})

    async def stop_all(self) -> None:
        """Gracefully stop all running channels."""
        for event in self._abort_events.values():
            event.set()

        if self._running:
            _done, pending = await asyncio.wait(
                self._running.values(), timeout=10.0,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        for channel_id in list(self._running.keys()):
            plugin = self._running_plugins.get(channel_id) or self._registry.get(channel_id)
            if plugin:
                try:
                    await plugin.stop()
                except Exception as e:
                    log.warning("gateway.stop_error", {
                        "channel": channel_id, "error": str(e),
                    })

        self._running.clear()
        self._running_plugins.clear()
        self._abort_events.clear()

        try:
            from flocks.channel.inbound.session_binding import close_binding_db
            await close_binding_db()
        except Exception:
            pass

    def get_status(self) -> dict[str, ChannelStatus]:
        """Snapshot of every running channel's health status."""
        result: dict[str, ChannelStatus] = {}
        for channel_id in self._running:
            # Use the plugin instance the running task holds, not the registry's
            # latest instance (registry may have been updated by the file watcher).
            plugin = self._running_plugins.get(channel_id) or self._registry.get(channel_id)
            if plugin:
                status = plugin.status
                if status.started_at:
                    status.uptime_seconds = time.monotonic() - status.started_at
                result[channel_id] = status
        return result

    def is_channel_running(self, channel_id: str) -> bool:
        return (
            channel_id in self._running
            and not self._running[channel_id].done()
        )

    async def start_channel(self, channel_id: str) -> None:
        """Start a single channel connection using the current config.

        Raises ``ValueError`` if the channel is unknown or its config is
        invalid.  No-ops if the channel is already running.
        """
        if self.is_channel_running(channel_id):
            return

        from flocks.config.config import Config

        cfg = await Config.get()
        channels_config = cfg.get_channel_configs()
        ch_config = channels_config.get(channel_id)

        if not ch_config or not ch_config.enabled:
            raise ValueError(
                f"Channel '{channel_id}' is not enabled in config"
            )

        plugin = self._registry.get(channel_id)
        if not plugin:
            raise ValueError(f"Channel plugin '{channel_id}' not found")

        config_dict = ch_config.model_dump(by_alias=True, exclude_none=True)
        error = plugin.validate_config(config_dict)
        if error:
            raise ValueError(f"Invalid channel config: {error}")

        if self._dispatcher is None:
            self._dispatcher = InboundDispatcher()

        abort_event = asyncio.Event()
        self._abort_events[channel_id] = abort_event

        task = asyncio.create_task(
            self._run_with_reconnect(
                channel_id=channel_id,
                plugin=plugin,
                config=config_dict,
                on_message=self._dispatcher.dispatch,
                abort_event=abort_event,
            ),
            name=f"channel-{channel_id}",
        )
        self._running[channel_id] = task
        self._running_plugins[channel_id] = plugin
        log.info("gateway.channel_started", {"channel": channel_id})

    async def stop_channel(self, channel_id: str) -> None:
        """Gracefully stop a single channel connection."""
        event = self._abort_events.pop(channel_id, None)
        if event:
            event.set()

        task = self._running.pop(channel_id, None)
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                task.cancel()

        plugin = self._running_plugins.pop(channel_id, None) or self._registry.get(channel_id)
        if plugin:
            try:
                await plugin.stop()
            except Exception as e:
                log.warning("gateway.stop_error", {
                    "channel": channel_id, "error": str(e),
                })

        log.info("gateway.channel_stopped", {"channel": channel_id})

    async def restart_channel(self, channel_id: str) -> None:
        """Stop a channel (if running) then start it fresh from config.

        If the channel is disabled in the current config, only stops the
        existing connection without starting a new one.  Used after a
        config save so that enable/disable and credential changes take
        effect without restarting the whole server.
        """
        from flocks.config.config import Config

        log.info("gateway.restarting", {"channel": channel_id})
        await self.stop_channel(channel_id)

        cfg = await Config.get()
        channels_config = cfg.get_channel_configs()
        ch_config = channels_config.get(channel_id)

        if ch_config and ch_config.enabled:
            await self.start_channel(channel_id)
            log.info("gateway.restarted", {"channel": channel_id})
        else:
            log.info("gateway.stopped_disabled", {"channel": channel_id})

    # ------------------------------------------------------------------
    # Reconnect loop
    # ------------------------------------------------------------------

    async def _run_with_reconnect(
        self,
        channel_id: str,
        plugin: ChannelPlugin,
        config: dict,
        on_message: Callable,
        abort_event: asyncio.Event,
    ) -> None:
        """Run plugin.start() with automatic exponential-backoff reconnect."""
        attempt = 0
        delay = self.RECONNECT_BASE_DELAY

        while not abort_event.is_set():
            try:
                plugin.reset_status(channel_id, attempt)

                log.info("gateway.connecting", {
                    "channel": channel_id, "attempt": attempt + 1,
                })

                start_task = asyncio.ensure_future(
                    plugin.start(config, on_message, abort_event)
                )

                done, _ = await asyncio.wait({start_task}, timeout=0.5)

                if done:
                    start_task.result()
                    if abort_event.is_set():
                        break
                    # Webhook / passive mode — start() returned immediately.
                    await self._mark_connected(plugin, channel_id)
                    log.info("gateway.connected_passive", {
                        "channel": channel_id,
                        "hint": "webhook/passive mode — waiting for abort",
                    })
                    await abort_event.wait()
                    break

                # Long-running start() (WebSocket / polling mode)
                await self._mark_connected(plugin, channel_id)
                await start_task

                if abort_event.is_set():
                    break

                plugin.mark_disconnected()
                delay = self.RECONNECT_BASE_DELAY
                attempt += 1

                # Brief pause before retrying a clean exit to avoid busy-looping
                # when the remote connection drops immediately after connect.
                if await self._sleep_or_abort(abort_event, self.RECONNECT_BASE_DELAY):
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._record_error(plugin, channel_id, e)

                if abort_event.is_set():
                    break
                if self.RECONNECT_MAX_ATTEMPTS and attempt >= self.RECONNECT_MAX_ATTEMPTS:
                    log.error("gateway.max_reconnect", {"channel": channel_id})
                    break

                log.warning("gateway.disconnected", {
                    "channel": channel_id,
                    "error": str(e),
                    "delay": delay,
                    "attempt": attempt + 1,
                })

                if await self._sleep_or_abort(abort_event, delay):
                    break

                attempt += 1
                delay = min(delay * 2, self.RECONNECT_MAX_DELAY)

        plugin.mark_disconnected()
        log.info("gateway.stopped", {"channel": channel_id})

    # ------------------------------------------------------------------
    # Reconnect helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _mark_connected(plugin: ChannelPlugin, channel_id: str) -> None:
        plugin.mark_connected()
        try:
            from flocks.channel.events import ChannelConnected
            from flocks.bus.bus import Bus
            await Bus.publish(ChannelConnected, {"channel_id": channel_id})
        except Exception:
            pass

    @staticmethod
    def _record_error(
        plugin: ChannelPlugin,
        channel_id: str,
        error: Exception,
    ) -> None:
        plugin.mark_disconnected(error=str(error))
        try:
            from flocks.channel.events import ChannelDisconnected
            from flocks.bus.bus import Bus
            asyncio.ensure_future(
                Bus.publish(ChannelDisconnected, {
                    "channel_id": channel_id,
                    "reason": str(error),
                })
            )
        except Exception:
            pass

    @staticmethod
    async def _sleep_or_abort(abort_event: asyncio.Event, delay: float) -> bool:
        """Sleep for *delay* seconds, returning ``True`` if abort was signaled."""
        try:
            await asyncio.wait_for(abort_event.wait(), timeout=delay)
            return True
        except asyncio.TimeoutError:
            return False


# Module-level default singleton
default_manager = GatewayManager()
