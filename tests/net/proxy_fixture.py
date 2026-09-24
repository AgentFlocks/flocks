"""A tiny local proxy for tests: speaks HTTP CONNECT and SOCKS5 and records what it saw."""

from __future__ import annotations

import asyncio
import socket
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class RecordingProxy:
    """Serves HTTP CONNECT / plain HTTP proxying and SOCKS5 on one port; logs each target."""

    host: str = "127.0.0.1"
    port: int = 0
    targets: List[Tuple[str, str, int]] = field(default_factory=list)  # (protocol, host, port)
    proxy_auth: List[Optional[str]] = field(default_factory=list)  # Proxy-Authorization per http request
    socks_users: List[Optional[str]] = field(default_factory=list)  # username offered per socks5 handshake
    _server: Optional[asyncio.AbstractServer] = None

    @property
    def http_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def socks_url(self) -> str:
        return f"socks5://{self.host}:{self.port}"

    @property
    def socks5h_url(self) -> str:
        return f"socks5h://{self.host}:{self.port}"

    async def start(self) -> "RecordingProxy":
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            first = await reader.readexactly(1)
            if first == b"\x05":
                await self._socks5(reader, writer)
            else:
                await self._http(first, reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _tunnel(self, host: str, port: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        await asyncio.gather(self._pipe(reader, upstream_writer), self._pipe(upstream_reader, writer))

    async def _http(self, first: bytes, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = first + await reader.readline()
        method, _, rest = line.decode("latin-1").partition(" ")
        target = rest.split(" ")[0]
        headers = b""
        while True:
            header = await reader.readline()
            headers += header
            if header in (b"\r\n", b"\n", b""):
                break
        auth = None
        for raw_line in headers.decode("latin-1").splitlines():
            name, _, value = raw_line.partition(":")
            if name.strip().lower() == "proxy-authorization":
                auth = value.strip()
        self.proxy_auth.append(auth)
        if method.upper() == "CONNECT":
            host, _, port_text = target.rpartition(":")
            self.targets.append(("connect", host, int(port_text)))
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await self._tunnel(host, int(port_text), reader, writer)
            return
        # absolute-form request: GET http://host:port/path HTTP/1.1 — forward as origin-form
        from urllib.parse import urlsplit

        parts = urlsplit(target)
        host = parts.hostname or ""
        port = parts.port or 80
        self.targets.append(("http", host, port))
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        upstream_writer.write(f"{method} {path} HTTP/1.1\r\n".encode("latin-1") + headers)
        await upstream_writer.drain()
        await asyncio.gather(self._pipe(reader, upstream_writer), self._pipe(upstream_reader, writer))

    async def _socks5(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        n_methods = (await reader.readexactly(1))[0]
        methods = await reader.readexactly(n_methods)
        if b"\x02" in methods:
            # username/password (RFC 1929): accept anything, remember who knocked
            writer.write(b"\x05\x02")
            await writer.drain()
            _version = await reader.readexactly(1)
            user = await reader.readexactly((await reader.readexactly(1))[0])
            await reader.readexactly((await reader.readexactly(1))[0])  # password
            self.socks_users.append(user.decode())
            writer.write(b"\x01\x00")
        else:
            self.socks_users.append(None)
            writer.write(b"\x05\x00")  # no auth
        await writer.drain()
        version, command, _reserved, address_type = await reader.readexactly(4)
        if address_type == 1:
            host = socket.inet_ntoa(await reader.readexactly(4))
        elif address_type == 3:
            length = (await reader.readexactly(1))[0]
            host = (await reader.readexactly(length)).decode()
        elif address_type == 4:
            host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
        else:
            writer.write(b"\x05\x08\x00\x01" + b"\x00" * 6)
            await writer.drain()
            return
        port = struct.unpack("!H", await reader.readexactly(2))[0]
        self.targets.append(("socks5", host, port))
        if command != 1:
            writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
            await writer.drain()
            return
        writer.write(b"\x05\x00\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")
        await writer.drain()
        await self._tunnel(host, port, reader, writer)


@dataclass
class EchoServer:
    """Plain HTTP server answering every request with a small JSON body (the proxy target)."""

    host: str = "127.0.0.1"
    port: int = 0
    hits: int = 0
    _server: Optional[asyncio.AbstractServer] = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def start(self) -> "EchoServer":
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            while True:
                header = await reader.readline()
                if header in (b"\r\n", b"\n", b""):
                    break
            self.hits += 1
            body = b'{"ok":true,"path":"' + request_line.split(b" ")[1] + b'"}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        except (ConnectionError, OSError, IndexError):
            pass
        finally:
            writer.close()
