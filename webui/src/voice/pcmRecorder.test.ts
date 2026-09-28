import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Pcm16Encoder, PcmRecorder, PCM_SAMPLE_RATE, PCM_WORKLET_SOURCE } from './pcmRecorder';

function encode(sampleRate: number, blocks: Float32Array[]): ArrayBuffer[] {
  const packets: ArrayBuffer[] = [];
  const encoder = new Pcm16Encoder(sampleRate, (packet) => packets.push(packet));
  blocks.forEach((block) => encoder.push(block));
  encoder.flush();
  encoder.flush();
  return packets;
}

describe('streaming microphone PCM encoding', () => {
  it('emits 200 ms packets at 16 kHz from a 48 kHz microphone', () => {
    const packets = encode(48000, [new Float32Array(48000).fill(0.5)]);
    expect(packets.map((packet) => packet.byteLength)).toEqual([6400, 6400, 6400, 6400, 6400]);
    expect(new DataView(packets[0]).getInt16(0, true)).toBe(16384);
  });

  it('preserves fractional resampling boundaries across arbitrary 44.1 kHz blocks', () => {
    const samples = Float32Array.from({ length: 44100 }, (_, index) => Math.sin(index / 10));
    const whole = encode(44100, [samples]);
    const blocks: Float32Array[] = [];
    for (let index = 0; index < samples.length; index += 128) blocks.push(samples.slice(index, index + 128));
    const chunked = encode(44100, blocks);
    expect(chunked.map((packet) => [...new Uint8Array(packet)])).toEqual(whole.map((packet) => [...new Uint8Array(packet)]));
    expect(chunked.reduce((size, packet) => size + packet.byteLength, 0)).toBe(32000);
  });

  it('clips PCM samples and encodes little endian without repeating a flushed packet', () => {
    const packets = encode(16000, [new Float32Array([-2, -1, 0, 1, 2, NaN])]);
    expect(packets).toHaveLength(1);
    expect([...new Uint8Array(packets[0])]).toEqual([0, 128, 0, 128, 0, 0, 255, 127, 255, 127, 0, 0]);
  });

  it('flushes a fractional tail and does not emit anything for silence of zero length', () => {
    expect(encode(48000, [])).toEqual([]);
    const packets = encode(48000, [new Float32Array([0.25])]);
    expect(new DataView(packets[0]).getInt16(0, true)).toBe(8192);
  });

  it.each([0, 8000, NaN, Infinity])('rejects unsupported microphone sample rate %s', (rate) => {
    expect(() => encode(rate, [])).toThrow('unsupported');
  });
});

