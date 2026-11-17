import { describe, expect, it } from 'vitest';
import { formatProductVersion } from './productVersion';

describe('formatProductVersion', () => {
  it('writes every calendar version as v<year>.<month>.<day>.<build>', () => {
    expect(formatProductVersion('2026.11.09.1')).toBe('v2026.11.09.1');
    expect(formatProductVersion('v2026.11.09.12')).toBe('v2026.11.09.12');
    expect(formatProductVersion('2026.11.9')).toBe('v2026.11.09.0');
    expect(formatProductVersion('v2026.11.09')).toBe('v2026.11.09.0');
    expect(formatProductVersion('pro-v2026.10.19')).toBe('v2026.10.19.0');
    expect(formatProductVersion('2026.11.09-2')).toBe('v2026.11.09.2');
    expect(formatProductVersion(' 2026.1.5.3 ')).toBe('v2026.01.05.3');
  });

  it('shows anything else as it came, and nothing for nothing', () => {
    expect(formatProductVersion('unknown')).toBe('unknown');
    expect(formatProductVersion('1.2.3')).toBe('v1.2.3');
    expect(formatProductVersion('20261109')).toBe('v20261109');
    expect(formatProductVersion('')).toBeNull();
    expect(formatProductVersion(null)).toBeNull();
    expect(formatProductVersion(undefined)).toBeNull();
  });
});
