import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Shield, ShieldCheck } from 'lucide-react';
import SettingsPage from './index';

// A private-build registry: one ordinary settings section, and one (the upgrade
// page) that also has its own account-menu entry.
const { registryMock } = vi.hoisted(() => ({
  registryMock: {
    routes: [],
    navItems: [
      { id: 'upgrade', href: '/settings/upgrade', labelKey: 'upgrade', icon: () => null, placement: 'accountMenu' },
    ],
    settingsSections: [] as unknown[],
    slots: [],
    i18n: {},
  },
}));

vi.mock('@ext/registry', () => ({ default: registryMock }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'zh-CN', changeLanguage: vi.fn(), exists: () => true } }),
}));
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'admin', username: 'admin', role: 'admin', status: 'active', must_reset_password: false } }),
}));
vi.mock('@/pages/SystemLog', () => ({ default: () => <div>system logs page</div> }));

registryMock.settingsSections = [
  { id: 'security', group: 'system', labelKey: 'securityConfig', icon: Shield, adminOnly: true, order: 10, load: async () => ({ default: () => <div>security page</div> }) },
  { id: 'upgrade', group: 'system', labelKey: 'upgradeTab', icon: ShieldCheck, adminOnly: true, order: 40, load: async () => ({ default: () => <div>upgrade page</div> }) },
];

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/settings/:sectionId" element={<SettingsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('SettingsPage extension sections', () => {
  it('shows extension sections as tabs except one reached from the account menu', async () => {
    renderAt('/settings/system-logs');
    expect(await screen.findByText('system logs page')).toBeInTheDocument();
    const tabs = within(screen.getByRole('navigation', { name: 'settingsTitle' })).getAllByRole('tab').map((tab) => tab.textContent);
    expect(tabs).toContain('securityConfig');
    expect(tabs).not.toContain('upgradeTab');
  });

  it('still opens the section that has no tab', async () => {
    renderAt('/settings/upgrade');
    expect(await screen.findByText('upgrade page')).toBeInTheDocument();
    expect(within(screen.getByRole('navigation', { name: 'settingsTitle' })).queryByRole('tab', { selected: true })).toBeNull();
  });
});
