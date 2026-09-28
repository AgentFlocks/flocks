from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from websockets.asyncio.server import serve as upstream_server

from flocks.config.config import Config
from flocks.utils.http_file_read_guard import _blocked_for_http_read
from flocks.voice import diagnostics, stream
from flocks.voice.protocol import QuietSpeechGain
from test_voice_protocol import response_packet
from test_voice_stream import Browser


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.delenv("FLOCKS_CONFIG_DIR", raising=False)
    # The config directory is resolved once per process; follow this test's root.
    monkeypatch.setattr(Config, "_global_config", None)
    return tmp_path


def _enable(root):
    directory = root / "config" / "voice-diagnostics"
    directory.mkdir(mode=0o700, parents=True)
    (directory / "enabled").write_text("")
    return directory


def _session(directory, name, mtime):
    recorder = diagnostics.SessionRecorder({}, {})
    recorder.name = name
    recorder.finish("ok")
    for suffix in (".input.pcm", ".upstream.pcm", ".json"):
        os.utime(directory / f"{name}{suffix}", (mtime, mtime))


def test_recordings_live_where_http_file_reads_are_always_denied(root):
    directory = _enable(root)
    assert diagnostics.diagnostics_dir() == directory
    assert _blocked_for_http_read(str((directory / "20260926-120000-00000000.input.pcm").resolve()))


def test_off_unless_a_regular_marker_file_exists(root):
    assert not diagnostics.enabled()
    directory = _enable(root)
    assert diagnostics.enabled()
    (directory / "enabled").unlink()
    (root / "elsewhere").write_text("")
    (directory / "enabled").symlink_to(root / "elsewhere")
    assert not diagnostics.enabled()


def test_client_info_keeps_only_well_formed_capture_fields():
    params = {
        "ctx_rate": "16000", "track_rate": "48000", "channels": "1",
        "aec": "1", "ns": "0", "agc": "true", "worklet": "1", "mode": "voice",
        "unknown": "x", "extra": "1",
    }
    assert diagnostics.client_info(params, "Mozilla/5.0 " + "x" * 400) == {
        "ctx_rate": 16000, "track_rate": 48000, "channels": 1,
        "aec": True, "ns": False, "worklet": True, "mode": "voice",
        "user_agent": ("Mozilla/5.0 " + "x" * 400)[:diagnostics.MAX_USER_AGENT_CHARS],
    }
    # Non-ASCII digits and huge numbers are dropped instead of raising.
    hostile = {"ctx_rate": "²", "track_rate": "٤٨٠٠٠", "channels": "9" * 5000, "mode": "loud"}
    assert diagnostics.client_info(hostile, None) == {}


def test_finished_session_is_private_and_complete(root):
    directory = _enable(root)
    recorder = diagnostics.SessionRecorder({"ctx_rate": 16000}, {"hotwords": 3})
    recorder.add_input(b"\x01\x00" * 3200)
    recorder.add_upstream(b"\x02\x00" * 3200)
    recorder.add_transcript("你好", False)
    recorder.add_transcript("你好世界", True)
    recorder.levels = {"peak_dbfs": -20.0}
    assert recorder.finish("ok") == directory / f"{recorder.name}.json"

    files = {path.name for path in directory.iterdir()}
    assert files == {"enabled", f"{recorder.name}.input.pcm", f"{recorder.name}.upstream.pcm", f"{recorder.name}.json"}
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for name in files - {"enabled"}:
        assert stat.S_IMODE((directory / name).stat().st_mode) == 0o600
    assert (directory / f"{recorder.name}.input.pcm").read_bytes() == b"\x01\x00" * 3200
    assert (directory / f"{recorder.name}.upstream.pcm").read_bytes() == b"\x02\x00" * 3200
    meta = json.loads((directory / f"{recorder.name}.json").read_text())
    assert meta["outcome"] == "ok"
    assert meta["client"] == {"ctx_rate": 16000} and meta["config"] == {"hotwords": 3}
    assert meta["timing"]["frames"] == 1 and meta["timing"]["audio_seconds"] == 0.2
    assert [row["text"] for row in meta["transcripts"]] == ["你好", "你好世界"]
    assert meta["levels"] == {"peak_dbfs": -20.0}


