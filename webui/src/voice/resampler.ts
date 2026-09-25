const OUTPUT_RATE = 16000;
const PHASES = 512;

/** Bounded, windowed-sinc resampling with enough lookahead to reject aliased sibilants. */
export class StreamingResampler {
  private readonly ratio: number;
  private readonly halfWidth: number;
  private readonly samples: Float32Array;
  private readonly kernels: Float64Array[] = [];
  private inputCount = 0;
  private outputCount = 0;
  private first = 0;
  private last = 0;
  private flushed = false;

  constructor(sampleRate: number, private readonly onSample: (value: number) => void) {
    if (!Number.isFinite(sampleRate) || sampleRate < OUTPUT_RATE || sampleRate > 384000) {
      throw new Error('unsupported');
    }
    this.ratio = sampleRate / OUTPUT_RATE;
    this.halfWidth = this.ratio === 1 ? 0 : Math.ceil(24 * this.ratio);
    this.samples = new Float32Array(2 * this.halfWidth + Math.ceil(this.ratio) + 2);
    if (this.ratio === 1) return;

    // 7.2 kHz cutoff gives the Blackman window a transition band before the
    // 8 kHz output Nyquist limit. Precompute phases once, off the audio thread.
    const cutoff = 0.45 / this.ratio;
    for (let phase = 0; phase <= PHASES; phase += 1) {
      const kernel = new Float64Array(2 * this.halfWidth + 1);
      let total = 0;
      for (let tap = 0; tap < kernel.length; tap += 1) {
        const distance = tap - this.halfWidth - phase / PHASES;
        if (Math.abs(distance) > this.halfWidth) continue;
        const x = 2 * Math.PI * cutoff * distance;
        const sinc = Math.abs(x) < 1e-12 ? 1 : Math.sin(x) / x;
        const window = 0.42 + 0.5 * Math.cos(Math.PI * distance / this.halfWidth)
          + 0.08 * Math.cos(2 * Math.PI * distance / this.halfWidth);
        kernel[tap] = 2 * cutoff * sinc * window;
        total += kernel[tap];
      }
      for (let tap = 0; tap < kernel.length; tap += 1) kernel[tap] /= total;
      this.kernels.push(kernel);
    }
  }

  push(block: Float32Array): void {
    if (this.flushed) return;
    for (const sample of block) {
      const value = Number.isFinite(sample) ? Math.max(-1, Math.min(1, sample)) : 0;
      if (this.ratio === 1) {
        this.onSample(value);
        continue;
      }
      if (!this.inputCount) this.first = value;
      this.last = value;
      this.samples[this.inputCount % this.samples.length] = value;
      this.inputCount += 1;
      while (Math.floor(this.outputCount * this.ratio) + this.halfWidth < this.inputCount) {
        this.emit();
      }
    }
  }

  private emit(): void {
    const position = this.outputCount * this.ratio;
    const center = Math.floor(position);
    const kernel = this.kernels[Math.round((position - center) * PHASES)];
    let value = 0;
    for (let tap = 0; tap < kernel.length; tap += 1) {
      const index = center + tap - this.halfWidth;
      const sample = index < 0 ? this.first : index >= this.inputCount
        ? this.last : this.samples[index % this.samples.length];
      value += sample * kernel[tap];
    }
    this.outputCount += 1;
    this.onSample(value);
  }

  flush(): void {
    if (this.flushed) return;
    this.flushed = true;
    // Extend the final sample only for filter lookahead; do not add duration.
    const length = Math.ceil(this.inputCount / this.ratio);
    while (this.outputCount < length) this.emit();
  }
}
