from __future__ import annotations

import pytest


def test_level_meter_tells_silence_from_quiet_and_loud_audio():
    import array
    import math
    from flocks.voice.audio import LevelMeter

    empty = LevelMeter()
    assert empty.summary() == {"seconds": 0.0}

    silent = LevelMeter()
    silent.add(b"\x00\x00" * 3200)
    assert silent.summary()["silent_frames_pct"] == 100.0
    assert silent.summary()["peak_dbfs"] == LevelMeter.FLOOR_DBFS

    amplitude = 32768 * 10 ** (-20 / 20)
    sine = array.array("h", [int(amplitude * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(16000)])
    meter = LevelMeter()
    meter.add(sine.tobytes())
    summary = meter.summary()
    assert summary["seconds"] == 1.0 and summary["peak_dbfs"] == -20.0
    assert -23.2 <= summary["frame_dbfs_p50"] <= -22.8  # a sine's RMS sits 3 dB under its peak
    assert summary["clipped_pct"] == 0.0 and summary["silent_frames_pct"] == 0.0

    clipped = LevelMeter()
    clipped.add(array.array("h", [32767, -32768] * 160).tobytes())
    assert clipped.summary()["peak_dbfs"] == 0.0 and clipped.summary()["clipped_pct"] == 100.0
    # numbers only: nothing that could carry the audio itself
    assert all(isinstance(value, float) for value in summary.values())


def _tone(dbfs: float, ms: int) -> list[int]:
    """A 300 Hz sine whose 20 ms frames measure `dbfs` RMS."""
    import math

    amplitude = 32768 * 10 ** (dbfs / 20) * math.sqrt(2)
    return [round(amplitude * math.sin(2 * math.pi * 300 * i / 16000)) for i in range(16 * ms)]


def _hiss(dbfs: float, ms: int, seed: int = 7) -> list[int]:
    import random

    rng = random.Random(seed)
    return [round(rng.gauss(0, 32768 * 10 ** (dbfs / 20))) for _ in range(16 * ms)]


def _pcm(samples: list[int]) -> bytes:
    import array

    return array.array("h", samples).tobytes()


def _frame_levels(pcm: bytes) -> list[float]:
    import array
    import math
    import sys

    samples = array.array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    levels = []
    for start in range(0, len(samples), 320):
        frame = samples[start:start + 320]
        energy = sum(sample * sample for sample in frame)
        levels.append(10 * math.log10(energy / len(frame) / 32768 ** 2) if energy else -120.0)
    return levels


def _gain(pcm: bytes) -> tuple[bytes, object]:
    """Run the gain the way the relay does: 200 ms packets from the browser."""
    from flocks.voice.audio import QuietSpeechGain

    gain = QuietSpeechGain()
    return b"".join(gain.process(pcm[i:i + 6400]) for i in range(0, len(pcm), 6400)), gain


def _syllables(dbfs: float, ms: int, floor: float = -90, seed: int = 11) -> list[int]:
    """Quiet speech as 200 ms syllables with 100 ms gaps of room tone."""
    samples: list[int] = []
    for index in range(ms // 300):
        samples += _tone(dbfs, 200) + _hiss(floor, 100, seed=seed + index)
    return samples


def _gain_on(levels_in: list[float], levels_out: list[float], frames: range) -> list[float]:
    return [levels_out[i] - levels_in[i] for i in frames]


def test_quiet_speech_is_raised_to_the_target_by_up_to_30_db():
    from flocks.voice.audio import QuietSpeechGain

    target = QuietSpeechGain.TARGET_DBFS
    quiet = _pcm(_hiss(-90, 300) + _tone(-60, 1000))
    out, gain = _gain(quiet)
    assert len(out) == len(quiet)
    levels = _frame_levels(out)
    # 20 ms frames: hiss 0-14, tone 15-64; the tone lands on the target
    for index in range(16, 65):
        assert target - 1 <= levels[index] <= target + 1, index

    quieter = _pcm(_hiss(-90, 300) + _tone(-70, 1000))
    out, gain = _gain(quieter)
    levels = _frame_levels(out)
    for index in range(16, 65):
        assert -41.0 <= levels[index] <= -39.0, index  # 34 dB short of the target, capped at 30
    assert gain.summary()["gain_db_max"] == 30.0


def test_normal_speech_is_left_alone():
    normal = _pcm(_hiss(-70, 300) + _tone(-24, 2000))
    out, _gain_state = _gain(normal)
    # from the first frame of the tone on, the audio is untouched
    start = 300 * 32
    assert out[start:] == normal[start:]


def test_background_is_lifted_no_further_than_the_ceiling():
    loud_room = _pcm(_hiss(-50, 2000))
    assert _gain(loud_room)[0] == loud_room

    quiet_room = _pcm(_hiss(-75, 2000))
    levels = sorted(_frame_levels(_gain(quiet_room)[0]))
    assert -56.0 <= levels[len(levels) // 2] <= -54.0  # lifted about 20 dB, to the -55 dBFS ceiling
    assert levels[-1] <= -53.0


def test_a_click_before_speech_does_not_hold_the_gain_down():
    from flocks.voice.audio import QuietSpeechGain

    target = QuietSpeechGain.TARGET_DBFS
    tapped = _pcm(_hiss(-90, 200) + _tone(-20, 40) + _hiss(-90, 200, seed=8) + _tone(-60, 1000))
    levels = _frame_levels(_gain(tapped)[0])
    # the click is limited instead of clipped, and the quiet speech after it still reaches the target
    assert max(levels[10:12]) <= -3.0 + 0.1
    for index in range(24, 72):
        assert target - 1 <= levels[index] <= target + 1, index


def test_a_cough_or_tap_while_speaking_does_not_hold_the_gain_down():
    # quiet syllables, then a 250 ms cough at -22 dBFS, then more syllables
    before, cough, after = _syllables(-60, 1500), _hiss(-22, 250, seed=3), _syllables(-60, 3000, seed=40)
    audio = _pcm(before + cough + after)
    levels_in = _frame_levels(audio)
    levels_out = _frame_levels(_gain(audio)[0])
    # the cough ends halfway through frame 87, which is limited; frame 88 ramps back up
    first = (1500 + 250) // 20 + 2
    syllable_frames = range(first, first + 140)
    gains = [g for i, g in zip(syllable_frames, _gain_on(levels_in, levels_out, syllable_frames)) if levels_in[i] > -70]
    assert gains and min(gains) >= 23.0  # still about the 24 dB the syllables got before the cough


def test_a_loud_first_frame_does_not_hold_the_gain_down():
    # the tap that starts the recording, then quiet speech
    audio = _pcm(_tone(-20, 20) + _hiss(-90, 200) + _syllables(-60, 3000))
    levels_in = _frame_levels(audio)
    levels_out = _frame_levels(_gain(audio)[0])
    frames = range(15, len(levels_in))
    gains = [g for i, g in zip(frames, _gain_on(levels_in, levels_out, frames)) if levels_in[i] > -70]
    assert gains and min(gains) >= 23.0


def test_the_edge_of_digital_silence_does_not_fool_the_ceiling():
    # two silent frames, a frame with only 8 samples of sound, then a -70 dBFS room
    edge = [0] * 640 + [0] * 312 + _hiss(-70, 1, seed=5)[:8]
    audio = _pcm(edge + _hiss(-70, 2000, seed=6))
    levels = _frame_levels(_gain(audio)[0])
    assert max(levels[3:]) <= -53.0  # no more than the -55 ceiling plus frame-to-frame spread


def test_fluent_speech_with_few_pauses_is_not_mistaken_for_the_background():
    # 280 ms of voice, 20 ms of room tone, over and over: pauses are about 7% of
    # the frames, so a background estimate from a higher percentile would land
    # on the voice itself and the ceiling would hold the gain down
    samples = _hiss(-90, 100)
    for index in range(10):
        samples += _tone(-60, 280) + _hiss(-90, 20, seed=60 + index)
    audio = _pcm(samples)
    levels_in = _frame_levels(audio)
    levels_out = _frame_levels(_gain(audio)[0])
    last_second = range(len(levels_in) - 50, len(levels_in))
    gains = [levels_out[i] - levels_in[i] for i in last_second if levels_in[i] > -70]
    assert gains and min(gains) >= 23.0


def test_mostly_zero_consonant_frames_get_the_same_gain_as_the_vowels():
    import random

    # very quiet speech: -60 dBFS vowels, and consonant frames that are mostly
    # zeros with a few samples of +-1..3; both are part of the words
    rng = random.Random(21)
    samples = _hiss(-90, 200)
    consonant_frames, vowel_frames = [], []
    for _ in range(20):
        for _ in range(3):
            vowel_frames.append(len(samples) // 320)
            samples += _tone(-60, 20)
        for _ in range(2):
            consonant_frames.append(len(samples) // 320)
            frame = [0] * 320
            for index in rng.sample(range(320), 60):
                frame[index] = rng.choice((-3, -2, -1, 1, 2, 3))
            samples += frame
    audio = _pcm(samples)
    levels_in = _frame_levels(audio)
    levels_out = _frame_levels(_gain(audio)[0])
    vowel = [levels_out[i] - levels_in[i] for i in vowel_frames[3:]]
    consonant = [levels_out[i] - levels_in[i] for i in consonant_frames[2:]]
    assert min(vowel) >= 20.0
    assert max(abs(c - v) for c in consonant for v in vowel) <= 1.0


def test_gain_never_clips_never_attenuates_and_leaves_silence_alone():
    import array

    # RMS -6 peaks at -3 dBFS, right on the limit; RMS -5 peaks above it and must pass as it is
    loud_after_quiet = _pcm([0] * 16000 + _hiss(-90, 500) + _tone(-6, 200) + _tone(-5, 200))
    out, _gain_state = _gain(loud_after_quiet)
    assert out[:32000] == b"\x00\x00" * 16000
    samples = array.array("h", out)
    original = array.array("h", loud_after_quiet)
    limit = 32768 * 10 ** (-3 / 20)
    tone_start = 16000 + 8000
    for index in range(tone_start, len(samples)):
        # never louder than the peak limit unless the input already was, and never quieter than the input
        assert abs(samples[index]) <= max(limit, abs(original[index])) + 1
        assert abs(samples[index]) >= abs(original[index]) - 1
    assert 32767 not in samples[tone_start:] and -32768 not in samples[tone_start:]


def test_gain_keeps_odd_lengths_and_is_the_same_in_packets_or_all_at_once():
    from flocks.voice.audio import QuietSpeechGain

    empty = QuietSpeechGain()
    assert empty.process(b"") == b"" and empty.summary() == {}
    odd = _pcm(_hiss(-80, 21)[:333])
    assert len(QuietSpeechGain().process(odd)) == len(odd)

    audio = _pcm(_hiss(-90, 300) + _tone(-50, 700))
    whole = QuietSpeechGain()
    assert _gain(audio)[0] == whole.process(audio)
    assert all(isinstance(value, float) for value in whole.summary().values())


def test_meter_is_independent_of_packet_boundaries_and_summary_does_not_change_it():
    from flocks.voice.audio import LevelMeter

    pcm = _pcm(_hiss(-85, 130) + _tone(-40, 377) + [0] * 73)
    whole = LevelMeter()
    whole.add(pcm)
    fragmented = LevelMeter()
    for start in range(0, len(pcm), 86):
        fragmented.add(pcm[start:start + 86])
        fragmented.summary()
    assert fragmented.summary() == whole.summary()


def test_statistics_remain_bounded_for_long_recordings():
    from flocks.voice.audio import LevelMeter, QuietSpeechGain

    meter, gain = LevelMeter(), QuietSpeechGain()
    normal = _pcm(_tone(-24, 1000))
    for _ in range(600):
        meter.add(normal)
        gain.process(normal)
    assert meter.summary()["seconds"] == 600.0
    assert len(meter._levels.counts) == 1201
    assert len(gain._applied.counts) == 301
    assert len(gain.recent) == gain.WINDOW_FRAMES
    # No per-frame archive of levels or samples remains in either processor.
    assert not hasattr(meter, "frame_dbfs")
    assert not hasattr(gain, "applied")


def test_peak_protection_applies_to_very_short_final_frames():
    import array
    from flocks.voice.audio import QuietSpeechGain

    gain = QuietSpeechGain()
    gain.process(_pcm(_hiss(-90, 300) + _tone(-70, 300)))
    peak = _pcm([32767, -32768, 24000])
    output = gain.process(peak)
    assert output == peak
    assert array.array("h", output)[1] == -32768


@pytest.mark.parametrize("sparse_noise", [False, True])
def test_a_long_digital_pause_expires_loud_history_before_quiet_speech(sparse_noise):
    from flocks.voice.audio import QuietSpeechGain

    gain = QuietSpeechGain()
    gain.process(_pcm(_tone(-24, 3000)))
    pause = _pcm(([0] * 319 + [1]) * 150) if sparse_noise else b"\x00\x00" * (3 * 16000)
    output = gain.process(pause)
    assert len(output) == len(pause)
    if not sparse_noise:
        assert output == pause
    quiet = _pcm(_hiss(-90, 300) + _tone(-60, 1000))
    levels = _frame_levels(gain.process(quiet))
    for level in levels[16:]:
        assert -37 <= level <= -35
