"""Opt-in capture of voice sessions for diagnosing recognition quality.

Off unless someone with server access creates the regular file
``<config dir>/voice-diagnostics/enabled``; nothing in the API can turn it on.
The config directory is always denied to the HTTP file routes, so recordings
cannot leak through them even when a project root encloses the flocks root.
That guard does not cover agent tools.
While the marker exists, every voice session of every user keeps:

* ``<name>.input.pcm``: 16 kHz mono PCM16 exactly as the browser sent it,
* ``<name>.upstream.pcm``: the same audio after the relay's gain, as sent upstream,
* ``<name>.json``: client capture parameters, frame timing, levels, outcome
  and every transcript the recogniser returned.

Meant for a test server used by testers only. The directory is private to the
service account (0700, files 0600). Only the newest ``KEEP_SESSIONS`` sessions
are kept and none longer than ``MAX_AGE_SECONDS``; stale sessions are pruned at
the start of every voice session even after the marker is removed, and pruning
only ever removes files matching the names above. To stop and erase at once,
delete the marker and the session files.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from flocks.config.config import Config

KEEP_SESSIONS = 5
MAX_AGE_SECONDS = 24 * 3600
MAX_TRANSCRIPTS = 400
MAX_TRANSCRIPT_CHARS = 4000
MAX_USER_AGENT_CHARS = 300
EDGE_OFFSETS = 50
_MARKER = "enabled"
_NAME = re.compile(r"^([0-9]{8}-[0-9]{6}-[0-9a-f]{8})\.(?:input\.pcm|upstream\.pcm|json)$")
_SUFFIXES = (".input.pcm", ".upstream.pcm", ".json")
# Query parameters the browser may report about its capture path.
_INT_FIELDS = {"ctx_rate": (8000, 384000), "track_rate": (8000, 384000), "channels": (1, 32)}
_BOOL_FIELDS = ("aec", "ns", "agc", "worklet")
_CHOICE_FIELDS = {"mode": {"voice", "raw"}}
# Upper bounds (ms) of the frame arrival gap histogram; the last bucket is open.
_GAP_BUCKETS = (100, 250, 500, 1000)


def diagnostics_dir() -> Path:
    return Config.get_config_path() / "voice-diagnostics"


def _usable(directory: Path) -> bool:
    return not directory.is_symlink() and directory.is_dir()


def enabled() -> bool:
    directory = diagnostics_dir()
    marker = directory / _MARKER
    try:
        return _usable(directory) and not marker.is_symlink() and marker.is_file()
    except OSError:
        return False


def client_info(params: Mapping[str, str], user_agent: str | None) -> dict:
    """Keep only well-formed capture parameters; unknown keys are dropped."""
    info: dict = {}
    for key, (low, high) in _INT_FIELDS.items():
        raw = params.get(key)
        if raw is not None and raw.isascii() and raw.isdigit() and len(raw) <= 6 and low <= int(raw) <= high:
            info[key] = int(raw)
    for key in _BOOL_FIELDS:
        raw = params.get(key)
        if raw in ("1", "0"):
            info[key] = raw == "1"
    for key, choices in _CHOICE_FIELDS.items():
        raw = params.get(key)
        if raw in choices:
            info[key] = raw
    if user_agent:
        info["user_agent"] = user_agent[:MAX_USER_AGENT_CHARS]
    return info


class SessionRecorder:
    """Collects one session in memory; ``finish`` writes it and prunes old ones.

    The relay bounds a session to five minutes of audio, which bounds each
    buffer here to about 9.6 MB; frame timing is kept as counts and edges only.
    """

    def __init__(self, client: dict, config: dict) -> None:
        self.started = time.time()
        self._clock = time.monotonic()
        self.name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.client = client
        self.config = config
        self.input = bytearray()
        self.upstream = bytearray()
        self.transcripts: list[dict] = []
        self.levels: dict = {}
        self._frames = 0
        self._first_frame_bytes = 0
        self._first_offsets: list[int] = []
        self._last_offsets: deque[int] = deque(maxlen=EDGE_OFFSETS)
        self._gap_counts = [0] * (len(_GAP_BUCKETS) + 1)
        self._max_gap = 0

    def _offset_ms(self) -> int:
        return round((time.monotonic() - self._clock) * 1000)

    def add_input(self, pcm: bytes) -> None:
        self.input.extend(pcm)
        offset = self._offset_ms()
        if self._last_offsets:
            gap = offset - self._last_offsets[-1]
            self._max_gap = max(self._max_gap, gap)
            self._gap_counts[sum(gap > bound for bound in _GAP_BUCKETS)] += 1
        if len(self._first_offsets) < EDGE_OFFSETS:
            self._first_offsets.append(offset)
        self._last_offsets.append(offset)
        if not self._frames:
            self._first_frame_bytes = len(pcm)
        self._frames += 1

    def add_upstream(self, pcm: bytes) -> None:
        self.upstream.extend(pcm)

    def add_transcript(self, text: str, final: bool) -> None:
        if len(self.transcripts) < MAX_TRANSCRIPTS:
            self.transcripts.append({"t_ms": self._offset_ms(), "final": final,
                                     "text": text[:MAX_TRANSCRIPT_CHARS]})

    def _timing(self) -> dict:
        audio_seconds = len(self.input) / 32000
        timing = {"frames": self._frames, "audio_seconds": round(audio_seconds, 3),
                  "wall_seconds": round(time.monotonic() - self._clock, 3)}
        if self._frames > 1:
            first, last = self._first_offsets[0], self._last_offsets[-1]
            labels = [f"<={bound}ms" for bound in _GAP_BUCKETS] + [f">{_GAP_BUCKETS[-1]}ms"]
            timing.update(first_frame_ms=first, last_frame_ms=last, max_gap_ms=self._max_gap,
                          gap_histogram=dict(zip(labels, self._gap_counts)),
                          first_offsets_ms=self._first_offsets, last_offsets_ms=list(self._last_offsets))
            if last > first:
                # Audio produced per second of arrival; about 1.0 when capture runs in real time.
                # The first frame was captured before it arrived, so it is outside the span.
                produced = (len(self.input) - self._first_frame_bytes) / 32000
                timing["audio_per_arrival_second"] = round(produced / ((last - first) / 1000), 3)
        return timing

    def finish(self, outcome: str) -> Path | None:
        """Write the session unless diagnostics were switched off meanwhile."""
        directory = diagnostics_dir()
        if not enabled():
            return None
        directory.chmod(0o700)
        meta = {
            "name": self.name,
            "started_at": datetime.fromtimestamp(self.started, timezone.utc).isoformat(),
            "outcome": outcome,
            "client": self.client,
            "config": self.config,
            "timing": self._timing(),
            "levels": self.levels,
            "input_bytes": len(self.input),
            "upstream_bytes": len(self.upstream),
            "transcripts": self.transcripts,
        }
        _write(directory / f"{self.name}.input.pcm", memoryview(self.input))
        _write(directory / f"{self.name}.upstream.pcm", memoryview(self.upstream))
        _write(directory / f"{self.name}.json",
               (json.dumps(meta, ensure_ascii=False, indent=1) + "\n").encode())
        return directory / f"{self.name}.json"


def _write(path: Path, data: bytes | memoryview) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def prune(directory: Path, *, now: float | None = None) -> None:
    """Remove all but the newest sessions, and any older than the age limit."""
    now = time.time() if now is None else now
    sessions: dict[str, float] = {}
    for entry in directory.iterdir():
        match = _NAME.match(entry.name)
        if not match:
            continue
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            stamp = entry.stat().st_mtime
        except FileNotFoundError:
            continue  # removed by a concurrent prune
        sessions[match.group(1)] = max(stamp, sessions.get(match.group(1), 0.0))
    newest = sorted(sessions, key=lambda name: (sessions[name], name), reverse=True)
    for index, name in enumerate(newest):
        if index >= KEEP_SESSIONS or now - sessions[name] > MAX_AGE_SECONDS:
            for suffix in _SUFFIXES:
                (directory / f"{name}{suffix}").unlink(missing_ok=True)


def start_session(params: Mapping[str, str], user_agent: str | None, config: dict) -> SessionRecorder | None:
    """Prune stale recordings, then return a recorder only while diagnostics are on.

    Pruning runs even when the marker is gone, so the age limit still holds after
    diagnostics are switched off. Nothing here may fail the voice session.
    """
    try:
        directory = diagnostics_dir()
        if _usable(directory):
            prune(directory)
        if not enabled():
            return None
        return SessionRecorder(client_info(params, user_agent), config)
    except Exception:
        return None
