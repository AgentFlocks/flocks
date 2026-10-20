import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import AccountMenu, { FLOCKS_LLM_USAGE_URL } from './AccountMenu';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

function CurrentPath() {
  return <div data-testid="path">{useLocation().pathname}</div>;
}

const versionProps = {
  version: 'v2026.10.13', isAdmin: true, isPro: true, proProductName: 'Flocks Pro', onCheckUpdate: vi.fn(),
};

describe('AccountMenu', () => {
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
    const items = within(menu).getAllByRole('menuitem');
    expect(items).toHaveLength(5);
    expect(items[0]).toHaveTextContent('systemConfiguration');
    expect(within(menu).getByRole('menuitem', { name: /Flocks Pro/ })).toHaveAttribute('href', '/settings/flockspro');
    expect(within(menu).getByRole('menuitem', { name: 'checkUpdate' })).toBeInTheDocument();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'logout' }));
    expect(onLogout).toHaveBeenCalledOnce();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});
