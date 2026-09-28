"""Bounded, cancellable browser PCM relay to Doubao Seed ASR 2.0."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable

from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from flocks.utils.log import Log

from flocks.voice.diagnostics import SessionRecorder, prune
from flocks.voice.protocol import (
    MAX_RESPONSE_BYTES, LevelMeter, QuietSpeechGain, VoiceProtocolError, audio_packet, parse_response,
    start_packet,
)
from flocks.voice.settings import RESOURCE_ID

# Keep Seed ASR 2.0, with stable sentence results for accuracy-first dictation.
ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
MAX_AUDIO_SECONDS = 300
MAX_FRAME_BYTES = 32_000  # One second of PCM; the browser normally sends 200 ms.
IO_TIMEOUT = 15
FINAL_TIMEOUT = 15
ACCESS_CHECK_INTERVAL = 5
# websockets DEBUG logs include headers and binary payloads. This private logger
# stays disabled even if the application's root logger is switched to DEBUG.
_TRANSPORT_LOGGER = logging.Logger("flocks.voice.transport")
_TRANSPORT_LOGGER.disabled = True
_LOG = logging.getLogger(__name__)
# Loudness numbers and transcript length only: never audio, text or credentials.
_AUDIO_LOG = Log.create(service="flocks.voice.stream")


class NoSpeechError(VoiceProtocolError):
    def __init__(self, meter: LevelMeter | None = None) -> None:
        levels = meter.summary() if meter else {}
        quiet = (levels.get("silent_frames_pct", 100) < 100
                 and levels.get("frame_dbfs_p90", -120) < -55)
        self.code = "audio_too_quiet" if quiet else "no_speech"
        super().__init__(
            "录音音量过低，未识别到文字。请靠近麦克风，或在语音设置中尝试原始录音模式。"
            if quiet else "未识别到语音。请检查麦克风后重试，已输入的内容会保留。"
        )


def _public_error(exc: Exception) -> tuple[str, str, int | None]:
    """Classify trusted status/type only, never echo headers, body or close reason."""
    if isinstance(exc, NoSpeechError):
        return exc.code, str(exc), None
    if isinstance(exc, VoiceProtocolError):
        return "voice_error", str(exc), None
    if isinstance(exc, TimeoutError):
        return "upstream_timeout", "语音服务连接超时，请重新开始", None
    if isinstance(exc, InvalidStatus):
        status = exc.response.status_code
        if status == 401:
            return (
                "upstream_auth",
                "豆包 ASR 拒绝了当前凭据。请确认使用豆包语音控制台的 API Key，并检查其有效状态。",
                status,
            )
        if status == 403:
            return (
                "upstream_forbidden",
                "豆包 ASR 拒绝访问。请检查此凭据是否有流式语音识别 2.0 的服务和资源权限。",
                status,
            )
        if status == 429:
            return "upstream_rate_limited", "豆包 ASR 请求过于频繁，请稍后重试", status
        if 500 <= status <= 599:
            return "upstream_unavailable", "豆包 ASR 服务暂时不可用，请稍后重试", status
        return "upstream_rejected", "豆包 ASR 拒绝建立连接，请检查服务配置后重试", status
    if isinstance(exc, ConnectionClosed):
        return "upstream_closed", "豆包 ASR 连接已中断，请重新开始", None
    return "upstream_unavailable", "豆包 ASR 连接失败，请检查网络和服务状态后重试", None


async def _send_json(browser: WebSocket, payload: dict) -> None:
    await asyncio.wait_for(browser.send_json(payload), timeout=IO_TIMEOUT)


async def relay(
    browser: WebSocket, api_key: str,
    authorize: Callable[[], Awaitable[bool]] | None = None,
    *, hotwords: tuple[str, ...] = (), context: str = "",
    diagnostics: SessionRecorder | None = None,
) -> None:
    connection_id = str(uuid.uuid4())
    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": RESOURCE_ID,
        "X-Api-Connect-Id": connection_id,
        "X-Api-Request-Id": connection_id,
        "X-Api-Sequence": "-1",
    }
    # Keep provider exceptions and HTTP headers out of responses and logs.
    async with asyncio.timeout(MAX_AUDIO_SECONDS + FINAL_TIMEOUT):
        from flocks.net import proxy_url_for

        async with connect(
            ENDPOINT, additional_headers=headers,
            # explicit route from the outbound proxy policy (None = direct);
            # websockets would otherwise read HTTPS_PROXY / WSS_PROXY on its own
            proxy=proxy_url_for(ENDPOINT),
            open_timeout=10, close_timeout=3, ping_interval=20, ping_timeout=10,
            max_size=MAX_RESPONSE_BYTES, max_queue=8, compression=None,
            logger=_TRANSPORT_LOGGER,
        ) as upstream:
            await asyncio.wait_for(
                upstream.send(start_packet(connection_id, hotwords=hotwords, context=context)),
                timeout=IO_TIMEOUT,
            )
            initial = parse_response(await asyncio.wait_for(upstream.recv(), timeout=IO_TIMEOUT))
            await _send_json(browser, {"type": "ready"})
            if initial.text is not None:
                if diagnostics is not None:
                    diagnostics.add_transcript(initial.text, initial.final)
                await _send_json(browser, {"type": "transcript", "text": initial.text, "is_final": initial.final})
            if initial.final:
                if not initial.text or not initial.text.strip():
                    raise NoSpeechError()
                await _send_json(browser, {"type": "done"})
                return

            meter = LevelMeter()
            gain = QuietSpeechGain()
            transcript = {"chars": 0}

            async def upload() -> bool:
                sequence, total = 2, 0
                pending_pcm = b""
                while True:
                    # Backpressure plus one pending frame lets the documented final
                    # packet carry real PCM, including a recording with just one frame.
                    message = await asyncio.wait_for(browser.receive(), timeout=IO_TIMEOUT)
                    if message["type"] == "websocket.disconnect":
                        raise WebSocketDisconnect(message.get("code", 1000))
                    pcm = message.get("bytes")
                    if pcm is not None:
                        if not pcm or len(pcm) > MAX_FRAME_BYTES or len(pcm) % 2:
                            raise VoiceProtocolError("音频帧无效，请重新开始录音")
                        total += len(pcm)
                        meter.add(pcm)  # what the browser sent, before the gain
                        if diagnostics is not None:
                            diagnostics.add_input(pcm)
                        pcm = gain.process(pcm)
                        if diagnostics is not None:
                            diagnostics.add_upstream(pcm)
                        if total > MAX_AUDIO_SECONDS * 32_000:
                            raise VoiceProtocolError("单次语音输入最多 5 分钟")
                        if pending_pcm:
                            await asyncio.wait_for(upstream.send(audio_packet(pending_pcm, sequence)), timeout=IO_TIMEOUT)
                            sequence += 1
                        pending_pcm = pcm
                        continue
                    text = message.get("text") or ""
                    if len(text) > 128:
                        raise VoiceProtocolError("语音控制消息过长")
                    try:
                        control = json.loads(text)
                    except ValueError as exc:
                        raise VoiceProtocolError("语音控制消息无效") from exc
                    if control != {"type": "stop"}:
                        raise VoiceProtocolError("语音控制消息无效")
                    if not pending_pcm:
                        return False
                    await asyncio.wait_for(upstream.send(audio_packet(pending_pcm, sequence, final=True)), timeout=IO_TIMEOUT)
                    return True

            async def download() -> None:
                while True:
                    # Sentence recognition may wait for a complete phrase before
                    # returning text. Ping/pong, total duration and stop-to-final
                    # deadlines bound this read without rejecting quiet recordings.
                    packet = await upstream.recv()
                    result = parse_response(packet)
                    if result.text is not None:
                        if diagnostics is not None:
                            diagnostics.add_transcript(result.text, result.final)
                        transcript["chars"] = len(result.text.strip())
                        await _send_json(browser, {"type": "transcript", "text": result.text, "is_final": result.final})
                    if result.final:
                        if not transcript["chars"]:
                            raise NoSpeechError(meter)
                        await _send_json(browser, {"type": "done"})
                        return

            sending = asyncio.create_task(upload())
            receiving = asyncio.create_task(download())
            tasks = [sending, receiving]

            async def check_access() -> None:
                while True:
                    await asyncio.sleep(ACCESS_CHECK_INTERVAL)
                    if not await authorize():
                        raise VoiceProtocolError("语音配置已变更，请重新开始")

            access_check = asyncio.create_task(check_access()) if authorize else None
            if access_check is not None:
                tasks.append(access_check)
            try:
                completed, _pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED,
                )
                if access_check in completed:
                    access_check.result()
                # Check upload failures even if a final result arrived concurrently.
                if sending in completed:
                    if not sending.result():
                        raise NoSpeechError(meter)
                if receiving in completed:
                    receiving.result()
                else:
                    # Revocation must still interrupt the final-response wait.
                    # Waiting only for receiving would swallow an access-check
                    # failure and forward the final transcript after revocation.
                    finishing = [receiving] + ([access_check] if access_check else [])
                    async with asyncio.timeout(FINAL_TIMEOUT):
                        completed, _pending = await asyncio.wait(finishing, return_when=asyncio.FIRST_COMPLETED)
                        if access_check in completed:
                            access_check.result()
                        receiving.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                # An empty transcript is either quiet audio or no audio at all;
                # these numbers tell which without keeping anything spoken.
                _AUDIO_LOG.info(
                    "voice.stream.audio",
                    {**meter.summary(), **gain.summary(), "text_chars": transcript["chars"]},
                )
                if diagnostics is not None:
                    diagnostics.levels = {**meter.summary(), **gain.summary()}


async def _save_diagnostics(diagnostics: SessionRecorder, outcome: str) -> None:
    # Diagnostics must never change what the user sees; failures only log a name.
    try:
        saved = await asyncio.to_thread(diagnostics.finish, outcome)
    except Exception as exc:
        _LOG.warning("voice.stream.diagnostics_failed error=%s", type(exc).__name__)
        return
    if saved is None:
        return  # switched off during the session: nothing was written
    _AUDIO_LOG.info("voice.stream.diagnostics", {"session": diagnostics.name, "outcome": outcome})
    try:
        await asyncio.to_thread(prune, saved.parent)
    except Exception as exc:
        _LOG.warning("voice.stream.diagnostics_prune_failed error=%s", type(exc).__name__)


async def serve(
    browser: WebSocket, api_key: str,
    authorize: Callable[[], Awaitable[bool]] | None = None,
    *, hotwords: tuple[str, ...] = (), context: str = "",
    diagnostics: SessionRecorder | None = None,
) -> None:
    outcome = "ok"
    try:
        await relay(browser, api_key, authorize, hotwords=hotwords, context=context, diagnostics=diagnostics)
    except WebSocketDisconnect:
        outcome = "client_disconnected"
        return
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception as exc:
        code, message, status = _public_error(exc)
        outcome = code
        # Never log repr(exc), traceback, upstream headers/body or close reason.
        _LOG.warning("voice.stream.failed code=%s http_status=%s", code, status)
        with contextlib.suppress(Exception):
            await _send_json(browser, {"type": "error", "code": code, "message": message})
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(browser.close(), timeout=3)
        if diagnostics is not None:
            await _save_diagnostics(diagnostics, outcome)
