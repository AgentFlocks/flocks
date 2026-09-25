from __future__ import annotations

import asyncio
import gzip
import json
import logging
import struct

import pytest
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve as upstream_server

from flocks.voice import stream
from flocks.voice.protocol import QuietSpeechGain
from test_voice_protocol import response_packet


class Browser:
    def __init__(self, messages):
        self.messages = asyncio.Queue()
        for message in messages:
            self.messages.put_nowait(message)
        self.output = []
        self.closed = False

    async def receive(self):
        return await self.messages.get()

    async def send_json(self, payload):
        self.output.append(payload)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_real_upstream_socket_roundtrip(monkeypatch):
    captured = []
    observed_headers = {}

    async def upstream(socket):
        observed_headers.update(socket.request.headers)
        captured.append(await socket.recv())
        await socket.send(response_packet())
        captured.append(await socket.recv())
        await socket.send(response_packet("测试"))
        captured.append(await socket.recv())
        await socket.send(response_packet("测试语音", final=True))

    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([
            {"type": "websocket.receive", "bytes": b"\x01\x00" * 3200},
            {"type": "websocket.receive", "bytes": b"\x02\x00" * 3200},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ])
        await stream.serve(browser, "test-only-credential")
    assert browser.output == [
        {"type": "ready"},
        {"type": "transcript", "text": "测试", "is_final": False},
        {"type": "transcript", "text": "测试语音", "is_final": True},
        {"type": "done"},
    ]
    assert browser.closed
    assert observed_headers["x-api-key"] == "test-only-credential"
    assert observed_headers["x-api-resource-id"] == "volc.seedasr.sauc.duration"
    assert captured[-1][1] == 0x23
    assert struct.unpack_from(">i", captured[-1], 4)[0] == -3
    # the final packet carries the last real audio, after the same gain the relay applies to every packet
    gain = QuietSpeechGain()
    gain.process(b"\x01\x00" * 3200)
    assert gzip.decompress(captured[-1][12:]) == gain.process(b"\x02\x00" * 3200)


@pytest.mark.asyncio
async def test_session_logs_loudness_and_length_but_not_what_was_said(monkeypatch):
    logged = []

    class Recorder:
        def info(self, message, extra=None):
            logged.append((message, extra))

    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        await socket.recv()
        await socket.recv()
        await socket.send(response_packet("测试语音", final=True))

    monkeypatch.setattr(stream, "_AUDIO_LOG", Recorder())
    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([
            {"type": "websocket.receive", "bytes": b"\x00\x00" * 3200},
            {"type": "websocket.receive", "bytes": b"\x00\x10" * 3200},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ])
        await stream.serve(browser, "test-only-credential")
    assert browser.output[-1] == {"type": "done"}
    assert [message for message, _ in logged] == ["voice.stream.audio"]
    summary = logged[0][1]
    assert summary["seconds"] == 0.4 and summary["text_chars"] == 4
    assert summary["silent_frames_pct"] == 50.0
    assert summary["peak_dbfs"] == round(20 * __import__("math").log10(4096 / 32768), 1)
    assert summary["gain_db_p50"] == 0.0 and summary["gain_db_max"] == 0.0  # -18 dBFS needs no help
    assert "测试" not in json.dumps(summary, ensure_ascii=False)


@pytest.mark.asyncio
async def test_quiet_audio_is_raised_before_it_goes_upstream(monkeypatch):
    import array
    import math

    sent = []

    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        sent.append(await socket.recv())
        sent.append(await socket.recv())
        await socket.send(response_packet("小声", final=True))

    # a 300 Hz tone 60 dB under full scale, like quiet speech from a phone, after a moment of near silence
    amplitude = 32768 * 10 ** (-60 / 20) * math.sqrt(2)
    tone = [round(amplitude * math.sin(2 * math.pi * 300 * i / 16000)) for i in range(3200)]
    hush = array.array("h", [1, -1] * 800 + tone[:1600]).tobytes()
    quiet = array.array("h", tone).tobytes()
    monkeypatch.setattr(stream, "_AUDIO_LOG", type("Quiet", (), {"info": lambda self, *args: None})())
    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([
            {"type": "websocket.receive", "bytes": hush},
            {"type": "websocket.receive", "bytes": quiet},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ])
        await stream.serve(browser, "test-only-credential")
    assert browser.output[-1] == {"type": "done"}
    upstream_audio = array.array("h", gzip.decompress(sent[-1][12:]))
    rms = math.sqrt(sum(value * value for value in upstream_audio) / len(upstream_audio))
    target = QuietSpeechGain.TARGET_DBFS
    assert target - 1 <= 20 * math.log10(rms / 32768) <= target + 1  # raised to the target on the way to Doubao


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    {"type": "websocket.receive", "bytes": b"x"},
    {"type": "websocket.receive", "bytes": b"xx" * 16001},
    {"type": "websocket.receive", "text": "not-json"},
    {"type": "websocket.receive", "text": '{"type":"start"}'},
])
async def test_invalid_client_frames_close_upstream(monkeypatch, message):
    closed = asyncio.Event()

    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        await socket.wait_closed()
        closed.set()

    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([message])
        await stream.serve(browser, "test")
        await asyncio.wait_for(closed.wait(), 1)
    assert browser.output[-1]["type"] == "error"
    assert browser.closed