describe('microphone resource cleanup', () => {
  const track = { stop: vi.fn(), onended: null };
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  const source = { connect: vi.fn(), disconnect: vi.fn() };
  const processor = { connect: vi.fn(), disconnect: vi.fn(), onaudioprocess: null as null | ((event: { inputBuffer: { getChannelData: () => Float32Array } }) => void) };
  const close = vi.fn();
  const getUserMedia = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    getUserMedia.mockResolvedValue(stream);
    close.mockResolvedValue(undefined);
    vi.stubGlobal('isSecureContext', true);
    Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true });
    // Real browsers honour the requested rate; the recorder asks for 16 kHz so
    // their resampler replaces its own fallback FIR. A stub that ignored the
    // argument would exercise the fallback path in every test.
    vi.stubGlobal('AudioContext', class {
      sampleRate: number;
      constructor(options?: { sampleRate?: number }) { this.sampleRate = options?.sampleRate ?? 48000; }
      state = 'running';
      destination = {};
      resume = () => Promise.resolve();
      close = close;
      createMediaStreamSource = () => source;
      createScriptProcessor = () => processor;
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('stops a microphone granted after the user already cancelled', async () => {
    let resolve!: (value: typeof stream) => void;
    getUserMedia.mockReturnValue(new Promise((done) => { resolve = done; }));
    const recorder = new PcmRecorder(vi.fn(), vi.fn());
    const pending = recorder.prepare();
    recorder.cancel();
    resolve(stream);
    await pending;
    expect(track.stop).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
  });

  it('captures fallback audio, flushes a partial packet and releases every audio resource', async () => {
    const onPacket = vi.fn();
    const recorder = new PcmRecorder(onPacket, vi.fn());
    await recorder.prepare();
    await recorder.start();
    // Half a packet at the capture rate the recorder now asks for (16 kHz,
    // 3200 samples per packet), so stop() has a fractional tail to flush.
    processor.onaudioprocess?.({ inputBuffer: { getChannelData: () => new Float32Array(1600).fill(0.5) } });
    await recorder.stop();
    expect(onPacket).toHaveBeenCalledOnce();
    expect(onPacket.mock.calls[0][0].byteLength).toBe(3200);
    expect(track.stop).toHaveBeenCalled();
    expect(source.disconnect).toHaveBeenCalledOnce();
    expect(processor.disconnect).toHaveBeenCalledOnce();
    expect(processor.onaudioprocess).toBeNull();
    expect(close).toHaveBeenCalledOnce();
  });

  it('keeps the call-processing path so a phone microphone is loud enough to recognise', async () => {
    // Per Chromium's Android audio code, without echo cancellation the WebView
    // records through the plain microphone preset; on a real phone that setting
    // left the 90th-percentile frame level at -63 to -84 dBFS and no text came back.
    await new PcmRecorder(vi.fn(), vi.fn()).prepare();
    expect(getUserMedia).toHaveBeenCalledOnce();
    expect(getUserMedia).toHaveBeenCalledWith({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    });
  });

  it('reports the granted capture path, and nothing before the microphone is open', async () => {
    const recorder = new PcmRecorder(vi.fn(), vi.fn());
    expect(recorder.captureInfo()).toBeNull();
    Object.assign(track, { getSettings: () => ({
      sampleRate: 48000, channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: false,
    }) });
    await recorder.prepare();
    expect(recorder.captureInfo()).toEqual({
      contextRate: 16000, trackRate: 48000, channels: 1,
      echoCancellation: true, noiseSuppression: true, autoGainControl: false,
      worklet: false, mode: 'voice',
    });
    recorder.cancel();
    expect(recorder.captureInfo()).toBeNull();
  });

  it('reports nothing instead of failing when a WebView cannot describe the track', async () => {
    Object.assign(track, { getSettings: () => { throw new Error('unsupported'); } });
    const recorder = new PcmRecorder(vi.fn(), vi.fn());
    await recorder.prepare();
    expect(recorder.captureInfo()).toBeNull();
    recorder.cancel();
  });

  it('disables browser voice processing only when raw capture is explicitly selected', async () => {
    await new PcmRecorder(vi.fn(), vi.fn(), 'raw').prepare();
    expect(getUserMedia).toHaveBeenCalledWith({
      audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      video: false,
    });
  });

  it('closes the audio context if microphone permission is rejected', async () => {
    getUserMedia.mockRejectedValue(new DOMException('denied', 'NotAllowedError'));
    const recorder = new PcmRecorder(vi.fn(), vi.fn());
    await expect(recorder.prepare()).rejects.toHaveProperty('name', 'NotAllowedError');
    expect(close).toHaveBeenCalledOnce();
  });

  it('does not request microphone permission on an insecure origin', async () => {
    vi.stubGlobal('isSecureContext', false);
    const recorder = new PcmRecorder(vi.fn(), vi.fn());
    await expect(recorder.prepare()).rejects.toThrow('insecure');
    expect(getUserMedia).not.toHaveBeenCalled();
  });
});

