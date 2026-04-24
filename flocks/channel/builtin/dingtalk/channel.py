"""
DingTalk ChannelPlugin — Stream Mode inbound + OAPI app-robot outbound.

This plugin replaces the legacy Node.js connector
(``.flocks/plugins/channels/dingtalk/dingtalk.py``) with a pure-Python
implementation modelled on the Hermes Agent's ``DingTalkAdapter``.

* Inbound:  long-lived WebSocket via :mod:`dingtalk_stream` (>= 0.20).
* Outbound: enterprise app-robot OAPI via the existing
  :func:`flocks.channel.builtin.dingtalk.send.send_message_app`.

Multi-account support follows the same pattern as the Feishu channel:
each ``accounts.<name>`` block spawns its own stream connection.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from flocks.channel.base import (
    ChannelCapabilities,
    ChannelMeta,
    ChannelPlugin,
    ChatType,
    DeliveryResult,
    InboundMessage,
    OutboundContext,
)
from flocks.channel.builtin.dingtalk.config import (
    list_account_configs,
    resolve_account_config,
    strip_target_prefix,
)
from flocks.utils.log import Log

log = Log.create(service="channel.dingtalk")


class DingTalkChannel(ChannelPlugin):
    """DingTalk channel — Stream Mode inbound, OAPI outbound."""

    def __init__(self) -> None:
        super().__init__()
        self._runner_tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def meta(self) -> ChannelMeta:
        return ChannelMeta(
            id="dingtalk",
            label="钉钉",
            aliases=["dingding"],
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
        accounts = list_account_configs(config, require_credentials=True)
        if not accounts:
            return (
                "Missing required config: appKey/appSecret (also accepted as "
                "clientId/clientSecret), at top-level or under accounts.<name>"
            )
        return None

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send_text(self, ctx: OutboundContext) -> DeliveryResult:
        """Send a text/markdown message via the DingTalk app-robot OAPI."""
        # Resolve via the package alias so test patches that target
        # ``flocks.channel.builtin.dingtalk.send_message_app`` (the public
        # surface) replace the same binding we look up here.
        from flocks.channel.builtin import dingtalk as _dingtalk_pkg
        from flocks.channel.builtin.dingtalk.client import DingTalkApiError

        send_message_app = _dingtalk_pkg.send_message_app

        if not ctx.to or not strip_target_prefix(ctx.to):
            return DeliveryResult(
                channel_id="dingtalk",
                message_id="",
                success=False,
                error="DingTalk send requires 'to' (user:<staffId> or chat:<openConversationId>)",
            )

        try:
            send_config = resolve_account_config(self._config or {}, ctx.account_id)
            result = await send_message_app(
                config=send_config,
                to=ctx.to,
                text=ctx.text,
                account_id=ctx.account_id,
            )
            self.record_message()
            return DeliveryResult(
                channel_id="dingtalk",
                message_id=str(result.get("message_id", "")),
                chat_id=result.get("chat_id"),
            )
        except Exception as exc:
            retryable = getattr(exc, "retryable", False)
            if not retryable and not isinstance(exc, DingTalkApiError):
                msg = str(exc).lower()
                retryable = "rate limit" in msg or "timeout" in msg
            log.warning("dingtalk.send_text.failed", {
                "to": ctx.to, "error": str(exc), "retryable": retryable,
            })
            return DeliveryResult(
                channel_id="dingtalk",
                message_id="",
                success=False,
                error=str(exc),
                retryable=retryable,
            )

    @property
    def text_chunk_limit(self) -> int:
        return int((self._config or {}).get("textChunkLimit", 4000))

    @property
    def rate_limit(self) -> tuple[float, int]:
        rate = (self._config or {}).get("rateLimit", 20.0)
        burst = (self._config or {}).get("rateBurst", 5)
        return float(rate), int(burst)

    def normalize_target(self, raw: str) -> Optional[str]:
        return strip_target_prefix(raw) or None

    def target_hint(self) -> str:
        return "user:<staffId> 或 chat:<openConversationId>"

    # ------------------------------------------------------------------
    # Inbound lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        config: dict,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        abort_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Start one Stream Mode connection per configured account.

        Blocks until *abort_event* fires or every runner task exits.

        * Permanent failures (subclasses of ``DingTalkPermanentError``)
          are logged per account and that account is dropped from the
          schedule, but they do **not** propagate to the gateway —
          retrying would be pointless.  Today this covers:
          - ``DingTalkPermanentAuthError`` (bad credentials / app
            revoked / Stream Mode subscription disabled)
          - ``DingTalkStreamStallError`` (SDK keeps returning instantly
            with no inbound traffic — gateway is silently blocking us)
        * Other (transient) runner exceptions DO propagate so the
          gateway's exponential-backoff reconnect policy can take over.
        """
        from flocks.channel.builtin.dingtalk.stream import (
            DINGTALK_STREAM_AVAILABLE,
            DingTalkStreamRunner,
        )

        self._config = config
        self._on_message = on_message

        if not DINGTALK_STREAM_AVAILABLE:
            raise RuntimeError(
                "dingtalk-stream is not installed. "
                "Run `pip install 'dingtalk-stream>=0.20'` to enable the DingTalk channel."
            )

        accounts = list_account_configs(config, require_credentials=True)
        if not accounts:
            log.warning("dingtalk.start.no_accounts")
            return

        tasks: list[asyncio.Task] = []
        for account in accounts:
            runner = DingTalkStreamRunner(
                account_config=account,
                on_message=on_message,
            )
            if not runner.is_configured():
                log.warning("dingtalk.start.account_skipped", {
                    "account": runner.account_id,
                    "reason": "missing appKey/appSecret",
                })
                continue
            log.info("dingtalk.start.account", {
                "account": runner.account_id,
                "client_id": runner.client_id,
            })
            tasks.append(asyncio.create_task(
                runner.run(abort_event),
                name=f"dingtalk-stream-{runner.account_id}",
            ))

        self._runner_tasks = tasks

        if not tasks:
            return

        try:
            await self._wait_until_done(abort_event)
        finally:
            await self._cancel_runners()

    async def _wait_until_done(self, abort_event: Optional[asyncio.Event]) -> None:
        """Wait for *all* runners to finish (or *abort_event* to fire).

        We deliberately wait for ``ALL_COMPLETED`` instead of
        ``FIRST_COMPLETED`` so that a single dead account doesn't tear
        down the still-healthy ones — multi-account configs must keep
        the surviving connections up.

        Post-condition: returns *cleanly* only when one of the following
        is true (anything else is re-raised so the gateway can react):

        * ``abort_event`` fired — the gateway asked us to stop.
        * Every runner exited with a permanent auth failure — retrying
          with the same bad credentials would be pointless.

        If runners are externally cancelled while ``abort_event`` is
        still clear (which happens when ``plugin.stop()`` races against
        a concurrent ``plugin.start()`` — see
        :meth:`ChannelGateway.stop_channel`), we raise a transient
        :class:`RuntimeError` so the gateway's exponential-backoff
        reconnect loop kicks in and a fresh connection is established.
        Returning ``None`` here would otherwise be mistaken for
        "webhook / passive mode" by the gateway and leave the channel
        permanently disconnected.
        """
        from flocks.channel.builtin.dingtalk.stream import (
            DingTalkPermanentError,
        )

        if abort_event is None:
            results = await asyncio.gather(
                *self._runner_tasks, return_exceptions=True,
            )
            self._classify_and_raise(
                results,
                permanent_exc_type=DingTalkPermanentError,
                abort_set=False,
            )
            return

        # Note: ``asyncio.gather(...)`` returns a ``_GatheringFuture`` —
        # it must NOT be wrapped in ``asyncio.create_task`` (that helper
        # rejects anything that is not a bare coroutine and would raise
        # ``TypeError: a coroutine was expected``).  ``asyncio.wait``
        # accepts Futures directly, which is what we want here.
        abort_waiter = asyncio.ensure_future(abort_event.wait())
        runners_waiter = asyncio.gather(
            *self._runner_tasks, return_exceptions=True,
        )
        try:
            await asyncio.wait(
                {abort_waiter, runners_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not abort_waiter.done():
                abort_waiter.cancel()
            try:
                await abort_waiter
            except (asyncio.CancelledError, Exception):
                pass

        if runners_waiter.done() and not runners_waiter.cancelled():
            results = runners_waiter.result()
            self._classify_and_raise(
                results,
                permanent_exc_type=DingTalkPermanentError,
                abort_set=abort_event.is_set(),
            )

    @staticmethod
    def _classify_and_raise(
        results: list,
        *,
        permanent_exc_type: type,
        abort_set: bool,
    ) -> None:
        """Inspect runner exit reasons and re-raise when retry is needed.

        Decision matrix (in order):

        1. Any transient (non-permanent, non-cancelled) exception → re-raise
           it so the gateway treats it as a connection error and retries.
        2. Any ``CancelledError`` while ``abort_set`` is False → external
           ``plugin.stop()`` race; raise a transient ``RuntimeError`` so
           the gateway reconnects (otherwise a clean return would be
           mis-classified as passive/webhook mode).
        3. Otherwise return silently — every account either failed
           permanently (logged elsewhere) or shut down cleanly because
           the gateway told us to.
        """
        had_external_cancel = False
        for result in results:
            if not isinstance(result, BaseException):
                continue
            if isinstance(result, permanent_exc_type):
                continue
            if isinstance(result, asyncio.CancelledError):
                if not abort_set:
                    had_external_cancel = True
                continue
            raise result

        if had_external_cancel:
            log.warning("dingtalk.start.external_cancel", {
                "hint": (
                    "runner cancelled without abort signal — likely a "
                    "concurrent restart race; surfacing as transient "
                    "error so the gateway reconnects"
                ),
            })
            raise RuntimeError(
                "DingTalk runner cancelled without abort signal "
                "(concurrent stop/restart race) — reconnecting"
            )

    async def _cancel_runners(self) -> None:
        for task in self._runner_tasks:
            if not task.done():
                task.cancel()
        if self._runner_tasks:
            await asyncio.gather(*self._runner_tasks, return_exceptions=True)
        self._runner_tasks = []

    async def stop(self) -> None:
        from flocks.channel.builtin.dingtalk.client import close_http_client

        await self._cancel_runners()
        try:
            await close_http_client()
        except Exception:
            pass
        # Connection-status bookkeeping is owned by the gateway — see
        # ``ChannelGateway._run_with_reconnect``; calling mark_disconnected
        # here would only race with the gateway's own call.


__all__ = ["DingTalkChannel"]
