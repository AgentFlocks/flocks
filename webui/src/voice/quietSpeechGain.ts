const FRAME_SAMPLES = 320;
const WINDOW_FRAMES = 150;
const MAX_GAIN_DB = 30;
const TARGET_DBFS = -36;
const NOISE_CEILING_DBFS = -55;
const PEAK_LIMIT_DBFS = -3;
const SPEECH_MARGIN_DB = 10;

/** Raises quiet float audio before PCM16 quantization can erase it. */
export class QuietSpeechGain {
  private readonly frame = new Float32Array(FRAME_SAMPLES);
  private count = 0;
  private readonly recent: number[] = [];
  private recentIndex = 0;
  private gainDb = 0;

  constructor(private readonly onSample: (value: number) => void) {}

  push(sample: number): void {
    this.frame[this.count++] = sample;
    if (this.count === FRAME_SAMPLES) this.flush();
  }

  flush(): void {
    if (!this.count) return;
    let energy = 0;
    let peak = 0;
    let zeros = 0;
    for (let index = 0; index < this.count; index += 1) {
      const sample = this.frame[index];
      energy += sample * sample;
      peak = Math.max(peak, Math.abs(sample));
      if (sample === 0) zeros += 1;
    }
    const level = energy ? 10 * Math.log10(energy / this.count) : NaN;
    // Time advances even through digital silence. Invalid placeholders evict
    // stale loud speech without treating digital zeros as acoustic background.
    this.recent[this.recentIndex] = zeros * 2 <= this.count ? level : NaN;
    this.recentIndex = (this.recentIndex + 1) % WINDOW_FRAMES;
    if (!energy) {
      for (let index = 0; index < this.count; index += 1) this.onSample(0);
      this.count = 0;
      return;
    }

    const levels = this.recent.filter(Number.isFinite).sort((a, b) => a - b);
    if (!levels.length) levels.push(level);
    const background = levels[Math.floor(levels.length / 20)];
    const speech = levels.filter((value) => value > background + SPEECH_MARGIN_DB);
    const aim = speech.length ? TARGET_DBFS - speech[Math.floor((speech.length - 1) / 2)] : MAX_GAIN_DB;
    const gainDb = Math.max(0, Math.min(
      MAX_GAIN_DB,
      aim,
      TARGET_DBFS - level,
      NOISE_CEILING_DBFS - background,
      PEAK_LIMIT_DBFS - 20 * Math.log10(peak),
    ));
    // Reduce before a louder frame; ramp increases over 20 ms to avoid steps.
    const begin = 10 ** (Math.min(this.gainDb, gainDb) / 20);
    const end = 10 ** (gainDb / 20);
    const step = (end - begin) / this.count;
    this.gainDb = gainDb;
    for (let index = 0; index < this.count; index += 1) {
      this.onSample(this.frame[index] * (begin + step * (index + 1)));
    }
    this.count = 0;
  }
}
