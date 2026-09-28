import type { VoiceCaptureInfo, VoiceCaptureMode } from '@/api/voice';
import { StreamingResampler } from './resampler';
import { QuietSpeechGain } from './quietSpeechGain';

export const PCM_SAMPLE_RATE = 16000;
export const PCM_PACKET_SAMPLES = 3200;
const WORKLET_FLUSH_TIMEOUT_MS = 2000;

/** Little-endian PCM16 packetization after anti-aliased streaming resampling. */
export class Pcm16Encoder {
  private readonly resampler: StreamingResampler;
  private readonly gain: QuietSpeechGain;
  private packet = new ArrayBuffer(PCM_PACKET_SAMPLES * 2);
  private view = new DataView(this.packet);
  private count = 0;

  constructor(sampleRate: number, private readonly onPacket: (packet: ArrayBuffer) => void) {
    this.gain = new QuietSpeechGain((sample) => this.write(sample));
    this.resampler = new StreamingResampler(sampleRate, (sample) => this.gain.push(sample));
  }

  push(samples: Float32Array): void {
    this.resampler.push(samples);
  }

  private write(sample: number): void {
    sample = Math.max(-1, Math.min(1, sample));
    this.view.setInt16(this.count * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
    this.count += 1;
    if (this.count === PCM_PACKET_SAMPLES) this.emit();
  }

  private emit(): void {
    if (!this.count) return;
    const packet = this.packet.slice(0, this.count * 2);
    this.count = 0;
    this.onPacket(packet);
  }

  flush(): void {
    this.resampler.flush();
    this.gain.flush();
    this.emit();
  }
}

/** An AudioContext at the rate the recogniser wants, if the browser allows it. */
function openContext(): AudioContext {
  try {
    const context = new AudioContext({ sampleRate: PCM_SAMPLE_RATE });
    // Chrome honours the hint; some builds silently ignore it, so check.
    if (context.sampleRate === PCM_SAMPLE_RATE) return context;
    void context.close();
  } catch {
    // An unsupported rate throws rather than falling back on its own.
  }
  return new AudioContext();
}

// Keep audio off the UI thread when supported. At most 24 transferred blocks are
// outstanding; stop with an error if the UI cannot keep up, rather than queueing
// an unbounded recording in the browser's message channel.
export const PCM_WORKLET_SOURCE = `
class FlocksPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(2048);
    this.offset = 0;
    this.pending = 0;
    this.stopped = false;
    this.port.onmessage = ({ data }) => {
      if (data === 'ack') this.pending = Math.max(0, this.pending - 1);
      if (data === 'flush') {
        this.stopped = true;
        if (this.offset) this.port.postMessage({ samples: this.buffer.slice(0, this.offset) });
        this.port.postMessage({ flushed: true });
      }
    };
  }
  process(inputs) {
    if (this.stopped) return false;
    const samples = inputs[0] && inputs[0][0];
    if (!samples) return true;
    for (let i = 0; i < samples.length; i++) {
      this.buffer[this.offset++] = samples[i];
      if (this.offset === this.buffer.length) {
        if (this.pending >= 24) {
          this.port.postMessage({ overflow: true });
          this.stopped = true;
          return false;
        }
        this.port.postMessage({ samples: this.buffer }, [this.buffer.buffer]);
        this.pending++;
        this.buffer = new Float32Array(2048);
        this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('flocks-pcm-capture', FlocksPcmCapture);
`;

export class PcmRecorder {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private node: AudioWorkletNode | ScriptProcessorNode | null = null;
  private encoder: Pcm16Encoder | null = null;
  private cancelled = false;
  private finishFlush: (() => void) | null = null;
  private stopPromise: Promise<void> | null = null;

  constructor(
    private readonly onPacket: (packet: ArrayBuffer) => void,
    private readonly onError: (code: string) => void,
    private readonly captureMode: VoiceCaptureMode = 'voice',
  ) {}

  /** What the browser actually granted; only known after prepare(). */
  captureInfo(): VoiceCaptureInfo | null {
    const context = this.context;
    const track = this.stream?.getAudioTracks()[0];
    if (!context || !track) return null;
    let settings: MediaTrackSettings;
    try {
      settings = track.getSettings();
    } catch {
      // Diagnostics only; an odd WebView must not fail the recording.
      return null;
    }
    return {
      contextRate: context.sampleRate,
      trackRate: settings.sampleRate,
      channels: settings.channelCount,
      echoCancellation: typeof settings.echoCancellation === 'boolean' ? settings.echoCancellation : undefined,
      noiseSuppression: settings.noiseSuppression,
      autoGainControl: settings.autoGainControl,
      worklet: Boolean(context.audioWorklet) && typeof AudioWorkletNode !== 'undefined',
      mode: this.captureMode,
    };
  }

