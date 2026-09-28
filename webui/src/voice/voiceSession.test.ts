import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { VoiceSession } from './voiceSession';

const audio = vi.hoisted(() => ({
  prepare: vi.fn(), start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), captureMode: vi.fn(), captureInfo: vi.fn(),
  onPacket: null as ((packet: ArrayBuffer) => void) | null,
  onError: null as ((code: string) => void) | null,
}));
vi.mock('./pcmRecorder', () => ({
  PcmRecorder: class {
    constructor(onPacket: (packet: ArrayBuffer) => void, onError: (code: string) => void, captureMode?: string) {
      audio.onPacket = onPacket; audio.onError = onError;
      audio.captureMode(captureMode);
    }
    prepare = audio.prepare;
    start = audio.start;
    stop = audio.stop;
    cancel = audio.cancel;
    captureInfo = audio.captureInfo;
  },
}));

class MockSocket {
  static OPEN = 1;
  static instances: MockSocket[] = [];
  readyState = 1;
  bufferedAmount = 0;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();
  constructor(readonly url: string) { MockSocket.instances.push(this); }
  message(message: unknown) { this.onmessage?.({ data: JSON.stringify(message) }); }
}

function setup() {
  const callbacks = { onText: vi.fn(), onPhase: vi.fn(), onError: vi.fn() };
  return { callbacks, session: new VoiceSession(callbacks) };
}

async function ready(session: VoiceSession) {
  await session.start();
  const socket = MockSocket.instances[0];
  socket.message({ type: 'ready' });
  await Promise.resolve();
  return socket;
}

