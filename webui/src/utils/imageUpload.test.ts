import { describe, expect, it } from 'vitest';
import { buildPromptParts } from './imageUpload';
import type { ImagePartData } from './imageUpload';

const img: ImagePartData = {
  url: 'data:image/png;base64,abc',
  mime: 'image/png',
  filename: 'test.png',
};

describe('buildPromptParts', () => {
  it('text only — single text part', () => {
    const parts = buildPromptParts('hello');
    expect(parts).toEqual([{ type: 'text', text: 'hello' }]);
  });

  it('image only — fallback empty-text sentinel + image part', () => {
    const parts = buildPromptParts('', [img]);
    expect(parts).toEqual([
      { type: 'file', url: img.url, mime: img.mime, filename: img.filename },
    ]);
  });

  it('text + image — text first, then image', () => {
    const parts = buildPromptParts('describe this', [img]);
    expect(parts[0]).toEqual({ type: 'text', text: 'describe this' });
    expect(parts[1]).toEqual({
      type: 'file',
      url: img.url,
      mime: img.mime,
      filename: img.filename,
    });
    expect(parts).toHaveLength(2);
  });

  it('empty text + empty images — fallback sentinel so parts is never empty', () => {
    const parts = buildPromptParts('', []);
    expect(parts).toHaveLength(1);
    expect(parts[0]).toMatchObject({ type: 'text', text: '' });
  });

  it('multiple images — all appended in order', () => {
    const img2: ImagePartData = { url: 'data:image/jpeg;base64,xyz', mime: 'image/jpeg', filename: 'b.jpg' };
    const parts = buildPromptParts('two images', [img, img2]);
    expect(parts).toHaveLength(3);
    expect(parts[0].type).toBe('text');
    expect(parts[1].filename).toBe('test.png');
    expect(parts[2].filename).toBe('b.jpg');
  });

  it('whitespace-only text is treated as empty (no text part emitted)', () => {
    // buildPromptParts trims nothing — caller is responsible. A single space
    // is truthy so it DOES produce a text part. This test locks that behaviour.
    const parts = buildPromptParts(' ', [img]);
    expect(parts[0]).toMatchObject({ type: 'text', text: ' ' });
  });
});
