import { describe, expect, it } from 'vitest';

import {
  filterOptionText,
  matchesFilterOptionSearch,
} from '../../../.flocks/flockshub/plugins/webuis/soc_ui/soc_alerts/src/filterValues';

const EN_TEXT: Record<string, string> = {
  '访问': 'Access',
  '高危': 'High',
};
const english = (text: string) => EN_TEXT[text] || text;

describe('SOC alert filter value localization', () => {
  it('localizes fixed values and preserves dynamic threat types', () => {
    expect(filterOptionText('threat_phase', 'access')).toBe('访问');
    expect(filterOptionText('threat_phase', 'access', english)).toBe('Access');
    expect(filterOptionText('threat_type', 'SQL injection')).toBe('SQL injection');
    expect(filterOptionText('threat_type', 'SQL injection', english)).toBe('SQL injection');
  });

  it('matches raw, Chinese, and English option text regardless of locale', () => {
    expect(matchesFilterOptionSearch('threat_level', 'high', 'high')).toBe(true);
    expect(matchesFilterOptionSearch('threat_level', 'high', '高危', english)).toBe(true);
    expect(matchesFilterOptionSearch('threat_level', 'high', 'High', english)).toBe(true);
    expect(matchesFilterOptionSearch('threat_type', 'SQL injection', 'SQL injection', english)).toBe(true);
    expect(matchesFilterOptionSearch('threat_type', 'SQL injection', 'SQL 注入', english)).toBe(false);
    expect(matchesFilterOptionSearch('threat_type', 'SQL injection', 'crawler', english)).toBe(false);
  });
});
