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
    ],
    slots: [],
    updateChannel: undefined,
    hideDefault: undefined,
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
    expect(sectionIds(result.current.groups)).toEqual(['preferences', 'archived-data', 'account', 'network-proxy', 'voice-input', 'system-logs']);
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
