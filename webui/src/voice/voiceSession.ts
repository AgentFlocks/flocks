import { voiceStreamUrl, type VoiceCaptureMode } from '@/api/voice';
import { PcmRecorder } from './pcmRecorder';

export type VoicePhase = 'connecting' | 'recording' | 'stopping' | 'idle';
const PCM_BYTES_PER_SECOND = 32000;
// Four seconds of audio in the socket's own send buffer; more waits in our queue.
const MAX_BUFFERED_BYTES = 128000;
// Audio captured while the relay is still connecting waits in the queue. The
// connection deadline fires long before this fills; it only bounds memory.
const MAX_QUEUED_BYTES = 60 * PCM_BYTES_PER_SECOND;
const PUMP_INTERVAL_MS = 50;
const MAX_TRANSCRIPT_CHARS = 32000;
// Allow the relay's 10 s handshake plus 15 s each for its start packet and reply.
const CONNECT_TIMEOUT_MS = 45000;
// Stop includes recorder flush (0.5 s), upstream send (15 s), final response
// handling (15 s), and a possible error response (15 s), plus transport margin.
const FINAL_TIMEOUT_MS = 50000;
const MAX_RECORDING_MS = 120000;
const UPSTREAM_ERROR_CODES = new Set([
  'upstream_auth', 'upstream_forbidden', 'upstream_rate_limited', 'upstream_unavailable',
  'upstream_timeout', 'upstream_closed', 'upstream_rejected',
  'no_speech', 'audio_too_quiet',
]);

export class VoiceSession {
  private socket: WebSocket | null = null;
  private recorder: PcmRecorder | null = null;
  private ended = false;
  private ready = false;
  private stopRequested = false;
  private stopSent = false;
  private hasText = false;
  private phase: VoicePhase = 'connecting';
  private timer: number | undefined;
  private connectTimer: number | undefined;
  private pumpTimer: number | undefined;
  private queue: ArrayBuffer[] = [];
  private queuedBytes = 0;
  // Once ready, how far behind the microphone the upload may fall. It starts
  // at the backlog from connecting and only tightens as that drains, so a slow
  // socket fails after four seconds of new lag, as before.
  private lagLimit = MAX_BUFFERED_BYTES;

  constructor(private readonly callbacks: {
    onText: (text: string) => void;
    onPhase: (phase: VoicePhase) => void;
    onError: (code: string, message?: string) => void;
  }, private readonly options: { captureMode?: VoiceCaptureMode } = {}) {}

  private setPhase(phase: VoicePhase): void {
    this.phase = phase;
    this.callbacks.onPhase(phase);
  }

  /** Stopping once the recording is on its way to the relay; before that the tap stays cancellable. */
  private enterStopping(): void {
    if (this.phase !== 'stopping') this.setPhase('stopping');
  }

  private timeout(milliseconds: number, code: string): void {
    window.clearTimeout(this.timer);
    this.timer = window.setTimeout(() => this.fail(code), milliseconds);
  }

  async start(): Promise<void> {
    this.setPhase('connecting');
    this.timeout(30000, 'timeout');
    this.recorder = new PcmRecorder(
      (packet) => this.enqueue(packet),
      (code) => this.fail(code),
      this.options.captureMode,
    );
    try {
      await this.recorder.prepare();
      if (this.ended) return;
      this.connect();
      // Record from the moment the microphone is open. People start talking
      // when they tap; the relay still needs a round trip to the recogniser
      // before it is ready, and audio from that gap waits in the queue instead
      // of being lost.
      await this.recorder.start();
      if (this.ended) return;
      this.setPhase('recording');
      window.clearTimeout(this.timer);
      this.timer = window.setTimeout(() => { void this.stop(); }, MAX_RECORDING_MS);
    } catch (error) {
      if (this.ended) return;
      const name = error && typeof error === 'object' && 'name' in error ? error.name : '';
      const code = error && typeof error === 'object' && 'message' in error && typeof error.message === 'string'
        ? error.message : '';
      this.fail(name === 'NotAllowedError' || name === 'SecurityError'
        ? 'permission'
        : ['insecure', 'unsupported'].includes(code) ? code : 'microphone');
    }
  }

