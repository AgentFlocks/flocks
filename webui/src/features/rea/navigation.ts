/*
 * The plugin square opens REA in a Rex session by navigating to /sessions
 * with this router state; the session page then does what the shortcut does
 * for that action, once per navigation. Browsers keep router state across a
 * reload, so handled navigations are remembered for the tab.
 */

const HANDLED_PREFIX = 'flocks:rea:entry-navigation:';

/** ``install`` / ``use`` / ``repair`` from the plugin square; the session page routes by the live state. */
export type ReaEntryAction = 'install' | 'use' | 'repair';

export interface ReaEntryNavigationState {
  skipLastSelectedSessionRestore: true;
  reaEntry: ReaEntryAction;
}

export function reaEntryNavigationState(action: ReaEntryAction): ReaEntryNavigationState {
  return { skipLastSelectedSessionRestore: true, reaEntry: action };
}

export function reaEntryNavigationAction(state: unknown): ReaEntryAction | null {
  if (!state || typeof state !== 'object') return null;
  const action = (state as { reaEntry?: unknown }).reaEntry;
  return action === 'install' || action === 'use' || action === 'repair' ? action : null;
}

/** True when this navigation was already handled; otherwise marks it handled. */
export function consumeReaEntryNavigation(key: string): boolean {
  try {
    const storageKey = `${HANDLED_PREFIX}${key}`;
    if (window.sessionStorage.getItem(storageKey)) return true;
    window.sessionStorage.setItem(storageKey, '1');
  } catch {
    // Without storage the page's own ref still prevents a second send.
  }
  return false;
}
