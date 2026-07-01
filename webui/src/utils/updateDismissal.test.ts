import { describe, expect, it } from 'vitest';
import { buildUpdateDismissalKey, isUpdateDismissed } from './updateDismissal';
import type { VersionInfo } from '@/api/update';

describe('update dismissal keys', () => {
  it('includes Pro bundle, core, and component versions', () => {
    const info: VersionInfo = {
      edition: 'flockspro',
      current_version: 'v2026.6.18',
      latest_version: 'v2026.6.18',
      current_bundle_version: 'v2026.6.18',
      latest_bundle_version: 'v2026.6.18',
      current_core_version: 'v2026.6.18',
      latest_core_version: 'v2026.6.18',
      current_pro_component_version: 'v2026.6.1',
      latest_pro_component_version: 'v2026.6.2',
      has_update: true,
      release_notes: null,
      release_url: null,
      error: null,
    };

    expect(buildUpdateDismissalKey(info)).toBe('flockspro:v2026.6.18:v2026.6.18:v2026.6.2');
    expect(isUpdateDismissed(info, 'v2026.6.18')).toBe(false);
  });

  it('keeps legacy OSS current_version dismissals valid', () => {
    const info: VersionInfo = {
      edition: 'flocks',
      current_version: '2026.6.18',
      latest_version: '2026.6.19',
      has_update: true,
      release_notes: null,
      release_url: null,
      error: null,
    };

    expect(buildUpdateDismissalKey(info)).toBe('flocks:2026.6.19');
    expect(isUpdateDismissed(info, '2026.6.18')).toBe(true);
  });
});
