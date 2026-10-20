import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import AccountMenu, { accountMenuPosition } from './AccountMenu';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => (options?.name ? `${key}:${options.name}` : key),
  }),
}));

function CurrentPath() {
  return <div data-testid="path">{useLocation().pathname}</div>;
}

function renderMenu({ collapsed = false, onNavigate = vi.fn() } = {}) {
  render(
    <MemoryRouter initialEntries={['/']}>
      {/* The real sidebar clips its overflow; the menu must not depend on it. */}
      <aside data-testid="sidebar" className="overflow-hidden" style={{ width: collapsed ? 64 : 256 }}>
        <AccountMenu
          collapsed={collapsed}
          username="admin"
          roleLabel="管理员"
          initial="A"
          version="v2026.9.23"
          isAdmin
          isPro
          proProductName="Flocks Pro"
          hubName="插件广场"
          onNavigate={onNavigate}
          onCheckUpdate={vi.fn()}
          onLogout={vi.fn()}
        />
      </aside>
      <Routes>
        <Route path="*" element={<CurrentPath />} />
      </Routes>
    </MemoryRouter>,
  );
  return { onNavigate };
}

describe('accountMenuPosition', () => {
  const viewport = { width: 1440, height: 900 };

  it('opens above the expanded account block, inset like its padding', () => {
    expect(accountMenuPosition({ top: 820, bottom: 900, left: 0, right: 256 }, false, viewport)).toEqual({
      left: 12,
      bottom: 900 - 812,
      maxHeight: 812 - 64,
    });
  });

  it('opens beside the collapsed rail, bottom-aligned with the avatar', () => {
    expect(accountMenuPosition({ top: 844, bottom: 900, left: 0, right: 64 }, true, viewport)).toEqual({
      left: 72,
      bottom: 900 - 892,
      maxHeight: 892 - 64,
    });
  });

  it('keeps the whole menu inside a narrow viewport', () => {
    const position = accountMenuPosition({ top: 600, bottom: 660, left: 120, right: 376 }, false, { width: 300, height: 660 });
    expect(position.left).toBe(300 - 256 - 8);
  });
});

describe('AccountMenu', () => {
  it.each([false, true])('renders outside the clipping sidebar (collapsed=%s)', (collapsed) => {
    renderMenu({ collapsed });
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal:admin' }));

    const menu = screen.getByRole('menu', { name: 'accountMenu' });
    expect(screen.getByTestId('sidebar')).not.toContainElement(menu);
    expect(menu.parentElement).toBe(document.body);
    expect(menu).toHaveClass('fixed');
    expect(menu.style.left).not.toBe('');
    expect(within(menu).getByRole('menuitem', { name: 'tools' })).toHaveAttribute('href', '/tools');
  });

  it('follows a menu link instead of closing on the pointerdown that starts the click', () => {
    const { onNavigate } = renderMenu({ collapsed: true });
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal:admin' }));
    const item = within(screen.getByRole('menu', { name: 'accountMenu' })).getByRole('menuitem', { name: 'skills' });

    fireEvent.pointerDown(item);
    expect(screen.getByRole('menu', { name: 'accountMenu' })).toBeInTheDocument();
    fireEvent.click(item);

    expect(screen.getByTestId('path')).toHaveTextContent('/skills');
    expect(onNavigate).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menu', { name: 'accountMenu' })).not.toBeInTheDocument();
  });

  it('links system management straight to its page without a submenu arrow', () => {
    const { onNavigate } = renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'accountAndGlobal:admin' }));
    const item = within(screen.getByRole('menu', { name: 'accountMenu' })).getByRole('menuitem', { name: 'systemManagement' });

    // A trailing chevron promises a flyout on hover; this entry is a plain link.
    expect(item).not.toHaveAttribute('aria-haspopup');
    expect(item.querySelector('.lucide-chevron-right')).toBeNull();

    fireEvent.click(item);
    expect(screen.getByTestId('path')).toHaveTextContent('/settings/preferences');
    expect(onNavigate).toHaveBeenCalledTimes(1);
  });

  it('closes on an outside pointerdown, on Escape and on a second avatar click', () => {
    renderMenu({ collapsed: true });
    const avatar = screen.getByRole('button', { name: 'accountAndGlobal:admin' });

    fireEvent.click(avatar);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();

    fireEvent.click(avatar);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(avatar).toHaveFocus();

    fireEvent.click(avatar);
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.click(avatar);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});
