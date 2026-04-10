"""
DingTalk ChannelPlugin for flocks.

通过 subprocess 启动 runner.ts（npm），runner.ts 构造假的 OpenClaw runtime，
驱动 plugin.ts 的 DWClient WebSocket 连接钉钉。
flocks 对外暴露的 POST /v1/chat/completions 端点承接所有 AI 推理请求。

放置位置：
    .flocks/plugins/channels/dingtalk/dingtalk.py

目录结构：
    dingtalk/
    ├── dingtalk.py               ← 本文件（flocks 自动加载）
    ├── runner.ts                 ← Node.js 桥接层（无需修改）
    └── dingtalk-openclaw-connector/
        └── plugin.ts             ← 原版 connector（无需修改）

flocks.json 配置示例：
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

可选额外字段（透传给 plugin.ts）：
    gatewayToken            Bearer 认证 token（通常不需要，flocks 本地无鉴权）
    debug                   true/false，开启 plugin.ts 调试日志
    separateSessionByConversation  true（默认）
    groupSessionScope       "group"（默认）/ "group_sender"
    sharedMemoryAcrossConversations  false（默认）
    dmPolicy                "open"（默认）/ "allowlist"
    allowFrom               允许的 senderStaffId 列表
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

# runner.ts 所在目录（与本文件同级）
_PLUGIN_DIR = Path(__file__).parent
_RUNNER_TS = _PLUGIN_DIR / "runner.ts"
_CONNECTOR_DIR = _PLUGIN_DIR / "dingtalk-openclaw-connector"
_CONNECTOR_PACKAGE = _CONNECTOR_DIR / "package.json"


def _find_npm() -> str:
    """返回 npm 可执行路径，找不到则抛出。"""
    if npm := os.environ.get("NPM_PATH"):
        return npm

    import shutil

    for candidate in ("npm", "npm.cmd"):
        if npm := shutil.which(candidate):
            return npm

    raise RuntimeError(
        "找不到 npm。请先安装 Node.js（包含 npm）或设置 NPM_PATH 环境变量。"
    )


class DingTalkChannel(ChannelPlugin):
    """DingTalk channel — 通过 runner.ts 子进程桥接 plugin.ts。"""

    def __init__(self) -> None:
        super().__init__()
        self._proc: Optional[subprocess.Popen] = None
        self._monitor_task: Optional[asyncio.Task] = None

    # ── 元数据 ────────────────────────────────────────────────────────────────

    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            id="dingtalk",
            label="钉钉",
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
                return f"缺少必填配置项: {key}"
        if not _RUNNER_TS.exists():
            return f"找不到 runner.ts: {_RUNNER_TS}"
        if not _CONNECTOR_PACKAGE.exists():
            return f"找不到 package.json: {_CONNECTOR_PACKAGE}"
        return None

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    async def start(
        self,
        config: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        abort_event: Optional[asyncio.Event] = None,
    ) -> None:
        """启动 runner.ts 子进程，监控其生命周期直到 abort_event 触发。"""
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
        }

        log.info("dingtalk.start", {
            "runner": str(_RUNNER_TS),
            "flocks_port": flocks_port,
            "client_id": config.get("clientId", ""),
        })

        self._start_process(npm, env)
        self.mark_connected()

        # 监控子进程直到 abort_event
        self._monitor_task = asyncio.create_task(
            self._monitor(abort_event)
        )
        await self._monitor_task

    async def stop(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        self._kill_process()
        self.mark_disconnected()

    # ── 出站消息 ──────────────────────────────────────────────────────────────
    # plugin.ts 通过 sessionWebhook 直接回复钉钉，flocks 不需要经过 send_text 投递。
    # 但框架要求实现此方法，留作主动推送备用。

    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        """
        主动推送文本消息（用于 Agent 主动发钉钉）。
        plugin.ts 的被动回复走 sessionWebhook，不经此路径。
        此处实现留作后续扩展，当前返回不支持。
        """
        log.warning("dingtalk.send_text.not_implemented", {
            "to": ctx.to,
            "hint": "主动推送需通过 dingtalk-connector.send GatewayMethod",
        })
        return DeliveryResult(
            channel_id="dingtalk",
            message_id="",
            success=False,
            error="主动推送暂未实现，plugin.ts 的被动回复走 sessionWebhook",
        )

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _get_flocks_port(self) -> int:
        """从环境变量或默认值获取 flocks HTTP 端口。"""
        return int(os.environ.get("FLOCKS_PORT", "8000"))

    def _start_process(self, npm: str, env: dict) -> None:
        """启动 runner.ts 子进程。"""
        self._proc = subprocess.Popen(
            [npm, "run", "start:runner"],
            cwd=str(_CONNECTOR_DIR),
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        log.info("dingtalk.process.started", {"pid": self._proc.pid})

    def _kill_process(self) -> None:
        """终止子进程。"""
        if self._proc and self._proc.poll() is None:
            log.info("dingtalk.process.terminating", {"pid": self._proc.pid})
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            log.info("dingtalk.process.stopped", {"pid": self._proc.pid})
        self._proc = None

    async def _monitor(self, abort_event: Optional[asyncio.Event]) -> None:
        """监控子进程；退出码非零时记录错误；abort_event 触发时停止。"""
        try:
            while True:
                if abort_event and abort_event.is_set():
                    log.info("dingtalk.monitor.abort")
                    break

                # 非阻塞检查进程是否已退出
                if self._proc and self._proc.poll() is not None:
                    rc = self._proc.returncode
                    if rc != 0:
                        log.error("dingtalk.process.exited_unexpectedly", {"returncode": rc})
                        self.mark_disconnected(f"runner.ts 意外退出，exit code={rc}")
                    else:
                        log.info("dingtalk.process.exited_normally", {"returncode": rc})
                    break

                await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass
        finally:
            self._kill_process()


# flocks PluginLoader 通过此变量发现插件
CHANNELS = [DingTalkChannel()]
