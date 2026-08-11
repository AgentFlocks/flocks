import { describe, expect, it } from 'vitest';

import {
  severityKey,
  severityRows,
} from '../../../.flocks/flockshub/plugins/webuis/soc_ui/soc_dashboard/src/severityValues';

describe('SOC dashboard severity values', () => {
  it('builds severity rows only from threat severity values', () => {
    expect(severityRows({
      severityLevels: [
        { label: 'critical', value: 2 },
        { label: '3', value: 3 },
        { label: 'medium', value: 5 },
        { label: 'low', value: 7 },
        { label: 'unknown', value: 11 },
      ],
    }).map((item) => [item.key, item.value])).toEqual([
      ['critical', 2],
      ['high', 3],
      ['medium', 5],
      ['low', 7],
    ]);
  });

  it('does not derive severity from attack verdicts', () => {
    expect(severityRows({ severityLevels: [] }).every((item) => item.value === 0)).toBe(true);
    expect(severityKey('attack_success')).toBe('');
    expect(severityKey('benign')).toBe('');
  });
});