describe('AudioWorklet bounded delivery', () => {
  function worklet() {
    const postMessage = vi.fn();
    class Processor {
      port = { postMessage, onmessage: null as null | ((event: { data: string }) => void) };
    }
    let Capture!: new () => Processor & { process: (inputs: Float32Array[][]) => boolean };
    const register = (_name: string, value: typeof Capture) => { Capture = value; };
    new Function('AudioWorkletProcessor', 'registerProcessor', PCM_WORKLET_SOURCE)(Processor, register);
    return { capture: new Capture(), postMessage };
  }

  it('tolerates delayed acknowledgements for up to 24 blocks, then releases acknowledged capacity', () => {
    const { capture, postMessage } = worklet();
    for (let index = 0; index < 24; index += 1) expect(capture.process([[new Float32Array(2048)]])).toBe(true);
    expect(postMessage).toHaveBeenCalledTimes(24);
    for (let index = 0; index < 12; index += 1) capture.port.onmessage?.({ data: 'ack' });
    for (let index = 0; index < 12; index += 1) expect(capture.process([[new Float32Array(2048)]])).toBe(true);
    expect(postMessage).toHaveBeenCalledTimes(36);
    expect(postMessage.mock.calls.some(([message]) => message.overflow)).toBe(false);
  });

  it('reports an overflow and stops instead of growing the queue indefinitely', () => {
    const { capture, postMessage } = worklet();
    for (let index = 0; index < 24; index += 1) capture.process([[new Float32Array(2048)]]);
    expect(capture.process([[new Float32Array(2048)]])).toBe(false);
    expect(postMessage).toHaveBeenLastCalledWith({ overflow: true });
    expect(capture.process([[new Float32Array(2048)]])).toBe(false);
    expect(postMessage).toHaveBeenCalledTimes(25);
  });

  it('flushes a final partial block before reporting completion', () => {
    const { capture, postMessage } = worklet();
    capture.process([[new Float32Array(128).fill(0.25)]]);
    capture.port.onmessage?.({ data: 'flush' });
    expect(postMessage.mock.calls[0][0].samples).toEqual(new Float32Array(128).fill(0.25));
    expect(postMessage).toHaveBeenLastCalledWith({ flushed: true });
    expect(capture.process([[new Float32Array(128)]])).toBe(false);
  });
});

