import { describe, expect, it } from 'vitest';
import { StreamingResampler } from './resampler';

function resample(rate: number, input: Float32Array, blockSize = input.length): number[] {
  const output: number[] = [];
  const resampler = new StreamingResampler(rate, (value) => output.push(value));
  for (let start = 0; start < input.length; start += blockSize) resampler.push(input.subarray(start, start + blockSize));
  resampler.flush();
  resampler.flush();
  return output;
}

function tone(rate: number, frequency: number): Float32Array {
  return Float32Array.from({ length: rate }, (_, index) => 0.5 * Math.sin(2 * Math.PI * frequency * index / rate));
}

function amplitude(samples: number[]): number {
  // Exclude the finite signal's edge padding when measuring the filter response.
  const interior = samples.slice(160, -160);
  return Math.sqrt(interior.reduce((sum, value) => sum + value * value, 0) / interior.length * 2);
}

describe('anti-aliased streaming resampling', () => {
  it.each([44100, 48000, 96000])('preserves the speech band and suppresses 9 kHz aliasing at %s Hz', (rate) => {
    const passband = amplitude(resample(rate, tone(rate, 3000), 128)); // secret-guard: allow
    const stopband = amplitude(resample(rate, tone(rate, 9000), 128));
    expect(20 * Math.log10(passband / 0.5)).toBeGreaterThan(-0.1);
    expect(20 * Math.log10(stopband / 0.5)).toBeLessThan(-55);
  });

  it.each([44100, 48000])('keeps block boundaries, the onset and the final fractional frame at %s Hz', (rate) => {
    const input = tone(rate, 1000).subarray(0, rate - 13);
    const whole = resample(rate, input);
    const chunked = resample(rate, input, 127);
    expect(chunked).toEqual(whole);
    expect(chunked).toHaveLength(Math.ceil(input.length * 16000 / rate));
    expect(Math.max(...chunked.slice(0, 160))).toBeGreaterThan(0.49);
    expect(Math.max(...chunked.slice(-160))).toBeGreaterThan(0.49);
  });
});
