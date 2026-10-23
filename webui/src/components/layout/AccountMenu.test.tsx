import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { Crown } from 'lucide-react';
import AccountMenu, { FLOCKS_LLM_USAGE_URL } from './AccountMenu';
import { __resetExtensionSlotCacheForTesting } from '@/components/extensions/ExtensionSlot';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

// Extensions reach the menu through the `layout.version` slot; tests register
// slot components here and clear them again.
const extensionRegistry = vi.hoisted(() => ({
  routes: [],
  navItems: [],
  settingsSections: [],
  slots: [] as Array<{ id: string; slot: string; component: (props: { context: Record<string, unknown> }) => JSX.Element }>,
  i18n: {},
}));
vi.mock('@ext/registry', () => ({ __esModule: true, default: extensionRegistry }));

function CurrentPath() {
  return <div data-testid="path">{useLocation().pathname}</div>;
}

const versionProps = {
  version: 'v2026.10.13', onCheckUpdate: vi.fn(),
};

describe('AccountMenu', () => {
  beforeEach(() => {
    extensionRegistry.slots = [];
    __resetExtensionSlotCacheForTesting();
  });

  it.each([false, true])('keeps the footer menu outside sidebar clipping (collapsed=%s)', (collapsed) => {
    const onNavigate = vi.fn();
    const { container } = render(
      <MemoryRouter>
        <aside className="overflow-hidden">
          <AccountMenu {...versionProps} collapsed={collapsed} username="admin" roleLabel="管理员" initial="A" onLogout={vi.fn()} onNavigate={onNavigate} />
        </aside>
      </MemoryRouter>,
    );
    const trigger = screen.getByRole('button', { name: 'accountAndGlobal' });
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    const menu = screen.getByRole('menu');
    expect(container.querySelector('aside')).not.toContainElement(menu);
    expect(menu.parentElement).toBe(document.body);
    expect(menu).toHaveClass('fixed');
    expect(menu.style.bottom).not.toBe('');
    const settings = within(menu).getByRole('menuitem', { name: 'systemConfiguration' });
    expect(settings).toHaveFocus();
    fireEvent.pointerDown(settings);
    expect(menu).toBeInTheDocument();
    fireEvent.click(settings);
    expect(onNavigate).toHaveBeenCalledOnce();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('links system configuration straight to its page without a submenu arrow', () => {
    const onNavigate = vi.fn();
    render(
      <MemoryRouter initialEntries={['/']}>
        <AccountMenu {...versionProps} username="admin" roleLabel="管理员" initial="A" onLogout={vi.fn()} onNavigate={onNavigate} />
        <Routes><Route path="*" element={<CurrentPath />} /></Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal' }));
    const item = within(screen.getByRole('menu')).getByRole('menuitem', { name: 'systemConfiguration' });

    // A trailing chevron promises a flyout on hover; this entry is a plain link.
    expect(item).not.toHaveAttribute('aria-haspopup');
    expect(item.querySelector('.lucide-chevron-right')).toBeNull();

    fireEvent.click(item);
    expect(screen.getByTestId('path')).toHaveTextContent('/settings/preferences');
    expect(onNavigate).toHaveBeenCalledTimes(1);
  });

  it('shows account details and exposes settings, usage and logout actions', () => {
    const onLogout = vi.fn();
    render(<MemoryRouter><AccountMenu {...versionProps} username="admin" roleLabel="管理员" initial="A" onLogout={onLogout} /></MemoryRouter>);
    expect(screen.getByText('admin')).toBeInTheDocument();
    expect(screen.getByText('管理员')).toBeInTheDocument();
    expect(within(screen.getByRole('button', { name: 'accountAndGlobal' })).getByText('v2026.10.13')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal' }));
    const menu = screen.getByRole('menu');
    expect(within(menu).getByRole('menuitem', { name: 'systemConfiguration' })).toHaveAttribute('href', '/settings/preferences');
    expect(within(menu).queryByRole('menuitem', { name: 'accountManagement' })).not.toBeInTheDocument();
    expect(within(menu).getByRole('menuitem', { name: /modelUsage/ })).toHaveAttribute('href', FLOCKS_LLM_USAGE_URL);
    // A stock build has no edition entry: settings, usage, check update, logout.
    const items = within(menu).getAllByRole('menuitem');
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveTextContent('systemConfiguration');
    expect(within(menu).getByRole('menuitem', { name: 'checkUpdate' })).toBeInTheDocument();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'logout' }));
    expect(onLogout).toHaveBeenCalledOnce();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('adds the entries an extension registered, a highlighted one framed with its tag', () => {
    const onNavigate = vi.fn();
    render(
      <MemoryRouter initialEntries={['/']}>
        <AccountMenu
          {...versionProps}
          username="admin"
          roleLabel="管理员"
          initial="A"
          extensionItems={[{ id: 'edition', name: 'Upgrade', href: '/settings/edition', icon: Crown, highlight: true, badge: 'PLUS' }]}
          onLogout={vi.fn()}
          onNavigate={onNavigate}
        />
        <Routes><Route path="*" element={<CurrentPath />} /></Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal' }));
    const menu = screen.getByRole('menu');
    const items = within(menu).getAllByRole('menuitem');
    expect(items.map((item) => item.textContent)).toEqual([
      'systemConfiguration', 'modelUsagemodelUsageHint', 'UpgradePLUS', 'checkUpdate', 'logout',
    ]);
    const upgrade = within(menu).getByRole('menuitem', { name: /Upgrade/ });
    expect(upgrade).toHaveAttribute('href', '/settings/edition');
    expect(upgrade).toHaveClass('border');
    expect(upgrade.querySelector('.lucide-crown')).toHaveClass('text-amber-500');
    fireEvent.click(upgrade);
    expect(screen.getByTestId('path')).toHaveTextContent('/settings/edition');
    expect(onNavigate).toHaveBeenCalledOnce();
  });

  it('leaves out the default entries a private build hides', () => {
    render(
      <MemoryRouter>
        <AccountMenu {...versionProps} username="admin" roleLabel="管理员" initial="A" isHidden={(id) => id === 'modelUsage'} onLogout={vi.fn()} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal' }));
    const menu = screen.getByRole('menu');
    expect(within(menu).getByRole('menuitem', { name: 'systemConfiguration' })).toBeInTheDocument();
    expect(within(menu).queryByRole('menuitem', { name: /modelUsage/ })).not.toBeInTheDocument();
  });

  it('lets a layout.version extension replace the version badge', () => {
    const seen: Array<Record<string, unknown>> = [];
    extensionRegistry.slots = [{
      id: 'test-version',
      slot: 'layout.version',
      component: ({ context }) => {
        seen.push(context);
        return <span>{`edition ${String(context.version)}`}</span>;
      },
    }];
    __resetExtensionSlotCacheForTesting();
    render(
      <MemoryRouter>
        <AccountMenu
          {...versionProps}
          username="admin"
          roleLabel="管理员"
          initial="A"
          versionContext={{ version: 'v2026.10.13', hasUpdate: false }}
          onLogout={vi.fn()}
        />
      </MemoryRouter>,
    );
    const trigger = screen.getByRole('button', { name: 'accountAndGlobal' });
    expect(within(trigger).getByText('edition v2026.10.13')).toBeInTheDocument();
    expect(within(trigger).queryByText('v2026.10.13')).not.toBeInTheDocument();
    expect(seen[0]).toEqual({ version: 'v2026.10.13', hasUpdate: false });
  });
});
