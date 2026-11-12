import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createInstance } from 'i18next';
import enGroups from '@/locales/en-US/pluginGroups.json';
const i18n = createInstance();
const t = i18n.t.bind(i18n);
import { assertGroupItemsEditable, deriveGroupNav, GroupActionCancelled, groupLabel, GroupSaveError, matchesGroup, orderGroupNames, saveGroupItems } from './groupView';

beforeEach(async () => {
  vi.restoreAllMocks();
  // Same as the application (src/i18n.ts): React escapes, i18next must not.
  await i18n.init({ lng: 'en-US', resources: { 'en-US': { pluginGroups: enGroups } }, interpolation: { escapeValue: false } });
});

describe('native group view helpers', () => {
  it('only translates exact known defaults, not custom names or translation-key-like names', () => {
    for (const [nativeName, label] of Object.entries(enGroups.defaultNames)) {
      expect(groupLabel(nativeName, t)).toBe(label);
    }
    for (const name of ['客户自定义', 'NDR', 'defaultNames', 'toString', '__proto__', 'custom.group', 'custom:group']) {
      expect(groupLabel(name, t)).toBe(name);
    }
  });

  it('localizes the confirmation destination without changing the saved group', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const save = vi.fn().mockResolvedValue(undefined);
    await saveGroupItems([{ key: 'custom', name: 'Custom' }], '平台集成', save, vi.fn(), t, true);
    expect(confirm.mock.lastCall?.[0]).toContain('Platform Integrations');
    expect(confirm.mock.lastCall?.[0]).not.toContain('平台集成');
    expect(save).toHaveBeenCalledExactlyOnceWith('custom', '平台集成');
  });

  it('derives trimmed nonempty names and keeps All distinct from Ungrouped and literal all', () => {
    const result = deriveGroupNav([
      { key: 'a', name: 'A', group: ' Operations ' },
      { key: 'b', name: 'B', group: 'Operations', readOnlyReason: 'read-only' },
      { key: 'c', name: 'C', group: ' ' },
      { key: 'd', name: 'D', group: null },
      { key: 'e', name: 'E', group: 'all' },
    ]);
    expect(result).toEqual({ total: 5, ungroupedCount: 2, groups: [
      { name: 'Operations', count: 2, readOnlyReason: 'read-only' }, { name: 'all', count: 1 },
    ] });
    expect(matchesGroup('all', null)).toBe(true);
    expect(matchesGroup('all', '')).toBe(false);
    expect(matchesGroup(undefined, '')).toBe(true);
    expect(matchesGroup(' Operations ', 'Operations')).toBe(true);
    expect(orderGroupNames(['Operations', 'all'], ['missing', 'all', 'all'])).toEqual(['all', 'Operations']);
    expect(orderGroupNames([], ['Operations'])).toEqual([]);
  });

  it('prechecks every member and blocks the whole operation before any write', async () => {
    const save = vi.fn();
    const reload = vi.fn();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await expect(saveGroupItems([
      { key: 'custom', name: 'Custom' },
      { key: 'builtin', name: 'Built-in', readOnlyReason: 'definition is read-only even for admin' },
    ], 'New', save, reload, t, true)).rejects.toThrow('Built-in');
    expect(save).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
  });

  it('confirms the exact scope and reports failed items while reloading real partial results', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const calls: string[] = [];
    const save = vi.fn(async (key: string) => {
      calls.push(key);
      if (key === 'b') throw new Error('disk full');
    });
    const reload = vi.fn(async () => { calls.push('reload'); });
    const result = saveGroupItems([
      { key: 'a', name: 'Alpha' }, { key: 'b', name: 'Beta' }, { key: 'c', name: 'Gamma' },
    ], null, save, reload, t, true);
    // Counts first, then one line per failure; the succeeded ones are only counted.
    await expect(result).rejects.toThrow('Saved 2; failed 1.\n\nBeta (b): disk full');
    await expect(result).rejects.not.toThrow('Alpha (a)');
    expect(confirm.mock.lastCall?.[0]).toContain('3 plugins');
    expect(confirm.mock.lastCall?.[0]).not.toContain('atomic');
    expect(confirm.mock.lastCall?.[0]).toContain('Alpha (a), Beta (b), Gamma (c)');
    expect(calls).toEqual(['a', 'b', 'c', 'reload']);
    expect(save.mock.calls.map((call) => call[0])).toEqual(['a', 'b', 'c']);
    expect(reload).toHaveBeenCalledOnce();
  });

  it('reports refresh failure and never claims a rollback or cancels successful writes', async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const reload = vi.fn().mockRejectedValue(new Error('offline'));
    await expect(saveGroupItems([{ key: 'a', name: 'Alpha' }], 'Ops', save, reload, t)).rejects.toThrow('Saved 1; failed 0');
    expect(save).toHaveBeenCalledExactlyOnceWith('a', 'Ops');
  });

  it('rewords a file that changed after it was checked and keeps the server text as details', async () => {
    const raw = 'Failed to update workflow: /home/u/.flocks/plugins/workflows/hq changed after it was checked; check it again and retry.';
    const save = vi.fn().mockRejectedValue(new Error(raw));
    const error = await saveGroupItems([{ key: 'hq', name: 'SOC' }], 'Ops', save, vi.fn(), t, false, 'workflow').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(GroupSaveError);
    expect((error as GroupSaveError).message).toBe('Saved 0; failed 1.\n\nSOC (hq): It changed after it was checked. Refresh and try again.');
    expect((error as GroupSaveError).message).not.toContain('/home/u');
    expect((error as GroupSaveError).details).toBe(`SOC (hq): ${raw}`);
  });

  it('names what the page groups instead of plugins', async () => {
    expect(() => assertGroupItemsEditable([{ key: 'rex', name: 'Rex', readOnlyReason: 'built-in' }], t, 'agent'))
      .toThrow('Groups cannot be changed for these agents: Rex (rex)');
    await expect(saveGroupItems([], 'Ops', vi.fn(), vi.fn(), t, false, 'device')).rejects.toThrow('This device or group is unavailable.');
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await expect(saveGroupItems([{ key: 'a', name: 'A' }], null, vi.fn(), vi.fn(), t, true, 'skill')).rejects.toBeInstanceOf(GroupActionCancelled);
    expect(confirm.mock.lastCall?.[0]).toContain('Move these 1 skills');
  });

  it('does not write or report saved when the actual-scope confirmation is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const save = vi.fn();
    await expect(saveGroupItems([{ key: 'a', name: 'A' }], null, save, vi.fn(), t, true)).rejects.toBeInstanceOf(GroupActionCancelled);
    expect(save).not.toHaveBeenCalled();
  });
});
