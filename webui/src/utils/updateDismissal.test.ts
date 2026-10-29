import { describe, expect, it } from 'vitest';
import { buildUpdateDismissalKey, isUpdateDismissed } from './updateDismissal';
import type { VersionInfo } from '@/api/update';

function versionInfo(overrides: Partial<VersionInfo> = {}): VersionInfo {
  return {
    current_version: '2026.6.18',
    latest_version: '2026.6.19',
    has_update: true,
    release_notes: null,
    release_url: null,
    error: null,
    ...overrides,
  };
}

describe('update dismissal keys', () => {
  it('uses the default channel when the server reports none', () => {
    expect(buildUpdateDismissalKey(versionInfo())).toBe('default:2026.6.19');
  });

  it('keeps dismissals of different channels apart', () => {
    const info = versionInfo({ channel: 'nightly', latest_version: 'v2026.6.20' });
    expect(buildUpdateDismissalKey(info)).toBe('nightly:v2026.6.20');
    expect(isUpdateDismissed(info, 'nightly:v2026.6.20')).toBe(true);
  });

  it('returns null without any version', () => {
    expect(buildUpdateDismissalKey(null)).toBeNull();
    expect(buildUpdateDismissalKey(versionInfo({ current_version: '', latest_version: null }))).toBeNull();
  });

  it('keeps legacy current_version dismissals valid', () => {
    expect(isUpdateDismissed(versionInfo(), '2026.6.18')).toBe(true);
  });

  it('keeps dismissals stored under an older key prefix valid for the same version', () => {
    expect(isUpdateDismissed(versionInfo(), 'flocks:2026.6.19')).toBe(true);
    expect(isUpdateDismissed(versionInfo(), 'flocks:2026.6.17')).toBe(false);
  });

  it('does not treat other versions as dismissed', () => {
    expect(isUpdateDismissed(versionInfo(), 'default:2026.6.20')).toBe(false);
    expect(isUpdateDismissed(versionInfo(), null)).toBe(false);
  });
});