  private connect(): void {
    this.connectTimer = window.setTimeout(() => this.fail('timeout'), CONNECT_TIMEOUT_MS);
    const socket = new WebSocket(voiceStreamUrl());
    this.socket = socket;
    socket.onmessage = (event) => {
      if (this.ended) return;
      try {
        if (typeof event.data !== 'string' || event.data.length > 65536) return this.fail('connection');
        const message = JSON.parse(event.data);
        if (message.type === 'ready' && !this.ready) {
          this.ready = true;
          window.clearTimeout(this.connectTimer);
          this.lagLimit = this.queuedBytes + MAX_BUFFERED_BYTES;
          if (this.stopRequested) this.enterStopping();
          this.pump();
        } else if (message.type === 'transcript' && typeof message.text === 'string') {
          if (message.text.length > MAX_TRANSCRIPT_CHARS) return this.fail('connection');
          this.hasText = Boolean(message.text.trim());
          this.callbacks.onText(message.text);
        } else if (message.type === 'done') {
          if (!this.hasText) return this.fail('no_speech');
          this.cancel();
        } else if (message.type === 'error') {
          // Only known codes may select local copy. Unknown/legacy responses
          // retain the backend's sanitized message, never an arbitrary i18n path.
          const code = typeof message.code === 'string' && UPSTREAM_ERROR_CODES.has(message.code)
            ? message.code : 'server';
          this.fail(code, code === 'server' && typeof message.message === 'string'
            ? message.message : undefined);
        }
      } catch {
        this.fail('connection');
      }
    };
    socket.onerror = () => this.fail('connection');
    socket.onclose = () => { if (!this.ended) this.fail('connection'); };
  }

  private enqueue(packet: ArrayBuffer): void {
    if (this.ended) return;
    this.queue.push(packet);
    this.queuedBytes += packet.byteLength;
    if (!this.ready) {
      if (this.queuedBytes > MAX_QUEUED_BYTES) this.fail('timeout');
      return;
    }
    if (this.queuedBytes + (this.socket?.bufferedAmount ?? 0) > this.lagLimit) return this.fail('backpressure');
    this.pump();
  }

  /** Moves queued audio into the socket without overfilling it, then sends stop once drained. */
  private pump(): void {
    window.clearTimeout(this.pumpTimer);
    this.pumpTimer = undefined;
    const socket = this.socket;
    if (this.ended || !this.ready || !socket) return;
    if (socket.readyState !== WebSocket.OPEN) return this.fail('connection');
    try {
      while (this.queue.length && socket.bufferedAmount + this.queue[0].byteLength <= MAX_BUFFERED_BYTES) {
        const packet = this.queue.shift()!;
        this.queuedBytes -= packet.byteLength;
        socket.send(packet);
      }
      this.lagLimit = Math.min(this.lagLimit, this.queuedBytes + socket.bufferedAmount + MAX_BUFFERED_BYTES);
      if (this.queue.length) {
        this.pumpTimer = window.setTimeout(() => this.pump(), PUMP_INTERVAL_MS);
      } else if (this.stopRequested && !this.stopSent) {
        this.stopSent = true;
        socket.send(JSON.stringify({ type: 'stop' }));
        // The relay's own final window starts now, not at the tap, which may
        // have come long before the relay was ready.
        this.timeout(FINAL_TIMEOUT_MS, 'timeout');
      }
    } catch {
      this.fail('connection');
    }
  }

  async stop(): Promise<void> {
    if (this.ended || this.phase === 'stopping') return;
    if (this.phase !== 'recording') return this.cancel();
    // Before the relay is ready the recording is only queued here, so stay in
    // connecting, whose button still cancels; ready moves it on to stopping.
    this.setPhase(this.ready ? 'stopping' : 'connecting');
    this.timeout(FINAL_TIMEOUT_MS, 'timeout');
    try {
      await this.recorder?.stop();
      if (this.ended) return;
      // Stop follows the last queued packet; before ready it waits for the relay.
      this.stopRequested = true;
      if (this.ready) this.enterStopping();
      this.pump();
    } catch {
      this.fail('connection');
    }
  }

  private fail(code: string, message?: string): void {
    if (this.ended) return;
    this.callbacks.onError(code, message);
    this.cancel();
  }

  cancel(): void {
    if (this.ended) return;
    this.ended = true;
    window.clearTimeout(this.timer);
    window.clearTimeout(this.connectTimer);
    window.clearTimeout(this.pumpTimer);
    this.queue = [];
    this.queuedBytes = 0;
    this.recorder?.cancel();
    this.recorder = null;
    if (this.socket) {
      this.socket.onmessage = null;
      this.socket.onerror = null;
      this.socket.onclose = null;
      this.socket.close();
      this.socket = null;
    }
    this.setPhase('idle');
  }
}
