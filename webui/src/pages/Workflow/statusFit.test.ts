import { describe, expect, it } from 'vitest';
import { countFittingStatuses } from './statusFit';

const base = { leadWidth: 50, gap: 12, overflowWidth: 30 };

describe('countFittingStatuses', () => {
  it('keeps every candidate when the row could not be measured', () => {
    expect(countFittingStatuses({ ...base, available: 0, itemWidths: [500, 500] }, 4)).toBe(2);
  });

  it('shows all candidates when they fit next to the "+N" button', () => {
    // 50 + (12 + 80) + (12 + 80) + 30 = 264
    expect(countFittingStatuses({ ...base, available: 264, itemWidths: [80, 80] }, 4)).toBe(2);
  });

  it('moves a candidate that would overflow into the "+N" list', () => {
    // The 375px case: the second label would run past the line.
    expect(countFittingStatuses({ ...base, available: 263, itemWidths: [80, 80] }, 4)).toBe(1);
  });

  it('can fold every trigger behind "+N"', () => {
    expect(countFittingStatuses({ ...base, available: 100, itemWidths: [120, 80] }, 2)).toBe(0);
  });

  it('needs no room for "+N" when the last candidate is the last trigger', () => {
    // 50 + (12 + 80) + (12 + 80) = 234: fits only because nothing is left over.
    expect(countFittingStatuses({ ...base, available: 234, itemWidths: [80, 80] }, 2)).toBe(2);
    expect(countFittingStatuses({ ...base, available: 234, itemWidths: [80, 80] }, 3)).toBe(1);
  });

  it('tolerates sub-pixel rounding at the edge', () => {
    expect(countFittingStatuses({ ...base, available: 263.6, itemWidths: [80, 80] }, 4)).toBe(2);
  });
});
