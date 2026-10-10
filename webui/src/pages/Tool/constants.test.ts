import { describe, expect, it } from 'vitest';

import { SOURCE_BADGE, SOURCE_SORT_ORDER, getSourceLabel } from './constants';

describe('tool source display', () => {
  it('renders device tools with a dedicated source label', () => {
    expect(SOURCE_BADGE.device).toMatchObject({
      label: 'Device',
      className: 'bg-amber-100 text-amber-800',
    });
    expect(getSourceLabel('device')).toBe('Device');
  });

  it('keeps translated labels for localized sources', () => {
    const t = (key: string) => ({
      'source.local': '本地',
      'source.builtin': '内置',
      'source.custom': '自定义',
    }[key] ?? key);

    expect(getSourceLabel('plugin_py', t)).toBe('本地');
    expect(getSourceLabel('builtin', t)).toBe('内置');
    expect(SOURCE_SORT_ORDER.device).toBeLessThan(SOURCE_SORT_ORDER.plugin_py);
  });
});
