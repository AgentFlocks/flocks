"""Asyncio UDP/TCP syslog listeners."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Union

from flocks.ingest.syslog.parser import parse_syslog
from flocks.workflow import soc_diagnostics

OnSyslogMessage = Callable[[dict], Union[None, Awaitable[None]]]


def _diagnose(callback, event, **fields):
    soc_diagnostics.record(getattr(callback, "_diagnostic_workflow_id", None), event, **fields)


class SyslogUDPProtocol(asyncio.DatagramProtocol):
    """Receive syslog datagrams and invoke async callback with parsed dict.

    The *on_message* callback is expected to be non-blocking (e.g. a queue
    put_nowait).  This protocol deliberately does NOT create unbounded asyncio
    tasks on every datagram — the caller owns concurrency control.
    """

    def __init__(
        self,
        on_message: OnSyslogMessage,
        format_hint: str,
    ) -> None:
        self._on_message = on_message
        self._format_hint = format_hint

    def datagram_received(self, data: bytes, _addr) -> None:  # noqa: ANN001
        _diagnose(self._on_message, "transport.received", count=len(data))
        text = data.decode("utf-8", errors="replace")
        try:
            parsed = parse_syslog(text, self._format_hint)
        except Exception as exc:
            _diagnose(self._on_message, "transport.parse_failed", error_type=type(exc).__name__)
            raise
        try:
            res = self._on_message(parsed)
            # If the callback returns a coroutine (legacy path), schedule it
            # but only once — do NOT create_task without bound.
            if asyncio.iscoroutine(res):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return
                loop.create_task(self._safe_await(res))
        except Exception as exc:
            _diagnose(self._on_message, "transport.callback_failed", error_type=type(exc).__name__)

    def error_received(self, exc):
        _diagnose(self._on_message, "transport.socket_error", error_type=type(exc).__name__)

    @staticmethod
    async def _safe_await(coro) -> None:  # noqa: ANN001
        try:
            await coro
        except Exception:
            pass


async def run_udp_syslog_server(
    host: str,
    port: int,
    format_hint: str,
    on_message: OnSyslogMessage,
    *,
    abort_event: asyncio.Event,
) -> None:
    loop = asyncio.get_running_loop()
    transport, _protocol = await loop.create_datagram_endpoint(
        lambda: SyslogUDPProtocol(on_message, format_hint),
        local_addr=(host, port),
    )
    try:
        await abort_event.wait()
    finally:
        transport.close()


async def _handle_tcp_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    format_hint: str,
    on_message: OnSyslogMessage,
) -> None:
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            _diagnose(on_message, "transport.received", count=len(line))
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            parsed = parse_syslog(text, format_hint)
            try:
                res = on_message(parsed)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                _diagnose(on_message, "transport.callback_failed", error_type=type(exc).__name__)
    except Exception as exc:
        _diagnose(on_message, "transport.client_failed", error_type=type(exc).__name__)
        raise
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def run_tcp_syslog_server(
    host: str,
    port: int,
    format_hint: str,
    on_message: OnSyslogMessage,
    *,
    abort_event: asyncio.Event,
) -> None:
    async def handle_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await _handle_tcp_client(reader, writer, format_hint, on_message)

    server = await asyncio.start_server(handle_client, host, port)
    serve_task: asyncio.Task[None] | None = None
    try:
        serve_task = asyncio.create_task(server.serve_forever())
        await abort_event.wait()
    finally:
        if serve_task and not serve_task.done():
            serve_task.cancel()
            try:
                await serve_task
            except asyncio.CancelledError:
                pass
        server.close()
        await server.wait_closed()