  /** Called directly from the click gesture, before the WebSocket handshake. */
  async prepare(): Promise<void> {
    if (!window.isSecureContext) throw new Error('insecure');
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) throw new Error('unsupported');
    // Ask for 16 kHz directly so the browser's own resampler does the work.
    // If the hint is ignored, the streaming FIR fallback removes content above
    // output Nyquist before decimation instead of folding it into speech.
    this.context = openContext();
    const resumed = this.context.resume();
    // Handle resume rejection immediately while microphone permission is pending.
    void resumed.catch(() => {});
    try {
      // Keep echo cancellation on. Per Chromium's Android audio code, turning
      // it off makes the WebView record through the plain microphone preset
      // instead of the voice path. On a Honor PTP-AN10 (Chrome 151) with it
      // off, the 90th-percentile level of 20 ms frames was -63 to -84 dBFS
      // (median about -95; measured with gain control on, noise suppression
      // off), and the recogniser returned no text with gain control on or
      // off; offline it produced text at a -61 dBFS median. The same phone did
      // return text with echo cancellation on, though those sessions' levels
      // were not measured.
      const voiceProcessing = this.captureMode !== 'raw';
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: voiceProcessing,
          noiseSuppression: voiceProcessing,
          autoGainControl: voiceProcessing,
        },
        video: false,
      });
      if (this.cancelled) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      await resumed;
    } catch (error) {
      this.cancel();
      throw error;
    }
  }

  async start(): Promise<void> {
    const context = this.context;
    const stream = this.stream;
    if (this.cancelled || !context || !stream) return;
    this.encoder = new Pcm16Encoder(context.sampleRate, this.onPacket);
    this.source = context.createMediaStreamSource(stream);
    if (context.audioWorklet && typeof AudioWorkletNode !== 'undefined') {
      const url = URL.createObjectURL(new Blob([PCM_WORKLET_SOURCE], { type: 'text/javascript' }));
      try {
        await context.audioWorklet.addModule(url);
        if (this.cancelled) return;
        const node = new AudioWorkletNode(context, 'flocks-pcm-capture', {
          channelCount: 1,
          channelCountMode: 'explicit',
        });
        node.port.onmessage = ({ data }) => {
          if (this.cancelled) return;
          if (data.samples) {
            this.encoder?.push(data.samples);
            if (!this.cancelled) node.port.postMessage('ack');
          }
          if (data.overflow) this.fail('backpressure');
          if (data.flushed) this.finishFlush?.();
        };
        node.onprocessorerror = () => this.fail('microphone');
        this.node = node;
      } finally {
        URL.revokeObjectURL(url);
      }
    } else {
      // Compatibility path for older browsers; no audio is played to speakers.
      const node = context.createScriptProcessor(4096, 1, 1);
      node.onaudioprocess = (event) => {
        if (!this.cancelled) this.encoder?.push(event.inputBuffer.getChannelData(0));
      };
      this.node = node;
    }
    if (this.cancelled || !this.node) return;
    stream.getAudioTracks().forEach((track) => {
      track.onended = () => this.fail('microphone');
    });
    this.source.connect(this.node);
    this.node.connect(context.destination);
  }

  stop(): Promise<void> {
    if (this.cancelled) return Promise.resolve();
    this.stopPromise ??= this.finishRecording();
    return this.stopPromise;
  }

  private async finishRecording(): Promise<void> {
    this.stream?.getTracks().forEach((track) => { track.onended = null; track.stop(); });
    if (this.node && 'port' in this.node) {
      const node = this.node;
      try {
        await new Promise<void>((resolve, reject) => {
          const timer = window.setTimeout(() => {
            this.finishFlush = null;
            reject(new Error('microphone'));
          }, WORKLET_FLUSH_TIMEOUT_MS);
          this.finishFlush = () => {
            window.clearTimeout(timer);
            this.finishFlush = null;
            resolve();
          };
          try {
            node.port.postMessage('flush');
          } catch (error) {
            window.clearTimeout(timer);
            this.finishFlush = null;
            reject(error);
          }
        });
      } catch {
        // A deadline is a failed capture, never proof that the final samples
        // arrived. Do not submit the encoder's partial tail as a complete take.
        this.fail('microphone');
        return;
      }
    }
    if (!this.cancelled) this.encoder?.flush();
    this.cancel();
  }

  private fail(code: string): void {
    if (this.cancelled) return;
    this.cancel();
    this.onError(code);
  }

  cancel(): void {
    this.cancelled = true;
    this.finishFlush?.();
    this.finishFlush = null;
    this.stream?.getTracks().forEach((track) => { track.onended = null; track.stop(); });
    this.stream = null;
    this.source?.disconnect();
    this.source = null;
    if (this.node && 'port' in this.node) {
      this.node.onprocessorerror = null;
      this.node.port.onmessage = null;
      this.node.port.close();
    } else if (this.node) this.node.onaudioprocess = null;
    this.node?.disconnect();
    this.node = null;
    this.encoder = null;
    if (this.context && this.context.state !== 'closed') void this.context.close().catch(() => {});
    this.context = null;
  }
}
