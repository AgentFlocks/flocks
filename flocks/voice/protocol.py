"""Seed ASR v3 binary framing, with bounded decoding and no upstream error echo."""

from __future__ import annotations

import gzip
import json
import struct
import zlib
from dataclasses import dataclass

from flocks.voice.audio import LevelMeter, QuietSpeechGain

MAX_RESPONSE_BYTES = 1_048_576


class VoiceProtocolError(ValueError):
    """A safe error description, without provider payloads or credentials."""


def _packet(message_type: int, payload: bytes, sequence: int, *, json_body: bool) -> bytes:
    flags = 3 if sequence < 0 else 1
    body = gzip.compress(payload)
    header = bytes((0x11, (message_type << 4) | flags, (0x10 if json_body else 0) | 1, 0))
    return header + struct.pack(">iI", sequence, len(body)) + body


def start_packet(connection_id: str, *, hotwords: tuple[str, ...] = (), context: str = "") -> bytes:
    payload = {
        "user": {"uid": connection_id},
        "audio": {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True, "enable_punc": True, "show_utterances": True,
            "result_type": "full",
        },
    }
    hints: dict = {}
    if hotwords:
        hints["hotwords"] = [{"word": word} for word in hotwords]
    if context:
        hints.update(context_type="dialog_ctx", context_data=[{"text": context}])
    if hints:
        # SAUC expects corpus.context to be a JSON string, not a nested object.
        payload["request"]["corpus"] = {
            "context": json.dumps(hints, ensure_ascii=False),
        }
    return _packet(1, json.dumps(payload).encode(), 1, json_body=True)


def audio_packet(pcm: bytes, sequence: int, *, final: bool = False) -> bytes:
    if sequence < 2:
        raise ValueError("audio sequence must start at 2")
    return _packet(2, pcm, -sequence if final else sequence, json_body=False)


@dataclass(frozen=True)
class Recognition:
    text: str | None
    final: bool


def parse_response(packet: bytes) -> Recognition:
    if not isinstance(packet, bytes) or not 4 <= len(packet) <= MAX_RESPONSE_BYTES:
        raise VoiceProtocolError("语音服务返回了无效数据")
    version, header_size = packet[0] >> 4, (packet[0] & 15) * 4
    kind, flags = packet[1] >> 4, packet[1] & 15
    encoding, compression = packet[2] >> 4, packet[2] & 15
    if version != 1 or header_size < 4 or header_size > len(packet):
        raise VoiceProtocolError("语音服务协议版本不受支持")
    if kind == 15:
        raise VoiceProtocolError("豆包 ASR 请求失败，请检查 API Key、服务开通状态和额度")
    if kind not in {9, 11} or flags not in {0, 1, 2, 3}:
        raise VoiceProtocolError("语音服务返回了未知消息")
    offset = header_size
    sequence = 0
    if flags & 1 or kind == 11:
        if len(packet) < offset + 4:
            raise VoiceProtocolError("语音服务响应不完整")
        sequence = struct.unpack_from(">i", packet, offset)[0]
        offset += 4
    final = bool(flags & 2) or sequence < 0
    if kind == 11 and len(packet) == offset:
        return Recognition(None, final)
    if len(packet) < offset + 4:
        raise VoiceProtocolError("语音服务响应不完整")
    size = struct.unpack_from(">I", packet, offset)[0]
    body = packet[offset + 4:]
    if size != len(body):
        raise VoiceProtocolError("语音服务响应长度无效")
    if compression == 1:
        try:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = decoder.decompress(body, MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES or not decoder.eof or decoder.unused_data:
                raise VoiceProtocolError("语音服务响应过大或损坏")
        except zlib.error as exc:
            raise VoiceProtocolError("语音服务响应解压失败") from exc
    elif compression != 0:
        raise VoiceProtocolError("语音服务响应压缩方式不受支持")
    if not body and kind == 11:
        return Recognition(None, final)
    if encoding != 1:
        raise VoiceProtocolError("语音服务响应格式不受支持")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise VoiceProtocolError("语音服务返回了无效 JSON") from exc
    if not isinstance(payload, dict):
        raise VoiceProtocolError("语音服务返回了无效结果")
    if payload.get("code") not in {None, 0, 1000, 20000000}:
        raise VoiceProtocolError("豆包 ASR 识别失败，请检查服务配置和额度")
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise VoiceProtocolError("语音服务返回了无效识别结果")
    text = result.get("text")
    if text is not None and not isinstance(text, str):
        raise VoiceProtocolError("语音服务返回了无效文本")
    return Recognition(text, final)
