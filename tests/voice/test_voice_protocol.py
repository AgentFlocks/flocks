from __future__ import annotations

import gzip
import json
import struct

import pytest

from flocks.voice.protocol import (
    VoiceProtocolError, audio_packet, parse_response, start_packet,
)


def response_packet(text=None, *, final=False, compressed=True):
    payload = {"result": {"text": text}} if text is not None else {}
    body = json.dumps(payload).encode()
    if compressed:
        body = gzip.compress(body)
    return bytes((0x11, 0x93 if final else 0x91, 0x11 if compressed else 0x10, 0)) + struct.pack(">iI", -3 if final else 2, len(body)) + body


def test_initial_packet_matches_documented_audio_and_model():
    packet = start_packet("connection-id")
    assert packet[:4] == bytes((0x11, 0x11, 0x11, 0))
    sequence, length = struct.unpack_from(">iI", packet, 4)
    assert sequence == 1 and length == len(packet) - 12
    payload = json.loads(gzip.decompress(packet[12:]))
    assert payload["audio"] == {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1}
    assert payload["request"]["model_name"] == "bigmodel"
    assert "model_version" not in payload["request"]


def test_initial_packet_asks_for_second_pass_recognition():
    # Streaming-only results drop earlier clauses once the speaker pauses; the
    # second (non-streaming) pass is what makes the final transcript complete.
    request = json.loads(gzip.decompress(start_packet("connection-id")[12:]))["request"]
    assert request["enable_nonstream"] is True
    assert request["result_type"] == "full" and request["show_utterances"] is True
    assert request["enable_itn"] is True and request["enable_punc"] is True
    assert "enable_ddc" not in request


def test_audio_and_final_packets_preserve_pcm_and_sequence():
    pcm = b"\x01\x00" * 3200
    for final in (False, True):
        packet = audio_packet(pcm, 2, final=final)
        assert packet[1] == (0x23 if final else 0x21)
        assert struct.unpack_from(">i", packet, 4)[0] == (-2 if final else 2)
        assert gzip.decompress(packet[12:]) == pcm


@pytest.mark.parametrize("compressed", [True, False])
def test_cumulative_text_and_terminal_result(compressed):
    assert parse_response(response_packet("你好", compressed=compressed)).text == "你好"
    result = parse_response(response_packet("你好世界", final=True, compressed=compressed))
    assert result.text == "你好世界" and result.final
    assert parse_response(response_packet()).text is None


@pytest.mark.parametrize("packet", [
    b"", b"\x11\x91\x11\0", b"\x21\x91\x11\0",
    b"\x10\x91\x11\0", b"\x11\xf0\x11\0credential-must-not-echo",
    b"\x11\x50\x11\0", b"\x11\x91\x11\0" + b"\0" * 12,
    b"\x11\x90\x10\0" + struct.pack(">I", 2) + b"[]",
    b"\x11\x90\x10\0" + struct.pack(">I", 1) + b"{",
])
def test_malformed_and_error_frames_fail_safely(packet):
    with pytest.raises(VoiceProtocolError) as error:
        parse_response(packet)
    assert "credential-must-not-echo" not in str(error.value)


def test_compressed_expansion_is_bounded():
    body = gzip.compress(b"x" * 1_048_577)
    packet = b"\x11\x90\x11\0" + struct.pack(">I", len(body)) + body
    with pytest.raises(VoiceProtocolError):
        parse_response(packet)


def test_hotwords_reach_documented_provider_context_without_changing_model():
    packet = start_packet("connection-id", hotwords=("Flocks", "海淀", "PostgreSQL"))
    payload = json.loads(gzip.decompress(packet[12:]))
    context = payload["request"]["corpus"]["context"]
    assert isinstance(context, str)
    assert json.loads(context) == {"hotwords": [{"word": "Flocks"}, {"word": "海淀"}, {"word": "PostgreSQL"}]}
    assert payload["request"]["model_name"] == "bigmodel"
    assert payload["request"]["enable_nonstream"] is True
