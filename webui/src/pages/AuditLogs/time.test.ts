import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { toAuditUtcTimestamp } from './time';

describe('audit datetime-local conversion', () => {
  beforeEach(() => vi.stubEnv('TZ', 'Asia/Shanghai'));
  afterEach(() => vi.unstubAllEnvs());

  it('converts a UTC+8 local minute to the UTC SQLite text format', () => {
    expect(toAuditUtcTimestamp('2026-09-30T16:25')).toBe('2026-09-30 08:25:00');
  });

  it('converts across the UTC date and year boundary', () => {
    expect(toAuditUtcTimestamp('2026-01-01T00:15')).toBe('2025-12-31 16:15:00');
  });

  it('preserves selected seconds and does not expand an end minute', () => {
    expect(toAuditUtcTimestamp('2026-09-30T16:25:37')).toBe('2026-09-30 08:25:37');
    expect(toAuditUtcTimestamp('2026-09-30T16:25:37.125')).toBe('2026-09-30 08:25:37.125');
    expect(toAuditUtcTimestamp('2026-09-30T16:25')).toBe('2026-09-30 08:25:00');
  });

  it('omits an empty filter', () => {
    expect(toAuditUtcTimestamp('')).toBeUndefined();
  });

  it.each([
    'not-a-date', 'Invalid Date', ' ', '2026-02-30T10:00',
    '2026-13-01T10:00', '2026-09-30T24:00', '2026-09-30T10:60',
    '2026-09-30', '2026-09-30T10:00Z',
  ])('rejects invalid or non-local input %s without emitting a timestamp', (value) => {
    expect(() => toAuditUtcTimestamp(value)).toThrow(RangeError);
  });

  it('uses the browser timezone instead of hard-coding UTC+8', () => {
    vi.stubEnv('TZ', 'America/New_York');
    expect(toAuditUtcTimestamp('2026-01-01T23:15')).toBe('2026-01-02 04:15:00');
    expect(toAuditUtcTimestamp('2026-07-01T23:15')).toBe('2026-07-02 03:15:00');
  });

  it('rejects a nonexistent local hour instead of normalizing the selected range', () => {
    vi.stubEnv('TZ', 'America/New_York');
    expect(() => toAuditUtcTimestamp('2026-03-08T02:30')).toThrow(RangeError);
  });
});