@pytest.mark.asyncio
async def test_silent_stop_does_not_send_empty_audio(monkeypatch):
    audio = []

    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        async for packet in socket:
            audio.append(packet)

    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([{"type": "websocket.receive", "text": '{"type":"stop"}'}])
        await stream.serve(browser, "test")
    assert browser.output[0] == {"type": "ready"}
    assert browser.output[-1]["type"] == "error"
    assert browser.output[-1]["code"] == "no_speech"
    assert not audio


@pytest.mark.asyncio
async def test_provider_error_never_echoes_secret(monkeypatch):
    async def upstream(socket):
        await socket.recv()
        await socket.send(b"\x11\xf0\x11\0test-only-credential")

    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([])
        await stream.serve(browser, "test-only-credential")
    assert browser.output[-1]["type"] == "error"
    assert "test-only-credential" not in json.dumps(browser.output)


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [True, False])
async def test_disconnect_or_timeout_releases_upstream(monkeypatch, disconnect):
    closed = asyncio.Event()

    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        await socket.wait_closed()
        closed.set()

    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        monkeypatch.setattr(stream, "IO_TIMEOUT", .1)
        browser = Browser([{"type": "websocket.disconnect"}] if disconnect else [])
        await stream.serve(browser, "test")
        await asyncio.wait_for(closed.wait(), 1)
    assert browser.closed
    if disconnect:
        assert browser.output == [{"type": "ready"}]
    else:
        assert browser.output[-1]["type"] == "error"


@pytest.mark.asyncio
async def test_disabling_access_releases_active_upstream(monkeypatch):
    closed = asyncio.Event()

    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        await socket.wait_closed()
        closed.set()

    async def disabled():
        return False

    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        monkeypatch.setattr(stream, "ACCESS_CHECK_INTERVAL", .01)
        browser = Browser([])
        await stream.serve(browser, "test", authorize=disabled)
        await asyncio.wait_for(closed.wait(), 1)
    assert browser.output[-1]["type"] == "error"
    assert "配置" in browser.output[-1]["message"]
    assert browser.closed


@pytest.mark.asyncio
async def test_async_upstream_silence_does_not_timeout_active_audio(monkeypatch):
    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        async for packet in socket:
            if packet[1] & 2:
                await socket.send(response_packet("完成", final=True))
                return

    class SlowBrowser(Browser):
        async def receive(self):
            await asyncio.sleep(.03)
            return await super().receive()

    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        monkeypatch.setattr(stream, "IO_TIMEOUT", .1)
        browser = SlowBrowser([
            *({"type": "websocket.receive", "bytes": b"\0\0" * 3200} for _ in range(6)),
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ])
        await stream.serve(browser, "test")
    assert browser.output[-1] == {"type": "done"}
    assert browser.output[-2]["text"] == "完成"


@pytest.mark.asyncio
async def test_final_response_timeout_and_audio_duration_limits(monkeypatch):
    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        async for _packet in socket:
            pass

    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        monkeypatch.setattr(stream, "FINAL_TIMEOUT", .05)
        browser = Browser([
            {"type": "websocket.receive", "bytes": b"\0\0" * 3200},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ])
        await stream.serve(browser, "test")
        assert browser.output[-1]["type"] == "error"
        assert "超时" in browser.output[-1]["message"]
        monkeypatch.setattr(stream, "MAX_AUDIO_SECONDS", 1)
        browser = Browser([
            {"type": "websocket.receive", "bytes": b"\0\0" * 16000},
            {"type": "websocket.receive", "bytes": b"\0\0" * 3200},
        ])
        await stream.serve(browser, "test")
        assert browser.output[-1]["type"] == "error"
        assert "最多" in browser.output[-1]["message"]