describe('AudioWorklet stop handshake', () => {
  type CaptureMessage = { samples?: Float32Array; flushed?: boolean; overflow?: boolean };
  const track = { stop: vi.fn(), onended: null as null | (() => void) };
  const source = { connect: vi.fn(), disconnect: vi.fn() };
  const close = vi.fn();
  const addModule = vi.fn();
  const revokeObjectURL = vi.fn();
  let replyDelay: number;
  let capture: { process: (inputs: Float32Array[][]) => boolean; port: { onmessage: null | ((event: { data: string }) => void) } };
  let node: {
    port: { onmessage: null | ((event: { data: CaptureMessage }) => void); postMessage: ReturnType<typeof vi.fn>; close: ReturnType<typeof vi.fn> };
    onprocessorerror: null | (() => void);
    disconnect: ReturnType<typeof vi.fn>;
  } | undefined;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    replyDelay = 0;
    node = undefined;
    track.onended = null;
    close.mockResolvedValue(undefined);
    addModule.mockResolvedValue(undefined);
    vi.stubGlobal('isSecureContext', true);
    vi.stubGlobal('URL', { createObjectURL: () => 'blob:worklet', revokeObjectURL });
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia: async () => ({ getTracks: () => [track], getAudioTracks: () => [track] }) },
      configurable: true,
    });
    vi.stubGlobal('AudioContext', class {
      sampleRate = PCM_SAMPLE_RATE;
      state = 'running';
      destination = {};
      audioWorklet = { addModule };
      resume = () => Promise.resolve();
      close = close;
      createMediaStreamSource = () => source;
    });
    vi.stubGlobal('AudioWorkletNode', class {
      port = {
        onmessage: null as null | ((event: { data: CaptureMessage }) => void),
        postMessage: vi.fn((data: string) => {
          window.setTimeout(() => capture.port.onmessage?.({ data }), 0);
        }),
        close: vi.fn(),
      };
      onprocessorerror = null;
      connect = vi.fn();
      disconnect = vi.fn();
      constructor() {
        node = this;
        class Processor {
          port = {
            onmessage: null as null | ((event: { data: string }) => void),
            postMessage: (data: CaptureMessage, transfer: Transferable[] = []) => {
              // Match MessagePort ownership: transferred buffers are detached
              // at the sender, and replies keep FIFO order at the receiver.
              const received = structuredClone(data, { transfer });
              window.setTimeout(() => node?.port.onmessage?.({ data: received }), replyDelay);
            },
          };
        }
        let Capture!: new () => Processor & { process: (inputs: Float32Array[][]) => boolean };
        new Function('AudioWorkletProcessor', 'registerProcessor', PCM_WORKLET_SOURCE)(
          Processor, (_name: string, value: typeof Capture) => { Capture = value; },
        );
        capture = new Capture();
      }
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  async function recording() {
    const packets: ArrayBuffer[] = [];
    const onError = vi.fn();
    const recorder = new PcmRecorder((packet) => packets.push(packet), onError);
    await recorder.prepare();
    await recorder.start();
    // Two full worklet blocks and a 100 ms tail still inside the processor.
    const samples = Float32Array.from({ length: 4096 + 1600 }, (_, index) => 0.25 * Math.sin(index / 20));
    for (let offset = 0; offset < samples.length; offset += 128) {
      capture.process([[samples.slice(offset, offset + 128)]]);
    }
    await vi.advanceTimersByTimeAsync(0);
    return { recorder, packets, onError, samples };
  }

  it.each([0, 600])('keeps the exact tail when the flush reply takes %s ms', async (delay) => {
    const { recorder, packets, onError, samples } = await recording();
    replyDelay = delay;
    let finished = false;
    const stopping = recorder.stop().then(() => { finished = true; });
    if (delay) {
      await vi.advanceTimersByTimeAsync(500);
      expect(finished).toBe(false);
    }
    await vi.advanceTimersByTimeAsync(delay || 1);
    await stopping;
    expect(packets.map((packet) => [...new Uint8Array(packet)])).toEqual(
      encode(PCM_SAMPLE_RATE, [samples]).map((packet) => [...new Uint8Array(packet)]),
    );
    expect(onError).not.toHaveBeenCalled();
    expect(node?.port.close).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
  });

  it('reports a missing flush reply and rejects late audio instead of completing a truncated take', async () => {
    const { recorder, packets, onError } = await recording();
    replyDelay = 5000;
    const stopping = recorder.stop();
    await vi.advanceTimersByTimeAsync(2100);
    await stopping;
    expect(onError).toHaveBeenCalledExactlyOnceWith('microphone');
    // Only the already transmitted complete packet survives; no false final tail.
    expect(packets.map((packet) => packet.byteLength)).toEqual([6400]);
    expect(node?.port.close).toHaveBeenCalledOnce();
    expect(source.disconnect).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(5000);
    expect(packets.map((packet) => packet.byteLength)).toEqual([6400]);
    expect(onError).toHaveBeenCalledTimes(1);
  });

  it.each(['cancel', 'processor error'] as const)('cleans up %s during a pending flush', async (reason) => {
    const { recorder, packets, onError } = await recording();
    replyDelay = 600;
    const stopping = recorder.stop();
    await vi.advanceTimersByTimeAsync(1);
    if (reason === 'cancel') recorder.cancel();
    else node?.onprocessorerror?.();
    await stopping;
    await vi.advanceTimersByTimeAsync(3000);
    expect(packets.map((packet) => packet.byteLength)).toEqual([6400]);
    expect(onError.mock.calls).toEqual(reason === 'cancel' ? [] : [['microphone']]);
    expect(node?.port.onmessage).toBeNull();
    expect(node?.onprocessorerror).toBeNull();
    expect(node?.port.close).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
  });

  it('shares one flush and cleanup between concurrent stop calls', async () => {
    const { recorder, packets, onError } = await recording();
    replyDelay = 600;
    const first = recorder.stop();
    expect(recorder.stop()).toBe(first);
    await vi.advanceTimersByTimeAsync(700);
    await first;
    expect(node?.port.postMessage.mock.calls.filter(([data]) => data === 'flush')).toHaveLength(1);
    expect(packets.reduce((total, packet) => total + packet.byteLength / 2, 0)).toBe(5696);
    expect(onError).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledOnce();
  });

  it('cancels cleanly when stop arrives while the worklet module is loading', async () => {
    let loaded!: () => void;
    addModule.mockReturnValue(new Promise<void>((resolve) => { loaded = resolve; }));
    const onPacket = vi.fn();
    const onError = vi.fn();
    const recorder = new PcmRecorder(onPacket, onError);
    await recorder.prepare();
    const starting = recorder.start();
    await recorder.stop();
    loaded();
    await starting;
    expect(node).toBeUndefined();
    expect(onPacket).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(track.stop).toHaveBeenCalled();
    expect(source.disconnect).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:worklet');
  });

  it('reports a broken flush port and releases the pending wait', async () => {
    const { recorder, packets, onError } = await recording();
    node!.port.postMessage.mockImplementation(() => { throw new Error('closed port'); });
    await recorder.stop();
    await vi.advanceTimersByTimeAsync(3000);
    expect(onError).toHaveBeenCalledExactlyOnceWith('microphone');
    expect(packets.map((packet) => packet.byteLength)).toEqual([6400]);
    expect(node?.port.close).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
  });
});

