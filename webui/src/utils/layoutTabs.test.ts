import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  findActiveTabHref,
  isPathWithinHref,
  LAYOUT_OPEN_TABS_CHANGED_EVENT,
  nextTabAfterClose,
  readOpenTabs,
  resolveOpenTabs,
  saveOpenTabs,
} from './layoutTabs';

const items = [
  { href: '/', name: '首页' },
  { href: '/sessions', name: '工作台' },
  { href: '/workflows', name: '工作流' },
  { href: '/contracts/webui/workspaces/soc_ui/soc-alerts', name: '告警调查' },
];

describe('layoutTabs', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('matches sub-routes to their sidebar entry, home only exactly', () => {
    expect(isPathWithinHref('/workflows/wf-1/edit', '/workflows')).toBe(true);
    expect(isPathWithinHref('/workflowsx', '/workflows')).toBe(false);
    expect(isPathWithinHref('/sessions', '/')).toBe(false);
    expect(isPathWithinHref('/', '/')).toBe(true);
    expect(findActiveTabHref(items.map((item) => item.href), '/workflows/wf-1')).toBe('/workflows');
    expect(findActiveTabHref(items.map((item) => item.href), '/settings/account')).toBeNull();
  });

  it('resolves stored records in order, keeps their last path, drops unknown and duplicated hrefs', () => {
    const tabs = resolveOpenTabs(items, [
      { href: '/workflows', path: '/workflows/wf-1' },
      { href: '/removed', path: '/removed' },
      { href: '/sessions', path: '/sessions?session=s1' },
      { href: '/workflows', path: '/workflows' },
    ]);
    expect(tabs.map((tab) => [tab.href, tab.path])).toEqual([
      ['/workflows', '/workflows/wf-1'],
      ['/sessions', '/sessions?session=s1'],
    ]);
  });

  it('activates the right neighbour after closing, then the left one, then nothing', () => {
    expect(nextTabAfterClose(['a', 'b', 'c'], 'b')).toBe('c');
    expect(nextTabAfterClose(['a', 'b', 'c'], 'c')).toBe('b');
    expect(nextTabAfterClose(['a'], 'a')).toBeNull();
    expect(nextTabAfterClose(['a', 'b'], 'zzz')).toBeNull();
  });

  it('persists records and notifies listeners, tolerating bad stored data', () => {
    const listener = vi.fn();
    window.addEventListener(LAYOUT_OPEN_TABS_CHANGED_EVENT, listener);
    try {
      saveOpenTabs([{ href: '/sessions', path: '/sessions' }]);
      expect(readOpenTabs()).toEqual([{ href: '/sessions', path: '/sessions' }]);
      expect(listener).toHaveBeenCalledTimes(1);
      saveOpenTabs([]);
      expect(localStorage.getItem('flocks_layout_open_tabs')).toBeNull();

      localStorage.setItem('flocks_layout_open_tabs', JSON.stringify([{ href: '/a' }, { href: 3 }, 'x', null, { href: '/b', path: '' }]));
      expect(readOpenTabs()).toEqual([{ href: '/a', path: '/a' }, { href: '/b', path: '/b' }]);
      localStorage.setItem('flocks_layout_open_tabs', '{oops');
      expect(readOpenTabs()).toEqual([]);
    } finally {
      window.removeEventListener(LAYOUT_OPEN_TABS_CHANGED_EVENT, listener);
    }
  });
});
