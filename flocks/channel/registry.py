"""
Channel plugin registry.

Pattern mirrors ToolRegistry: singleton instance with register/get/list.
Plugins are discovered via the ``CHANNELS`` ExtensionPoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from flocks.utils.log import Log
from flocks.channel.base import ChannelPlugin

log = Log.create(service="channel.registry")


class ChannelRegistry:
    """Registry of ChannelPlugin instances.

    All state is instance-level so that tests can create isolated registries.
    The module-level ``default_registry`` singleton is used by the rest of the
    system; class-level convenience methods delegate to it.
    """

    def __init__(self) -> None:
        self._channels: dict[str, ChannelPlugin] = {}
        self._initialized: bool = False

    # --- instance API ---

    def register(self, plugin: ChannelPlugin) -> None:
        meta = plugin.meta()
        self._channels[meta.id.lower()] = plugin
        for alias in meta.aliases:
            self._channels[alias.lower()] = plugin
        log.info("channel.registered", {"id": meta.id, "aliases": meta.aliases})

    def get(self, channel_id: str) -> Optional[ChannelPlugin]:
        return self._channels.get(channel_id.lower())

    def list_channels(self) -> list[ChannelPlugin]:
        """Return all registered channels (deduplicated)."""
        seen: set[int] = set()
        result: list[ChannelPlugin] = []
        for plugin in self._channels.values():
            pid = id(plugin)
            if pid not in seen:
                seen.add(pid)
                result.append(plugin)
        return result

    def init(self) -> None:
        """
        Initialise the channel registry:
        1. Register built-in channels
        2. Register the CHANNELS ExtensionPoint
        3. Load global + project-level plugin channels
        """
        if self._initialized:
            return
        self._initialized = True
        self._register_builtin_channels()
        self._register_plugin_extension_point()
        self._load_plugin_channels()
        log.info("channel.registry.initialized", {
            "count": len(self.list_channels()),
        })

    def reset(self) -> None:
        """Reset all state (for testing)."""
        self._channels.clear()
        self._initialized = False

    # --- internal ---

    def _register_builtin_channels(self) -> None:
        from flocks.channel.builtin.dingtalk.channel import DingTalkChannel
        from flocks.channel.builtin.feishu.channel import FeishuChannel
        from flocks.channel.builtin.telegram.channel import TelegramChannel
        from flocks.channel.builtin.wecom.channel import WeComChannel
        self.register(FeishuChannel())
        self.register(WeComChannel())
        self.register(TelegramChannel())
        self.register(DingTalkChannel())

    def _register_plugin_extension_point(self) -> None:
        from flocks.plugin import PluginLoader, ExtensionPoint

        registry = self

        def _consume_channels(items: list, source: str) -> None:
            for item in items:
                if isinstance(item, ChannelPlugin):
                    registry.register(item)

        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="CHANNELS",
            subdir="channels",
            consumer=_consume_channels,
            item_type=ChannelPlugin,
            dedup_key=lambda ch: ch.meta().id,
            recursive=True,
            max_depth=2,
        ))

    def _load_plugin_channels(self) -> None:
        from flocks.plugin import PluginLoader
        PluginLoader.load_default_for_extension("CHANNELS")
        self._load_project_channels()

    @staticmethod
    def _load_project_channels() -> None:
        from flocks.plugin import PluginLoader, scan_directory
        project_channels_dir = Path.cwd() / ".flocks" / "plugins" / "channels"
        if not project_channels_dir.is_dir():
            return
        sources = scan_directory(
            project_channels_dir,
            recursive=True,
            max_depth=2,
        )
        if sources:
            PluginLoader.load_for_extension(
                "CHANNELS", sources, project_channels_dir
            )


# Module-level default singleton
default_registry = ChannelRegistry()