describe('voice WebSocket lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    audio.captureInfo.mockReturnValue(null);
    audio.prepare.mockResolvedValue(undefined);
    audio.start.mockResolvedValue(undefined);
    audio.stop.mockResolvedValue(undefined);
    MockSocket.instances = [];
    vi.stubGlobal('WebSocket', MockSocket);
  });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('passes the saved capture mode through to microphone acquisition', async () => {
    const session = new VoiceSession({ onText: vi.fn(), onPhase: vi.fn(), onError: vi.fn() }, { captureMode: 'raw' });
    await session.start();
    expect(audio.captureMode).toHaveBeenCalledWith('raw');
    session.cancel();
  });

  it('explains an empty recognition result instead of silently completing', async () => {
    const { session, callbacks } = setup();
    const socket = await ready(session);
    await session.stop();
    socket.message({ type: 'transcript', text: '   ', is_final: true });
    socket.message({ type: 'done' });
    expect(callbacks.onError).toHaveBeenCalledWith('no_speech', undefined);
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('idle');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('records from the moment the microphone opens and delivers that audio once the relay is ready', async () => {
    const { session, callbacks } = setup();
    await session.start();
    const socket = MockSocket.instances[0];
    expect(socket.url).toBe('ws://localhost:3000/api/voice/stream');
    // Capture and the recording state do not wait for the relay's round trip
    // to the recogniser: whatever is said right after the tap is kept.
    expect(audio.start).toHaveBeenCalledOnce();
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('recording');
    const early = [new ArrayBuffer(6400), new ArrayBuffer(6400)];
    early.forEach((packet) => audio.onPacket?.(packet));
    expect(socket.send).not.toHaveBeenCalled();
    socket.message({ type: 'ready' });
    expect(socket.send).toHaveBeenCalledTimes(2);
    expect(socket.send.mock.calls[0][0]).toBe(early[0]);
    expect(socket.send.mock.calls[1][0]).toBe(early[1]);
    const late = new ArrayBuffer(6400);
    audio.onPacket?.(late);
    expect(socket.send).toHaveBeenLastCalledWith(late);
    socket.message({ type: 'transcript', text: '你好', is_final: false });
    await session.stop();
    expect(socket.send).toHaveBeenLastCalledWith('{"type":"stop"}');
    socket.message({ type: 'transcript', text: '你好世界', is_final: true });
    socket.message({ type: 'done' });
    expect(callbacks.onText.mock.calls).toEqual([['你好'], ['你好世界']]);
    expect(callbacks.onPhase.mock.calls).toEqual([['connecting'], ['recording'], ['stopping'], ['idle']]);
    expect(socket.close).toHaveBeenCalledOnce();
    expect(audio.cancel).toHaveBeenCalledOnce();
  });

  it('stopping before the relay is ready waits for it, then sends every packet and stop in order', async () => {
    const { session, callbacks } = setup();
    await session.start();
    const socket = MockSocket.instances[0];
    const first = new ArrayBuffer(6400);
    const tail = new ArrayBuffer(3200);
    audio.stop.mockImplementation(async () => { audio.onPacket?.(tail); });
    audio.onPacket?.(first);
    await session.stop();
    // Nothing has left the browser yet, so the button keeps its cancel action.
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('connecting');
    expect(socket.send).not.toHaveBeenCalled();
    socket.message({ type: 'ready' });
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('stopping');
    expect(socket.send.mock.calls.map(([data]) => data)).toEqual([first, tail, '{"type":"stop"}']);
    expect(socket.send.mock.calls[0][0]).toBe(first);
    expect(socket.send.mock.calls[1][0]).toBe(tail);
    socket.message({ type: 'transcript', text: '你好。', is_final: true });
    socket.message({ type: 'done' });
    expect(callbacks.onText).toHaveBeenCalledWith('你好。');
    expect(callbacks.onError).not.toHaveBeenCalled();
    expect(callbacks.onPhase.mock.calls).toEqual([['connecting'], ['recording'], ['connecting'], ['stopping'], ['idle']]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('a stop tapped before the relay is ready can still be cancelled', async () => {
    const { session, callbacks } = setup();
    await session.start();
    const socket = MockSocket.instances[0];
    audio.onPacket?.(new ArrayBuffer(6400));
    await session.stop();
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('connecting');
    // The second tap on the same button is a cancel, not a no-op.
    await session.stop();
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('idle');
    expect(callbacks.onError).not.toHaveBeenCalled();
    expect(socket.close).toHaveBeenCalledOnce();
    expect(socket.send).not.toHaveBeenCalled();
    expect(audio.cancel).toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('starts the final deadline when the stop frame is sent, not at the tap', async () => {
    const { session, callbacks } = setup();
    await session.start();
    const socket = MockSocket.instances[0];
    await vi.advanceTimersByTimeAsync(1000);
    await session.stop();
    await vi.advanceTimersByTimeAsync(43000);
    socket.message({ type: 'ready' });
    expect(socket.send).toHaveBeenLastCalledWith('{"type":"stop"}');
    // Fifty seconds after the tap would be 7 s after ready; the relay allows 15 s for its final.
    await vi.advanceTimersByTimeAsync(49999);
    expect(callbacks.onError).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(callbacks.onError).toHaveBeenCalledWith('timeout', undefined);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('keeps connecting when ready arrives before capture has started, then records normally', async () => {
    let started!: () => void;
    audio.start.mockReturnValue(new Promise<void>((done) => { started = done; }));
    const { session, callbacks } = setup();
    const pending = session.start();
    await Promise.resolve();
    const socket = MockSocket.instances[0];
    socket.message({ type: 'ready' });
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('connecting');
    started();
    await pending;
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('recording');
    const packet = new ArrayBuffer(6400);
    audio.onPacket?.(packet);
    expect(socket.send).toHaveBeenCalledWith(packet);
    await session.stop();
    expect(socket.send).toHaveBeenLastCalledWith('{"type":"stop"}');
    expect(callbacks.onError).not.toHaveBeenCalled();
    session.cancel();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('feeds a long connecting backlog through the socket buffer instead of failing', async () => {
    const { session, callbacks } = setup();
    await session.start();
    const socket = MockSocket.instances[0];
    socket.send.mockImplementation((data: unknown) => {
      if (data instanceof ArrayBuffer) socket.bufferedAmount += data.byteLength;
    });
    // Six seconds said while the relay connected: more than the four the socket may hold.
    for (let index = 0; index < 30; index += 1) audio.onPacket?.(new ArrayBuffer(6400));
    socket.message({ type: 'ready' });
    expect(socket.send).toHaveBeenCalledTimes(20);
    socket.bufferedAmount = 0;
    await vi.advanceTimersByTimeAsync(50);
    expect(socket.send).toHaveBeenCalledTimes(30);
    socket.bufferedAmount = 0;
    audio.onPacket?.(new ArrayBuffer(6400));
    expect(socket.send).toHaveBeenCalledTimes(31);
    expect(callbacks.onError).not.toHaveBeenCalled();
    session.cancel();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('bounds the audio kept while the relay is still connecting', async () => {
    const { session, callbacks } = setup();
    await session.start();
    const socket = MockSocket.instances[0];
    for (let index = 0; index < 300; index += 1) audio.onPacket?.(new ArrayBuffer(6400));
    expect(callbacks.onError).not.toHaveBeenCalled();
    audio.onPacket?.(new ArrayBuffer(6400));
    expect(callbacks.onError).toHaveBeenCalledWith('timeout', undefined);
    expect(socket.close).toHaveBeenCalledOnce();
    expect(audio.cancel).toHaveBeenCalledOnce();
  });

  it('closes the socket when the microphone cannot start after it was opened', async () => {
    audio.start.mockRejectedValue(new Error('worklet failed'));
    const { session, callbacks } = setup();
    await session.start();
    expect(callbacks.onError).toHaveBeenCalledWith('microphone', undefined);
    expect(MockSocket.instances[0].close).toHaveBeenCalledOnce();
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('idle');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('cancels pending microphone setup without creating a socket afterwards', async () => {
    let resolve!: () => void;
    audio.prepare.mockReturnValue(new Promise<void>((done) => { resolve = done; }));
    const { session, callbacks } = setup();
    const pending = session.start();
    session.cancel();
    resolve();
    await pending;
    expect(MockSocket.instances).toHaveLength(0);
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('idle');
  });

  it('ignores already queued transcript callbacks after cancelling', async () => {
    const { session, callbacks } = setup();
    const socket = await ready(session);
    const lateCallback = socket.onmessage;
    session.cancel();
    lateCallback?.({ data: '{"type":"transcript","text":"late"}' });
    expect(callbacks.onText).not.toHaveBeenCalled();
  });

  it('does not buffer more than four seconds of PCM on a slow socket', async () => {
    const { session, callbacks } = setup();
    const socket = await ready(session);
    socket.bufferedAmount = 128000;
    audio.onPacket?.(new ArrayBuffer(6400));
    expect(callbacks.onError).toHaveBeenCalledWith('backpressure', undefined);
    expect(socket.send).not.toHaveBeenCalled();
    expect(audio.cancel).toHaveBeenCalledOnce();
  });

  it.each([
    [new DOMException('denied', 'NotAllowedError'), 'permission'],
    [new Error('insecure'), 'insecure'],
    [new Error('unsupported'), 'unsupported'],
    [new DOMException('no device', 'NotFoundError'), 'microphone'],
  ])('reports capture failure without opening a socket (%s)', async (error, code) => {
    audio.prepare.mockRejectedValue(error);
    const { session, callbacks } = setup();
    await session.start();
    expect(callbacks.onError).toHaveBeenCalledWith(code, undefined);
    expect(MockSocket.instances).toHaveLength(0);
  });

  it('keeps the microphone permission timeout at thirty seconds', async () => {
    let resolve!: () => void;
    audio.prepare.mockReturnValue(new Promise<void>((done) => { resolve = done; }));
    const { session, callbacks } = setup();
    const pending = session.start();
    await vi.advanceTimersByTimeAsync(29999);
    expect(callbacks.onError).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(callbacks.onError).toHaveBeenCalledWith('timeout', undefined);
    expect(audio.cancel).toHaveBeenCalledOnce();
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('idle');
    resolve();
    await pending;
    expect(MockSocket.instances).toHaveLength(0);
  });

  it('accepts ready after a slow upstream handshake instead of cancelling at fifteen seconds', async () => {
    const { session, callbacks } = setup();
    await session.start();
    const socket = MockSocket.instances[0];
    await vi.advanceTimersByTimeAsync(16000);
    expect(callbacks.onError).not.toHaveBeenCalled();
    const early = new ArrayBuffer(6400);
    audio.onPacket?.(early);
    socket.message({ type: 'ready' });
    expect(socket.send).toHaveBeenCalledWith(early);
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('recording');
    // Passing the connection deadline must not leave a stale timer.
    await vi.advanceTimersByTimeAsync(30000);
    expect(callbacks.onError).not.toHaveBeenCalled();
    session.cancel();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('closes the socket and recorder when ready never arrives within the connection budget', async () => {
    const { session, callbacks } = setup();
    await session.start();
    const socket = MockSocket.instances[0];
    await vi.advanceTimersByTimeAsync(44999);
    expect(callbacks.onError).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(callbacks.onError).toHaveBeenCalledWith('timeout', undefined);
    expect(audio.cancel).toHaveBeenCalledOnce();
    expect(socket.close).toHaveBeenCalledOnce();
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('idle');
    expect(vi.getTimerCount()).toBe(0);
    socket.message({ type: 'ready' });
    expect(socket.send).not.toHaveBeenCalled();
  });

  it('keeps a final transcript arriving twelve seconds after stop', async () => {
    const { session, callbacks } = setup();
    const socket = await ready(session);
    socket.message({ type: 'transcript', text: '你好', is_final: false });
    await session.stop();
    await vi.advanceTimersByTimeAsync(12000);
    expect(callbacks.onError).not.toHaveBeenCalled();
    socket.message({ type: 'transcript', text: '你好世界。', is_final: true });
    socket.message({ type: 'done' });
    expect(callbacks.onText.mock.calls).toEqual([['你好'], ['你好世界。']]);
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('idle');
    expect(audio.cancel).toHaveBeenCalledOnce();
    expect(socket.close).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('preserves the backend timeout error after its fifteen-second final deadline', async () => {
    const { session, callbacks } = setup();
    const socket = await ready(session);
    await session.stop();
    await vi.advanceTimersByTimeAsync(16000);
    expect(callbacks.onError).not.toHaveBeenCalled();
    socket.message({ type: 'error', code: 'upstream_timeout' });
    expect(callbacks.onError).toHaveBeenCalledWith('upstream_timeout', undefined);
    expect(callbacks.onError).toHaveBeenCalledOnce();
    expect(audio.cancel).toHaveBeenCalledOnce();
    expect(socket.close).toHaveBeenCalledOnce();
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('idle');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('closes the socket and recorder when the final transcript never arrives', async () => {
    const { session, callbacks } = setup();
    const socket = await ready(session);
    await session.stop();
    await vi.advanceTimersByTimeAsync(49999);
    expect(callbacks.onError).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(callbacks.onError).toHaveBeenCalledWith('timeout', undefined);
    expect(audio.cancel).toHaveBeenCalledOnce();
    expect(socket.close).toHaveBeenCalledOnce();
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('idle');
    expect(vi.getTimerCount()).toBe(0);
    socket.message({ type: 'transcript', text: 'late', is_final: true });
    expect(callbacks.onText).not.toHaveBeenCalled();
  });

  it('stops automatically at two minutes', async () => {
    const { session } = setup();
    const socket = await ready(session);
    await vi.advanceTimersByTimeAsync(120000);
    expect(socket.send).toHaveBeenLastCalledWith('{"type":"stop"}');
    session.cancel();
  });

  it('cleans up on disconnection and reports backend errors', async () => {
    const { session, callbacks } = setup();
    const socket = await ready(session);
    socket.message({ type: 'error', message: 'ASR not enabled' });
    socket.onclose?.();
    expect(callbacks.onError).toHaveBeenCalledOnce();
    expect(callbacks.onError).toHaveBeenCalledWith('server', 'ASR not enabled');
    expect(audio.cancel).toHaveBeenCalledOnce();
  });

  it.each([
    'upstream_auth', 'upstream_forbidden', 'upstream_rate_limited', 'upstream_unavailable',
    'upstream_timeout', 'upstream_closed', 'upstream_rejected',
    'no_speech', 'audio_too_quiet',
  ])('maps the known %s code without forwarding raw upstream details', async (code) => {
    const { session, callbacks } = setup();
    const socket = await ready(session);
    socket.message({ type: 'error', code, message: 'Internal upstream response must not replace local copy' });
    expect(callbacks.onError).toHaveBeenCalledWith(code, undefined);
    expect(callbacks.onPhase).toHaveBeenLastCalledWith('idle');
    expect(audio.cancel).toHaveBeenCalledOnce();
  });

  it.each(['voice_error', '../../private.translation', '__proto__', 401, null, { upstream_auth: true }])(
    'falls back to the backend sanitized message for unknown error code %j',
    async (code) => {
      const { session, callbacks } = setup();
      const socket = await ready(session);
      socket.message({ type: 'error', code, message: 'Voice input was disabled by the administrator.' });
      expect(callbacks.onError).toHaveBeenCalledWith('server', 'Voice input was disabled by the administrator.');
      expect(audio.cancel).toHaveBeenCalledOnce();
    },
  );
});

describe('capture parameters on the voice socket', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    audio.prepare.mockResolvedValue(undefined);
    audio.start.mockResolvedValue(undefined);
    MockSocket.instances = [];
    vi.stubGlobal('WebSocket', MockSocket);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('opens the socket with what the microphone was granted', async () => {
    audio.captureInfo.mockReturnValue({ contextRate: 16000, trackRate: 48000, worklet: true, mode: 'voice' });
    const { session } = setup();
    await session.start();
    const url = new URL(MockSocket.instances[0].url);
    expect(url.searchParams.get('ctx_rate')).toBe('16000');
    expect(url.searchParams.get('track_rate')).toBe('48000');
    session.cancel();
  });
});
