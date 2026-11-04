import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Shield } from 'lucide-react';

const gateCheck = vi.fn<() => Promise<boolean>>();

vi.mock('@ext/registry', () => ({
  default: {
    routes: [],
    navItems: [],
    settingsSections: [
      {
        id: 'ext-gated',
        group: 'system',
        labelKey: 'extGated',
        icon: Shield,
        adminOnly: true,
        load: () => Promise.resolve({ default: () => null }),
        gate: { check: () => gateCheck(), refreshOn: ['ext-gate-changed'] },
      },
      {
        // Like the private build's security config: between account and system logs.
        id: 'ext-ordered',
        group: 'system',
        labelKey: 'extOrdered',
        icon: Shield,
        order: 10,
        load: () => Promise.resolve({ default: () => null }),
      },
    ],
    slots: [],
    updateChannel: undefined,
  },
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { exists: () => true, language: 'zh-CN' } }),
}));
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', username: 'admin', role: 'admin', status: 'active' } }),
}));
vi.mock('@/contexts/ProductNameContext', () => ({
  useProductName: () => ({ productName: 'Flocks', configuredDisplayName: null }),
}));

import { resetSettingsGatesForTests, useSettingsSectionGroups } from './settingsSections';

function sectionIds(groups: ReturnType<typeof useSettingsSectionGroups>['groups']): string[] {
  return groups.flatMap((group) => group.items.map((item) => item.id));
}

describe('useSettingsSectionGroups gates', () => {
  beforeEach(() => {
    resetSettingsGatesForTests();
    gateCheck.mockReset();
  });

  it('shows a gated section until its check answers, then hides it on false', async () => {
    let answer: (ok: boolean) => void = () => undefined;
    gateCheck.mockImplementation(() => new Promise<boolean>((resolve) => { answer = resolve; }));

    const { result } = renderHook(() => useSettingsSectionGroups());
    // not answered yet: shown, not ready
    expect(sectionIds(result.current.groups)).toContain('ext-gated');
    expect(result.current.ready).toBe(false);

    await act(async () => { answer(false); });
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(sectionIds(result.current.groups)).not.toContain('ext-gated');
    // dev's order first (account, system logs); the sections dev does not have go last,
    // and the data group follows the system group as in dev.
    expect(sectionIds(result.current.groups)).toEqual(['preferences', 'account', 'ext-ordered', 'system-logs', 'archived-data']);
  });

  it('places a registered section by its order among the built-in ones', async () => {
    gateCheck.mockResolvedValue(true);
    const { result } = renderHook(() => useSettingsSectionGroups());
    await waitFor(() => expect(result.current.ready).toBe(true));
    const ids = sectionIds(result.current.groups);
    expect(ids.indexOf('ext-ordered')).toBe(ids.indexOf('account') + 1);
    expect(ids.indexOf('ext-ordered')).toBeLessThan(ids.indexOf('system-logs'));
    // Without an order a registered section comes after every built-in one of its group.
    const system = result.current.groups.find((group) => group.id === 'system')!.items.map((item) => item.id);
    expect(system[system.length - 1]).toBe('ext-gated');
    expect(ids[ids.length - 1]).toBe('archived-data');
  });

  it('runs the check once for several mounted instances and re-runs on refreshOn', async () => {
    gateCheck.mockResolvedValue(true);
    const first = renderHook(() => useSettingsSectionGroups());
    const second = renderHook(() => useSettingsSectionGroups());
    await waitFor(() => expect(first.result.current.ready).toBe(true));
    await waitFor(() => expect(second.result.current.ready).toBe(true));
    expect(gateCheck).toHaveBeenCalledTimes(1);
    expect(sectionIds(second.result.current.groups)).toContain('ext-gated');

    gateCheck.mockResolvedValue(false);
    await act(async () => { window.dispatchEvent(new Event('ext-gate-changed')); });
    await waitFor(() => expect(sectionIds(first.result.current.groups)).not.toContain('ext-gated'));
    expect(sectionIds(second.result.current.groups)).not.toContain('ext-gated');
    expect(gateCheck).toHaveBeenCalledTimes(2);
  });

  it('does not drop a refresh that arrives while a check is in flight', async () => {
    let answerFirst: (ok: boolean) => void = () => undefined;
    gateCheck
      .mockImplementationOnce(() => new Promise<boolean>((resolve) => { answerFirst = resolve; }))
      .mockResolvedValue(false);

    const { result } = renderHook(() => useSettingsSectionGroups());
    await act(async () => { window.dispatchEvent(new Event('ext-gate-changed')); });
    await act(async () => { answerFirst(true); });
    // the first answer was about the state before the event; a second round runs
    await waitFor(() => expect(gateCheck).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(sectionIds(result.current.groups)).not.toContain('ext-gated'));
  });
});