@pytest.mark.asyncio
async def test_cancelled_relay_closes_provider_connection(monkeypatch):
    initialized = asyncio.Event()
    closed = asyncio.Event()

    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        initialized.set()
        await socket.wait_closed()
        closed.set()

    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([])
        task = asyncio.create_task(stream.serve(browser, "test"))
        await asyncio.wait_for(initialized.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(closed.wait(), 1)
    assert browser.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "code", "hint"), [
    (401, "upstream_auth", "凭据"),
    (403, "upstream_forbidden", "权限"),
    (429, "upstream_rate_limited", "频繁"),
    (500, "upstream_unavailable", "暂时不可用"),
    (503, "upstream_unavailable", "暂时不可用"),
    (400, "upstream_rejected", "拒绝建立连接"),
])
async def test_handshake_failures_are_classified_without_provider_secrets(monkeypatch, caplog, status, code, hint):
    async def refuse(connection, _request):
        response = connection.respond(status, "private-provider-body credential-must-not-echo")
        response.headers["X-Private-Diagnostic"] = "private-header-must-not-echo"
        return response

    async def unused(_socket):
        pytest.fail("rejected handshake must not start an audio session")

    caplog.set_level(logging.DEBUG, logger="flocks.voice")
    async with upstream_server(
        unused, "127.0.0.1", 0, process_request=refuse,
        logger=stream._TRANSPORT_LOGGER,
    ) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([])
        await stream.serve(browser, "credential-must-not-echo")
    assert browser.closed
    assert len(browser.output) == 1
    assert browser.output[0]["type"] == "error"
    assert browser.output[0]["code"] == code
    assert hint in browser.output[0]["message"]
    assert f"code={code} http_status={status}" in caplog.text
    combined = json.dumps(browser.output) + caplog.text
    for private in ["credential-must-not-echo", "private-provider-body", "private-header-must-not-echo"]:
        assert private not in combined


@pytest.mark.asyncio
async def test_provider_close_reason_is_not_exposed(monkeypatch, caplog):
    async def upstream(socket):
        await socket.recv()
        await socket.close(1011, "private-close-reason credential-must-not-echo")

    async with upstream_server(upstream, "127.0.0.1", 0, logger=stream._TRANSPORT_LOGGER) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([])
        await stream.serve(browser, "credential-must-not-echo")
    assert browser.output[0]["code"] == "upstream_closed"
    assert browser.closed
    combined = json.dumps(browser.output) + caplog.text
    assert "private-close-reason" not in combined
    assert "credential-must-not-echo" not in combined


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "code"), [
    (TimeoutError("credential-must-not-echo"), "upstream_timeout"),
    (OSError("credential-must-not-echo"), "upstream_unavailable"),
])
async def test_transport_exception_text_is_never_exposed(monkeypatch, caplog, error, code):
    async def failed(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(stream, "relay", failed)
    browser = Browser([])
    await stream.serve(browser, "credential-must-not-echo")
    assert browser.output[0]["code"] == code
    assert "credential-must-not-echo" not in json.dumps(browser.output) + caplog.text
    assert browser.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("pcm,code", [(b"\0\0" * 3200, "no_speech"), (b"\x01\x00" * 3200, "audio_too_quiet")])
async def test_empty_final_is_explained_and_hotwords_are_forwarded(monkeypatch, pcm, code):
    requests = []

    async def upstream(socket):
        first = await socket.recv()
        requests.append(json.loads(gzip.decompress(first[12:])))
        await socket.send(response_packet())
        async for packet in socket:
            if packet[1] & 2:
                await socket.send(response_packet("", final=True))
                return

    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([
            {"type": "websocket.receive", "bytes": pcm},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ])
        await stream.serve(browser, "test", hotwords=("Flocks",))
    assert browser.output[-1]["type"] == "error"
    assert browser.output[-1]["code"] == code
    assert {"type": "done"} not in browser.output
    assert json.loads(requests[0]["request"]["corpus"]["context"]) == {"hotwords": [{"word": "Flocks"}]}


@pytest.mark.asyncio
async def test_revocation_after_stop_interrupts_final_response(monkeypatch):
    stopped = asyncio.Event()
    revoked = asyncio.Event()
    provider_closed = asyncio.Event()

    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        async for packet in socket:
            if packet[1] & 2:
                stopped.set()
                await revoked.wait()
                await socket.wait_closed()
                provider_closed.set()
                return

    async def authorize():
        if stopped.is_set():
            revoked.set()
            return False
        return True

    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        monkeypatch.setattr(stream, "ACCESS_CHECK_INTERVAL", .01)
        monkeypatch.setattr(stream, "FINAL_TIMEOUT", .5)
        browser = Browser([
            {"type": "websocket.receive", "bytes": b"\x01\x00" * 3200},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ])
        await asyncio.wait_for(stream.serve(browser, "test", authorize=authorize), .3)
        await asyncio.wait_for(provider_closed.wait(), .1)
    assert revoked.is_set()
    assert browser.output[-1]["code"] == "voice_error"
    assert "配置" in browser.output[-1]["message"]
    assert not any(item["type"] in ("transcript", "done") for item in browser.output)
