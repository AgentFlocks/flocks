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
