"""Asyncio UDP/TCP syslog listeners."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional, Union

from flocks.ingest.syslog.parser import parse_syslog
from flocks.utils.log import Log

log = Log.create(service="syslog.listener")

OnSyslogMessage = Callable[[dict], Union[None, Awaitable[None]]]
OnBound = Callable[[], None]

# ``StreamReader.readline`` raises once a single line exceeds the reader limit;
# a syslog peer that sends one oversized line must not lose the whole
# connection (and every line queued behind it), so we bound lines explicitly
# and skip the oversized one.
TCP_MAX_LINE_BYTES = 64 * 1024


def _describe_bind_error(exc: OSError) -> str:
    """Include the underlying socket error when the loop wrapped it.

    uvloop reports ``could not bind to local_addr (...)`` and chains the real
    ``OSError`` (EADDRINUSE / EACCES ...) as ``__cause__``; surface both so the
    operator can tell "port busy" from "permission denied".
    """
    cause = exc.__cause__ if isinstance(exc.__cause__, OSError) else None
    if cause is None or str(cause) in str(exc):
        return str(exc)
    return f"{exc}: {cause}"


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
        text = data.decode("utf-8", errors="replace")
        parsed = parse_syslog(text, self._format_hint)
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
        except Exception:
            pass

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
    on_bound: Optional[OnBound] = None,
) -> None:
    loop = asyncio.get_running_loop()
    try:
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: SyslogUDPProtocol(on_message, format_hint),
            local_addr=(host, port),
        )
    except OSError as exc:
        raise OSError(_describe_bind_error(exc)) from exc
    # Only now is the socket really bound; callers key "listening" off this.
    if on_bound is not None:
        on_bound()
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
            try:
                line = await reader.readuntil(b"\n")
            except asyncio.IncompleteReadError as exc:
                # Peer closed without a trailing newline: process the tail.
                line = exc.partial
                if not line:
                    break
            except asyncio.LimitOverrunError as exc:
                # One oversized line: drop it and keep the connection alive
                # instead of tearing down the stream for every later message.
                log.warning(
                    "syslog.tcp_line_too_long",
                    {"limit_bytes": TCP_MAX_LINE_BYTES, "peer": _peer_name(writer)},
                )
                await _discard_oversized_line(reader, exc.consumed)
                continue
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


def _peer_name(writer: asyncio.StreamWriter) -> str:
    try:
        peer = writer.get_extra_info("peername")
    except Exception:
        return ""
    return f"{peer[0]}:{peer[1]}" if isinstance(peer, tuple) and len(peer) >= 2 else ""


async def _discard_oversized_line(reader: asyncio.StreamReader, consumed: int) -> None:
    """Skip the rest of a line that overran the reader limit."""
    if consumed:
        await reader.readexactly(consumed)
    while True:
        try:
            await reader.readuntil(b"\n")
            return
        except asyncio.IncompleteReadError:
            return
        except asyncio.LimitOverrunError as exc:
            if exc.consumed:
                await reader.readexactly(exc.consumed)
            else:
                # Nothing consumable in the buffer: pull a chunk to make progress.
                await reader.read(TCP_MAX_LINE_BYTES)


async def run_tcp_syslog_server(
    host: str,
    port: int,
    format_hint: str,
    on_message: OnSyslogMessage,
    *,
    abort_event: asyncio.Event,
    on_bound: Optional[OnBound] = None,
) -> None:
    async def handle_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await _handle_tcp_client(reader, writer, format_hint, on_message)

    try:
        server = await asyncio.start_server(handle_client, host, port, limit=TCP_MAX_LINE_BYTES)
    except OSError as exc:
        raise OSError(_describe_bind_error(exc)) from exc
    if on_bound is not None:
        on_bound()
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
