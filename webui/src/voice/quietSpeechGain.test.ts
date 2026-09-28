import { describe, expect, it } from 'vitest';
import { QuietSpeechGain } from './quietSpeechGain';
import { Pcm16Encoder } from './pcmRecorder';

function process(input: Float32Array): number[] {
  const output: number[] = [];
  const gain = new QuietSpeechGain((value) => output.push(value));
  input.forEach((sample) => gain.push(sample));
  gain.flush();
  gain.flush();
  return output;
}

function tone(dbfs: number, milliseconds: number): Float32Array {
  const amplitude = Math.SQRT2 * 10 ** (dbfs / 20);
  return Float32Array.from({ length: milliseconds * 16 }, (_, index) => amplitude * Math.sin(2 * Math.PI * 440 * index / 16000));
}

function encode(input: Float32Array): number[] {
  const packets: ArrayBuffer[] = [];
  const encoder = new Pcm16Encoder(16000, (packet) => packets.push(packet));
  for (let index = 0; index < input.length; index += 128) encoder.push(input.subarray(index, index + 128));
  encoder.flush();
  return packets.flatMap((packet) => [...new Int16Array(packet)]);
}

describe('quiet speech before integer quantization', () => {
  it('preserves audible information below one PCM16 step', () => {
    const input = Float32Array.from({ length: 16000 }, (_, index) => 0.00001 * Math.sin(2 * Math.PI * 440 * index / 16000));
    expect(input.every((sample) => Math.round(sample * 32768) === 0)).toBe(true);
    const packets: ArrayBuffer[] = [];
    const encoder = new Pcm16Encoder(16000, (packet) => packets.push(packet));
    encoder.push(input);
    encoder.flush();
    const values = packets.flatMap((packet) => [...new Int16Array(packet)]);
    expect(values).toHaveLength(input.length);
    expect(values.filter((sample) => sample !== 0).length / values.length).toBeGreaterThan(0.9);
    expect(Math.max(...values)).toBeGreaterThan(8);
  });

  it('leaves normal voice amplitude unchanged and never turns digital silence into signal', () => {
    const input = Float32Array.from({ length: 3200 }, (_, index) => 0.2 * Math.sin(2 * Math.PI * 440 * index / 16000));
    expect(process(input)).toEqual([...input]);
    expect(process(new Float32Array(3213))).toEqual(Array(3213).fill(0));
  });

  it('bounds noise amplification and keeps a following loud impulse unamplified', () => {
    const quiet = Float32Array.from({ length: 16000 }, (_, index) => (index % 2 ? -1 : 1) * 0.0001);
    const input = new Float32Array(quiet.length + 320);
    input.set(quiet);
    input[quiet.length + 100] = 0.9;
    const output = process(input);
    const background = output.slice(quiet.length - 320, quiet.length);
    const rms = Math.sqrt(background.reduce((energy, sample) => energy + sample * sample, 0) / background.length);
    expect(20 * Math.log10(rms)).toBeLessThanOrEqual(-54.99);
    expect(output[quiet.length + 100]).toBe(input[quiet.length + 100]);
    expect(Math.max(...output)).toBeLessThan(1);
  });

  it('ramps gain upward without frame-boundary jumps and flushes a short tail once', () => {
    const output = process(new Float32Array(645).fill(0.00001));
    expect(output).toHaveLength(645);
    const steps = output.slice(1).map((sample, index) => Math.abs(sample - output[index]));
    expect(Math.max(...steps)).toBeLessThan(0.000002);
  });

  it.each(['silence', 'mostly zero'])('expires old loud speech during %s before a quiet utterance', (pause) => {
    const input = new Float32Array(48000 + 80000 + 16000);
    for (let index = 0; index < 48000; index += 1) input[index] = 0.2 * Math.sin(2 * Math.PI * 440 * index / 16000);
    if (pause === 'mostly zero') {
      for (let index = 48000; index < 128000; index += 320) input[index] = 0.000001;
    }
    for (let index = 128000; index < input.length; index += 1) input[index] = 0.00001 * Math.sin(2 * Math.PI * 440 * index / 16000);
    const packets: ArrayBuffer[] = [];
    const encoder = new Pcm16Encoder(16000, (packet) => packets.push(packet));
    encoder.push(input);
    encoder.flush();
    const lastSecond = packets.flatMap((packet) => [...new Int16Array(packet)]).slice(-16000);
    expect(lastSecond.filter((sample) => sample !== 0).length / lastSecond.length).toBeGreaterThan(0.9);
    expect(Math.max(...lastSecond)).toBeGreaterThan(8);
  });

  it.each([0, 200, 1000])('preserves sub-PCM16 speech after normal speech and a %s ms pause', (pauseMs) => {
    const normal = tone(-30, 1000);
    const quiet = tone(-100, 1000);
    expect(quiet.every((sample) => Math.round(sample * 32768) === 0)).toBe(true);
    const input = new Float32Array(normal.length + pauseMs * 16 + quiet.length);
    input.set(normal);
    input.set(quiet, normal.length + pauseMs * 16);
    const output = encode(input);
    expect(output).toHaveLength(input.length);
    expect(output.slice(0, normal.length)).toEqual(encode(normal));
    expect(output.slice(normal.length, normal.length + pauseMs * 16).every((sample) => sample === 0)).toBe(true);
    const lastSecond = output.slice(-quiet.length);
    expect(lastSecond.filter((sample) => sample !== 0).length / lastSecond.length).toBeGreaterThan(0.9);
    expect(Math.max(...lastSecond.map(Math.abs))).toBeLessThanOrEqual(4);
  });

  it.each([0.51 / 32768, 0.0001])('keeps history protection for already representable quiet audio at peak %s', (amplitude) => {
    const normal = tone(-30, 1000);
    const quiet = Float32Array.from({ length: 16000 }, (_, index) => amplitude * Math.sin(2 * Math.PI * 440 * index / 16000));
    const input = new Float32Array(normal.length + quiet.length);
    input.set(normal);
    input.set(quiet, normal.length);
    expect(process(input).slice(normal.length)).toEqual([...quiet]);
  });

  it.each([0.00001, 0.0000001])('keeps sub-PCM16 background noise far below the noise ceiling and within 30 dB at peak %s', (amplitude) => {
    const normal = tone(-30, 1000);
    let seed = 7;
    const noise = Float32Array.from({ length: 16000 }, () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return amplitude * (seed / 0x100000000 * 2 - 1);
    });
    const input = new Float32Array(normal.length + noise.length);
    input.set(normal);
    input.set(noise, normal.length);
    const output = process(input).slice(normal.length);
    const maxGain = 10 ** (30 / 20);
    expect(output.every((sample, index) => Math.abs(sample) <= Math.abs(noise[index]) * maxGain + 1e-12)).toBe(true);
    const peak = Math.max(...output.map(Math.abs));
    expect(peak).toBeLessThanOrEqual(4 / 32768 + 1e-12);
    expect(20 * Math.log10(peak)).toBeLessThan(-78);
    expect(output.some((sample, index) => Math.abs(sample) > Math.abs(noise[index]))).toBe(true);
  });

  it('drops the quantization rescue before a loud transient and leaves following normal speech unchanged', () => {
    const normal = tone(-30, 1000);
    const quiet = tone(-100, 1000);
    const impulse = new Float32Array(320);
    impulse[100] = 0.9;
    const input = new Float32Array(normal.length * 2 + quiet.length + impulse.length);
    input.set(normal);
    input.set(quiet, normal.length);
    input.set(impulse, normal.length + quiet.length);
    input.set(normal, normal.length + quiet.length + impulse.length);
    const output = process(input);
    expect(output.slice(normal.length, normal.length + quiet.length).some((sample, index) => Math.abs(sample) > Math.abs(quiet[index]))).toBe(true);
    expect(output.slice(normal.length + quiet.length)).toEqual([...impulse, ...normal]);
    expect(Math.max(...output.map(Math.abs))).toBeLessThan(1);
  });
});