describe('capture rate', () => {
  const close = vi.fn();
  const getUserMedia = vi.fn();
  beforeEach(() => {
    vi.clearAllMocks();
    close.mockResolvedValue(undefined);
    getUserMedia.mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] });
    vi.stubGlobal('isSecureContext', true);
    Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('asks the browser for 16 kHz so its resampler replaces the fallback FIR', async () => {
    const seen: Array<{ sampleRate?: number } | undefined> = [];
    vi.stubGlobal('AudioContext', class {
      sampleRate: number;
      constructor(options?: { sampleRate?: number }) {
        seen.push(options);
        this.sampleRate = options?.sampleRate ?? 48000;
      }
      state = 'running';
      destination = {};
      resume = () => Promise.resolve();
      close = close;
    });

    await new PcmRecorder(vi.fn(), vi.fn()).prepare();
    expect(seen).toHaveLength(1);
    expect(seen[0]).toEqual({ sampleRate: PCM_SAMPLE_RATE });
  });

  it('falls back to the default rate when the browser will not give 16 kHz', async () => {
    // Safari has refused unusual rates; the recorder must still record, with
    // its own resampling, rather than failing to start.
    const rates: number[] = [];
    vi.stubGlobal('AudioContext', class {
      sampleRate: number;
      constructor(options?: { sampleRate?: number }) {
        if (options?.sampleRate) throw new Error('unsupported sample rate');
        this.sampleRate = 44100;
        rates.push(this.sampleRate);
      }
      state = 'running';
      destination = {};
      resume = () => Promise.resolve();
      close = close;
    });

    await new PcmRecorder(vi.fn(), vi.fn()).prepare();
    expect(rates).toEqual([44100]);
  });

  it('falls back when the browser silently ignores the requested rate', async () => {
    const closed: number[] = [];
    vi.stubGlobal('AudioContext', class {
      sampleRate = 48000;   // hint ignored
      state = 'running';
      destination = {};
      resume = () => Promise.resolve();
      close = () => { closed.push(1); return Promise.resolve(); };
    });

    await new PcmRecorder(vi.fn(), vi.fn()).prepare();
    // The probe context is closed rather than left running alongside the real one.
    expect(closed).toHaveLength(1);
  });
});
