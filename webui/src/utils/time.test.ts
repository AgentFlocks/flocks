import { describe, expect, it } from 'vitest';

import { formatRelativeTime, formatSmartTime } from './time';

describe('formatSmartTime', () => {
  const at = (year: number, month: number, day: number, hour = 15, minute = 34) =>
    new Date(year, month - 1, day, hour, minute).getTime();

  it('uses a compact 24-hour clock for messages from today', () => {
    expect(formatSmartTime(at(2026, 7, 24), 'zh-CN', at(2026, 7, 24, 18, 0))).toBe('15:34');
  });

  it('labels messages from yesterday', () => {
    expect(formatSmartTime(at(2026, 7, 23), 'zh-CN', at(2026, 7, 24, 18, 0))).toBe('昨天 15:34');
  });

  it('uses the weekday for messages within the previous week', () => {
    expect(formatSmartTime(at(2026, 7, 23), 'zh-CN', at(2026, 7, 25, 18, 0))).toBe('星期四 15:34');
  });

  it('uses a Chinese month and day for older messages from the same year', () => {
    expect(formatSmartTime(at(2026, 7, 10), 'zh-CN', at(2026, 7, 24, 18, 0))).toBe('7月10日 15:34');
    expect(formatSmartTime(at(2026, 6, 20), 'zh-CN', at(2026, 7, 24, 18, 0))).toBe('6月20日 15:34');
  });
});

describe('formatRelativeTime', () => {
  const now = new Date('2026-07-24T12:00:00+08:00').getTime();

  it('formats recent session activity as compact Chinese relative time', () => {
    expect(formatRelativeTime(now - 17 * 60 * 60 * 1000, 'zh-CN', now)).toBe('17小时前');
    expect(formatRelativeTime(now - 12 * 60 * 1000, 'zh-CN', now)).toBe('12分钟前');
    expect(formatRelativeTime(now - 3 * 24 * 60 * 60 * 1000, 'zh-CN', now)).toBe('3天前');
  });

  it('uses the requested locale', () => {
    expect(formatRelativeTime(now - 17 * 60 * 60 * 1000, 'en-US', now)).toBe('17 hours ago');
  });

  it('uses a compact just-now label for activity within the last minute', () => {
    expect(formatRelativeTime(now - 20 * 1000, 'zh-CN', now)).toBe('刚刚');
    expect(formatRelativeTime(now - 20 * 1000, 'en-US', now)).toBe('just now');
  });
});
