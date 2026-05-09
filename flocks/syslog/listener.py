"""Asyncio UDP/TCP syslog listeners."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Union

from flocks.syslog.parser import parse_syslog

OnSyslogMessage = Callable[[dict], Union[None, Awaitable[None]]]


class SyslogUDPProtocol(asyncio.DatagramProtocol):
    """Receive syslog datagrams and invoke async callback with parsed dict."""

    def __init__(
        self,
        on_message: OnSyslogMessage,
        format_hint: str,
    ) -> None:
        self._on_message = on_message
        self._format_hint = format_hint

    def datagram_received(self, data: bytes, _addr) -> None:  # noqa: ANN001
        text = data.decode("utf-8", errors="replace")
        parsed = parse_syslog(text, self._format_hint)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._safe_dispatch(parsed))

    async def _safe_dispatch(self, parsed: dict) -> None:
        try:
            res = self._on_message(parsed)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            # Logged by caller / manager
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
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            parsed = parse_syslog(text, format_hint)
            try:
                res = on_message(parsed)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass
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
