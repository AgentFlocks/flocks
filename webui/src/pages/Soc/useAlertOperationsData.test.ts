import { describe, expect, it } from 'vitest';
import {
  getAlertNeedsReview,
  normalizeAlertOperationsData,
} from './useAlertOperationsData';

describe('normalizeAlertOperationsData', () => {
  it('normalizes user-defined page stats into the SOC alert contract', () => {
    const data = normalizeAlertOperationsData({
      date: '2026-06-24',
      generatedAt: '2026-06-24T10:00:00',
      sourceStatus: {
        sampleMode: true,
        sampleFile: '~/plugins/user_defined_pages/alert-denoise-triage-dashboard/assets/2026-06-24/dedup_result_001.jsonl',
        assets: {
          selectedDates: ['2026-06-24'],
        },
      },
      denoise: {
        totalRaw: 6427,
        totalUnique: 846,
        duplicates: 5581,
      },
      triage: {
        attackSuccess: 208,
        attack: 413,
        attackFailed: 225,
        benign: 0,
        unknown: 0,
      },
    });

    expect(data.schemaVersion).toBe('soc.alerts.v1');
    expect(data.source.label).toBe('自定义页 assets');
    expect(data.summary.totalRaw).toBe(6427);
    expect(data.summary.totalUnique).toBe(846);
    expect(data.summary.duplicates).toBe(5581);
    expect(getAlertNeedsReview(data.summary)).toBe(621);
    expect(data.incidents.length).toBeGreaterThan(0);
  });

  it('accepts the future standard SOC alert response directly', () => {
    const data = normalizeAlertOperationsData({
      schemaVersion: 'soc.alerts.v1',
      generatedAt: '2026-06-24T11:00:00',
      source: {
        pageId: 'soc-alerts',
        endpoint: '/alerts',
        label: 'SOC 标准接口',
      },
      summary: {
        totalRaw: '100',
        totalUnique: 60,
        duplicates: 40,
        attackSuccess: 3,
        attack: 7,
        attackFailed: 50,
        benign: 0,
        unknown: 0,
      },
      incidents: [],
    });

    expect(data.source.label).toBe('SOC 标准接口');
    expect(data.summary.totalRaw).toBe(100);
    expect(data.summary.sourcePageId).toBe('soc-alerts');
    expect(getAlertNeedsReview(data.summary)).toBe(10);
  });
});