def test_frame_timing_stays_bounded_for_many_tiny_frames(root):
    _enable(root)
    recorder = diagnostics.SessionRecorder({}, {})
    for _ in range(20000):
        recorder.add_input(b"\x00\x00")
    timing = json.loads(recorder.finish("ok").read_text())["timing"]
    assert timing["frames"] == 20000
    assert len(timing["first_offsets_ms"]) == len(timing["last_offsets_ms"]) == diagnostics.EDGE_OFFSETS
    assert sum(timing["gap_histogram"].values()) == 19999


def test_real_time_capture_reports_one_second_of_audio_per_second_of_arrival(root, monkeypatch):
    _enable(root)
    clock = iter([100.0, 100.0, 100.2, 100.4, 100.6, 100.8])
    monkeypatch.setattr(diagnostics, "time", SimpleNamespace(time=time.time, monotonic=lambda: next(clock, 101.0)))
    recorder = diagnostics.SessionRecorder({}, {})
    for _ in range(5):
        recorder.add_input(b"\x00\x00" * 3200)  # 200 ms of audio, arriving every 200 ms
    timing = json.loads(recorder.finish("ok").read_text())["timing"]
    assert timing["audio_seconds"] == 1.0
    assert timing["last_frame_ms"] - timing["first_frame_ms"] == 800
    # Only the four frames captured after the first arrival fall inside the 800 ms span.
    assert timing["audio_per_arrival_second"] == 1.0


def test_switching_off_during_a_session_discards_it(root):
    directory = _enable(root)
    recorder = diagnostics.SessionRecorder({}, {})
    recorder.add_input(b"\x01\x00" * 320)
    (directory / "enabled").unlink()
    assert recorder.finish("ok") is None
    assert list(directory.iterdir()) == []
    # A directory swapped for a symlink is not followed either.
    target = root / "redirected"
    target.mkdir()
    directory.rmdir()
    directory.symlink_to(target, target_is_directory=True)
    (target / "enabled").write_text("")
    assert not diagnostics.enabled()
    assert recorder.finish("ok") is None
    assert [path.name for path in target.iterdir()] == ["enabled"]


def test_prune_keeps_the_newest_sessions_for_one_day_only(root):
    directory = _enable(root)
    (directory / "notes.txt").write_text("not ours")
    (directory / "２0260926-120000-00000000.json").write_text("non-ASCII digits are not ours")
    now = time.time()
    names = [f"20260926-1200{index:02d}-{index:08x}" for index in range(7)]
    for index, name in enumerate(names):
        _session(directory, name, now - 60 * (7 - index))
    diagnostics.prune(directory, now=now)
    kept = {path.name.split(".")[0] for path in directory.iterdir() if path.name.startswith("2026")}
    assert kept == set(names[-diagnostics.KEEP_SESSIONS:])
    diagnostics.prune(directory, now=now + diagnostics.MAX_AGE_SECONDS + 3600)
    assert sorted(path.name for path in directory.iterdir()) == sorted(
        ["enabled", "notes.txt", "２0260926-120000-00000000.json"])


