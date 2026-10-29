import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Bot, Home, Shield } from 'lucide-react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { navItemOrderStorageKey } from '@/utils/workspaceNavOrder';
import WorkspaceModuleNav, { type HeaderWorkspace } from './WorkspaceModuleNav';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const workspaces: HeaderWorkspace[] = [
  { id: 'agent', name: 'Workbench', icon: Home },
  { id: 'scene:sample', name: 'Sample scene', icon: Shield },
  { id: 'agent-workshop', name: 'Workshop', icon: Bot, separated: true },
];
const storageKey = navItemOrderStorageKey('header-modules');

function navigation(items = workspaces, onSelectWorkspace = vi.fn()) {
  return <MemoryRouter>
    <WorkspaceModuleNav workspaces={items} currentWorkspace="agent" onSelectWorkspace={onSelectWorkspace}
      hasUnseenSuites={false} onOpenSceneManager={vi.fn()} />
  </MemoryRouter>;
}

function order(container: HTMLElement) {
  return Array.from(container.querySelectorAll('[data-header-module]')).map((item) => item.getAttribute('data-header-module'));
}

describe('header module ordering from dev', () => {
  beforeEach(() => localStorage.clear());

  it('moves a module with the keyboard and restores the order after remount', () => {
    const selected = vi.fn();
    const first = render(navigation(workspaces, selected));
    fireEvent.keyDown(screen.getByRole('button', { name: 'Workshop' }), { key: 'ArrowLeft', altKey: true });
    expect(order(first.container)).toEqual(['agent', 'agent-workshop', 'scene:sample', 'scene-management']);
    expect(selected).not.toHaveBeenCalled();
    first.unmount();
    const second = render(navigation());
    expect(order(second.container)).toEqual(['agent', 'agent-workshop', 'scene:sample', 'scene-management']);
  });

  it('retains a temporarily hidden extension scene in its saved slot', () => {
    localStorage.setItem(storageKey, JSON.stringify(['agent', 'scene:sample', 'agent-workshop', 'scene-management']));
    const view = render(navigation(workspaces.filter((item) => item.id !== 'scene:sample')));
    fireEvent.keyDown(screen.getByRole('button', { name: 'Workshop' }), { key: 'ArrowLeft', altKey: true });
    expect(JSON.parse(localStorage.getItem(storageKey)!)).toEqual(['agent-workshop', 'scene:sample', 'agent', 'scene-management']);
    view.rerender(navigation());
    expect(order(view.container)).toEqual(['agent-workshop', 'scene:sample', 'agent', 'scene-management']);
  });

  it('reorders an internal drag, ignores an external drop, and suppresses its click', () => {
    const selected = vi.fn();
    const view = render(navigation(workspaces, selected));
    const source = screen.getByRole('button', { name: 'Workshop' });
    const target = screen.getByRole('button', { name: 'Workbench' });
    const values = new Map<string, string>();
    const dataTransfer = { setData: (key: string, value: string) => values.set(key, value), getData: (key: string) => values.get(key) ?? '' };
    fireEvent.drop(target, { dataTransfer, clientX: 0 });
    expect(order(view.container)).toEqual(['agent', 'scene:sample', 'agent-workshop', 'scene-management']);
    fireEvent.dragStart(source, { dataTransfer });
    fireEvent.dragOver(target, { dataTransfer, clientX: 0 });
    fireEvent.drop(target, { dataTransfer, clientX: 0 });
    expect(order(view.container)).toEqual(['agent-workshop', 'agent', 'scene:sample', 'scene-management']);
    fireEvent.click(source);
    expect(selected).not.toHaveBeenCalled();
    fireEvent.pointerDown(source);
    fireEvent.click(source);
    expect(selected).toHaveBeenCalledWith('agent-workshop');
  });

  it('synchronizes a changed order from another browser tab', () => {
    const view = render(navigation());
    localStorage.setItem(storageKey, JSON.stringify(['scene-management', 'agent-workshop', 'agent']));
    fireEvent(window, new StorageEvent('storage', { key: storageKey }));
    expect(order(view.container)).toEqual(['scene-management', 'agent-workshop', 'agent', 'scene:sample']);
  });
});
