import { describe, expect, it, vi } from 'vitest';

import { withReleaseUpdate } from './releaseUpdate';

const managed = {
  current_version: '2026.11.04.32', latest_version: '2026.11.04.30', has_update: false, release_notes: null,
  release_url: null, error: null, deploy_mode: 'managed' as const, update_allowed: false,
};

describe('withReleaseUpdate', () => {
  const source = (status: unknown) => ({ status: vi.fn().mockResolvedValue(status), start: vi.fn(), outcome: vi.fn() });

  it('makes a release install one that can be updated, to what the source offers', async () => {
    const info = await withReleaseUpdate(managed, source({ release_install: true, latest_version: '2026.11.04.33', has_update: true, error: null }));
    expect(info).toMatchObject({ latest_version: '2026.11.04.33', has_update: true, update_allowed: true, error: null });
  });

  it('drops notes of another version, and keeps those of this one', async () => {
    const noted = { ...managed, release_notes: 'of .30', release_url: 'https://x/.30' };
    expect(await withReleaseUpdate(noted, source({ release_install: true, latest_version: '2026.11.04.33', has_update: true })))
      .toMatchObject({ release_notes: null, release_url: null });
    expect(await withReleaseUpdate(noted, source({ release_install: true, latest_version: '2026.11.04.30', has_update: true })))
      .toMatchObject({ release_notes: 'of .30', release_url: 'https://x/.30' });
  });

  it('says what the source could not', async () => {
    const info = await withReleaseUpdate(managed, source({ release_install: true, has_update: false, latest_version: null, error: 'Console 没有' }));
    expect(info).toMatchObject({ latest_version: '2026.11.04.30', has_update: false, update_allowed: true, error: 'Console 没有' });
  });

  it('leaves alone what it has no say in', async () => {
    expect(await withReleaseUpdate(managed)).toBe(managed);
    expect(await withReleaseUpdate(managed, source({ release_install: false }))).toBe(managed);
    const open = { ...managed, deploy_mode: 'source' as const };
    const asked = source({ release_install: true, has_update: true });
    expect(await withReleaseUpdate(open, asked)).toBe(open);
    expect(asked.status).not.toHaveBeenCalled();
    const failing = { status: vi.fn().mockRejectedValue(new Error('404')), start: vi.fn(), outcome: vi.fn() };
    expect(await withReleaseUpdate(managed, failing)).toBe(managed);
  });
});
