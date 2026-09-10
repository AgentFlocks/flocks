/**
 * Browser-style open tabs for the main layout.
 *
 * Every sidebar entry the user visits (home, AI workbench pages, agent studio
 * pages, SOC workspace pages, ...) becomes a tab in the bar above the content.
 * Tabs keep their opening order, remember the last path visited inside them
 * (a workflow detail page stays behind the "Workflows" tab), can be closed, and
 * survive reloads through localStorage.
 */

export interface OpenTabRecord {
  /** Sidebar entry href, the tab's identity. */
  href: string;
  /** Last visited location (pathname + search) inside this tab. */
  path: string;
}

export const LAYOUT_OPEN_TABS_KEY = 'flocks_layout_open_tabs';
export const LAYOUT_OPEN_TABS_CHANGED_EVENT = 'flocks:layout-open-tabs-changed';

export function readOpenTabs(): OpenTabRecord[] {
  try {
    const rawValue = localStorage.getItem(LAYOUT_OPEN_TABS_KEY);
    if (!rawValue) return [];
    const parsedValue: unknown = JSON.parse(rawValue);
    if (!Array.isArray(parsedValue)) return [];
    const records: OpenTabRecord[] = [];
    for (const item of parsedValue) {
      if (!item || typeof item !== 'object') continue;
      const { href, path } = item as { href?: unknown; path?: unknown };
      if (typeof href !== 'string' || !href) continue;
      records.push({ href, path: typeof path === 'string' && path ? path : href });
    }
    return records;
  } catch {
    return [];
  }
}

export function saveOpenTabs(records: OpenTabRecord[]): void {
  try {
    if (records.length === 0) {
      localStorage.removeItem(LAYOUT_OPEN_TABS_KEY);
    } else {
      localStorage.setItem(LAYOUT_OPEN_TABS_KEY, JSON.stringify(records));
    }
  } catch {
    // Local storage can be unavailable in restricted browser contexts.
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(LAYOUT_OPEN_TABS_CHANGED_EVENT));
  }
}

/**
 * Whether `pathname` lives under the sidebar entry `href`: exact match, or a
 * sub-route of it. Home (`/`) only matches exactly.
 */
export function isPathWithinHref(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** The sidebar entry that owns `pathname`; the longest matching href wins. */
export function findActiveTabHref(hrefs: string[], pathname: string): string | null {
  let best: string | null = null;
  for (const href of hrefs) {
    if (!isPathWithinHref(pathname, href)) continue;
    if (best === null || href.length > best.length) best = href;
  }
  return best;
}

/**
 * Map stored records onto the current sidebar entries, keeping the stored
 * order and dropping entries that no longer exist (plugin pages can go away).
 */
export function resolveOpenTabs<T extends { href: string }>(
  items: T[],
  records: OpenTabRecord[],
): Array<T & { path: string }> {
  const byHref = new Map(items.map((item) => [item.href, item]));
  const seen = new Set<string>();
  const tabs: Array<T & { path: string }> = [];
  for (const record of records) {
    const item = byHref.get(record.href);
    if (!item || seen.has(record.href)) continue;
    seen.add(record.href);
    tabs.push({ ...item, path: record.path });
  }
  return tabs;
}

/**
 * Which tab becomes active after closing `closedHref`: the one to its right,
 * otherwise the one to its left, otherwise nothing (browser behaviour).
 */
export function nextTabAfterClose(hrefs: string[], closedHref: string): string | null {
  const index = hrefs.indexOf(closedHref);
  if (index < 0) return null;
  if (index + 1 < hrefs.length) return hrefs[index + 1];
  if (index - 1 >= 0) return hrefs[index - 1];
  return null;
}
