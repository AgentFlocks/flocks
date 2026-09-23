import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { Bell, Plus, Settings, ShieldCheck, Sparkles, User } from 'lucide-react';
import PartitionTopBar from './PartitionTopBar';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

function renderBar() {
  const onSelect = vi.fn();
  render(<MemoryRouter>
    <PartitionTopBar
      items={[
        { id: 'agent', name: 'Agent', icon: Sparkles },
        { id: 'workspace:soc_ui', name: 'SOC 工作区', icon: ShieldCheck },
      ]}
      activeId="agent"
      onSelect={onSelect}
      action={{ href: '/scenes/suites', name: '添加场景', icon: Plus, badge: true }}
      settingsGroups={[
        { name: '偏好设置', items: [{ name: '外观', href: '/settings/preferences', icon: Settings }] },
        { name: '系统管理', items: [
          { name: '用户管理', href: '/settings/account', icon: User },
          { name: '通知', href: '/settings/notifications', icon: Bell },
        ] },
      ]}
    />
    <button type="button">outside</button>
    <LocationProbe />
  </MemoryRouter>);
  return { onSelect };
}

describe('PartitionTopBar', () => {
  it('uses installed scene tabs and keeps add-scene and settings outside the tab list', async () => {
    const { onSelect } = renderBar();
    expect(within(screen.getByRole('tablist')).getAllByRole('tab').map((tab) => tab.textContent))
      .toEqual(['Agent', 'SOC 工作区']);
    expect(screen.getByRole('link', { name: '添加场景' })).toHaveAttribute('href', '/scenes/suites');
    expect(within(screen.getByRole('tablist')).queryByRole('link', { name: '添加场景' })).not.toBeInTheDocument();
    expect(screen.getByText('NEW')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'partitionSettings' })).toHaveAttribute('aria-expanded', 'false');
    await userEvent.setup().click(screen.getByRole('tab', { name: 'SOC 工作区' }));
    expect(onSelect).toHaveBeenCalledWith('workspace:soc_ui');
  });

  it('keeps the settings menu open after the pointer hover preceding a real click', async () => {
    const user = userEvent.setup();
    renderBar();
    const settings = screen.getByRole('button', { name: 'partitionSettings' });
    await user.hover(settings);
    expect(screen.getByRole('menu')).toBeInTheDocument();
    await user.click(settings);
    expect(screen.getByRole('menu')).toBeInTheDocument();
    await user.click(screen.getByRole('menuitem', { name: '用户管理' }));
    expect(screen.getByTestId('location')).toHaveTextContent('/settings/account');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('opens the menu on a direct user click and closes it on an outside click', async () => {
    const user = userEvent.setup();
    renderBar();
    await user.click(screen.getByRole('button', { name: 'partitionSettings' }));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'partitionSettings' }));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'outside' }));
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('stacks the bar above page toolbars and portals the settings menu out of it', async () => {
    // Contract pages put sticky toolbars at z-30 (SOC 概览的时间范围栏) and custom
    // pages pick any z-index, so the menu cannot live inside the bar's stacking
    // context: it hangs from <body> above page content (and the z-50 aside) while
    // staying under the app's own dialogs (ConfirmDialog is z-[60]).
    const user = userEvent.setup();
    renderBar();
    const bar = document.querySelector('[data-partition-top-bar]') as HTMLElement;
    const level = (el: Element | null) => Number((el?.className.toString().match(/\bz-\[(\d+)\]/) || [])[1]);
    expect(level(bar)).toBeGreaterThan(30);
    expect(level(bar)).toBeLessThan(40);
    await user.click(screen.getByRole('button', { name: 'partitionSettings' }));
    const menu = screen.getByRole('menu');
    const layer = menu.closest('[data-topbar-settings-layer]');
    expect(bar.contains(menu)).toBe(false);
    expect(layer?.parentElement).toBe(document.body);
    expect(layer).toHaveClass('fixed');
    expect(level(layer)).toBeGreaterThan(50);
    expect(level(layer)).toBeLessThan(60);
  });

  it('keeps the portalled menu open while the pointer moves from the gear into it', async () => {
    const user = userEvent.setup();
    renderBar();
    await user.hover(screen.getByRole('button', { name: 'partitionSettings' }));
    await user.hover(screen.getByRole('menuitem', { name: '通知' }));
    // Longer than the close delay: leaving the gear for the menu must not close it.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    await user.click(screen.getByRole('menuitem', { name: '通知' }));
    expect(screen.getByTestId('location')).toHaveTextContent('/settings/notifications');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('keeps the portalled menu open while the pointer goes back and forth between it and the gear', async () => {
    // user-event hovers without relatedTarget in jsdom; React pairs enter/leave
    // through relatedTarget, so the moves are dispatched explicitly here.
    const user = userEvent.setup();
    renderBar();
    const gear = screen.getByRole('button', { name: 'partitionSettings' });
    const outside = screen.getByRole('button', { name: 'outside' });
    await user.hover(gear);
    const item = screen.getByRole('menuitem', { name: '外观' });
    const move = (from: Element, to: Element) => {
      fireEvent.mouseOut(from, { relatedTarget: to });
      fireEvent.mouseOver(to, { relatedTarget: from });
    };
    move(gear, item);
    move(item, gear);
    move(gear, item);
    move(item, gear);
    // Longer than the close delay: none of these moves may close the menu.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    move(gear, outside);
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
    // Coming back reopens it; leaving from the menu side to the page closes it.
    move(outside, gear);
    const notices = screen.getByRole('menuitem', { name: '通知' });
    move(gear, notices);
    move(notices, outside);
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
  });

  it('moves into the menu when Enter or Space opens it, but not on a pointer click', async () => {
    const user = userEvent.setup();
    renderBar();
    const settings = screen.getByRole('button', { name: 'partitionSettings' });
    await user.click(settings);
    expect(screen.getByRole('menu')).toBeInTheDocument();
    expect(settings).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();

    settings.focus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('menuitem', { name: '外观' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(settings).toHaveFocus();
    await user.keyboard(' ');
    expect(screen.getByRole('menuitem', { name: '外观' })).toHaveFocus();
    await user.keyboard('{Escape}');

    // Enter on the gear while hover already opened the menu also moves into it.
    await user.hover(settings);
    settings.focus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('menuitem', { name: '外观' })).toHaveFocus();
  });

  it('hands focus back to the gear when Tab leaves the menu', async () => {
    const user = userEvent.setup();
    renderBar();
    const settings = screen.getByRole('button', { name: 'partitionSettings' });
    settings.focus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: '外观' })).toHaveFocus();
    await user.keyboard('{Tab}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(settings).toHaveFocus();
  });

  it('closes the menu when the pointer leaves it', async () => {
    const user = userEvent.setup();
    renderBar();
    await user.hover(screen.getByRole('button', { name: 'partitionSettings' }));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    await user.hover(screen.getByRole('button', { name: 'outside' }));
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
  });

  it('focuses the first menu item with ArrowDown even when hover already opened the menu', async () => {
    const user = userEvent.setup();
    renderBar();
    const settings = screen.getByRole('button', { name: 'partitionSettings' });
    await user.click(settings);
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: '外观' })).toHaveFocus();
  });

  it('supports keyboard focus, arrows, Home/End and Escape', async () => {
    const user = userEvent.setup();
    renderBar();
    const settings = screen.getByRole('button', { name: 'partitionSettings' });
    settings.focus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: '外观' })).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: '用户管理' })).toHaveFocus();
    await user.keyboard('{End}');
    expect(screen.getByRole('menuitem', { name: '通知' })).toHaveFocus();
    await user.keyboard('{Home}');
    expect(screen.getByRole('menuitem', { name: '外观' })).toHaveFocus();
    await user.keyboard('{ArrowUp}');
    expect(screen.getByRole('menuitem', { name: '通知' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(settings).toHaveFocus();
  });
});
