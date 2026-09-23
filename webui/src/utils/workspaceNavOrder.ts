/**
 * Per-browser ordering for WebUI contract workspace pages.
 *
 * The sidebar lets users drag workspace pages (for example the SOC workspace
 * second-level menu) into their preferred order. The order is stored per
 * workspace in localStorage and applied on top of the server-provided page
 * list, so plugin updates that add or remove pages keep working: unknown ids
 * are ignored and new pages are appended in their default position.
 */

export const WORKSPACE_PAGE_ORDER_KEY_PREFIX = 'flocks_layout_workspace_page_order:';
export const NAV_ITEM_ORDER_KEY_PREFIX = 'flocks_layout_nav_item_order:';
/** Fired after any sidebar order (workspace pages or built-in entries) is saved. */
export const WORKSPACE_PAGE_ORDER_CHANGED_EVENT = 'flocks:workspace-page-order-changed';

function readStoredOrder(storageKey: string): string[] {
  try {
    const rawValue = localStorage.getItem(storageKey);
    if (!rawValue) return [];
    const parsedValue: unknown = JSON.parse(rawValue);
    if (!Array.isArray(parsedValue)) return [];
    return parsedValue.filter((item): item is string => typeof item === 'string');
  } catch {
    return [];
  }
}

function saveStoredOrder(storageKey: string, keys: string[], detail: Record<string, string>): void {
  try {
    if (keys.length === 0) {
      localStorage.removeItem(storageKey);
    } else {
      localStorage.setItem(storageKey, JSON.stringify(keys));
    }
  } catch {
    // Local storage can be unavailable in restricted browser contexts.
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(WORKSPACE_PAGE_ORDER_CHANGED_EVENT, { detail }));
  }
}

export function workspacePageOrderStorageKey(workspaceId: string): string {
  return `${WORKSPACE_PAGE_ORDER_KEY_PREFIX}${workspaceId}`;
}

export function readWorkspacePageOrder(workspaceId: string): string[] {
  return readStoredOrder(workspacePageOrderStorageKey(workspaceId));
}

export function saveWorkspacePageOrder(workspaceId: string, pageIds: string[]): void {
  saveStoredOrder(workspacePageOrderStorageKey(workspaceId), pageIds, { workspaceId });
}

/** Order of built-in sidebar entries inside one group (AI workbench, agent studio...). */
export function navItemOrderStorageKey(scopeId: string): string {
  return `${NAV_ITEM_ORDER_KEY_PREFIX}${scopeId}`;
}

export function readNavItemOrder(scopeId: string): string[] {
  return readStoredOrder(navItemOrderStorageKey(scopeId));
}

export function saveNavItemOrder(scopeId: string, keys: string[]): void {
  saveStoredOrder(navItemOrderStorageKey(scopeId), keys, { scopeId });
}

/**
 * Reorder `items` following `order` (a list of keys). Items missing from
 * `order` keep their relative default position and are appended after the
 * ordered ones; keys in `order` that no longer exist are ignored.
 */
export function applyOrderByKey<T>(items: T[], keyOf: (item: T) => string, order: string[]): T[] {
  if (order.length === 0 || items.length < 2) return items;
  const byKey = new Map(items.map((item) => [keyOf(item), item]));
  const ordered: T[] = [];
  const seen = new Set<string>();
  for (const key of order) {
    const item = byKey.get(key);
    if (!item || seen.has(key)) continue;
    seen.add(key);
    ordered.push(item);
  }
  for (const item of items) {
    if (!seen.has(keyOf(item))) ordered.push(item);
  }
  return ordered;
}

/** `applyOrderByKey` for workspace pages, keyed by page id. */
export function applyWorkspacePageOrder<T extends { id: string }>(pages: T[], order: string[]): T[] {
  return applyOrderByKey(pages, (page) => page.id, order);
}

export function moveItem<T>(items: T[], fromIndex: number, toIndex: number): T[] {
  if (
    fromIndex === toIndex
    || fromIndex < 0
    || toIndex < 0
    || fromIndex >= items.length
    || toIndex >= items.length
  ) {
    return items;
  }
  const next = [...items];
  const [moved] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, moved);
  return next;
}