def test_prune_tolerates_files_removed_by_a_concurrent_prune(root, monkeypatch):
    directory = _enable(root)
    _session(directory, "20260926-120000-00000001", time.time())
    original = Path.stat

    def vanished(self, *args, **kwargs):
        if self.name.endswith(".upstream.pcm") and kwargs.get("follow_symlinks", True):
            raise FileNotFoundError(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", vanished)
    diagnostics.prune(directory)


def test_start_session_prunes_stale_recordings_even_when_switched_off(root):
    directory = _enable(root)
    _session(directory, "20260926-120000-00000001", time.time() - diagnostics.MAX_AGE_SECONDS - 60)
    (directory / "enabled").unlink()
    assert diagnostics.start_session({}, None, {}) is None
    assert list(directory.iterdir()) == []


def test_start_session_falls_back_to_a_plain_session_on_any_error(root, monkeypatch):
    _enable(root)

    def broken(*_args, **_kwargs):
        raise RuntimeError("no recorder")

    monkeypatch.setattr(diagnostics, "SessionRecorder", broken)
    assert diagnostics.start_session({"ctx_rate": "16000"}, None, {}) is None


@pytest.mark.asyncio
async def test_relay_keeps_browser_audio_upstream_audio_and_transcripts(root, monkeypatch):
    directory = _enable(root)

    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        await socket.recv()
        await socket.send(response_packet("测试"))
        await socket.recv()
        await socket.send(response_packet("测试语音", final=True))

    first, second = b"\x01\x00" * 3200, b"\x02\x00" * 3200
    recorder = diagnostics.SessionRecorder({"mode": "voice"}, {})
    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([
            {"type": "websocket.receive", "bytes": first},
            {"type": "websocket.receive", "bytes": second},
            {"type": "websocket.receive", "text": '{"type":"stop"}'},
        ])
        await stream.serve(browser, "test-only-credential", diagnostics=recorder)
    assert browser.output[-1] == {"type": "done"}
    assert (directory / f"{recorder.name}.input.pcm").read_bytes() == first + second
    gain = QuietSpeechGain()
    expected = gain.process(first) + gain.process(second)
    assert (directory / f"{recorder.name}.upstream.pcm").read_bytes() == expected
    meta = json.loads((directory / f"{recorder.name}.json").read_text())
    assert meta["outcome"] == "ok"
    assert [row["text"] for row in meta["transcripts"]] == ["测试", "测试语音"]
    assert meta["timing"]["frames"] == 2
    assert meta["levels"]["seconds"] == 0.4


@pytest.mark.asyncio
async def test_error_outcome_is_recorded_and_a_failing_recorder_is_harmless(root, monkeypatch):
    directory = _enable(root)

    async def rejected(socket):
        await socket.recv()
        await socket.send(bytes((0x11, 0xF0, 0x10, 0)) + b"\x00" * 8)

    async with upstream_server(rejected, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        recorder = diagnostics.SessionRecorder({}, {})
        browser = Browser([])
        await stream.serve(browser, "test-only-credential", diagnostics=recorder)
        assert browser.output[-1]["type"] == "error"
        meta = json.loads((directory / f"{recorder.name}.json").read_text())
        assert meta["outcome"] == browser.output[-1]["code"]

        broken = diagnostics.SessionRecorder({}, {})

        def fail(_outcome):
            raise OSError("disk full")

        broken.finish = fail
        browser = Browser([])
        await stream.serve(browser, "test-only-credential", diagnostics=broken)
        assert browser.output[-1]["type"] == "error" and browser.closed


@pytest.mark.asyncio
async def test_cancelled_session_is_saved_and_the_cancellation_still_propagates(root, monkeypatch):
    directory = _enable(root)
    initialized = asyncio.Event()

    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        initialized.set()
        await socket.wait_closed()

    recorder = diagnostics.SessionRecorder({}, {})
    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([])
        task = asyncio.create_task(stream.serve(browser, "test-only-credential", diagnostics=recorder))
        await asyncio.wait_for(initialized.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert browser.closed
    meta = json.loads((directory / f"{recorder.name}.json").read_text())
    assert meta["outcome"] == "cancelled"


@pytest.mark.asyncio
async def test_browser_disconnect_is_saved_as_client_disconnected(root, monkeypatch):
    directory = _enable(root)

    async def upstream(socket):
        await socket.recv()
        await socket.send(response_packet())
        await socket.wait_closed()

    recorder = diagnostics.SessionRecorder({}, {})
    audio = b"\x01\x00" * 3200
    async with upstream_server(upstream, "127.0.0.1", 0) as server:
        monkeypatch.setattr(stream, "ENDPOINT", f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        browser = Browser([
            {"type": "websocket.receive", "bytes": audio},
            {"type": "websocket.disconnect", "code": 1001},
        ])
        await stream.serve(browser, "test-only-credential", diagnostics=recorder)
    assert not any(message["type"] == "error" for message in browser.output)
    meta = json.loads((directory / f"{recorder.name}.json").read_text())
    assert meta["outcome"] == "client_disconnected"
    assert (directory / f"{recorder.name}.input.pcm").read_bytes() == audio
