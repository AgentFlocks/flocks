"""Bounded PCM16 level diagnostics and conservative quiet-speech gain.

These processors retain level statistics, never an audio history. Gain cannot
recover samples already lost while converting browser float audio to PCM16;
the browser also raises quiet audio before that conversion.
"""

from __future__ import annotations

import array
import math
import operator
import sys
from collections import deque


def _samples(pcm: bytes) -> array.array:
    values = array.array("h")
    values.frombytes(pcm)
    if sys.byteorder != "little":
        values.byteswap()
    return values


def _level(energy: int, count: int) -> float:
    return 10 * math.log10(energy / count / (32768 * 32768)) if energy else -120.0


class _LevelHistogram:
    """0.1 dB bins keep diagnostic percentiles independent of recording length."""

    def __init__(self, floor: float, ceiling: float) -> None:
        self.floor = floor
        self.counts = [0] * (round((ceiling - floor) * 10) + 1)
        self.count = 0

    def _index(self, level: float) -> int:
        return max(0, min(len(self.counts) - 1, round((level - self.floor) * 10)))

    def add(self, level: float) -> None:
        self.counts[self._index(level)] += 1
        self.count += 1

    def percentile(self, share: float, *, pending: float | None = None) -> float:
        count = self.count + (pending is not None)
        if not count:
            return self.floor
        target = min(count - 1, int(share * count))
        pending_index = self._index(pending) if pending is not None else -1
        seen = 0
        for index, frequency in enumerate(self.counts):
            seen += frequency + (index == pending_index)
            if seen > target:
                return round(self.floor + index / 10, 1)
        return round(self.floor + (len(self.counts) - 1) / 10, 1)


class LevelMeter:
    """Measure the captured level without retaining audio or unbounded lists."""

    FRAME_SAMPLES = 320  # 20 ms at 16 kHz
    FLOOR_DBFS = -120.0

    def __init__(self) -> None:
        self.samples = 0
        self.peak = 0
        self.clipped = 0
        self.silent_frames = 0
        self._levels = _LevelHistogram(self.FLOOR_DBFS, 0.0)
        self._frame_energy = 0
        self._frame_samples = 0

    def add(self, pcm: bytes) -> None:
        values = _samples(pcm)
        if not values:
            return
        self.samples += len(values)
        self.peak = max(self.peak, max(values), -min(values))
        self.clipped += values.count(32767) + values.count(-32768)
        start = 0
        while start < len(values):
            size = min(self.FRAME_SAMPLES - self._frame_samples, len(values) - start)
            frame = values[start:start + size]
            self._frame_energy += sum(map(operator.mul, frame, frame))
            self._frame_samples += size
            start += size
            if self._frame_samples == self.FRAME_SAMPLES:
                self._levels.add(_level(self._frame_energy, self._frame_samples))
                self.silent_frames += self._frame_energy == 0
                self._frame_energy = self._frame_samples = 0

    def summary(self) -> dict[str, float]:
        if not self.samples:
            return {"seconds": 0.0}
        pending = _level(self._frame_energy, self._frame_samples) if self._frame_samples else None
        frames = self._levels.count + (pending is not None)
        silent = self.silent_frames + (pending == self.FLOOR_DBFS)
        return {
            "seconds": round(self.samples / 16000, 2),
            "peak_dbfs": round(20 * math.log10(self.peak / 32768), 1) if self.peak else self.FLOOR_DBFS,
            "frame_dbfs_p50": self._levels.percentile(0.5, pending=pending),
            "frame_dbfs_p90": self._levels.percentile(0.9, pending=pending),
            "frame_dbfs_p99": self._levels.percentile(0.99, pending=pending),
            "clipped_pct": round(100 * self.clipped / self.samples, 3),
            "silent_frames_pct": round(100 * silent / frames, 1),
        }


class QuietSpeechGain:
    """Raise quiet PCM with a background and peak limit, preserving silence.

    The lower median of speech levels in the last three seconds sets the gain,
    so a brief cough does not suppress following words. Background estimation
    uses the fifth percentile and excludes mostly-zero, quantized frames.
    Normal speech is left unchanged; an abrupt loud input drops the gain
    immediately, while an increase is ramped over the current frame.
    """

    FRAME_SAMPLES = 320
    WINDOW_FRAMES = 150
    TARGET_DBFS = -36.0
    MAX_GAIN_DB = 30.0
    NOISE_CEILING_DBFS = -55.0
    PEAK_LIMIT_DBFS = -3.0
    SPEECH_MARGIN_DB = 10.0

    def __init__(self) -> None:
        self.gain_db = 0.0
        self.recent: deque[float | None] = deque(maxlen=self.WINDOW_FRAMES)
        self._applied = _LevelHistogram(0.0, self.MAX_GAIN_DB)

    def process(self, pcm: bytes) -> bytes:
        values = _samples(pcm)
        out = array.array("h")
        for start in range(0, len(values), self.FRAME_SAMPLES):
            out.extend(self._frame(values[start:start + self.FRAME_SAMPLES]))
        if sys.byteorder != "little":
            out.byteswap()
        return out.tobytes()

    def _frame(self, frame: array.array) -> array.array | list[int]:
        zeros = frame.count(0)
        if zeros == len(frame):
            # Silence ages out old speech, but is not evidence of a quieter
            # acoustic background. Retaining old loud frames across silence
            # would suppress the first words after a long pause.
            self.recent.append(None)
            return frame
        energy = sum(map(operator.mul, frame, frame))
        level = _level(energy, len(frame))
        self.recent.append(level if zeros * 2 <= len(frame) else None)
        levels = sorted(value for value in self.recent if value is not None) or [level]
        background = levels[len(levels) // 20]
        speech = [value for value in levels if value > background + self.SPEECH_MARGIN_DB]
        aim = self.TARGET_DBFS - speech[(len(speech) - 1) // 2] if speech else self.MAX_GAIN_DB
        peak = max(max(frame), -min(frame))
        gain_db = max(0.0, min(
            self.MAX_GAIN_DB,
            aim,
            self.NOISE_CEILING_DBFS - background,
            self.PEAK_LIMIT_DBFS - 20 * math.log10(peak / 32768),
        ))
        start_db, self.gain_db = min(self.gain_db, gain_db), gain_db
        self._applied.add(gain_db)
        if not gain_db and not start_db:
            return frame
        begin, end = 10 ** (start_db / 20), 10 ** (gain_db / 20)
        step = (end - begin) / len(frame)
        return [max(-32768, min(32767, round(value * (begin + step * (i + 1))))) for i, value in enumerate(frame)]

    def summary(self) -> dict[str, float]:
        if not self._applied.count:
            return {}
        return {
            "gain_db_p50": self._applied.percentile(0.5),
            "gain_db_max": self._applied.percentile(1.0),
        }
