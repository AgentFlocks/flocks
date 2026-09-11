import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  applyOrderByKey,
  applyWorkspacePageOrder,
  moveItem,
  readNavItemOrder,
  readWorkspacePageOrder,
  saveNavItemOrder,
  saveWorkspacePageOrder,
  WORKSPACE_PAGE_ORDER_CHANGED_EVENT,
} from './workspaceNavOrder';

const pages = [
  { id: 'soc-dashboard', title: '态势' },
  { id: 'soc-overview', title: 'SOC 总览' },
  { id: 'soc-alerts', title: '告警调查' },
];

describe('workspaceNavOrder', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('applies a stored order and appends pages that are not in it', () => {
    const ordered = applyWorkspacePageOrder(pages, ['soc-alerts', 'soc-dashboard']);
    expect(ordered.map((page) => page.id)).toEqual(['soc-alerts', 'soc-dashboard', 'soc-overview']);
  });

  it('ignores unknown and duplicated ids in the stored order', () => {
    const ordered = applyWorkspacePageOrder(pages, ['removed-page', 'soc-overview', 'soc-overview']);
    expect(ordered.map((page) => page.id)).toEqual(['soc-overview', 'soc-dashboard', 'soc-alerts']);
  });

  it('keeps the default order when nothing is stored', () => {
    expect(applyWorkspacePageOrder(pages, [])).toBe(pages);
  });

  it('moves an item to a new index and rejects out-of-range moves', () => {
    expect(moveItem(['a', 'b', 'c'], 0, 2)).toEqual(['b', 'c', 'a']);
    expect(moveItem(['a', 'b', 'c'], 2, 0)).toEqual(['c', 'a', 'b']);
    const original = ['a', 'b', 'c'];
    expect(moveItem(original, 1, 1)).toBe(original);
    expect(moveItem(original, 5, 0)).toBe(original);
    expect(moveItem(original, 0, -1)).toBe(original);
  });

  it('persists the order per workspace and notifies listeners', () => {
    const listener = vi.fn();
    window.addEventListener(WORKSPACE_PAGE_ORDER_CHANGED_EVENT, listener);
    try {
      saveWorkspacePageOrder('soc_ui', ['soc-alerts', 'soc-dashboard']);
      expect(readWorkspacePageOrder('soc_ui')).toEqual(['soc-alerts', 'soc-dashboard']);
      expect(readWorkspacePageOrder('other_ws')).toEqual([]);
      expect(listener).toHaveBeenCalledTimes(1);
      expect(listener.mock.calls[0][0]).toMatchObject({ detail: { workspaceId: 'soc_ui' } });

      saveWorkspacePageOrder('soc_ui', []);
      expect(localStorage.getItem('flocks_layout_workspace_page_order:soc_ui')).toBeNull();
    } finally {
      window.removeEventListener(WORKSPACE_PAGE_ORDER_CHANGED_EVENT, listener);
    }
  });

  it('tolerates corrupted stored values', () => {
    localStorage.setItem('flocks_layout_workspace_page_order:soc_ui', '{not json');
    expect(readWorkspacePageOrder('soc_ui')).toEqual([]);
    localStorage.setItem('flocks_layout_workspace_page_order:soc_ui', JSON.stringify({ a: 1 }));
    expect(readWorkspacePageOrder('soc_ui')).toEqual([]);
  });

  it('orders built-in sidebar entries by href and persists them per group', () => {
    const items = [{ href: '/sessions' }, { href: '/workspace' }, { href: '/tasks' }, { href: '/workflows' }];
    expect(applyOrderByKey(items, (item) => item.href, ['/workflows', '/tasks']).map((item) => item.href))
      .toEqual(['/workflows', '/tasks', '/sessions', '/workspace']);

    const listener = vi.fn();
    window.addEventListener(WORKSPACE_PAGE_ORDER_CHANGED_EVENT, listener);
    try {
      saveNavItemOrder('aiWorkbench', ['/workflows', '/sessions']);
      expect(readNavItemOrder('aiWorkbench')).toEqual(['/workflows', '/sessions']);
      expect(readNavItemOrder('agentHub')).toEqual([]);
      expect(localStorage.getItem('flocks_layout_nav_item_order:aiWorkbench')).toBe(JSON.stringify(['/workflows', '/sessions']));
      expect(listener.mock.calls[0][0]).toMatchObject({ detail: { scopeId: 'aiWorkbench' } });
      saveNavItemOrder('aiWorkbench', []);
      expect(localStorage.getItem('flocks_layout_nav_item_order:aiWorkbench')).toBeNull();
    } finally {
      window.removeEventListener(WORKSPACE_PAGE_ORDER_CHANGED_EVENT, listener);
    }
  });
});
